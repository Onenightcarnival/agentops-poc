"""Configure usage, skill trigger counts and Agent-only evaluation cards."""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DASHBOARD_ID = "cmwk7cpv80009rm07f8ib2kc9"


def main():
    generation = [
        {"column": "type", "operator": "any of", "value": ["GENERATION"], "type": "stringOptions"}
    ]
    tool_filter = [
        {"column": "type", "operator": "any of", "value": ["TOOL"], "type": "stringOptions"},
        {"column": "version", "operator": "any of", "value": ["0.6.0"], "type": "stringOptions"},
    ]
    definitions = [
        {
            "name": "用户 Token 用量 · 按用户",
            "description": "真实 GENERATION Token 总量，按 userId 分组；不依赖模型价格配置。",
            "view": "observations",
            "dimensions": [{"field": "userId"}],
            "metrics": [{"measure": "totalTokens", "agg": "sum"}],
            "filters": generation,
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "原生工具调用次数 · 按工具",
            "description": "Callback 自动记录的原生工具调用次数。read_file 包含技能文档和其他文件；按技能名统计使用独立 skill_usage 脚本。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": tool_filter,
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "Agent 评估 · 各指标均值趋势",
            "description": "独立 Agent 评估：task_success、quality、response_nonempty；不包含历史 skill_success。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": [
                {
                    "column": "name",
                    "operator": "any of",
                    "value": ["task_success", "quality", "response_nonempty"],
                    "type": "stringOptions",
                }
            ],
            "chartType": "LINE_TIME_SERIES",
            "chartConfig": {"type": "LINE_TIME_SERIES"},
        },
    ]
    with httpx.Client(
        base_url=os.environ["LANGFUSE_BASE_URL"],
        auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]),
        timeout=30,
        trust_env=False,
    ) as client:

        def call(method, path, **kwargs):
            response = client.request(method, "/api/public/unstable/" + path, **kwargs)
            if response.is_error:
                raise RuntimeError(f"{response.status_code}: {response.text[:1000]}")
            return response.json()

        existing = call("GET", "dashboard-widgets")["data"]
        by_name = {w["name"]: w for w in existing}
        added = []
        for definition in definitions:
            old = by_name.get(definition["name"])
            widget = (
                call("PATCH", f"dashboard-widgets/{old['id']}", json=definition)
                if old
                else call("POST", "dashboard-widgets", json=definition)
            )
            added.append(widget)
        dashboard = call("GET", f"dashboards/{DASHBOARD_ID}")
        placements = dashboard["definition"]["widgets"]
        # Remove obsolete skill timing and the unfiltered preset exposing old skill scores.
        obsolete = {
            w["id"]
            for w in existing
            if w["name"] in {"SKILL 耗时 · P95", "SKILL 调用次数 · Top 20"}
        }
        placements = [
            p
            for p in placements
            if p.get("widgetId") not in obsolete and p.get("presetId") != "home-chart-scores"
        ]
        for widget in added:
            if not any(p.get("widgetId") == widget["id"] for p in placements):
                placements.append(
                    call(
                        "POST",
                        f"dashboards/{DASHBOARD_ID}/placements",
                        json={"type": "widget", "widgetId": widget["id"]},
                    )
                )
        all_widgets = existing + added
        id_to_name = {w["id"]: w["name"] for w in all_widgets}
        order = [
            "用户 Token 用量 · 按用户",
            "原生工具调用次数 · 按工具",
            "模型调用次数 · 按模型",
            "Agent 评估 · 各指标均值趋势",
            "home-generation-latency",
            "home-traces-obs-time-series",
            "home-users",
            "home-model-usage",
            "home-model-costs",
        ]

        def rank(p):
            label = p.get("presetId") or id_to_name[p["widgetId"]]
            return order.index(label) if label in order else len(order)

        placements.sort(key=rank)
        for i, placement in enumerate(placements):
            placement.update(x=(i % 2) * 6, y=(i // 2) * 4, width=6, height=4)
        saved = call(
            "PATCH",
            f"dashboards/{DASHBOARD_ID}",
            json={
                "description": "DeepAgents 0.6.2 + FastAPI + SQLite：单文件原生Agent：用户Token、模型与工具调用、独立Agent评估。SKILL分组由离线查询提供。",
                "definition": {"widgets": placements},
            },
        )
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts" / "dashboard-config.json").write_text(
            json.dumps(
                {"dashboard": saved, "widgets": call("GET", "dashboard-widgets")["data"]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Saved {len(placements)} dashboard cards")


if __name__ == "__main__":
    main()
