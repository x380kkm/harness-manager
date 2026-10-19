# session-memory-registration
"""会话记录脚本按宿主登记为两个关闭的 Hook, 绝对执行位置由当前安装目录生成."""
from __future__ import annotations

from copy import deepcopy
import re

from .protocol import document_identity
from .usage import usage_document


# //// 为两个宿主生成记录与回顾的独立 Hook 声明 [@x380kkm 2026-10-19] ////
def memory_documents(root, scope: str):
    script = root / "tools" / "session-memory" / "session-memory.mjs"
    for host in ("codex", "claude"):
        for action, event, name in (("record", "UserPromptSubmit", "记录用户请求并注入回顾"),
                                    ("recap", "Stop", "保存会话答复")):
            document = {
                "apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
                "id": f"plugin:toolkit/{scope}/memory-{host}-{action}", "release": {"version": "local"},
                "metadata": {"name": f"{host} {name}"},
                "contributions": [{
                    "id": "hook", "point": "hook.x380kkm/lifecycle",
                    "contract": {"id": "hook.x380kkm/lifecycle", "range": "^1.0.0"},
                    "scope": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": host}},
                    "payload": {"name": name, "event": event, "handlers": [{
                        "type": "command", "command": f'node "{script.as_posix()}" {host} {action}',
                    }]},
                }],
            }
            binding = usage_document(document, {"state": "disabled", "selector": {"host": host}}, None, scope)
            yield document, binding


# //// 更新当前机器的 Hook 位置并保留既有启停选择 [@x380kkm 2026-10-19] ////
def register_session_memory(manager, root, scope: str) -> list[str]:
    store = manager.catalogs.select(scope)
    current = {document_identity(document): document for document in store.snapshot()}
    plans, registered = [], []
    for document, binding in memory_documents(root, scope):
        baseline = current.get(document_identity(document))
        if baseline is not None:
            updated = deepcopy(baseline)
            handler = updated["contributions"][0]["payload"]["handlers"][0]
            expected_command = document["contributions"][0]["payload"]["handlers"][0]["command"]
            arguments = expected_command.rsplit(" ", 2)[1:]
            if re.fullmatch(r'node "[^"]+/tools/session-memory/session-memory\.mjs" '
                            + re.escape(" ".join(arguments)), handler.get("command", "")):
                handler["command"] = document["contributions"][0]["payload"]["handlers"][0]["command"]
            document = updated
        plans.append(store.preview_put(document, baseline))
        if document_identity(binding) not in current:
            plans.append(store.preview_put(binding, None))
        registered.append(document["id"])
    store.apply_many(plans)
    return registered
