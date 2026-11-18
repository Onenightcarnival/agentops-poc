"""Confirm offline score delivery and idempotency against the Langfuse Scores API."""

import argparse
import json
import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langfuse import get_client

from evaluators.langfuse_data import scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--check-metrics",
        action="store_true",
        help="Also compare dashboard counts and averages for this batch",
    )
    args = parser.parse_args()
    expected = json.loads(args.report.read_text(encoding="utf-8"))
    trace_ids = {s["trace_id"] for s in expected}
    expected_by_id = {s["score_id"]: s for s in expected}
    deadline = time.monotonic() + 60
    api = get_client().api
    while True:
        actual = [score for trace_id in trace_ids for score in scores(api, trace_id)]
        by_id = {s["id"]: s for s in actual}
        if expected_by_id.keys() <= by_id.keys():
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Offline scores not visible in Langfuse")
        time.sleep(2)
    assert len(expected_by_id) == len(expected), "Duplicate score IDs in report"
    assert sum(s["id"] in expected_by_id for s in actual) == len(expected), "Duplicate scores"
    for score_id, item in expected_by_id.items():
        actual_score = by_id[score_id]
        assert actual_score["name"] == item["name"]
        assert actual_score["value"] == item["value"]
        subject = actual_score["subject"]
        assert subject["kind"] == "observation"
        assert subject["id"] == item["observation_id"]
        assert subject["traceId"] == item["trace_id"]
        assert actual_score.get("comment") == item["comment"]
        for key, value in item.get("metadata", {}).items():
            assert actual_score.get("metadata", {}).get(key) == value
    if args.check_metrics:
        grouped = defaultdict(list)
        for item in expected:
            grouped[item["name"]].append(item["value"])
        now = datetime.now(UTC)
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
                    "value": list(grouped),
                    "type": "stringOptions",
                },
                {
                    "column": "traceId",
                    "operator": "any of",
                    "value": list(trace_ids),
                    "type": "stringOptions",
                },
            ],
            "fromTimestamp": min(s["timestamp"] for s in expected),
            "toTimestamp": (now + timedelta(minutes=1)).isoformat(),
            "timeDimension": None,
            "config": {"row_limit": 100},
        }
        metrics = api.metrics.metrics(query=json.dumps(query)).data
        assert {r["name"] for r in metrics} == set(grouped)
        for row in metrics:
            values = grouped[row["name"]]
            assert int(row["count_count"]) == len(values)
            assert math.isclose(row["avg_value"], sum(values) / len(values), abs_tol=1e-8)
        print(json.dumps({"dashboard_metrics": metrics}, ensure_ascii=False, indent=2))
    print(
        f"PASS: {len(expected)} unique scores on {len(trace_ids)} traces; values and observation IDs match"
    )


if __name__ == "__main__":
    main()
