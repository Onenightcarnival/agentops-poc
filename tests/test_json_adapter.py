import json

import httpx
import pytest
from fastapi.testclient import TestClient

import json_adapter

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["score", "reasoning"],
    "additionalProperties": False,
}
BODY = {
    "model": "test-model",
    "messages": [{"role": "user", "content": "Evaluate this answer"}],
    "response_format": {
        "type": "json_schema",
        "json_schema": {"name": "evaluation", "strict": True, "schema": SCHEMA},
    },
}
HEADERS = {"Authorization": "Bearer test-key"}


def completion(content, **kwargs):
    return {
        "id": "test-completion",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content, **kwargs},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    monkeypatch.setenv("MODEL_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setattr(json_adapter, "ROOT", tmp_path)
    with TestClient(json_adapter.app) as client:
        yield client


def mock_upstream(monkeypatch, replies):
    calls = []
    replies = iter(replies)

    def respond(request):
        calls.append(json.loads(request.content))
        reply = next(replies)
        return httpx.Response(200, json=reply)

    client = httpx.AsyncClient(
        base_url="http://upstream.test/v1/", transport=httpx.MockTransport(respond)
    )
    monkeypatch.setattr(json_adapter.app.state, "upstream", client)
    return calls


def test_schema_is_prompted_and_validated_without_coercion(adapter, monkeypatch):
    calls = mock_upstream(
        monkeypatch,
        [
            completion('{"score": "1", "reasoning": "wrong type"}'),
            completion('{"score": 0.75, "reasoning": "minor issue"}'),
        ],
    )
    response = adapter.post("/v1/chat/completions", json=BODY, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["x-json-adapter-attempts"] == "2"
    result = response.json()
    assert json.loads(result["choices"][0]["message"]["content"])["score"] == 0.75
    assert result["usage"]["total_tokens"] == 30
    assert "JSON Schema" in calls[0]["messages"][0]["content"]
    assert calls[0]["messages"][1:] == BODY["messages"]
    assert "response_format" not in calls[0]
    assert "Validation error" in calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"score": 7, "reasoning": "out of range"}',
        '{"score": true, "reasoning": "wrong type"}',
        '{"score": NaN, "reasoning": "invalid JSON"}',
    ],
)
def test_invalid_outputs_fail_after_bounded_retries(adapter, monkeypatch, text):
    calls = mock_upstream(monkeypatch, [completion(text)] * 3)
    response = adapter.post("/v1/chat/completions", json=BODY, headers=HEADERS)
    assert response.status_code == 502 and len(calls) == 3
    assert "choices" not in response.json()


def test_refusal_is_not_retried_or_converted_to_a_score(adapter, monkeypatch):
    calls = mock_upstream(monkeypatch, [completion(None, refusal="Cannot comply")])
    response = adapter.post("/v1/chat/completions", json=BODY, headers=HEADERS)
    assert response.status_code == 502 and len(calls) == 1


def test_authentication_and_unsupported_requests(adapter, monkeypatch):
    calls = mock_upstream(monkeypatch, [])
    assert adapter.post("/v1/chat/completions", json=BODY).status_code == 401
    for overrides in [{"model": "other"}, {"stream": True}, {"tools": [{"type": "function"}]}]:
        assert (
            adapter.post(
                "/v1/chat/completions", json={**BODY, **overrides}, headers=HEADERS
            ).status_code
            == 400
        )
    external = {
        **BODY,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"schema": {"$ref": "http://example.com/schema"}},
        },
    }
    assert adapter.post("/v1/chat/completions", json=external, headers=HEADERS).status_code == 400
    assert not calls


def test_plain_connection_probe_passes_through(adapter, monkeypatch):
    calls = mock_upstream(monkeypatch, [completion("Hello")])
    body = {k: v for k, v in BODY.items() if k != "response_format"}
    response = adapter.post("/v1/chat/completions", json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Hello"
    assert calls[0]["messages"] == BODY["messages"]
