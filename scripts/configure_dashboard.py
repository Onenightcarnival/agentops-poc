"""Configure usage, skill trigger counts and Agent-only evaluation cards."""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from evaluators.llm import SCORE_NAMES

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
    skill_filter = [
        {"column": "type", "operator": "any of", "value": ["EVENT"], "type": "stringOptions"},
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
    ]
    definitions = [
        {
            "name": "Agent 模型评估 · 指标均分",
            "description": "独立 LLM judge：任务完成度、正确性、指令遵循度；0～1，越高越好。评分依据见各 trace 的 Scores。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": [
                {
                    "column": "name",
                    "operator": "any of",
                    "value": SCORE_NAMES,
                    "type": "stringOptions",
                }
            ],
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "Agent 模型评估 · 均分趋势",
            "description": "按 Agent 请求发生时间展示 LLM judge 各指标均分；独立于规则评分。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": [
                {
                    "column": "name",
                    "operator": "any of",
                    "value": SCORE_NAMES,
                    "type": "stringOptions",
                }
            ],
            "chartType": "LINE_TIME_SERIES",
            "chartConfig": {"type": "LINE_TIME_SERIES"},
        },
        {
            "name": "Agent 模型评估 · 各指标样本数",
            "description": "每个请求每项指标一条模型评分；样本为已完成独立评估的请求，不代表全部线上流量。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": [
                {
                    "column": "name",
                    "operator": "any of",
                    "value": SCORE_NAMES,
                    "type": "stringOptions",
                }
            ],
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "SKILL 调用次数 · Top 20",
            "description": "原生 SKILL.md 读取触发次数，按技能名称分组；重复读取计多次，不代表执行成功。仅统计 skill-trigger-v3 EVENT。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": skill_filter,
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "SKILL 调用次数 · 趋势",
            "description": "按技能分组的触发次数时间趋势，只统计触发，不统计耗时或成功率。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": skill_filter,
            "chartType": "LINE_TIME_SERIES",
            "chartConfig": {"type": "LINE_TIME_SERIES"},
        },
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
            "description": "Callback 自动记录的原生工具调用次数。read_file 包含技能文档和其他文件；SKILL 触发次数见专用卡片。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": tool_filter,
            "chartType": "HORIZONTAL_BAR",
            "chartConfig": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "Agent 评估 · 各指标均值趋势",
            "description": "独立规则评估：task_success、quality、response_nonempty；不包含模型评分或历史 skill_success。",
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
        obsolete = {w["id"] for w in existing if w["name"] == "SKILL 耗时 · P95"}
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
            "Agent 模型评估 · 指标均分",
            "Agent 模型评估 · 均分趋势",
            "Agent 模型评估 · 各指标样本数",
            "SKILL 调用次数 · Top 20",
            "SKILL 调用次数 · 趋势",
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
                "description": "DeepAgents 0.6.2 原生Agent：独立模型及规则评估、SKILL触发次数、用户Token、模型与工具调用。SKILL只计次数。",
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
