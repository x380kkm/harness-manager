# audience: internal
# # usage-settings
"""使用设置编辑独立绑定, 来源发布保持原样. 成员接受集合限制新增内容, 显式选择和继承分别表达使用意图."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from .catalogs import CatalogView
from .declarations import contribution_name, index_declarations, matches_version
from .projection import _bindings, _plugin_selection, project_content
from .protocol import document_identity, document_name, value_diagnostics

SETTINGS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "state": {"enum": ["inherit", "enabled", "disabled"]},
        "members": {"type": "object", "additionalProperties": {"enum": ["inherit", "include", "exclude", "require"]}},
        "options": {"type": ["object", "null"]},
        "selector": {"type": "object"},
        "acceptance": {"enum": ["keep", "current", "inherit"]},
        "constraint": {"type": ["string", "null"], "minLength": 1},
        "channel": {"type": ["string", "null"], "minLength": 1},
    },
}


# //// 保存使用设置的可定位输入错误 [@x380kkm 2026-09-06] ////
class UsageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 按具体发布身份选择可管理的来源包 [@x380kkm 2026-09-06] ////
def find_plugin(view: CatalogView, identity: str) -> dict:
    matches = [document for document in view.documents
               if document["kind"] == "Plugin" and document_identity(document) == identity]
    if len(matches) != 1:
        raise UsageError("usage_plugin", "使用设置需要当前目录中唯一的 Plugin 发布.")
    return matches[0]


# //// 按目标范围与绑定层次解析实际选择的发布身份 [@x380kkm 2026-09-10] ////
def selected_releases(view: CatalogView, context: dict) -> tuple[dict[str, str | None], list[dict]]:
    index = index_declarations(view.documents)
    result = {}
    for identifier, bindings in _bindings(view.documents, context, index.diagnostics, view.layers).items():
        selector = _plugin_selection(identifier, bindings, index.diagnostics)
        plugin = index.resolve_plugin(identifier, [selector]) if selector is not None else None
        result[identifier] = document_identity(plugin) if plugin is not None else None
    return result, index.diagnostics


# //// 描述成员用途与当前可发现内容 [@x380kkm 2026-09-06] ////
def describe_usage(view: CatalogView, local: list[dict], identity: str, scope: str, context: dict) -> dict:
    plugin = find_plugin(view, identity)
    bindings = [document for document in local if document["kind"] == "PluginBinding" and document["plugin"]["id"] == plugin["id"]]
    base_id = f"binding:{scope}/{plugin['id']}"
    default = next((item for item in bindings if item["id"] == base_id), bindings[0] if len(bindings) == 1 else None)
    suggestion = base_id if all(item["id"] != base_id for item in bindings) else f"{base_id}/custom-{uuid4().hex[:8]}"
    index = index_declarations(view.documents)
    members = []
    for member in plugin["contributions"]:
        chain = index.resolve_reference(f"{plugin['id']}#{member['id']}", plugin["release"]["version"])
        content = chain[-1][1] if chain else member
        members.append({"id": member["id"], "ref": f"{plugin['id']}#{member['id']}",
                        "name": contribution_name(content), "point": content.get("point", "reference"),
                        "required": any(value.get("criticality", {}).get("default") == "required" for _, value in chain or []),
                        "reference": member.get("ref")})
    projected, diagnostics = project_content(view.documents, context, layers=view.layers)
    matching = {entry.subject for entry in _bindings(view.documents, context, [], view.layers).get(plugin["id"], [])}
    releases, release_diagnostics = selected_releases(view, context)
    diagnostics.extend(note for note in release_diagnostics if note not in diagnostics)
    effective = [item.summary for item in projected if item.summary["plugin"] == plugin["id"]
                 and item.summary["version"] == plugin["release"]["version"]]
    return {"plugin": identity, "pluginId": plugin["id"], "name": document_name(plugin), "release": plugin["release"], "scope": scope,
            "members": members, "bindings": bindings, "contextBindings": [binding for binding in bindings if binding["id"] in matching],
            "selectedPlugin": releases.get(plugin["id"]),
            "preferred": default["id"] if default else None,
            "suggestedId": suggestion, "defaults": plugin.get("options", {}).get("defaults", {}),
            "settingsSchema": deepcopy(SETTINGS_SCHEMA),
            "optionsSchema": plugin.get("options", {}).get("schema"), "effective": effective,
            "diagnostics": [*view.diagnostics, *index.diagnostics, *diagnostics]}


# //// 将明确的使用设置转换为独立绑定 [@x380kkm 2026-09-06] ////
def usage_document(plugin: dict, settings: dict, baseline: dict | None, scope: str) -> dict:
    errors = value_diagnostics(settings, SETTINGS_SCHEMA)
    if errors:
        raise UsageError("usage_settings", "使用设置不符合字段契约: " + " | ".join(errors[:4]))
    if baseline is not None and (not isinstance(baseline, dict) or baseline.get("kind") != "PluginBinding" or baseline.get("plugin", {}).get("id") != plugin["id"]):
        raise UsageError("usage_baseline", "读取基线需要属于当前 Plugin 的使用绑定.")
    document = deepcopy(baseline) if baseline is not None else {
        "apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding",
        "id": settings.get("id") or f"binding:{scope}/{plugin['id']}",
        "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"},
                   "selector": {"user": "current"} if scope == "user" else {}},
    }
    if settings.get("id"):
        document["id"] = settings["id"]
    if baseline is None:
        document["plugin"] = {"id": plugin["id"], "constraint": plugin["release"]["version"]}
    if "constraint" in settings:
        if settings["constraint"] is None:
            document["plugin"].pop("constraint", None)
        else:
            document["plugin"]["constraint"] = settings["constraint"]
    if "channel" in settings:
        if settings["channel"] is None:
            document["plugin"].pop("channel", None)
        else:
            document["plugin"]["channel"] = settings["channel"]
    if "state" in settings:
        state = settings["state"]
        if state == "inherit":
            document.pop("enabled", None)
        else:
            document["enabled"] = state == "enabled"
    if "selector" in settings:
        document["target"]["selector"] = deepcopy(settings["selector"])
    if "options" in settings:
        options = settings["options"]
        if options is None or options == {}:
            document.pop("options", None)
        else:
            document["options"] = deepcopy(options)
    members = settings.get("members", {})
    known = {member["id"] for member in plugin["contributions"]}
    if set(members) - known:
        raise UsageError("usage_members", "成员选择包含当前发布之外的身份.")
    selection = document.get("selection", {})
    for identity, state in members.items():
        current = next((name for name in ("require", "exclude", "include") if identity in selection.get(name, [])), "inherit")
        if current == state:
            continue
        for values in selection.values():
            if identity in values:
                values.remove(identity)
        if state != "inherit":
            selection.setdefault(state, []).append(identity)
        if state == "require":
            selection.setdefault("include", []).append(identity)
    selection = {key: values for key, values in selection.items() if values}
    if selection:
        document["selection"] = selection
    else:
        document.pop("selection", None)
    acceptance = settings.get("acceptance", "keep")
    if acceptance == "current":
        selector = document["plugin"]
        if matches_version(plugin["release"]["version"], selector.get("constraint")) is not True or (
                selector.get("channel") and selector["channel"] != plugin["release"].get("channel")):
            raise UsageError("usage_release", "确认成员前需要选择包含当前发布的版本约束与通道.")
        document["selectionBaseline"] = {"release": deepcopy(plugin["release"]), "included": sorted(members)}
    elif acceptance == "inherit":
        document.pop("selectionBaseline", None)
    return document
