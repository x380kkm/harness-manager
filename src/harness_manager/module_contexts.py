# audience: internal
# # module-contexts
"""模块成员说明由展示扩展中的 role 派生为内容贡献. 原始模块声明保存正文, 上游成员保留独立发布."""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

PRESENTATION_CONTRACT = "manager.module/presentation"
MEMBER_CONTEXT_CONTRACT = "manager.module/member-context"


# //// 提取模块在既有声明中的展示与使用说明 [@x380kkm 2026-09-08] ////
def module_presentation(document: dict) -> dict | None:
    return next((entry["payload"] for entry in document.get("extensions", [])
                 if entry["contract"]["id"] == PRESENTATION_CONTRACT and isinstance(entry.get("payload"), dict)), None)


# //// 汇集已参与读取的模块发布身份 [@x380kkm 2026-09-08] ////
def included_modules(included: list) -> set[tuple[str, str]]:
    return {(owner["id"], owner["release"]["version"]) for entry in included for owner, _ in entry.chain
            if module_presentation(owner) is not None}


# //// 将一份成员说明派生为绑定同源的内容贡献 [@x380kkm 2026-09-08] ////
def member_context(entry, owner: dict, alias: dict, role: str):
    from .content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT, TASK_CONTEXT_POINT
    from .projection import ProjectedContent

    module_name = owner.get("metadata", {}).get("name") or owner["id"]
    member_name = entry.summary["name"]
    alias_ref = owner["id"] + "#" + alias["id"]
    point = TASK_CONTEXT_POINT if entry.member["point"] in {INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT} else PREFERENCE_POINT
    payload = {"name": f"{module_name} / {member_name} 使用说明"}
    text = f"使用模块 {module_name} 中的 {member_name} 时:\n\n{role}"
    if point == TASK_CONTEXT_POINT:
        payload["skill" if entry.member["point"] == SKILL_POINT else "subject"] = alias_ref
        payload["text"] = text
    else:
        payload["guidance"] = text
    member = {"id": "context:" + uuid5(NAMESPACE_URL, alias_ref).hex, "point": point,
              "contract": {"id": point, "range": "^1.0.0"}, "payload": payload,
              "extensions": [{"contract": {"id": MEMBER_CONTEXT_CONTRACT, "range": "^1.0.0"},
                              "payload": {"member": alias["id"], "reference": alias_ref}}]}
    reference = owner["id"] + "#" + member["id"]
    summary = {"ref": reference, "name": payload["name"], "description": "模块成员的使用说明.", "point": point,
               "plugin": owner["id"], "version": owner["release"]["version"],
               "binding_ids": list(entry.summary["binding_ids"]), "options": deepcopy(entry.summary["options"]),
               "source": {"reference": "inherit", "owner": owner["id"], "version": owner["release"]["version"], "bindings": []},
               "entry": None, "content": reference, "content_version": owner["release"]["version"], "required": False,
               "reference_chain": [{"ref": reference, "version": owner["release"]["version"]}]}
    return ProjectedContent(summary, owner, member, [(owner, member)], list(entry.bindings))


# //// 从实际成员选择派生可进入宿主与按需读取的模块说明 [@x380kkm 2026-09-08] ////
def module_contexts(projected: list, *, included: list | None = None) -> list:
    from .content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT

    active = included_modules(included) if included is not None else None
    result = {}
    for entry in projected:
        contextual = entry.member["point"] in {INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT}
        for owner, alias in entry.chain:
            presentation = module_presentation(owner)
            if presentation is None or "ref" not in alias:
                continue
            annotations = presentation.get("members", {})
            annotation = annotations.get(alias["id"], {}) if isinstance(annotations, dict) else {}
            role = annotation.get("role") if isinstance(annotation, dict) else None
            if not isinstance(role, str) or not role.strip():
                continue
            if not contextual and active is not None and (owner["id"], owner["release"]["version"]) not in active:
                continue
            context = member_context(entry, owner, alias, role)
            key = (context.summary["ref"], context.summary["version"])
            if key not in result:
                result[key] = context
            else:
                known = {binding.subject for binding in result[key].bindings}
                result[key].bindings.extend(binding for binding in context.bindings if binding.subject not in known)
                result[key].summary["binding_ids"] = sorted(binding.subject for binding in result[key].bindings)
    return list(result.values())
