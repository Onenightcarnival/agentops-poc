"""Judge existing Langfuse traces with a model, independently of the Agent service."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from langchain_openai import ChatOpenAI
from langfuse import get_client

from evaluators.langfuse_data import ROOT, api_client, completed_trace
from evaluators.llm import (
    SYSTEM_PROMPT,
    Judgment,
    canonical,
    evaluation_key,
    evidence,
    score_records,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--manifest", type=Path, help="Trace IDs and optional reference expectations"
    )
    source.add_argument(
        "--trace-id", action="append", help="Repeat for multiple traces; no reference required"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the judge and save the report without writing scores",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rejudge instead of using validated cached results; score IDs stay stable",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--max-input-chars",
        type=int,
        default=120000,
        help="Fail rather than silently truncate evaluation evidence",
    )
    args = parser.parse_args()
    cases = (
        json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.manifest
        else [{"response": {"trace_id": trace_id}, "reference": {}} for trace_id in args.trace_id]
    )
    model = os.getenv("JUDGE_MODEL_NAME") or os.environ["MODEL_NAME"]
    base_url = os.getenv("JUDGE_MODEL_BASE_URL") or os.environ["MODEL_BASE_URL"]
    judge = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=os.getenv("JUDGE_MODEL_API_KEY") or os.environ["MODEL_API_KEY"],
        use_responses_api=False,
        timeout=120,
        max_retries=2,
    )
    cache_dir = ROOT / "artifacts" / "llm-judge-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    seen = set()
    # Validate all traces before spending model calls or writing scores.
    with api_client() as api:
        for case in cases:
            trace_id = case["response"]["trace_id"]
            if trace_id in seen:
                raise ValueError(f"Duplicate trace in input: {trace_id}")
            seen.add(trace_id)
            root, rows = completed_trace(api, trace_id)
            payload = evidence(root, rows, case.get("reference", {}))
            if len(canonical(payload)) > args.max_input_chars:
                raise ValueError(f"Trace {trace_id} exceeds --max-input-chars; no scores written")
            prepared.append((root, payload))
    if not prepared:
        raise ValueError("No traces to evaluate")

    report = []
    for root, payload in prepared:
        key = evaluation_key(root, payload, model, base_url)
        cache = cache_dir / f"{key}.json"
        cached = cache.exists() and not args.refresh
        if cached:
            result = json.loads(cache.read_text(encoding="utf-8"))
            judgment = Judgment.model_validate(result["judgment"])
        else:
            response = judge.invoke(
                [
                    ("system", SYSTEM_PROMPT),
                    ("human", canonical(payload)),
                ]
            )
            # Malformed, missing or out-of-range values fail closed: no fabricated scores.
            judgment = Judgment.model_validate_json(response.content)
            result = {"judgment": judgment.model_dump(), "usage": response.usage_metadata or {}}
            cache.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report.extend(score_records(root, judgment, key, model, result["usage"]))
        print(
            json.dumps(
                {
                    "trace_id": root["traceId"],
                    "cached": cached,
                    "scores": {
                        name: score["value"] for name, score in judgment.model_dump().items()
                    },
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    label = args.manifest.stem if args.manifest else "traces"
    target = (
        args.output
        or ROOT / "artifacts" / f"llm-evaluation-{label}{'-dry-run' if args.dry_run else ''}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Nothing is submitted until all judgments have passed schema validation.
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
    print(f"{'Prepared' if args.dry_run else 'Submitted'} {len(report)} model scores: {target}")


if __name__ == "__main__":
    main()
