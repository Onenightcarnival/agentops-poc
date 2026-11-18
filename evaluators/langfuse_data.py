"""Public API reader shared by offline evaluation and its integration checks."""

import json
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
ONLINE_SCORE_NAMES = [
    "online_task_success",
    "online_correctness",
    "online_instruction_following",
]


def json_object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object from Langfuse")
    return value


def _trace_rows(fetch_page, trace_id, fields):
    rows = []
    cursor = None
    while True:
        page = fetch_page(trace_id=trace_id, fields=fields, limit=100, cursor=cursor)
        rows.extend(row.model_dump(mode="json") for row in page.data)
        cursor = page.meta.cursor
        if not cursor:
            return rows


def observations(api, trace_id):
    return _trace_rows(api.observations.get_many, trace_id, "basic,io,metadata,time,usage")


def scores(api, trace_id):
    return _trace_rows(api.scores_v3.get_many_v3, trace_id, "core,subject,details")


def answer_from_trace(root):
    messages = json_object(root.get("output") or {}).get("messages", [])
    final = next(
        (m for m in reversed(messages) if m.get("type") == "ai" and not m.get("tool_calls")), {}
    )
    content = final.get("content", "")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def skill_calls(root, rows):
    # Native SkillsMiddleware metadata and callback tool inputs are the only source.
    skills = json_object(root.get("output") or {}).get("skills_metadata", [])
    by_path = {s["path"]: s["name"] for s in skills}
    calls = []
    for row in rows:
        if row.get("type") == "TOOL" and row.get("name") == "read_file":
            path = json_object(row.get("input") or {}).get("file_path")
            if path in by_path:
                calls.append({"name": by_path[path], "observation_id": row["id"]})
    return calls


def completed_trace(client, trace_id, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        rows = observations(client, trace_id)
        roots = [r for r in rows if r.get("name") == "agentops.chat" and r.get("isRootObservation")]
        if len(roots) > 1:
            raise ValueError(f"Ambiguous root for trace {trace_id}")
        if roots and roots[0].get("endTime"):
            root = roots[0]
            if root.get("level") == "ERROR":
                return root, rows
            messages = json_object(root.get("output") or {}).get("messages", [])
            # Callback root output includes checkpoint history; require only this turn's children.
            last_user = max(
                (i for i, m in enumerate(messages) if m.get("type") == "human"), default=-1
            )
            turn = messages[last_user + 1 :]
            expected_models = sum(m.get("type") == "ai" for m in turn)
            expected_tools = sum(m.get("type") == "tool" for m in turn)
            models = sum(r.get("type") == "GENERATION" and bool(r.get("endTime")) for r in rows)
            tools = sum(r.get("type") == "TOOL" and bool(r.get("endTime")) for r in rows)
            if turn and models >= expected_models and tools >= expected_tools:
                return root, rows
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Trace {trace_id} is incomplete in Langfuse; no scores written")
        time.sleep(2)
