"""Read back actual ingested observations and aggregate metrics through public v2 APIs."""

import json
from datetime import UTC, datetime, timedelta

from langfuse import get_client

from evaluators.langfuse_data import ONLINE_SCORE_NAMES, ROOT


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
    queries["online_scores"] = {
        **queries["scores"],
        "filters": [
            {
                "column": "name",
                "operator": "any of",
                "value": ONLINE_SCORE_NAMES,
                "type": "stringOptions",
            },
            {"column": "source", "operator": "any of", "value": ["EVAL"], "type": "stringOptions"},
        ],
    }
    # Widget editor uses names only; detect any accidental API/manual scores reusing them.
    queries["online_score_sources"] = {
        **queries["online_scores"],
        "dimensions": [{"field": "name"}, {"field": "source"}],
        "filters": [f for f in queries["online_scores"]["filters"] if f["column"] != "source"],
    }
    api = get_client().api
    report = {
        name: api.metrics.metrics(query=json.dumps({**common, **query})).data
        for name, query in queries.items()
    }
    requests = api.observations.get_many(
        name="agentops.chat",
        limit=100,
        fields="basic,metadata,usage",
        from_start_time=now - timedelta(days=7),
    )
    report["requests"] = [
        {
            key: row.get(key)
            for key in ("id", "traceId", "name", "userId", "sessionId", "level", "metadata")
        }
        for row in (item.model_dump(mode="json") for item in requests.data)
    ]
    (ROOT / "artifacts" / "langfuse-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("requests", "skill_order", "skill_text")},
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
    assert {r["name"] for r in report["online_scores"]} == set(ONLINE_SCORE_NAMES), (
        "native model evaluation metrics missing"
    )
    assert all(r["source"] == "EVAL" for r in report["online_score_sources"]), (
        "Reserved online_* score names contain non-EVAL scores; dashboard would mix sources"
    )
    print("PASS: Langfuse ingested users, token usage, native tool counts and Agent scores")


if __name__ == "__main__":
    main()
