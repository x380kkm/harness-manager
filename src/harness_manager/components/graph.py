# audience: internal
# # agent-manager-graph
"""把组件清单和检测结果投影为 Archify architecture JSON IR.

本模块只生成 Archify schema 规定的 JSON, 不调用 Archify renderer. 健康和更新
结果可以从已有 JSON 导入, 也可以由 CLI 在用户明确要求时传入.
"""
from __future__ import annotations

from typing import Any

from .manifest import Component




# //// 把组件类型映射到 Archify architecture schema 的有限类型集合 [@x380kkm 2026-09-03] ////
def archify_component_type(kind: str) -> str:
    lower = kind.lower()
    if "host" in lower:
        return "external"
    if "skill" in lower:
        return "frontend"
    if "adapter" in lower:
        return "messagebus"
    return "backend"


# //// 将 doctor 和 check 结果合并为图节点显示所需的状态 [@x380kkm 2026-09-03] ////
def graph_status(component: Component, doctor: dict[str, Any], check: dict[str, Any]) -> tuple[str, str]:
    health = str(doctor.get("status", "not-run")) if doctor else (
        "missing" if not component.source.is_dir() else "not-run"
    )
    update = str(check.get("status", "not-run")) if check else (
        "missing" if not component.source.is_dir() else "not-run"
    )
    return health, update


# //// 按必需依赖深度把组件放入稳定的横向层级 [@x380kkm 2026-09-03] ////
def graph_positions(components: list[Component]) -> dict[str, list[int]]:
    canonical = {
        "codegraph": [40, 100],
        "codegraph-skill": [40, 300],
        "clean-tools-skill": [300, 300],
        "archify": [560, 100],
        "archify-skill": [560, 300],
        "agent-manager": [820, 300],
        "codex-host": [820, 700],
    }
    if all(component.id in canonical for component in components):
        return {component.id: canonical[component.id] for component in components}
    by_id = {component.id: component for component in components}
    depths: dict[str, int] = {}

    # //// 递归计算一个组件的必需依赖深度 [@x380kkm 2026-09-03] ////
    def depth_of(component_id: str) -> int:
        if component_id in depths:
            return depths[component_id]
        dependencies = [dep for dep in by_id[component_id].dependencies if dep in by_id]
        depths[component_id] = 0 if not dependencies else max(depth_of(dep) for dep in dependencies) + 1
        return depths[component_id]

    for component in components:
        depth_of(component.id)
    layers: dict[int, list[str]] = {}
    order_index = {component.id: index for index, component in enumerate(components)}
    for component in components:
        layers.setdefault(depths[component.id], []).append(component.id)
    for layer in layers.values():
        layer.sort(key=lambda component_id: (
            min((order_index[dep] for dep in by_id[component_id].dependencies if dep in by_id), default=-1),
            order_index[component_id],
        ))
    max_columns = max(len(layer) for layer in layers.values())
    positions: dict[str, list[int]] = {}
    for depth, layer in layers.items():
        if len(layer) == 1:
            slots = [max_columns // 2]
        elif len(layer) == 2 and max_columns >= 3:
            slots = [0, max_columns - 1]
        else:
            slots = list(range(len(layer)))
        for component_id, slot in zip(layer, slots, strict=True):
            positions[component_id] = [40 + slot * 270, 100 + depth * 190]
    return positions


# //// 生成可交给 Archify validate 的 architecture JSON IR [@x380kkm 2026-09-03] ////
def build_graph_ir(
    components: list[Component],
    doctor: dict[str, dict[str, Any]] | None = None,
    checks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    doctor = doctor or {}
    checks = checks or {}
    selected_ids = {component.id for component in components}
    positions = graph_positions(components)
    nodes: list[dict[str, Any]] = []
    for component in components:
        health, update = graph_status(component, doctor.get(component.id, {}), checks.get(component.id, {}))
        nodes.append({
            "id": component.id,
            "type": archify_component_type(component.kind),
            "label": component.name,
            "sublabel": f"{component.kind} / h:{health} / u:{update}",
            "tag": "可替换" if component.replaceable else component.update.get("strategy", "manual"),
            "pos": positions[component.id],
            "size": [220, 110],
        })

    connections: list[dict[str, Any]] = []
    for component in components:
        for dependency in component.dependencies:
            if dependency in selected_ids:
                connections.append({
                    "from": dependency,
                    "to": component.id,
                    "variant": "emphasis",
                })
    health_items = [
        f"{component.id}: {graph_status(component, doctor.get(component.id, {}), checks.get(component.id, {}))[0]}"
        for component in components
    ]
    update_items = [
        f"{component.id}: {graph_status(component, doctor.get(component.id, {}), checks.get(component.id, {}))[1]}"
        for component in components
    ]
    boundaries: list[dict[str, Any]] = []
    replaceable = [component.id for component in components if component.replaceable]
    host_owned = [component.id for component in components if not component.replaceable]
    if replaceable:
        boundaries.append({"kind": "region", "label": "可替换组件", "wraps": replaceable})
    if host_owned:
        boundaries.append({"kind": "security-group", "label": "用户宿主", "wraps": host_owned})
    return {
        "schema_version": 1,
        "diagram_type": "architecture",
        "meta": {
            "title": "Agent harness 组件",
            "locale": "zh-CN",
            "output": "agent-components.architecture.html",
        },
        "components": nodes,
        "boundaries": boundaries,
        "connections": connections,
        "cards": [
            {"dot": "cyan", "title": "健康状态", "items": health_items},
            {"dot": "amber", "title": "更新检查", "items": update_items},
            {"dot": "slate", "title": "可选依赖", "items": [
                f"{component.id}: {', '.join(component.optional_dependencies) if component.optional_dependencies else '无'}"
                for component in components
            ]},
            {"dot": "violet", "title": "能力", "items": [
                f"{component.id}: {', '.join(component.capabilities) if component.capabilities else '未声明'}"
                for component in components
            ]},
        ],
    }
