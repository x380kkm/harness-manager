# audience: internal
# # agent-interface
"""方法输入来自管理核心的公开签名. 查询路由使用明确的只读集合, 其他调用由操作入口承载."""
from __future__ import annotations

from inspect import Parameter, signature
from typing import Any, get_type_hints

from .agent_help import read_help

QUERY_METHODS = frozenset({
    "agent.capabilities", "agent.help", "host.status", "host.inspect", "host.preview", "host.preview_restore",
    "module.describe", "module.preview", "card.inventory", "card.describe", "card.relations", "statistics.reads",
    "context.describe", "context.preview", "context.preview_remove", "codex.snapshot", "codex.list", "codex.groups",
    "codex.read", "codex.import", "catalog.snapshot", "catalog.list", "document.read", "document.preview",
    "document.preview_remove", "document.import", "catalog.graph", "catalog.effective", "protocol.schema",
    "catalog.discover", "skill.list", "content.read", "content.snapshot", "usage.describe", "usage.preview",
    "project.relocate_preview", "rule.describe",
})
CATALOG_WRITES = frozenset({"card.configure", "card.set_relation", "card.remove_relation", "card.set_shared",
                           "document.apply", "context.apply", "module.apply"})
OBSERVATION_WRITES = frozenset({"content.open", "content.preview", "content.continue"})
HOST_WRITES = frozenset({"host.initialize", "host.set_enabled", "host.apply"})


# //// 描述公开调用的持久化边界 [@x380kkm 2026-09-08] ////
def method_effects(method: str) -> dict:
    if method in QUERY_METHODS:
        return {"readOnly": True, "writes": [], "authorization": "read",
                "planLifetime": "process" if method in {"host.preview", "host.preview_restore"} else "baseline" if "preview" in method else None}
    if method in CATALOG_WRITES:
        return {"readOnly": False, "writes": ["catalog", "host-if-enabled", "host-backup"], "authorization": "user-change"}
    if method == "project.relocate_apply":
        return {"readOnly": False, "writes": ["catalog", "project-private-location", "host-backup-location"],
                "authorization": "user-change", "hostApply": "explicit-preview"}
    if method in OBSERVATION_WRITES:
        return {"readOnly": False, "writes": ["content-snapshot", "read-observations"], "authorization": "content-read"}
    if method in HOST_WRITES:
        return {"readOnly": False, "writes": ["host-control", "host-backup", "host-files"] if method != "host.initialize" else ["host-backup"],
                "authorization": "user-change"}
    return {"readOnly": False, "writes": ["method-defined"], "authorization": "user-change"}


# //// 从实际处理器签名生成参数结构 [@x380kkm 2026-09-08] ////
def input_schema(method: str, handler) -> dict:
    from pydantic import ConfigDict, create_model

    hints = get_type_hints(handler)
    fields = {}
    for name, parameter in signature(handler).parameters.items():
        if parameter.kind not in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}:
            continue
        default = ... if parameter.default is Parameter.empty else parameter.default
        fallback = type(default) if default is not None and default is not ... else Any
        fields[name] = (hints.get(name, fallback), default)
    model = create_model(method.replace(".", "_"), __config__=ConfigDict(extra="forbid"), **fields)
    return model.model_json_schema()


# //// 提供有界能力发现与按需帮助 [@x380kkm 2026-09-08] ////
class AgentInterface:
    def __init__(self, manager) -> None:
        self.manager = manager

    # //// 列出方法摘要或读取单个方法的输入与副作用 [@x380kkm 2026-09-08] ////
    def capabilities(self, method: str | None = None, group: str = "", limit: int = 20, cursor: int = 0) -> dict:
        handlers = self.manager.handlers()
        if method is not None:
            if not isinstance(method, str) or method not in handlers:
                raise ValueError("方法不存在, 请先查询能力目录.")
            cli = {"command": "call", "method": method}
            if method == "host.apply":
                cli = {"command": "host", "actions": ["apply", "restore"], "confirmation": "same-process",
                       "rpcMethod": "host.apply"}
            family = method.partition(".")[0]
            topic = {"module": "modules", "usage": "modules", "catalog": "content", "skill": "content", "statistics": "content",
                     "codex": "overview", "agent": "overview", "host": "host", "content": "content", "project": "relocation", "rule": "rules"}.get(family, "editing")
            return {"method": method, "inputSchema": input_schema(method, handlers[method]), **method_effects(method),
                    "help": "harness://agent/" + topic,
                    "cli": cli, "mcp": {"tool": "manager_query" if method in QUERY_METHODS else "manager_action"}}
        if not isinstance(group, str) or type(limit) is not int or type(cursor) is not int or not 1 <= limit <= 100 or cursor < 0:
            raise ValueError("能力查询需要 group 文本, 1 至 100 的 limit 和非负 cursor.")
        names = sorted(name for name in handlers if not group or name.partition(".")[0] == group)
        end = cursor + limit
        return {"methods": [{"method": name, "readOnly": name in QUERY_METHODS} for name in names[cursor:end]],
                "total": len(names), "nextCursor": end if end < len(names) else None,
                "groups": sorted({name.partition(".")[0] for name in handlers}),
                "scopes": ["user", *(["project", "project-local"] if self.manager.workspace is not None else [])],
                "context": {"userRoot": str(self.manager.catalogs.user.workspace),
                            "workspace": str(self.manager.workspace) if self.manager.workspace else None,
                            "readRoots": [str(root) for root in self.manager.reader.roots]},
                "help": "harness://agent/overview", "methodDetails": "agent.capabilities(method=...)"}

    # //// 返回外部客户端请求的单个帮助主题 [@x380kkm 2026-09-08] ////
    def help(self, topic: str = "overview") -> dict:
        return read_help(topic)

    # //// 约束只读路由可调用的管理方法 [@x380kkm 2026-09-08] ////
    def require_query(self, method: str) -> None:
        if not isinstance(method, str) or method not in self.manager.handlers():
            raise ValueError("方法不存在, 请先查询能力目录.")
        if method not in QUERY_METHODS:
            raise ValueError("此方法会保存数据, 请在取得相应授权后使用 manager_action.")
