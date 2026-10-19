# toolkit-catalog
"""安装目录中的 Skill 通过相对来源登记到相应配置层, 默认绑定面向 Manager 内容读取."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .importers import import_document
from .protocol import document_identity
from .usage import usage_document


# //// 将安装后的 Skill 登记为可迁移的声明并保留使用选择 [@x380kkm 2026-10-19] ////
def register_skills(manager, records, scope: str) -> list[str]:
    store = manager.catalogs.select(scope)
    current = {document_identity(document): document for document in store.snapshot()}
    plans, registered = [], []
    for record in records:
        entry = record.target / "SKILL.md"
        if not entry.is_file():
            continue
        document = import_document(manager.reader, str(entry))
        document["id"] = f"plugin:toolkit/{scope}/{record.component_id}"
        document["sources"][0]["source"]["locator"] = entry.parent.relative_to(store.workspace).as_posix()
        identity = document_identity(document)
        baseline = current.get(identity)
        if baseline is not None:
            document = deepcopy(baseline)
        plans.append(store.preview_put(document, baseline))
        binding = usage_document(document, {"state": "enabled", "selector": {"host": "harness-manager"}}, None, scope)
        previous_binding = current.get(document_identity(binding))
        if previous_binding is None:
            plans.append(store.preview_put(binding, None))
        registered.append(document["id"])
    if plans:
        store.apply_many(plans)
    return registered


# //// 解除发行包移除的 Skill 绑定并保留本机正文 [@x380kkm 2026-10-19] ////
def retire_skills(manager, components: set[str], scope: str) -> list[dict]:
    store = manager.catalogs.select(scope)
    documents = store.snapshot()
    identifiers = {f"plugin:toolkit/{scope}/{component}" for component in components}
    plugins = [document for document in documents if document.get("kind") == "Plugin"
               and document["id"] in identifiers]
    bindings = [document for document in documents if document.get("kind") == "PluginBinding"
                and document.get("plugin", {}).get("id") in identifiers]
    plans = [store.preview_remove(document_identity(document), document) for document in [*bindings, *plugins]]
    if plans:
        store.apply_many(plans)
    return [{"plugin": document["id"], "action": "unregistered", "files": "preserved"} for document in plugins]
