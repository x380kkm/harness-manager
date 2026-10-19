# toolkit-rules
"""规则正文来自受管文件, 独立 Plugin 通过模块引用组合, 使用选择保存在各宿主绑定中."""
from __future__ import annotations

import json
from pathlib import Path

from .content_plan import INSTRUCTION_POINT
from .protocol import document_identity
from .usage import usage_document


# //// 从相对文件入口声明独立规则 [@x380kkm 2026-10-19] ////
def rule_document(scope: str, rule: dict, location: str) -> dict:
    return {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
        "id": f"plugin:toolkit/{scope}/rule-{rule['id']}", "release": {"version": "local"},
        "metadata": {"name": rule["name"]},
        "sources": [{"id": "rules", "source": {
            "resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": location,
        }}],
        "contributions": [{
            "id": "rule", "point": INSTRUCTION_POINT,
            "contract": {"id": INSTRUCTION_POINT, "range": "^1.0.0"},
            "source": "source:rules",
            "payload": {"name": rule["name"], "entry": rule["entry"], "slot": f"harness:{rule['id']}"},
        }],
    }


# //// 登记规则来源与组合并保留用户选择 [@x380kkm 2026-10-19] ////
def register_rules(manager, root: Path, scope: str) -> list[str]:
    store = manager.catalogs.select(scope)
    directory = root / "rules" / "agent-rules"
    definitions = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))["rules"]
    current = {document_identity(document): document for document in store.snapshot()}
    documents = [rule_document(scope, rule, directory.relative_to(store.workspace).as_posix()) for rule in definitions]
    module = {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
        "id": f"plugin:toolkit/{scope}/agent-rules", "release": {"version": "local"},
        "metadata": {"name": "Agent 通用规则", "description": "按项目和宿主能力使用的独立规则集合."},
        "contributions": [{"id": rule["id"], "ref": document["id"] + "#rule", "constraint": "local"}
                          for rule, document in zip(definitions, documents)],
    }
    documents.append(module)
    for host in ("codex", "claude"):
        documents.append(usage_document(module, {
            "id": f"binding:{scope}/{module['id']}/{host}",
            "state": "enabled", "selector": {"host": host},
            "members": {rule["id"]: "include" for rule in definitions}, "acceptance": "current",
        }, None, scope))
    plans = [store.preview_put(document, None) for document in documents if document_identity(document) not in current]
    if plans:
        store.apply_many(plans)
    return [document["id"] for document in documents if document["kind"] == "Plugin"]
