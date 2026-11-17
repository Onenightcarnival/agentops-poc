import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.encoders import jsonable_encoder
from langchain_core.messages import AIMessageChunk
from pydantic import ValidationError

import app.main as serving


class Agent:
    def __init__(self):
        self.configs = []
        self.chunks = [
            {
                "type": "messages",
                "ns": (),
                "data": (AIMessageChunk(content="hello", id="msg"), {"node": "model"}),
            },
            {
                "type": "updates",
                "ns": (),
                "data": {"model": {"messages": [{"type": "ai", "content": "hello"}]}},
            },
        ]

    async def astream(self, inputs, **kwargs):
        assert kwargs["stream_mode"] == ["messages", "updates"]
        assert kwargs["version"] == "v2"
        self.configs.append(kwargs["config"])
        for chunk in self.chunks:
            yield chunk


def context(monkeypatch):
    states = []

    @contextmanager
    def propagate(**kwargs):
        states.append(kwargs)
        try:
            yield
        finally:
            states.append("closed")

    monkeypatch.setattr(serving, "propagate_attributes", propagate)
    monkeypatch.setattr(serving, "CallbackHandler", lambda **kwargs: kwargs)
    return states


def test_raw_native_chunks_only_and_identity_isolation(monkeypatch):
    states = context(monkeypatch)
    agent = Agent()

    async def execute(user):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=agent)))
        response = await serving.chat(
            serving.ChatRequest(user_id=user, session_id="demo", message="hi"), request
        )
        chunks = [json.loads(chunk) async for chunk in response.body_iterator]
        assert agent.configs[-1]["callbacks"] == [
            {"trace_context": {"trace_id": response.headers["x-langfuse-trace-id"]}}
        ]
        return chunks

    actual = asyncio.run(execute("alice"))
    asyncio.run(execute("bob"))
    assert actual == jsonable_encoder(agent.chunks)
    assert (
        agent.configs[0]["configurable"]["thread_id"]
        != agent.configs[1]["configurable"]["thread_id"]
    )
    assert states[-1] == "closed"


def test_closing_stream_exits_propagation(monkeypatch):
    states = context(monkeypatch)

    async def execute():
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=Agent())))
        response = await serving.chat(
            serving.ChatRequest(user_id="u", session_id="s", message="hi"), request
        )
        stream = response.body_iterator
        await anext(stream)
        await stream.aclose()

    asyncio.run(execute())
    assert states[-1] == "closed"


def test_route_returns_streaming_response(monkeypatch):
    context(monkeypatch)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent=Agent())))
    response = asyncio.run(
        serving.chat(serving.ChatRequest(user_id="u", session_id="s", message="hi"), request)
    )
    assert response.media_type == "application/x-ndjson"
    assert len(response.headers["x-langfuse-trace-id"]) == 32


def test_evaluation_is_not_a_request_field():
    with pytest.raises(ValidationError):
        serving.ChatRequest(user_id="u", session_id="s", message="hi", evaluation={})
