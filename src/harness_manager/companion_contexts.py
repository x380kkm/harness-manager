# audience: internal
# # companion-contexts
"""配套正文属于独立 Plugin, 使用范围属于独立绑定. 同一目录中的两项声明在一次原子替换中保存."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .catalogs import Catalogs
from .content_plan import SKILL_POINT, TASK_CONTEXT_POINT
from .declarations import contribution_name, index_declarations
from .projection import SCOPE_KEYS
from .protocol import document_identity, document_name, validate_document, value_diagnostics
from .storage_errors import StorageConflictError
from .usage import find_plugin, usage_document

SETTINGS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "skill", "text", "selector", "enabled"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 160},
        "skill": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1, "maxLength": 1048576},
        "enabled": {"type": "boolean"},
        "selector": {"type": "object", "additionalProperties": False, "properties": {
            key: {"oneOf": [{"type": "string", "minLength": 1},
                            {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}]}
            for key in sorted(SCOPE_KEYS)
        }},
    },
}


# //// 保留配套内容操作的明确错误 [@x380kkm 2026-09-06] ////
class ContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 提取选定发布中的 Skill 选择身份 [@x380kkm 2026-09-06] ////
def skill_choices(documents: list[dict], plugin: dict) -> list[dict]:
    index = index_declarations(documents)
    result = []
    for member in plugin["contributions"]:
        ref = f"{plugin['id']}#{member['id']}"
        chain = index.resolve_reference(ref, plugin["release"]["version"])
        if chain and chain[-1][1].get("point") == SKILL_POINT:
            result.append({"ref": ref, "name": contribution_name(chain[-1][1]),
                           "references": [f"{owner['id']}#{value['id']}" for owner, value in chain]})
    return result


# //// 判断声明是否可以由单份配套说明表单维护 [@x380kkm 2026-09-06] ////
def editable_context(document: dict, bindings: list[dict]) -> bool:
    members = document.get("contributions", [])
    if len(members) != 1 or len(bindings) > 1:
        return False
    member = members[0]
    payload = member.get("payload")
    return (member.get("point") == TASK_CONTEXT_POINT and member.get("source") is None
            and isinstance(payload, dict) and isinstance(payload.get("skill"), str)
            and isinstance(payload.get("text"), str))


# //// 为独立正文与使用设置提供共同的管理用例 [@x380kkm 2026-09-06] ////
class CompanionContexts:
    def __init__(self, catalogs: Catalogs) -> None:
        self.catalogs = catalogs

    # //// 列出目标 Skill 和已有配套正文的维护归属 [@x380kkm 2026-09-06] ////
    def describe(self, plugin: str, scope: str = "user") -> dict:
        view = self.catalogs.for_scope(scope)
        selected = find_plugin(view, plugin)
        skills = skill_choices(view.documents, selected)
        references = {ref for skill in skills for ref in skill["references"]}
        local = self.catalogs.select(scope).snapshot()
        local_by_id = {document_identity(value): value for value in local}
        contexts = []
        for document in view.documents:
            if document["kind"] != "Plugin":
                continue
            members = [member for member in document["contributions"] if member.get("point") == TASK_CONTEXT_POINT
                       and isinstance(member.get("payload"), dict) and member["payload"].get("skill") in references]
            if not members:
                continue
            identity = document_identity(document)
            original = local_by_id.get(identity)
            origin_scope = scope if original is not None else view.origins[identity][-1]["scope"]
            binding_sources = local if original is not None else self.catalogs.select(origin_scope).snapshot()
            bindings = [value for value in binding_sources if value["kind"] == "PluginBinding" and value["plugin"]["id"] == document["id"]]
            writable = original is not None and editable_context(original, bindings)
            binding = bindings[0] if len(bindings) == 1 else None
            payload = members[0]["payload"]
            contexts.append({"id": identity, "name": document_name(document), "editable": writable,
                             "scope": origin_scope, "skill": payload["skill"],
                             "settings": {"name": document_name(document), "skill": payload["skill"], "text": payload.get("text", ""),
                                          "selector": deepcopy(binding["target"]["selector"]) if binding else {"user": "current"} if scope == "user" else {},
                                          "enabled": binding.get("enabled", True) if binding else False},
                             "baseline": {"document": original, "binding": binding} if writable else None})
        return {"plugin": plugin, "name": document_name(selected), "scope": scope,
                "catalog": str(self.catalogs.select(scope).catalog), "skills": skills, "contexts": contexts,
                "settingsSchema": deepcopy(SETTINGS_SCHEMA)}

    # //// 从表单准备独立内容与绑定的联合差异 [@x380kkm 2026-09-06] ////
    def preview(self, plugin: str, settings: dict, baseline: dict | None = None, scope: str = "user") -> dict:
        description = self.describe(plugin, scope)
        self._check_settings(description, settings)
        previous, previous_binding = self._baseline(baseline)
        if previous is not None and not editable_context(previous, [previous_binding] if previous_binding else []):
            raise ContextError("context_format", "此声明包含独立来源或组合成员, 请使用声明编辑器维护.")
        document = deepcopy(previous) if previous is not None else {
            "apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": f"plugin:context/{uuid4().hex}",
            "release": {"version": "local"}, "contributions": [{"id": "context", "point": TASK_CONTEXT_POINT,
                "contract": {"id": TASK_CONTEXT_POINT, "range": "^1.0.0"}, "payload": {}}],
        }
        document.setdefault("metadata", {})["name"] = settings["name"]
        document["contributions"][0]["payload"].update(name=settings["name"], skill=settings["skill"], text=settings["text"])
        binding = usage_document(document, {"state": "enabled" if settings["enabled"] else "disabled", "selector": settings["selector"]}, previous_binding, scope)
        store = self.catalogs.select(scope)
        plans = [store.preview_put(document, previous), store.preview_put(binding, previous_binding)]
        return {"plan": {"scope": scope, "catalog": str(store.catalog), "plugin": plugin, "entries": plans},
                "name": settings["name"], "diagnostics": []}

    # //// 准备同时移出正文与其本层绑定 [@x380kkm 2026-09-06] ////
    def preview_remove(self, plugin: str, baseline: dict, scope: str = "user") -> dict:
        description = self.describe(plugin, scope)
        document, binding = self._baseline(baseline)
        if document is None or not any(item["id"] == document_identity(document) and item["editable"] for item in description["contexts"]):
            raise ContextError("context_missing", "当前层缺少可以移出的配套说明.")
        store = self.catalogs.select(scope)
        entries = [store.preview_remove(document_identity(value), value) for value in (document, binding) if value]
        return {"plan": {"scope": scope, "catalog": str(store.catalog), "plugin": plugin, "entries": entries},
                "name": document_name(document), "diagnostics": []}

    # //// 检查联合计划的内容关系并原子保存 [@x380kkm 2026-09-06] ////
    def apply(self, plan: dict) -> dict:
        if not isinstance(plan, dict) or set(plan) != {"scope", "catalog", "plugin", "entries"}:
            raise ContextError("context_plan", "配套说明计划需要固定目录, 目标发布和联合差异.")
        store = self.catalogs.select(plan["scope"])
        if plan["catalog"] != str(store.catalog):
            raise ContextError("context_catalog", "当前目录与计划写入位置不同.")
        entries = plan["entries"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 2 or any(not isinstance(entry, dict) for entry in entries):
            raise ContextError("context_plan", "联合差异需要包含配套正文与对应绑定.")
        removing = entries[0].get("operation") == "remove"
        values = [entry.get("before" if removing else "after") for entry in entries]
        for value in values:
            validate_document(value)
        document = values[0]
        binding = values[1] if len(values) == 2 else None
        if (document["kind"] != "Plugin" or not editable_context(document, [binding] if binding else [])
                or any(entry.get("operation") != ("remove" if removing else "put") for entry in entries)
                or (not removing and binding is None)
                or (binding is not None and (binding["kind"] != "PluginBinding" or binding["plugin"]["id"] != document["id"]))):
            raise ContextError("context_plan", "正文与使用绑定需要属于同一配套内容.")
        payload = document["contributions"][0]["payload"]
        description = self.describe(plan["plugin"], plan["scope"])
        if not removing:
            self._check_settings(description, {"name": document_name(document), "skill": payload["skill"], "text": payload["text"],
                                               "selector": binding["target"]["selector"], "enabled": binding.get("enabled", True)})
        # //// 核对预览中的配套绑定集合仍然完整 [@x380kkm 2026-09-06] ////
        def verify_current(documents: list[dict]) -> None:
            expected = {entry["id"] for entry in entries[1:] if entry.get("before") is not None}
            actual = {value["id"] for value in documents if value["kind"] == "PluginBinding" and value["plugin"]["id"] == document["id"]}
            if actual != expected:
                raise StorageConflictError(next(iter(actual - expected or expected - actual)))

        try:
            results = store.apply_many(entries, verify_current=verify_current)
        except StorageConflictError as error:
            error.details["scope"] = plan["scope"]
            raise
        return {"scope": plan["scope"], "catalog": str(store.catalog), "id": document_identity(document),
                "removed": removing, "changed": any(item["changed"] for item in results)}

    # //// 核对表单的适用 Skill 与静态范围字段 [@x380kkm 2026-09-06] ////
    def _check_settings(self, description: dict, settings: dict) -> None:
        if value_diagnostics(settings, SETTINGS_SCHEMA):
            raise ContextError("context_settings", "配套说明需要名称, 正文, 有效 Skill 与明确范围.")
        references = {ref for skill in description["skills"] for ref in skill["references"]}
        if settings["skill"] not in references or not settings["name"].strip() or not settings["text"].strip():
            raise ContextError("context_skill", "请选择当前发布中的 Skill 并填写配套正文.")
        path = settings["selector"].get("path", [])
        paths = path if isinstance(path, list) else [path]
        if any(any(character in value for character in "*?[]") for value in paths):
            raise ContextError("context_path", "路径范围需要明确的目录或文件位置.")
        if description["scope"] in {"project", "project-local"}:
            selector = settings["selector"].get("project", "current")
            projects = selector if isinstance(selector, list) else [selector]
            if "current" not in projects and self.catalogs.project.workspace.as_uri() not in projects:
                raise ContextError("context_project", "项目配套说明的范围需要包含当前项目.")

    # //// 核对正文与绑定的原始读取基线 [@x380kkm 2026-09-06] ////
    def _baseline(self, baseline: dict | None) -> tuple[dict | None, dict | None]:
        if baseline is None:
            return None, None
        if not isinstance(baseline, dict) or set(baseline) != {"document", "binding"} or not isinstance(baseline["document"], dict):
            raise ContextError("context_baseline", "读取基线需要配套正文与绑定记录.")
        document, binding = baseline["document"], baseline["binding"]
        validate_document(document)
        if document["kind"] != "Plugin":
            raise ContextError("context_baseline", "配套正文基线需要独立 Plugin.")
        if binding is not None:
            validate_document(binding)
            if binding["kind"] != "PluginBinding" or binding["plugin"]["id"] != document["id"]:
                raise ContextError("context_baseline", "读取基线的正文与绑定属于不同内容.")
        return document, binding
