"""Read back actual ingested observations and aggregate metrics through public v2 APIs."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

from evaluators.llm import SCORE_NAMES

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main():
    now = datetime.now(UTC)
    common = {
        "fromTimestamp": (now - timedelta(days=7)).isoformat(),
        "toTimestamp": (now + timedelta(minutes=1)).isoformat(),
        "timeDimension": None,
        "config": {"row_limit": 100},
    }
    generation = [
        {"column": "type", "operator": "any of", "value": ["GENERATION"], "type": "stringOptions"}
    ]
    queries = {
        "model_usage": {
            "view": "observations",
            "dimensions": [{"field": "providedModelName"}],
            "metrics": [
                {"measure": "count", "aggregation": "count"},
                {"measure": "totalTokens", "aggregation": "sum"},
            ],
            "filters": generation,
            "orderBy": [{"field": "count_count", "direction": "desc"}],
        },
        "user_usage": {
            "view": "observations",
            "dimensions": [{"field": "userId"}],
            "metrics": [{"measure": "totalTokens", "aggregation": "sum"}],
            "filters": generation,
            "orderBy": [{"field": "sum_totalTokens", "direction": "desc"}],
        },
        "scores": {
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
                    "value": ["task_success", "quality", "response_nonempty"],
                    "type": "stringOptions",
                }
            ],
            "orderBy": [{"field": "avg_value", "direction": "desc"}],
        },
        "skill_triggers": {
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
            ],
            "orderBy": [{"field": "count_count", "direction": "desc"}],
        },
    }
    queries["tool_calls"] = {
        **queries["skill_triggers"],
        "filters": [
            {"column": "type", "operator": "any of", "value": ["TOOL"], "type": "stringOptions"},
            {
                "column": "version",
                "operator": "any of",
                "value": ["0.6.0"],
                "type": "stringOptions",
            },
        ],
    }
    queries["llm_scores"] = {
        **queries["scores"],
        "filters": [
            {"column": "name", "operator": "any of", "value": SCORE_NAMES, "type": "stringOptions"}
        ],
    }
    with httpx.Client(
        base_url=os.environ["LANGFUSE_BASE_URL"],
        auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]),
        timeout=30,
        trust_env=False,
    ) as client:
        report = {}
        for name, query in queries.items():
            response = client.get(
                "/api/public/v2/metrics", params={"query": json.dumps({**common, **query})}
            )
            if response.is_error:
                raise RuntimeError(f"{name}: {response.status_code} {response.text[:1000]}")
            report[name] = response.json()["data"]
        for label, name in [
            ("requests", "agentops.chat"),
        ]:
            response = client.get(
                "/api/public/v2/observations",
                params={
                    "name": name,
                    "limit": 100,
                    "fields": "basic,metadata,usage",
                    "fromStartTime": common["fromTimestamp"],
                },
            )
            response.raise_for_status()
            report[label] = [
                {
                    k: row.get(k)
                    for k in ("id", "traceId", "name", "userId", "sessionId", "level", "metadata")
                }
                for row in response.json()["data"]
            ]
        (ROOT / "artifacts" / "langfuse-verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in ("requests", "skill_order", "skill_text")
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print("requests", len(report["requests"]))
        assert len(report["user_usage"]) >= 3, "three users not ingested yet"
        assert all(r["userId"] for r in report["requests"]), "root user attribution missing"
        assert report["tool_calls"], "native tool observations missing"
        assert report["skill_triggers"], "SKILL trigger events missing"
        assert any(r["name"] == "task_success" for r in report["scores"]), "scores missing"
        print("PASS: Langfuse ingested users, token usage, native tool counts and Agent scores")


if __name__ == "__main__":
    main()
