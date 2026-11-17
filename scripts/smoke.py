"""Real HTTP + model smoke requests; creates live Langfuse data, not fixtures."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument(
        "--assert-unscored",
        action="store_true",
        help="Read Langfuse after ingestion and verify serving created zero scores",
    )
    args = parser.parse_args()
    run = args.run_id
    cases = [
        {
            "user_id": "poc-alice",
            "session_id": run + "-orders",
            "message": '请用 order-total 技能计算订单总额：{"items":[{"unit_price":"12.50","quantity":2},{"unit_price":"7.20","quantity":3}]}。请记住订单代号 ORCHID-731。',
            "evaluation": {"expected_skill": "order-total", "expected_contains": ["46.60"]},
        },
        {
            "user_id": "poc-alice",
            "session_id": run + "-orders",
            "message": "刚才的订单代号和总额是什么？直接回答，不用重新计算。",
            "evaluation": {"expected_contains": ["ORCHID-731", "46.60"]},
        },
        {
            "user_id": "poc-bob",
            "session_id": run + "-text",
            "message": "请使用 text-digest 技能统计这段原文：hello agent ops。原文不含句号，只包含这三个英文单词和两个空格。",
            "evaluation": {"expected_skill": "text-digest", "expected_contains": ["15", "3"]},
        },
        {
            "user_id": "poc-carol",
            "session_id": run + "-orders",
            "message": '用 order-total 技能处理原始订单：{"items":[{"unit_price":"10.00","quantity":-2}]}。请按技能步骤检查原始输入，无效时报告问题，不要修正参数。',
            "evaluation": {"expected_skill": "order-total", "expected_contains": ["quantity"]},
        },
        {
            "user_id": "poc-bob",
            "session_id": run + "-orders",
            "message": "我之前有没有告诉你一个订单代号？如果没有请回答：没有记录。不要猜测。",
            "evaluation": {"expected_contains": ["没有记录"]},
        },
    ]
    results = []
    with httpx.Client(base_url=args.base_url, timeout=240, trust_env=False) as client:
        client.get("/health").raise_for_status()
        for index, case in enumerate(cases):
            reference = case.pop("evaluation")
            chunks = []
            with client.stream("POST", "/chat", json=case) as response:
                response.raise_for_status()
                assert response.headers["content-type"].startswith("application/x-ndjson")
                trace_id = response.headers["x-langfuse-trace-id"]
                for line in response.iter_lines():
                    if line:
                        chunks.append(json.loads(line))
            assert chunks and all(set(c) == {"type", "ns", "data"} for c in chunks)
            assert any(c["type"] == "messages" for c in chunks), "Missing native message chunks"
            data = {"trace_id": trace_id}
            results.append(
                {
                    "case": index + 1,
                    "request": case,
                    "reference": reference,
                    "response": data,
                    "chunks": len(chunks),
                }
            )
            print(
                json.dumps(
                    {
                        "case": index + 1,
                        "chunks": len(chunks),
                        "trace_id": data["trace_id"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        client.post("/telemetry/flush").raise_for_status()
    # Operational metrics come from Langfuse, never from hand-built SSE fields.
    from evaluators.langfuse_data import (
        answer_from_trace,
        api_client,
        completed_trace,
        scores,
        skill_calls,
    )

    with api_client() as api:
        for row in results:
            trace_id = row["response"]["trace_id"]
            root, observations = completed_trace(api, trace_id)
            generations = [r for r in observations if r["type"] == "GENERATION"]
            triggers = skill_calls(root, observations)
            total_tokens = sum(r.get("totalUsage") or 0 for r in generations)
            assert total_tokens > 0, "Langfuse callback model usage missing"
            row["response"]["answer"] = answer_from_trace(root)
            assert row["response"]["answer"]
            expected = row["reference"].get("expected_skill")
            if expected:
                assert any(r["name"] == expected for r in triggers)
            if args.assert_unscored:
                assert not scores(api, trace_id), "Serving unexpectedly wrote scores"
            row["telemetry"] = {
                "total_tokens": total_tokens,
                "model_calls": len(generations),
                "skill_triggers": len(triggers),
            }
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / f"smoke-{run}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("PASS: streaming model usage and skills; references saved for independent evaluation")


if __name__ == "__main__":
    main()
