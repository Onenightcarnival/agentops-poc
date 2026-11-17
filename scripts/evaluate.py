"""Read completed traces from Langfuse, evaluate offline, and write idempotent scores."""

import argparse
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from langfuse import get_client

from evaluators.langfuse_data import (
    ROOT,
    answer_from_trace,
    api_client,
    completed_trace,
    skill_calls,
)
from evaluators.rules import evaluate

EVALUATOR_VERSION = "offline-rules-v4"


def score_identity(trace_id, observation_id, name, reference):
    reference_hash = hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"agentops/{EVALUATOR_VERSION}/{trace_id}/{observation_id}/{name}/{reference_hash}",
        )
    )


def evaluate_observations(root, observations, reference):
    runs = skill_calls(root, observations)
    scores_to_write = []
    if root.get("level") == "ERROR":
        request_scores = {"task_success": 0.0}
    else:
        answer = answer_from_trace(root)
        request_scores = evaluate(
            answer, runs, reference.get("expected_contains", []), reference.get("expected_skill")
        )
    scores_to_write.extend((root, name, value) for name, value in request_scores.items())
    return scores_to_write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Smoke manifest provides trace IDs and reference expectations only",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = []
    # Read/validate every input before submitting any scores. Answers and skill
    # outputs always come from Langfuse, never from the local smoke response.
    with api_client() as api:
        for case in manifest:
            trace_id = case["response"]["trace_id"]
            reference = case["reference"]
            root, rows = completed_trace(api, trace_id)
            for observation, name, value in evaluate_observations(root, rows, reference):
                report.append(
                    {
                        "score_id": score_identity(trace_id, observation["id"], name, reference),
                        "trace_id": trace_id,
                        "observation_id": observation["id"],
                        "name": name,
                        "value": value,
                        "data_type": "NUMERIC",
                        "timestamp": observation["startTime"],
                        "environment": observation.get("environment", "default"),
                        "comment": "Offline deterministic reference checks; quality is substring match fraction, not LLM judge.",
                        "metadata": {"evaluator": EVALUATOR_VERSION, "reference": reference},
                    }
                )
    if not args.dry_run:
        client = get_client()
        try:
            for score in report:
                client.create_score(
                    **{
                        **score,
                        "timestamp": datetime.fromisoformat(
                            score["timestamp"].replace("Z", "+00:00")
                        ),
                    }
                )
            client.flush()
        finally:
            client.shutdown()
    target = (
        ROOT
        / "artifacts"
        / f"evaluation-{args.manifest.stem}{'-dry-run' if args.dry_run else ''}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"{'Prepared' if args.dry_run else 'Submitted'} {len(report)} scores for {len(manifest)} traces: {target}"
    )


if __name__ == "__main__":
    main()
