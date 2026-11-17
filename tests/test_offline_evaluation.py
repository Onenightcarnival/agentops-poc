import pytest

from evaluators.langfuse_data import completed_trace, skill_calls
from scripts.evaluate import evaluate_observations, score_identity


def native_root():
    return {
        "id": "root",
        "name": "agentops.chat",
        "isRootObservation": True,
        "endTime": "now",
        "level": "DEFAULT",
        "output": {
            "messages": [
                {"type": "human", "content": "hi"},
                {"type": "ai", "content": "", "tool_calls": [{"name": "read_file"}]},
                {"type": "tool", "content": "instructions"},
                {"type": "ai", "content": "25.00 CNY"},
            ],
            "skills_metadata": [{"name": "order-total", "path": "/skills/order-total/SKILL.md"}],
        },
    }


def test_native_tool_reads_feed_evaluation_without_custom_events():
    root = native_root()
    skill = {
        "id": "read",
        "type": "TOOL",
        "name": "read_file",
        "input": {"file_path": "/skills/order-total/SKILL.md"},
    }
    other = {
        "id": "other",
        "type": "TOOL",
        "name": "read_file",
        "input": {"file_path": "/other.txt"},
    }
    assert skill_calls(root, [skill, other]) == [{"name": "order-total", "observation_id": "read"}]
    results = evaluate_observations(
        root,
        [root, skill, other],
        {"expected_skill": "order-total", "expected_contains": ["25.00"]},
    )
    assert {name: value for _, name, value in results} == {
        "response_nonempty": 1,
        "task_success": 1,
        "quality": 1,
    }


def test_score_identity_is_idempotent_and_reference_versioned():
    args = ("trace", "root", "quality")
    assert score_identity(*args, {"expected_contains": ["a"]}) == score_identity(
        *args, {"expected_contains": ["a"]}
    )
    assert score_identity(*args, {"expected_contains": ["a"]}) != score_identity(
        *args, {"expected_contains": ["b"]}
    )


def test_waits_for_callback_children_not_custom_status(monkeypatch):
    rows = [native_root()]
    monkeypatch.setattr("evaluators.langfuse_data.observations", lambda *args: rows)
    with pytest.raises(TimeoutError):
        completed_trace(None, "trace", timeout=0)
    rows.extend(
        [
            {"id": "g1", "type": "GENERATION", "endTime": "now"},
            {"id": "g2", "type": "GENERATION", "endTime": "now"},
            {"id": "tool", "type": "TOOL", "endTime": "now"},
        ]
    )
    assert completed_trace(None, "trace", timeout=0)[0]["id"] == "root"
