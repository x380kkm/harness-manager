# audience: internal
# # declaration-projection
# 候选由传入声明和目标范围合成, context 仅提供筛选值, 读取与执行权限由宿主判断.
# 声明先按存储层合成, 同层依次按 user, project, path, task 和范围包含关系细化.

from __future__ import annotations

import posixpath
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .declarations import DeclarationIndex, contribution_name, diagnose, index_declarations, matches_version
from .protocol import document_identity, value_diagnostics


SCOPE_KEYS = frozenset({"user", "host", "project", "path", "task", "agent"})


# //// 保存范围内的一项声明值 [@x380kkm 2026-09-06] ////
@dataclass
class ScopedValue:
    scope: dict[str, tuple[str, ...]]
    subject: str
    value: Any
    is_default: bool = False
    layer: int = 0


# //// 保存候选摘要与已规范化的内容输入 [@x380kkm 2026-09-06] ////
@dataclass
class ProjectedContent:
    summary: dict
    owner: dict
    member: dict
    chain: list[tuple[dict, dict]]
    bindings: list[ScopedValue]


# //// 保存必需成员的固定引用链 [@x380kkm 2026-09-08] ////
@dataclass
class UnavailableContent:
    reference: str
    chain: list[tuple[dict, dict]]


# //// 保存范围内的内容选择和必需缺口 [@x380kkm 2026-09-08] ////
@dataclass
class ContentProjection:
    candidates: list[ProjectedContent] = field(default_factory=list)
    unavailable: list[UnavailableContent] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)


# //// 规范化声明中的路径 [@x380kkm 2026-09-06] ////
def _path(value: str) -> str:
    value = value.replace("\\", "/")
    normalized = posixpath.normpath(value)
    return normalized.casefold() if ":" in value or value.startswith("//") else normalized


# //// 判断路径范围包含关系 [@x380kkm 2026-09-06] ////
def contains_path(parent: str, child: str) -> bool:
    parent, child = _path(parent), _path(child)
    if parent == ".":
        return child != ".." and not child.startswith(("../", "/")) and ":" not in child
    return parent == child or child.startswith(parent.rstrip("/") + "/")


# //// 解析并匹配明确的目标范围 [@x380kkm 2026-09-06] ////
def resolve_scope(scope: dict | None, context: dict, subject: str, diagnostics: list[dict]) -> dict | None:
    if scope is None:
        return {}
    contract = scope.get("contract", {})
    if contract.get("id") != "manager.scope" or matches_version("1.0.0", contract.get("range")) is not True:
        diagnose(diagnostics, "unknown_scope", subject, "范围契约需要可识别的 manager.scope 版本.")
        return None
    selector = scope.get("selector", {})
    if set(selector) - SCOPE_KEYS or scope.get("inherit") is False:
        diagnose(diagnostics, "unknown_scope", subject, "范围字段或继承方式缺少静态解释.")
        return None
    resolved = {}
    for key, selection in selector.items():
        values = selection if isinstance(selection, list) else [selection]
        if not values or any(not isinstance(value, str) or not value for value in values):
            diagnose(diagnostics, "unknown_scope_value", subject, f"范围 {key} 需要明确的字符串或字符串集合.")
            return None
        if key == "agent" and "all" in values:
            continue
        actual = context.get(key)
        if not isinstance(actual, str) or not actual:
            diagnose(diagnostics, "missing_context", subject, f"当前目标缺少 {key}.")
            return None
        values = [actual if value == "current" else value for value in values]
        if key == "path":
            if any(character in value for value in values for character in "*?[]"):
                diagnose(diagnostics, "unknown_path_pattern", subject, "路径范围需要明确的目录或文件位置.")
                return None
            values = list(map(_path, values))
            matched = any(contains_path(value, actual) for value in values)
        else:
            matched = actual in values
        if not matched:
            return None
        resolved[key] = tuple(sorted(set(values)))
    return resolved


# //// 判断声明范围的包含关系 [@x380kkm 2026-09-06] ////
def _contains_scope(parent: dict, child: dict) -> bool:
    for key, values in parent.items():
        if key not in child:
            return False
        if key == "path":
            if not all(any(contains_path(value, item) for value in values) for item in child[key]):
                return False
        elif not set(child[key]).issubset(values):
            return False
    return True


# //// 选取仍影响有效值的最具体声明 [@x380kkm 2026-09-06] ////
def _frontier(values: list[ScopedValue]) -> list[ScopedValue]:
    return [value for value in values if not any(_overrides(other, value) for other in values)]


# //// 判断一项声明是否细化另一项声明 [@x380kkm 2026-09-06] ////
def _overrides(candidate: ScopedValue, previous: ScopedValue) -> bool:
    if candidate.is_default != previous.is_default:
        return previous.is_default
    if candidate.layer != previous.layer:
        return candidate.layer > previous.layer
    candidate_level = _scope_level(candidate.scope)
    previous_level = _scope_level(previous.scope)
    if candidate_level != previous_level:
        return candidate_level > previous_level
    return _contains_scope(previous.scope, candidate.scope) and not _contains_scope(candidate.scope, previous.scope)


# //// 取得声明的继承层级 [@x380kkm 2026-09-06] ////
def _scope_level(scope: dict) -> int:
    return max((level for level, key in enumerate(("user", "project", "path", "task")) if key in scope), default=0)


# //// 合成值并保留相互冲突的来源 [@x380kkm 2026-09-06] ////
def resolve_values(values: list[ScopedValue], diagnostics: list[dict], field: str) -> tuple[bool, Any]:
    current = _frontier(values)
    if not current:
        return True, None
    if all(isinstance(item.value, dict) for item in current):
        scalars = [item for item in values if not isinstance(item.value, dict)]
        objects = [item for item in values if isinstance(item.value, dict)
                   and not any(_overrides(scalar, item) for scalar in scalars)]
        result, valid = {}, True
        for key in sorted({key for item in objects for key in item.value}):
            children = [ScopedValue(item.scope, item.subject, item.value[key], item.is_default, item.layer)
                        for item in objects if key in item.value]
            accepted, value = resolve_values(children, diagnostics, f"{field}/{key}")
            valid = valid and accepted
            result[key] = value
        return valid, result
    first = current[0].value
    if any(type(item.value) is not type(first) or item.value != first for item in current[1:]):
        sources = ", ".join(sorted(item.subject for item in current))
        diagnose(diagnostics, "binding_conflict", field, f"同层或不可比较范围的设置相互冲突: {sources}.")
        return False, None
    return True, deepcopy(first)


# //// 合并点契约默认值并核对内容字段 [@x380kkm 2026-09-08] ////
def normalize_member(member: dict, contracts: list[dict], diagnostics: list[dict], subject: str) -> tuple[bool, dict]:
    if "ref" in member:
        return True, deepcopy(member)
    available = [contract for contract in contracts if contract["point"] == member["point"]
                 and contract["contract"]["id"] == member["contract"]["id"]]
    matching = [contract for contract in available
                if matches_version(contract["contract"]["version"], member["contract"].get("range")) is True]
    if available and not matching:
        diagnose(diagnostics, "unresolved_point_contract", subject, "已提供的点契约与内容声明的版本要求尚未匹配.")
        return False, deepcopy(member)
    if len(matching) > 1:
        diagnose(diagnostics, "ambiguous_point_contract", subject, "内容默认值匹配多个点契约.")
        return False, deepcopy(member)
    defaults = matching[0].get("defaults", {}) if matching else {}
    valid, result = resolve_values([ScopedValue({}, "contract-defaults", defaults, True),
                              ScopedValue({}, subject, member)], diagnostics, subject)
    if not valid:
        return False, deepcopy(member)
    if matching:
        shape = matching[0]["payloadSchema"]
        shape = {"$ref": shape} if isinstance(shape, str) else shape
        try:
            errors = value_diagnostics(result["payload"], shape)
        except ValueError as error:
            diagnose(diagnostics, "unresolved_payload_schema", subject, str(error))
            return False, result
        if errors:
            diagnose(diagnostics, "invalid_payload", subject, "内容字段不符合点契约: " + " | ".join(errors[:4]))
            return False, result
    return True, result


# //// 检查静态内容的条件与必要性 [@x380kkm 2026-09-06] ////
def _content_state(member: dict, context: dict, subject: str, diagnostics: list[dict]) -> tuple[bool, bool]:
    required = member.get("criticality", {}).get("default") == "required"
    if resolve_scope(member.get("scope"), context, subject, diagnostics) is None:
        return False, required
    if member.get("activation") or member.get("criticality", {}).get("rules"):
        diagnose(diagnostics, "unknown_condition", subject, "内容启用或必要性条件需要对应契约的静态解释.")
        return False, required
    return True, required


# //// 汇集当前目标的适用绑定 [@x380kkm 2026-09-06] ////
def _bindings(documents: list[dict], context: dict, diagnostics: list[dict], layers: dict[str, int]) -> dict[str, list[ScopedValue]]:
    grouped: dict[str, list[ScopedValue]] = {}
    for document in documents:
        if document.get("kind") != "PluginBinding":
            continue
        scope = resolve_scope(document["target"], context, document["id"], diagnostics)
        if scope is not None:
            grouped.setdefault(document["plugin"]["id"], []).append(
                ScopedValue(scope, document["id"], document, layer=layers.get(document_identity(document), 0)))
    return grouped


# //// 合成绑定的启用状态与使用选项 [@x380kkm 2026-09-06] ////
def _binding_configuration(plugin: dict, bindings: list[ScopedValue], diagnostics: list[dict]) -> tuple[bool, dict]:
    enabled = [ScopedValue(item.scope, item.subject, item.value["enabled"], layer=item.layer)
               for item in bindings if "enabled" in item.value]
    valid, active = resolve_values(enabled, diagnostics, f"{plugin['id']}/enabled")
    if not valid or active is False:
        return False, {}
    options = [ScopedValue({}, plugin["id"], plugin.get("options", {}).get("defaults", {}), True)]
    options.extend(ScopedValue(item.scope, item.subject, item.value["options"], layer=item.layer)
                   for item in bindings if "options" in item.value)
    valid, merged = resolve_values(options, diagnostics, f"{plugin['id']}/options")
    shape = plugin.get("options", {}).get("schema")
    if valid and shape is not None:
        try:
            errors = value_diagnostics(merged, shape)
        except ValueError as error:
            diagnose(diagnostics, "unresolved_options_schema", plugin["id"], str(error))
            return False, {}
        if errors:
            diagnose(diagnostics, "invalid_options", plugin["id"], "使用选项不符合内容契约: " + " | ".join(errors[:4]))
            return False, {}
    return valid, merged


# //// 合成当前范围的发布选择 [@x380kkm 2026-09-06] ////
def _plugin_selection(identifier: str, bindings: list[ScopedValue], diagnostics: list[dict]) -> dict | None:
    values = [ScopedValue(item.scope, item.subject,
                          {key: value for key, value in item.value["plugin"].items() if key != "id"}, layer=item.layer)
              for item in bindings]
    valid, selector = resolve_values(values, diagnostics, f"{identifier}/release")
    return selector if valid else None


# //// 合成局部成员的选择与必要性 [@x380kkm 2026-09-06] ////
def _selection(local_id: str, bindings: list[ScopedValue], diagnostics: list[dict], subject: str) -> tuple[bool, bool]:
    values, required = [], False
    for binding in bindings:
        selection = binding.value.get("selection", {})
        required = required or local_id in selection.get("require", [])
        included = local_id in selection.get("include", [])
        excluded = local_id in selection.get("exclude", [])
        if excluded and (included or local_id in selection.get("require", [])):
            diagnose(diagnostics, "selection_conflict", binding.subject, f"成员 {local_id} 同时被选入与排除.")
            return False, required
        if included or excluded:
            values.append(ScopedValue(binding.scope, binding.subject, included, layer=binding.layer))
        elif "selectionBaseline" in binding.value and local_id not in binding.value["selectionBaseline"]["included"]:
            values.append(ScopedValue(binding.scope, binding.subject, False, layer=binding.layer))
    valid, selected = resolve_values(values, diagnostics, f"{subject}/selection")
    return valid and selected is not False, required


# //// 保留内容来源与组合路径 [@x380kkm 2026-09-06] ////
def _source(chain: list[tuple[dict, dict]], diagnostics: list[dict]) -> dict:
    owner, member = chain[-1]
    reference = member.get("source", "inherit")
    if isinstance(reference, str) and reference.startswith("source:"):
        if not any(source["id"] == reference.removeprefix("source:") for source in owner.get("sources", [])):
            diagnose(diagnostics, "missing_source_binding", f"{owner['id']}#{member['id']}",
                     f"来源引用 {reference} 缺少所属 Plugin 的来源绑定.")
    return {"reference": deepcopy(member.get("source", "inherit")), "owner": owner["id"],
            "version": owner["release"]["version"], "bindings": deepcopy(owner.get("sources", []))}


# //// 求值单个 Plugin 的可用内容和必需缺口 [@x380kkm 2026-09-08] ////
def _project_plugin(index: DeclarationIndex, plugin: dict, bindings: list[ScopedValue], context: dict) -> ContentProjection:
    projection = ContentProjection(diagnostics=index.diagnostics)
    active, options = _binding_configuration(plugin, bindings, index.diagnostics)
    if not active:
        return projection
    member_ids = {member["id"] for member in plugin["contributions"]}
    for binding in bindings:
        for identifiers in binding.value.get("selection", {}).values():
            for identifier in set(identifiers) - member_ids:
                diagnose(index.diagnostics, "missing_selection_target", binding.subject, f"成员 {identifier} 尚未提供声明.")
    for alias in plugin["contributions"]:
        reference = f"{plugin['id']}#{alias['id']}"
        selected, required = _selection(alias["id"], bindings, index.diagnostics, reference)
        chain = index.resolve_reference(reference, plugin["release"]["version"])
        if chain is None:
            continue
        normalized, applicable = [], True
        for owner, member in chain:
            member_ref = f"{owner['id']}#{member['id']}"
            valid, content = normalize_member(member, index.point_contracts, index.diagnostics, member_ref)
            normalized.append((owner, content))
            if not valid:
                required = required or content.get("criticality", {}).get("default") == "required"
                applicable = False
                break
            accepted, necessary = _content_state(content, context, member_ref, index.diagnostics)
            required = required or necessary
            applicable = applicable and accepted
        if required and (not selected or not applicable):
            projection.unavailable.append(UnavailableContent(reference, [*normalized, *chain[len(normalized):]]))
        if not selected:
            if required:
                diagnose(index.diagnostics, "required_excluded", reference, "必要成员被当前范围的选择排除.")
            continue
        if not applicable:
            if required:
                diagnose(index.diagnostics, "required_unavailable", reference, "必要成员的适用性或内容契约需要进一步解释.")
            continue
        owner, content = normalized[-1]
        payload = content.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        summary = {"ref": reference, "name": contribution_name(content),
                           "description": payload.get("description", ""), "point": content["point"],
                           "plugin": plugin["id"], "version": plugin["release"]["version"],
                           "binding_ids": sorted(item.subject for item in bindings), "options": deepcopy(options),
                           "source": _source(normalized, index.diagnostics), "entry": payload.get("entry"),
                           "content": f"{owner['id']}#{content['id']}",
                           "content_version": owner["release"]["version"], "required": required,
                           "reference_chain": [{"ref": f"{item_owner['id']}#{item['id']}",
                                                "version": item_owner["release"]["version"]}
                                               for item_owner, item in normalized]}
        projection.candidates.append(ProjectedContent(summary, owner, content, normalized, bindings))
    return projection


# //// 求值当前目标的全部静态内容输入 [@x380kkm 2026-09-06] ////
def project_selection(documents: list[dict], context: dict, *, layers: dict[str, int] | None = None) -> ContentProjection:
    index = index_declarations(documents)
    projection = ContentProjection(diagnostics=index.diagnostics)
    for identifier, bindings in sorted(_bindings(documents, context, index.diagnostics, layers or {}).items()):
        selector = _plugin_selection(identifier, bindings, index.diagnostics)
        if selector is None:
            continue
        plugin = index.resolve_plugin(identifier, [selector])
        if plugin is not None:
            selected = _project_plugin(index, plugin, bindings, context)
            projection.candidates.extend(selected.candidates)
            projection.unavailable.extend(selected.unavailable)
    return projection


# //// 生成当前目标的可用内容与诊断 [@x380kkm 2026-09-08] ////
def project_content(documents: list[dict], context: dict, *, layers: dict[str, int] | None = None) -> tuple[list[ProjectedContent], list[dict]]:
    projection = project_selection(documents, context, layers=layers)
    return projection.candidates, projection.diagnostics


# //// 发现目标范围内的静态候选 [@x380kkm 2026-09-06] ////
def discover(documents: list[dict], context: dict, query: str = "", limit: int = 20, cursor: int = 0,
             *, layers: dict[str, int] | None = None, point: str = "") -> dict:
    if type(limit) is not int or type(cursor) is not int or limit < 1 or cursor < 0:
        diagnostics = []
        diagnose(diagnostics, "invalid_page", "discover", "limit 需要为正整数, cursor 需要为非负整数.")
        return {"candidates": [], "next_cursor": None, "diagnostics": diagnostics}
    projected, diagnostics = project_content(documents, context, layers=layers)
    candidates = [item.summary for item in projected if not point or item.summary["point"] == point]
    terms = query.casefold().split()
    candidates = [candidate for candidate in candidates if all(term in " ".join(
        str(candidate[key]) for key in ("ref", "name", "description", "point")).casefold() for term in terms)]
    candidates.sort(key=lambda candidate: (candidate["name"].casefold(), candidate["ref"], candidate["version"]))
    page = candidates[cursor:cursor + limit]
    next_cursor = cursor + len(page) if cursor + len(page) < len(candidates) else None
    return {"candidates": page, "next_cursor": next_cursor, "diagnostics": diagnostics}
