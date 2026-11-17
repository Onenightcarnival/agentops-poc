"""Cross-check live SKILL events against native tool calls and dashboard metrics."""

import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evaluators.langfuse_data import api_client, completed_trace, observations, skill_calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    totals = Counter()
    report = []
    with api_client() as api:
        for case in cases:
            trace_id = case["response"]["trace_id"]
            root, rows = completed_trace(api, trace_id)
            by_id = {r["id"]: r for r in rows}
            expected = Counter(
                (f"skill:{s['name']}", by_id[s["observation_id"]]["metadata"]["tool_call_id"])
                for s in skill_calls(root, rows)
            )
            deadline = time.monotonic() + 60
            while True:
                events = [
                    r
                    for r in rows
                    if r["type"] == "EVENT"
                    and r.get("metadata", {}).get("instrumentation") == "skill-trigger-v3"
                ]
                actual = Counter((r["name"], r["metadata"]["tool_call_id"]) for r in events)
                if actual == expected:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(f"{trace_id}: expected {expected}, got {actual}")
                time.sleep(2)
                rows = observations(api, trace_id)
            for event in events:
                assert event["traceId"] == trace_id
                parent = by_id[event["parentObservationId"]]
                assert parent["type"] == "TOOL" and parent["name"] == "read_file"
                assert parent["metadata"]["tool_call_id"] == event["metadata"]["tool_call_id"]
                assert not event["isRootObservation"]
                assert parent["startTime"] <= event["startTime"] <= parent["endTime"]
                assert event["userId"] == root["userId"]
                assert event["sessionId"] == root["sessionId"]
                assert event["startTime"] == event["endTime"], "SKILL duration recorded"
                assert not event.get("input") and not event.get("output")
                totals[event["name"]] += 1
            report.append({"trace_id": trace_id, "skill_events": len(events)})

        now = datetime.now(UTC)
        query = {
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "aggregation": "count"}],
            "filters": [
                {
                    "column": "type",
                    "operator": "any of",
                    "value": ["EVENT"],
                    "type": "stringOptions",
                },
                {
                    "column": "metadata",
                    "key": "kind",
                    "operator": "=",
                    "value": "skill_trigger",
                    "type": "stringObject",
                },
                {
                    "column": "metadata",
                    "key": "instrumentation",
                    "operator": "=",
                    "value": "skill-trigger-v3",
                    "type": "stringObject",
                },
                {
                    "column": "traceId",
                    "operator": "any of",
                    "value": [r["trace_id"] for r in report],
                    "type": "stringOptions",
                },
            ],
            "fromTimestamp": (now - timedelta(days=7)).isoformat(),
            "toTimestamp": (now + timedelta(minutes=1)).isoformat(),
            "timeDimension": None,
            "config": {"row_limit": 100},
        }
        response = api.get("/api/public/v2/metrics", params={"query": json.dumps(query)})
        response.raise_for_status()
        metrics = response.json()["data"]
        assert {r["name"]: int(r["count_count"]) for r in metrics} == dict(totals)
    result = {"traces": report, "counts": dict(totals), "metrics": metrics}
    output = args.manifest.with_name("skill-events-" + args.manifest.name)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("PASS: native reads = linked instantaneous events = dashboard metric counts")


if __name__ == "__main__":
    main()
