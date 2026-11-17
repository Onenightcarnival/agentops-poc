"""Confirm offline score delivery and idempotency against the Langfuse Scores API."""

import argparse
import json
import time
from pathlib import Path

from evaluators.langfuse_data import api_client, scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(args.report.read_text(encoding="utf-8"))
    trace_ids = {s["trace_id"] for s in expected}
    expected_by_id = {s["score_id"]: s for s in expected}
    deadline = time.monotonic() + 60
    with api_client() as api:
        while True:
            actual = [score for trace_id in trace_ids for score in scores(api, trace_id)]
            by_id = {s["id"]: s for s in actual}
            if expected_by_id.keys() <= by_id.keys():
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Offline scores not visible in Langfuse")
            time.sleep(2)
    assert len(actual) == len(expected), "Unexpected/duplicate scores on the evaluated traces"
    for score_id, item in expected_by_id.items():
        actual_score = by_id[score_id]
        assert actual_score["name"] == item["name"]
        assert actual_score["value"] == item["value"]
        subject = actual_score["subject"]
        assert subject["kind"] == "observation"
        assert subject["id"] == item["observation_id"]
        assert subject["traceId"] == item["trace_id"]
    print(
        f"PASS: {len(expected)} unique scores on {len(trace_ids)} traces; values and observation IDs match"
    )


if __name__ == "__main__":
    main()
