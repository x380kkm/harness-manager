# audience: internal
# # content-selection-plan
"""读取计划汇集选定方法, 当前适用说明和按 Skill 选择的配套内容. 依赖通过当前范围内的内容引用解析."""
from __future__ import annotations

from dataclasses import dataclass, field

from .content import ContentError
from .declarations import matches_version
from .projection import ProjectedContent, ScopedValue, contains_path, project_selection, resolve_scope, resolve_values
from .module_contexts import module_contexts

INSTRUCTION_POINT = "context.x380kkm/instruction"
PREFERENCE_POINT = "preference.x380kkm/method"
TASK_CONTEXT_POINT = "context.x380kkm/task"
SKILL_POINT = "skill.x380kkm/deployment"


# //// 保存一个内容入口的用途与附加单元 [@x380kkm 2026-09-06] ////
@dataclass
class PlannedEntry:
    content: ProjectedContent
    purpose: str
    resources: list[str] = field(default_factory=list)


# //// 保存完整读取的所选内容与必需缺口 [@x380kkm 2026-09-06] ////
@dataclass
class ContentPlan:
    selected: ProjectedContent
    entries: list[PlannedEntry]
    missing: list[str]
    diagnostics: list[dict]


# //// 为协议结果构造可定位的读取诊断 [@x380kkm 2026-09-06] ////
def content_diagnostic(code: str, subject: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "severity": severity, "subject": subject, "message": message}


# //// 从当前候选选择唯一的使用身份 [@x380kkm 2026-09-06] ////
def choose_content(entries: list[ProjectedContent], ref: str, version: str | None) -> ProjectedContent:
    matches = [entry for entry in entries if entry.summary["ref"] == ref
               and matches_version(entry.summary["version"], version) is True]
    if len(matches) != 1:
        raise ContentError("content_selection_unavailable", "内容引用需要在当前绑定范围中匹配一个使用版本.",
                           {"ref": ref, "version": version, "matches": len(matches)})
    return matches[0]


# //// 取得绑定与内容声明共同限定的范围 [@x380kkm 2026-09-06] ////
def content_scopes(entry: ProjectedContent, context: dict, diagnostics: list[dict]) -> list[tuple[dict, int]]:
    scopes = []
    for binding in entry.bindings:
        scope = dict(binding.scope)
        for _, member in entry.chain:
            local = resolve_scope(member.get("scope"), context, entry.summary["ref"], diagnostics)
            for key, values in (local or {}).items():
                if key not in scope:
                    scope[key] = values
                elif key == "path":
                    scope[key] = tuple(sorted({right if contains_path(left, right) else left
                                               for left in scope[key] for right in values
                                               if contains_path(left, right) or contains_path(right, left)}))
                else:
                    scope[key] = tuple(sorted(set(scope[key]).intersection(values)))
        scopes.append((scope, binding.layer))
    return scopes


# //// 按语义位置合成当前说明入口 [@x380kkm 2026-09-06] ////
def select_instructions(entries: list[ProjectedContent], context: dict, diagnostics: list[dict]) -> list[ProjectedContent]:
    slots: dict[str, list[ScopedValue]] = {}
    slot_entries: dict[tuple[str, tuple[str, str]], list[ProjectedContent]] = {}
    for entry in entries:
        payload = entry.member["payload"]
        slot = payload.get("slot") if isinstance(payload, dict) else None
        slot = slot if isinstance(slot, str) and slot else entry.summary["content"]
        identity = (entry.summary["content"], entry.summary["content_version"])
        slot_entries.setdefault((slot, identity), []).append(entry)
        values = slots.setdefault(slot, [])
        values.extend(ScopedValue(scope, entry.summary["ref"], identity, layer=layer)
                      for scope, layer in content_scopes(entry, context, diagnostics))
    selected = []
    for slot, values in sorted(slots.items()):
        valid, identity = resolve_values(values, diagnostics, f"instruction-slot:{slot}")
        if valid:
            selected.extend(slot_entries[(slot, identity)])
        else:
            raise ContentError("instruction_slot_conflict", "当前范围内的说明在同一语义位置发生冲突.", diagnostics)
    return selected


# //// 读取载体显式声明的必需相对入口 [@x380kkm 2026-09-06] ////
def required_resources(member: dict) -> list[str]:
    resources = []
    for extension in member.get("extensions", []):
        contract = extension["contract"]
        if contract["id"] != "carrier.x380kkm/required-units":
            continue
        if matches_version("1.0.0", contract["range"]) is not True:
            raise ContentError("content_carrier_contract", "必需单元扩展的版本需要对应读取实现.")
        payload = extension["payload"]
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or any(not isinstance(entry, str) or not entry for entry in entries):
            raise ContentError("content_required_units", "必需单元扩展需要 entries 相对文件列表.")
        resources.extend(entries)
    return list(dict.fromkeys(resources))


# //// 根据方法选择匹配配套上下文 [@x380kkm 2026-09-06] ////
def matches_context(member: dict, references: set[str]) -> bool:
    payload = member["payload"]
    return isinstance(payload, dict) and any(isinstance(payload.get(key), str) and payload[key] in references for key in ("skill", "subject"))


# //// 汇集当前使用的完整入口与强内容依赖 [@x380kkm 2026-09-06] ////
def plan_content(documents: list[dict], context: dict, ref: str, version: str | None,
                 resources: list[str], *, layers: dict[str, int] | None = None) -> ContentPlan:
    projection = project_selection(documents, context, layers=layers)
    projected, diagnostics = projection.candidates, projection.diagnostics
    try:
        selected = choose_content(projected, ref, version)
    except ContentError as error:
        error.details["diagnostics"] = diagnostics
        raise
    if selected.member["point"] != SKILL_POINT:
        raise ContentError("content_method_required", "完整方法读取需要 Skill 入口; 单独内容可通过单元读取接口查看.")
    references = {entry["ref"] for entry in selected.summary["reference_chain"]}
    instructions = select_instructions([entry for entry in projected if entry.member["point"] == INSTRUCTION_POINT], context, diagnostics)
    planned = [PlannedEntry(entry, "instruction") for entry in instructions]
    planned.extend(PlannedEntry(entry, "preference") for entry in projected if entry.member["point"] == PREFERENCE_POINT)
    planned.append(PlannedEntry(selected, "method", resources))
    derived = module_contexts(projected, included=[entry.content for entry in planned])
    projected.extend(derived)
    planned.extend(PlannedEntry(entry, "preference") for entry in derived if entry.member["point"] == PREFERENCE_POINT)
    references.update(item["ref"] for entry in planned for item in entry.content.summary["reference_chain"])
    planned.extend(PlannedEntry(entry, "task-context") for entry in projected
                   if entry.member["point"] == TASK_CONTEXT_POINT and matches_context(entry.member, references))
    missing = []
    seen: dict[tuple[str, str], PlannedEntry] = {}
    entries = []
    while planned:
        entry = planned.pop(0)
        summary = entry.content.summary
        key = (summary["ref"], summary["version"])
        if key in seen:
            seen[key].resources = list(dict.fromkeys([*seen[key].resources, *entry.resources]))
            continue
        seen[key] = entry
        contract = entry.content.member["contract"]
        if contract["id"] != entry.content.member["point"] or matches_version("1.0.0", contract["range"]) is not True:
            raise ContentError("content_use_contract", "当前内容用途的版本需要对应读取契约.", contract)
        entry.resources = list(dict.fromkeys([*entry.resources, *required_resources(entry.content.member)]))
        entries.append(entry)
        payload = entry.content.member["payload"]
        if isinstance(payload, dict) and payload.get("when"):
            raise ContentError("content_condition_unresolved", "当前内容的加载条件需要对应契约解释.", {"ref": summary["ref"]})
        requirements = [requirement for owner, member in entry.content.chain
                        for declaration in (owner, member) for requirement in declaration.get("requirements", [])]
        for requirement in requirements:
            if requirement["strength"] != "required" or "#" not in requirement["target"]:
                continue
            target = requirement["target"]
            matches = [candidate for candidate in projected if candidate.summary["ref"] == target]
            if len(matches) != 1 or requirement.get("when") or requirement.get("resolver"):
                missing.append(target)
                diagnostics.append(content_diagnostic("content.required-reference-unavailable", target,
                                                      "必需内容引用在当前范围中缺少唯一可读取结果."))
            else:
                planned.append(PlannedEntry(matches[0], "reference"))
    for unavailable in projection.unavailable:
        member = unavailable.chain[-1][1]
        point = member["point"]
        if point in {INSTRUCTION_POINT, PREFERENCE_POINT} or point == TASK_CONTEXT_POINT and matches_context(member, references):
            missing.append(unavailable.reference)
    normalized = [item if "severity" in item else content_diagnostic(item["code"], item["subject"], item["message"], "warning")
                  for item in diagnostics]
    return ContentPlan(selected, entries, list(dict.fromkeys(missing)), normalized)
