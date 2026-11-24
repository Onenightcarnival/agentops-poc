"""Configure usage, skill trigger counts and Agent-only evaluation cards."""

import json

from langfuse import get_client
from langfuse.api.unstable.dashboard_widgets.types import CreateDashboardWidgetRequest
from langfuse.api.unstable.dashboards.types import CreateDashboardPlacementRequest_Widget

from evaluators.langfuse_data import ONLINE_SCORE_NAMES, ROOT

DASHBOARD_ID = "cmwk7cpv80009rm07f8ib2kc9"


def list_widgets(client):
    first = client.list()
    widgets = list(first.data)
    for number in range(2, first.meta.total_pages + 1):
        widgets.extend(client.list(page=number).data)
    return widgets


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
    online_filter = [
        {
            "column": "name",
            "operator": "any of",
            "value": ONLINE_SCORE_NAMES,
            "type": "stringOptions",
        },
    ]
    definitions = [
        {
            "name": "Langfuse 自动评估 · 指标均分",
            "description": "Langfuse 自动执行任务完成度、正确性和指令遵循度评估；0～1，越高越好，按专用 online_* 指标名筛选。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": online_filter,
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "Langfuse 自动评估 · 均分趋势",
            "description": "按被评估 Agent 请求时间展示三项自动模型评分的均分趋势，不混入旧版本地脚本评分。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "value", "agg": "avg"}],
            "filters": online_filter,
            "chart_type": "LINE_TIME_SERIES",
            "chart_config": {"type": "LINE_TIME_SERIES"},
        },
        {
            "name": "Langfuse 自动评估 · 样本数",
            "description": "各指标已完成并写回的自动评分数量；不代表全部请求数。",
            "view": "scores-numeric",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": online_filter,
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "SKILL 调用次数 · Top 20",
            "description": "原生 SKILL.md 读取触发次数，按技能名称分组；重复读取计多次，不代表执行成功。仅统计 skill-trigger-v3 EVENT。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": skill_filter,
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "SKILL 调用次数 · 趋势",
            "description": "按技能分组的触发次数时间趋势，只统计触发，不统计耗时或成功率。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": skill_filter,
            "chart_type": "LINE_TIME_SERIES",
            "chart_config": {"type": "LINE_TIME_SERIES"},
        },
        {
            "name": "用户 Token 用量 · 按用户",
            "description": "真实 GENERATION Token 总量，按 userId 分组；不依赖模型价格配置。",
            "view": "observations",
            "dimensions": [{"field": "userId"}],
            "metrics": [{"measure": "totalTokens", "agg": "sum"}],
            "filters": generation,
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 20},
        },
        {
            "name": "原生工具调用次数 · 按工具",
            "description": "Callback 自动记录的原生工具调用次数。read_file 包含技能文档和其他文件；SKILL 触发次数见专用卡片。",
            "view": "observations",
            "dimensions": [{"field": "name"}],
            "metrics": [{"measure": "count", "agg": "count"}],
            "filters": tool_filter,
            "chart_type": "HORIZONTAL_BAR",
            "chart_config": {"type": "HORIZONTAL_BAR", "row_limit": 20},
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
            "chart_type": "LINE_TIME_SERIES",
            "chart_config": {"type": "LINE_TIME_SERIES"},
        },
    ]
    api = get_client().api.unstable
    existing = list_widgets(api.dashboard_widgets)
    by_name = {w.name: w for w in existing}
    # The retired pilot card is still accessible in the 4.16 widget editor.
    pilot = by_name.get("Langfuse 自动评估 · 任务完成度")
    if pilot:
        api.dashboard_widgets.update(
            pilot.id,
            filters=[f for f in pilot.filters if f.column != "source"],
            description="试点历史卡片：按 online_task_success 专用指标名统计任务完成度，0～1，越高越好。",
        )
    added = []
    for config in definitions:
        definition = CreateDashboardWidgetRequest.model_validate(config)
        old = by_name.get(definition.name)
        fields = definition.model_dump(by_alias=False, exclude_unset=True)
        widget = (
            api.dashboard_widgets.update(old.id, **fields)
            if old
            else api.dashboard_widgets.create(**fields)
        )
        added.append(widget)
    dashboard = api.dashboards.get(DASHBOARD_ID)
    obsolete_names = {
        "SKILL 耗时 · P95",
        "Langfuse 自动评估 · 任务完成度",
        "Agent 模型评估 · 指标均分",
        "Agent 模型评估 · 均分趋势",
        "Agent 模型评估 · 各指标样本数",
    }
    id_to_name = {w.id: w.name for w in [*existing, *added]}

    def label(placement):
        if placement.type == "preset":
            return placement.preset_id
        return id_to_name[placement.widget_id]

    placements = [
        p
        for p in dashboard.definition.widgets
        if label(p) not in obsolete_names and label(p) != "home-chart-scores"
    ]
    placed_ids = {p.widget_id for p in placements if p.type == "widget"}
    for widget in added:
        if widget.id not in placed_ids:
            placements.append(
                api.dashboards.add_placement(
                    DASHBOARD_ID,
                    request=CreateDashboardPlacementRequest_Widget(
                        type="widget", widget_id=widget.id
                    ),
                )
            )
    order = [
        "Langfuse 自动评估 · 指标均分",
        "Langfuse 自动评估 · 均分趋势",
        "Langfuse 自动评估 · 样本数",
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

    priority = {name: i for i, name in enumerate(order)}
    placements.sort(key=lambda p: priority.get(label(p), len(order)))
    layout = [
        p.model_copy(update={"x": (i % 2) * 6, "y": (i // 2) * 4, "width": 6, "height": 4})
        for i, p in enumerate(placements)
    ]
    saved = api.dashboards.update(
        DASHBOARD_ID,
        description="DeepAgents 0.6.2 原生Agent：Langfuse自动模型评估及独立规则评估、SKILL触发次数、用户Token、模型与工具调用。SKILL只计次数。",
        definition=dashboard.definition.model_copy(update={"widgets": layout}),
    )
    (ROOT / "artifacts").mkdir(exist_ok=True)
    snapshot = {
        "dashboard": saved.model_dump(mode="json"),
        "widgets": [w.model_dump(mode="json") for w in list_widgets(api.dashboard_widgets)],
    }
    (ROOT / "artifacts" / "dashboard-config.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {len(layout)} dashboard cards")


if __name__ == "__main__":
    main()
