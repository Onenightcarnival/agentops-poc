import copy
import json
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evaluators.llm import Judgment, evaluation_key, evidence, score_records


def root():
    return {
        "id": "root",
        "traceId": "trace",
        "startTime": "2026-11-17T00:00:00Z",
        "level": "DEFAULT",
        "output": {
            "messages": [
                {"type": "human", "content": "Old request"},
                {"type": "ai", "content": "Old answer"},
                {"type": "human", "content": "New request"},
                {"type": "ai", "content": "New answer"},
            ]
        },
    }


def judgment():
    return {
        name: {"value": 0.75, "reason": "存在一处具体的小问题。"}
        for name in ("task_success", "correctness", "instruction_following")
    }


def test_only_latest_turn_is_graded_and_native_tools_are_evidence():
    r = root()
    payload = evidence(
        r,
        [
            {"id": "event", "type": "EVENT", "name": "skill:order-total"},
            {
                "id": "tool",
                "type": "TOOL",
                "name": "read_file",
                "input": {"file_path": "SKILL.md"},
                "output": "native instructions",
            },
        ],
        {"expected_contains": ["hint"]},
    )
    assert payload["current_request"]["content"] == "New request"
    assert payload["current_turn"] == [{"type": "ai", "content": "New answer"}]
    assert len(payload["history"]) == 2
    assert len(payload["tools"]) == 1 and payload["tools"][0]["output"] == "native instructions"
    assert r == root(), "Evidence extraction must not mutate trace data"


def test_failed_trace_has_no_old_final_answer():
    r = root()
    r.update(
        level="ERROR",
        output=None,
        input={"messages": [{"role": "user", "content": "Failed request"}]},
    )
    payload = evidence(r, [], {})
    assert payload["current_request"]["content"] == "Failed request"
    assert payload["current_turn"] == []


def test_failed_trace_with_stale_checkpoint_does_not_grade_previous_answer():
    r = root()
    r.update(level="ERROR", input={"messages": [{"role": "user", "content": "Failed request"}]})
    payload = evidence(r, [], {})
    assert payload["current_request"]["content"] == "Failed request"
    assert payload["current_turn"] == []
    assert len(payload["history"]) == 4


@pytest.mark.parametrize("value", [-1, 1.01, "0.8", True, float("nan"), float("inf")])
def test_invalid_model_scores_are_rejected(value):
    data = judgment()
    data["correctness"]["value"] = value
    with pytest.raises(ValidationError):
        Judgment.model_validate(data)


def test_incomplete_or_extra_output_is_rejected():
    data = judgment()
    del data["correctness"]
    with pytest.raises(ValidationError):
        Judgment.model_validate(data)
    with pytest.raises(ValidationError):
        Judgment.model_validate({**judgment(), "extra_score": 1})


def test_ids_are_stable_but_change_with_evidence_model_or_reference():
    r = root()
    payload = evidence(r, [], {})
    key = evaluation_key(r, payload, "model-a", "local")
    rows = score_records(r, Judgment.model_validate(judgment()), key, "model-a", {})
    repeated = score_records(r, Judgment.model_validate(judgment()), key, "model-a", {})
    assert rows == repeated
    assert len({s["score_id"] for s in rows}) == 3
    assert all(s["observation_id"] == "root" and s["comment"] for s in rows)
    assert evaluation_key(r, payload, "model-b", "local") != key
    changed = copy.deepcopy(payload)
    changed["reference"] = {"expected_contains": ["different"]}
    assert evaluation_key(r, changed, "model-a", "local") != key
    changed = copy.deepcopy(payload)
    changed["current_turn"][0]["content"] = "different answer"
    assert evaluation_key(r, changed, "model-a", "local") != key


def test_bad_judgment_prevents_all_score_writes(tmp_path, monkeypatch):
    from scripts import evaluate_llm

    for name, value in {
        "MODEL_NAME": "test",
        "MODEL_BASE_URL": "http://localhost",
        "MODEL_API_KEY": "test",
    }.items():
        monkeypatch.setenv(name, value)
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([{"response": {"trace_id": t}} for t in ["one", "two"]]))
    monkeypatch.setattr(sys, "argv", ["evaluate_llm", "--manifest", str(manifest)])
    monkeypatch.setattr(evaluate_llm, "ROOT", tmp_path)
    monkeypatch.setattr(evaluate_llm, "api_client", lambda: nullcontext(None))
    monkeypatch.setattr(
        evaluate_llm, "completed_trace", lambda api, t: ({**root(), "traceId": t}, [])
    )
    responses = iter([json.dumps(judgment()), '{"task_success": {"value": 7}}'])
    monkeypatch.setattr(
        evaluate_llm,
        "ChatOpenAI",
        lambda **kw: SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(content=next(responses), usage_metadata={})
        ),
    )

    def unexpected_write():
        pytest.fail("No score client should be created before all judgments validate")

    monkeypatch.setattr(evaluate_llm, "get_client", unexpected_write)
    with pytest.raises(ValidationError):
        evaluate_llm.main()
    assert len(list((tmp_path / "artifacts" / "llm-judge-cache").glob("*.json"))) == 1
