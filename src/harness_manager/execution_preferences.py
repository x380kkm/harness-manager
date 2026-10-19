# execution-preferences
"""执行偏好使用宿主限定的 PluginBinding 选项, 静态说明只包含对应宿主的已选值."""
from __future__ import annotations

import json

from .content_plan import PREFERENCE_POINT
from .protocol import document_identity, value_diagnostics
from .usage import usage_document


# //// 声明每个宿主可以设置的执行字段 [@x380kkm 2026-10-19] ////
def execution_schema(host: str) -> dict:
    effort = {"codex": "model_reasoning_effort", "claude": "effort"}[host]
    choice = {"type": "string", "minLength": 1}
    task = {"type": "object", "additionalProperties": False, "properties": {
        "workflow": choice, "model": choice, effort: choice,
        "audit_round_limit": {"type": "integer", "minimum": 1},
    }}
    return {"type": "object", "additionalProperties": False, "properties": {
        "defaults": task,
        "tasks": {"type": "object", "additionalProperties": task},
    }}


# //// 生成带宿主边界的可选执行偏好 [@x380kkm 2026-10-19] ////
def execution_document(scope: str, host: str) -> dict:
    return {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
        "id": f"plugin:toolkit/{scope}/execution-{host}", "release": {"version": "local"},
        "metadata": {"name": f"{host} 执行偏好", "description": "按任务选择工作流, 模型, 推理强度与审计修复次数."},
        "options": {"schema": execution_schema(host)},
        "contributions": [{
            "id": "execution", "point": PREFERENCE_POINT,
            "contract": {"id": PREFERENCE_POINT, "range": "^1.0.0"},
            "scope": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": host}},
            "payload": {
                "name": f"{host} 执行偏好", "executionHost": host,
                "guidance": (
                    f"## {host} 执行偏好\n\n"
                    "按当前任务匹配 tasks, 对应字段覆盖 defaults. 省略的字段沿用宿主当前选择. "
                    "执行前确认所选工作流, 模型和推理档位在当前宿主可用; 缺少能力时说明差异并由用户选择. "
                    "这些值用于具备相应参数的任务调用, 主会话设置通过宿主自己的配置入口修改.\n\n"
                    "审计检查具体改动. 修复有证据支持的问题后复查受影响部分. "
                    "audit_round_limit 限制自动修复轮数; 到达限制, 遇到相同阻碍或需要额外授权时, 报告证据并请求用户决定."
                ),
            },
        }],
    }


# //// 为安装范围登记可选配置并保留用户声明和选择 [@x380kkm 2026-10-19] ////
def register_execution_preferences(manager, scope: str) -> list[str]:
    store = manager.catalogs.select(scope)
    current = {document_identity(document): document for document in store.snapshot()}
    plans, registered = [], []
    for host in ("codex", "claude"):
        document = execution_document(scope, host)
        if document_identity(document) not in current:
            plans.append(store.preview_put(document, None))
        binding = usage_document(document, {"state": "disabled", "selector": {"host": host}}, None, scope)
        if document_identity(binding) not in current:
            plans.append(store.preview_put(binding, None))
        registered.append(document["id"])
    if plans:
        store.apply_many(plans)
    return registered


# //// 将匹配宿主的执行选项呈现为静态任务说明 [@x380kkm 2026-10-19] ////
def execution_text(payload: dict, options: dict) -> str:
    host = payload["executionHost"]
    errors = value_diagnostics(options, execution_schema(host))
    if errors:
        raise ValueError("执行选项不符合宿主字段: " + " | ".join(errors))
    guidance = payload["guidance"]
    if options:
        guidance += "\n\n```json\n" + json.dumps(options, ensure_ascii=False, indent=2) + "\n```"
    return guidance
