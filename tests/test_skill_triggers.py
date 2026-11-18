import asyncio
import uuid
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.main as serving


@pytest.fixture
def telemetry(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    key = "pk-test-" + uuid.uuid4().hex
    client = Langfuse(
        public_key=key,
        secret_key="test-only",
        tracer_provider=provider,
        should_export_span=lambda span: False,
    )
    monkeypatch.setattr(serving, "langfuse", client)
    yield exporter, key
    client.shutdown()


def events_and_parents(exporter):
    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    events = [s for s in spans if s.name.startswith("skill:")]
    for event in events:
        parent = by_id[event.parent.span_id]
        assert parent.name == "read_file"
        assert event.context.trace_id == parent.context.trace_id
        assert event.start_time == event.end_time
        assert parent.start_time <= event.start_time <= parent.end_time
        key = "langfuse.observation.metadata.tool_call_id"
        assert event.attributes[key] == parent.attributes[key]
        assert not event.attributes.get("langfuse.internal.as_root")
    return events


def request(callback, *, path="/skills/order-total/SKILL.md", discovered=True):
    return SimpleNamespace(
        tool_call={
            "name": "read_file",
            "args": {"file_path": path},
            "id": "call-1",
            "type": "tool_call",
        },
        state={
            "skills_metadata": [{"name": "order-total", "path": "/skills/order-total/SKILL.md"}]
            if discovered
            else []
        },
        runtime=SimpleNamespace(config={"callbacks": [callback], "metadata": {"existing": True}}),
    )


@pytest.mark.parametrize("error", [None, ValueError("read failed"), asyncio.CancelledError()])
def test_event_is_inside_native_tool_even_on_error_or_cancel(telemetry, error):
    exporter, key = telemetry
    req = request(serving.CallbackHandler(public_key=key))

    async def read_file(file_path: str):
        # Event exists before the native implementation runs.
        assert len(exporter.get_finished_spans()) == 1
        if error:
            raise error
        return "native instructions"

    tool = StructuredTool.from_function(coroutine=read_file, description="Read a file")

    async def handler(actual):
        assert actual is req
        assert actual.runtime.config["metadata"]["existing"] is True
        return await tool.ainvoke(actual.tool_call, config=actual.runtime.config)

    if error:
        with pytest.raises(type(error)) as caught:
            asyncio.run(serving.skill_triggers.awrap_tool_call(req, handler))
        assert caught.value is error
    else:
        result = asyncio.run(serving.skill_triggers.awrap_tool_call(req, handler))
        assert result.content == "native instructions"
    assert len(events_and_parents(exporter)) == 1


@pytest.mark.parametrize(
    "path,discovered",
    [
        ("/other/SKILL.md", True),
        ("/skills/order-total/ref.txt", True),
        ("/skills/order-total/SKILL.md", False),
    ],
)
def test_unregistered_reads_do_not_add_metadata(telemetry, path, discovered):
    exporter, key = telemetry
    req = request(serving.CallbackHandler(public_key=key), path=path, discovered=discovered)
    response = object()

    async def handler(actual):
        assert actual is req
        assert actual.runtime.config["metadata"] == {"existing": True}
        return response

    assert asyncio.run(serving.skill_triggers.awrap_tool_call(req, handler)) is response
    assert not events_and_parents(exporter)


class NativeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        assert "read_file" in {t.name for t in tools}
        assert "run_skill" not in {t.name for t in tools}
        return self


def test_native_parallel_reads_repeats_and_concurrent_requests(telemetry):
    exporter, key = telemetry

    async def run():
        calls = [
            {
                "name": "read_file",
                "args": {"file_path": f"/skills/{name}/SKILL.md"},
                "id": f"call-{i}",
                "type": "tool_call",
            }
            for i, name in enumerate(["order-total", "order-total", "text-digest"])
        ]
        calls.append(
            {"name": "ls", "args": {"path": "/skills/"}, "id": "ordinary", "type": "tool_call"}
        )
        agent = create_deep_agent(
            model=NativeModel(
                messages=iter([AIMessage(content="", tool_calls=calls), AIMessage(content="done")])
            ),
            backend=FilesystemBackend(
                root_dir=str(serving.ROOT / "agent_workspace"), virtual_mode=True
            ),
            skills=["/skills/"],
            middleware=[serving.skill_triggers],
        )
        trace_id = uuid.uuid4().hex
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "read skills"}]},
            config={
                "callbacks": [
                    serving.CallbackHandler(public_key=key, trace_context={"trace_id": trace_id})
                ]
            },
        )
        reads = [
            m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "read_file"
        ]
        assert len(reads) == 3 and all(m.status == "success" for m in reads)
        assert "quantity" in reads[0].content
        return int(trace_id, 16)

    async def execute():
        return await asyncio.gather(run(), run())

    traces = asyncio.run(execute())
    events = events_and_parents(exporter)
    assert len(events) == 6
    for event in events:
        name = event.name.removeprefix("skill:")
        assert event.attributes["langfuse.observation.metadata.skill_id"] == f"agentops.{name}"
        assert event.attributes["langfuse.observation.metadata.skill_version"] == "1.0.0"
    for trace_id in traces:
        assert sum(e.context.trace_id == trace_id for e in events) == 3
    ordinary = [s for s in exporter.get_finished_spans() if s.name == "ls"]
    assert len(ordinary) == 2
    assert all("langfuse.observation.metadata.skill_trigger" not in s.attributes for s in ordinary)
