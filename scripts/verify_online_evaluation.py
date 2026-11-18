"""Read-only verification of Langfuse native evaluator scores and dashboard metrics."""

import argparse
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langfuse import get_client

from evaluators.langfuse_data import (
    ONLINE_SCORE_NAMES,
    completed_trace,
    scores,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    trace_ids = [row["response"]["trace_id"] for row in cases]
    assert trace_ids and len(set(trace_ids)) == len(trace_ids), "Empty or duplicate traces"
    deadline = time.monotonic() + args.timeout
    expected_names = set(ONLINE_SCORE_NAMES)
    api = get_client().api
    roots = {trace_id: completed_trace(api, trace_id)[0] for trace_id in trace_ids}
    while True:
        grouped = {
            trace_id: [s for s in scores(api, trace_id) if s["name"] in expected_names]
            for trace_id in trace_ids
        }
        if all({s["name"] for s in rows} == expected_names for rows in grouped.values()):
            break
        if time.monotonic() >= deadline:
            missing = {
                trace_id: sorted(expected_names - {s["name"] for s in rows})
                for trace_id, rows in grouped.items()
            }
            raise TimeoutError(f"Native evaluation scores missing: {missing}")
        time.sleep(3)
    values = defaultdict(list)
    for trace_id, rows in grouped.items():
        assert len(rows) == len(expected_names), f"Duplicate scores for {trace_id}"
        for score in rows:
            assert score["source"] == "EVAL"
            assert score["dataType"] == "NUMERIC"
            assert math.isfinite(score["value"]) and 0 <= score["value"] <= 1
            assert score["comment"] and "\ufffd" not in score["comment"]
            assert score["subject"] == {
                "kind": "observation",
                "id": roots[trace_id]["id"],
                "traceId": trace_id,
            }
            assert score["metadata"]["job_configuration_id"]
            values[score["name"]].append(score["value"])
    query = {
        "view": "scores-numeric",
        "dimensions": [{"field": "name"}],
        "metrics": [
            {"measure": "value", "aggregation": "avg"},
            {"measure": "count", "aggregation": "count"},
        ],
        "filters": [
            {
                "column": "name",
                "operator": "any of",
                "value": ONLINE_SCORE_NAMES,
                "type": "stringOptions",
            },
            {
                "column": "source",
                "operator": "any of",
                "value": ["EVAL"],
                "type": "stringOptions",
            },
            {
                "column": "traceId",
                "operator": "any of",
                "value": trace_ids,
                "type": "stringOptions",
            },
        ],
        "fromTimestamp": min(root["startTime"] for root in roots.values()),
        "toTimestamp": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "timeDimension": None,
        "config": {"row_limit": 100},
    }
    metrics = api.metrics.metrics(query=json.dumps(query)).data
    assert {row["name"] for row in metrics} == expected_names
    for row in metrics:
        batch = values[row["name"]]
        assert int(row["count_count"]) == len(batch)
        assert math.isclose(row["avg_value"], sum(batch) / len(batch), abs_tol=1e-8)
    output = args.manifest.with_name("online-evaluation-" + args.manifest.stem + ".json")
    output.write_text(
        json.dumps({"scores": grouped, "metrics": metrics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"metrics": metrics}, ensure_ascii=False))
    print(
        f"PASS: {len(trace_ids) * len(expected_names)} native EVAL scores on Agent roots; metrics match"
    )


if __name__ == "__main__":
    main()
