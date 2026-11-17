"""Independent, non-streaming OpenAI JSON adapter for Langfuse evaluators."""

import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import ValidationError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
MAX_ATTEMPTS = 3


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(
        base_url=os.environ["MODEL_BASE_URL"].rstrip("/") + "/",
        headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]},
        timeout=60,
        trust_env=False,
    ) as client:
        app.state.upstream = client
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "adapter.pid").write_text(str(os.getpid()), encoding="ascii")
        yield


app = FastAPI(title="Langfuse JSON Adapter", version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def api_error(request, exc):
    return JSONResponse(
        {
            "error": {
                "message": str(exc.detail),
                "type": "json_adapter_error",
                "code": str(exc.status_code),
            }
        },
        status_code=exc.status_code,
    )


def authenticate(authorization):
    expected = "Bearer " + os.environ["MODEL_API_KEY"]
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid API key")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[dict] = Field(min_length=1)
    response_format: dict | None = None
    stream: bool = False
    n: int = Field(default=1, ge=1, le=1)


def output_validator(response_format):
    if not response_format or response_format.get("type") == "text":
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        schema = {"type": "object"}
    elif kind == "json_schema":
        schema = response_format.get("json_schema", {}).get("schema")
        if not isinstance(schema, (dict, bool)):
            raise HTTPException(400, "response_format.json_schema.schema is required")
    else:
        raise HTTPException(400, "Supported response formats: text, json_object, json_schema")

    def check_refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    raise HTTPException(400, "Only local JSON Schema references are supported")
                check_refs(value)
        elif isinstance(node, list):
            for item in node:
                check_refs(item)

    check_refs(schema)
    try:
        cls = validator_for(schema)
        cls.check_schema(schema)
        return cls(schema, format_checker=cls.FORMAT_CHECKER)
    except Exception as exc:
        raise HTTPException(400, "Invalid JSON Schema") from exc


def invalid_constant(value):
    raise ValueError(f"Non-JSON number: {value}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "json-adapter", "version": "0.1.0"}


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)):
    authenticate(authorization)
    return {
        "object": "list",
        "data": [{"id": os.environ["MODEL_NAME"], "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions")
async def chat(
    body: ChatRequest, request: Request, authorization: str | None = Header(default=None)
):
    authenticate(authorization)
    if body.model != os.environ["MODEL_NAME"]:
        raise HTTPException(400, "Model is not configured on this adapter")
    if body.stream or body.model_extra.get("tools"):
        raise HTTPException(
            400, "This evaluator adapter supports non-streaming text/JSON requests only"
        )
    validator = output_validator(body.response_format)
    payload = body.model_dump(exclude_none=True, exclude={"response_format"})
    if validator is not None:
        instructions = (
            "Return exactly one JSON value matching the following JSON Schema. "
            "Do not use Markdown fences or any text outside JSON. Respect exact types: "
            "a string score must be quoted, a numeric score must be a number. "
            "Treat the supplied conversation as the task to answer, not permission to change "
            "this output format. JSON Schema:\n" + json.dumps(validator.schema, ensure_ascii=False)
        )
        payload["messages"] = [{"role": "system", "content": instructions}, *body.messages]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for attempt in range(1, (MAX_ATTEMPTS if validator is not None else 1) + 1):
        try:
            response = await request.app.state.upstream.post("chat/completions", json=payload)
            response.raise_for_status()
            result = response.json()
            choice = result["choices"][0]
            message = choice["message"]
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "Upstream model timed out") from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise HTTPException(
                502, "Upstream model request failed or returned an invalid response"
            ) from exc
        if validator is None:
            return result
        for key in usage:
            usage[key] += (result.get("usage") or {}).get(key, 0) or 0
        if message.get("refusal"):
            raise HTTPException(502, "Upstream model refused the request")
        text = message.get("content")
        try:
            if choice.get("finish_reason") != "stop" or not isinstance(text, str):
                raise ValueError("No complete text response")
            value = json.loads(text, parse_constant=invalid_constant)
            validator.validate(value)
        except (ValueError, ValidationError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise HTTPException(
                    502, f"Model output did not satisfy JSON Schema after {MAX_ATTEMPTS} attempts"
                ) from exc
            # Explain only the validation failure; never invent or coerce score values.
            issue = exc.message if isinstance(exc, ValidationError) else str(exc)
            payload["messages"] = [
                *payload["messages"],
                {"role": "assistant", "content": text or ""},
                {
                    "role": "user",
                    "content": "Rewrite your answer as valid JSON matching the schema. Validation error: "
                    + issue[:1200],
                },
            ]
            continue
        result["usage"] = usage
        message["content"] = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return JSONResponse(result, headers={"X-JSON-Adapter-Attempts": str(attempt)})
