"""Single-file DeepAgents + FastAPI demo; native streaming and Langfuse callback."""

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemPermission
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from langchain_openai import ChatOpenAI
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
langfuse = get_client()


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpoint = ROOT / os.getenv("CHECKPOINT_PATH", "data/checkpoints.sqlite")
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        app.state.agent = create_deep_agent(
            model=ChatOpenAI(
                model=os.environ["MODEL_NAME"],
                base_url=os.environ["MODEL_BASE_URL"],
                api_key=os.environ["MODEL_API_KEY"],
                use_responses_api=False,
                stream_usage=True,
                timeout=90,
            ),
            system_prompt="Answer concisely in Chinese. Preserve original inputs. Use conversation history for follow-up questions.",
            backend=FilesystemBackend(root_dir=str(ROOT / "agent_workspace"), virtual_mode=True),
            permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],
            skills=["/skills/"],
            checkpointer=saver,
        )
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "server.pid").write_text(str(os.getpid()), encoding="ascii")
        try:
            yield
        finally:
            await asyncio.to_thread(langfuse.shutdown)


app = FastAPI(title="AgentOps POC", version="0.6.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=12000)


@app.post("/chat")
async def chat(body: ChatRequest, request: Request):
    trace_id = uuid.uuid4().hex

    async def stream():
        with propagate_attributes(
            user_id=body.user_id,
            session_id=json.dumps([body.user_id, body.session_id]),
            version="0.6.0",
        ):
            async for chunk in request.app.state.agent.astream(
                {"messages": [{"role": "user", "content": body.message}]},
                config={
                    "configurable": {"thread_id": json.dumps([body.user_id, body.session_id])},
                    "callbacks": [CallbackHandler(trace_context={"trace_id": trace_id})],
                    "run_name": "agentops.chat",
                    "recursion_limit": 32,
                },
                stream_mode=["messages", "updates"],
                version="v2",
                durability="sync",
            ):
                yield json.dumps(jsonable_encoder(chunk), ensure_ascii=False) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"X-Langfuse-Trace-Id": trace_id, "Cache-Control": "no-cache"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.6.0", "deepagents_version": "0.6.2"}


@app.post("/telemetry/flush")
async def flush():
    await asyncio.to_thread(langfuse.flush)
    return {"status": "flushed"}
