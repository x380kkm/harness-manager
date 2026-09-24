# audience: internal
# # module-inventory
"""模块卡片投影 Plugin 的贡献和显式引用. 成员身份保留所属发布, 载体说明来自模块展示扩展."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .card_subjects import member_path, source_record
from .catalogs import Catalogs, CatalogView
from .content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT, TASK_CONTEXT_POINT
from .declarations import DeclarationIndex, contribution_name, index_declarations
from .module_contexts import module_presentation
from .protocol import document_identity, document_name

POINT_KINDS = {SKILL_POINT: "skill", INSTRUCTION_POINT: "rule", PREFERENCE_POINT: "rule",
               TASK_CONTEXT_POINT: "rule", "tool.x380kkm/endpoint": "tool", "hook.x380kkm/lifecycle": "hook"}
MEMBER_KINDS = {"skill", "rule", "hook", "tool", "source"}


# //// 识别具有组合内容或显式模块声明的发布 [@x380kkm 2026-09-07] ////
def is_module(document: dict) -> bool:
    if document["kind"] != "Plugin":
        return False
    members = document["contributions"]
    return module_presentation(document) is not None or len(members) > 1 or any("ref" in member for member in members)


# //// 将已确认来源身份对应到现有卡片 [@x380kkm 2026-09-07] ////
def member_card(document: dict, member: dict, items: list[dict]) -> str | None:
    record = source_record(document)
    if record and record.get("kind") == POINT_KINDS.get(member.get("point")) and len([
            value for value in document["contributions"] if value.get("point") == member.get("point")]) == 1:
        matching = [item for item in items if item["id"] == record.get("id")]
    elif member.get("point") == SKILL_POINT:
        location = member_path(document, member, Path.cwd())
        matching = [item for item in items if item["kind"] == "skill" and location is not None
                    and Path(item["path"]).resolve() == location]
    else:
        matching = []
    return matching[0]["id"] if len(matching) == 1 else None


# //// 保留组合别名与原始成员的发布身份 [@x380kkm 2026-09-07] ////
def describe_member(document: dict, member: dict, annotation: dict, index: DeclarationIndex, items: list[dict]) -> dict:
    reference = document["id"] + "#" + member["id"]
    chain = index.resolve_reference(reference, document["release"]["version"])
    owner, content = chain[-1] if chain else (document, member)
    point = content.get("point")
    kind = POINT_KINDS.get(point, annotation.get("kind", "source"))
    result = {"id": member["id"], "ref": reference, "documentId": document_identity(document),
              "name": contribution_name(content), "kind": kind if kind in MEMBER_KINDS else "source",
              "point": point, "resolved": chain is not None,
              "referenceChain": [{"ref": plugin["id"] + "#" + value["id"],
                                  "documentId": document_identity(plugin), "version": plugin["release"]["version"]}
                                 for plugin, value in chain or []]}
    if "ref" in member:
        result["reference"] = member["ref"]
    if chain:
        result.update(originalRef=owner["id"] + "#" + content["id"],
                      originalDocumentId=document_identity(owner), version=owner["release"]["version"])
        identity = member_card(owner, content, items)
        if identity:
            result["cardId"] = identity
    for key in ("role", "carrier"):
        if isinstance(annotation.get(key), str) and annotation[key].strip():
            result[key] = annotation[key]
    result["hasContext"] = isinstance(annotation.get("role"), str) and bool(annotation["role"].strip())
    return result


# //// 从已声明组合生成模块卡片和成员导航边 [@x380kkm 2026-09-07] ////
def module_inventory(catalogs: Catalogs, scope: str, items: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    view = catalogs.for_scope(scope)
    return modules_from_view(view, scope, items, catalogs.select(scope).catalog)


# //// 使用已读取的声明层投影模块成员与来源 [@x380kkm 2026-09-07] ////
def modules_from_view(view: CatalogView, scope: str, items: list[dict], catalog: Path) -> tuple[list[dict], list[dict], list[dict]]:
    index = index_declarations(view.documents)
    modules, edges = [], []
    for document in view.documents:
        if not is_module(document):
            continue
        identity = document_identity(document)
        origins = view.origins.get(identity, [])
        origin = origins[-1] if origins else {"scope": scope, "path": str(catalog)}
        presentation = module_presentation(document) or {}
        annotations = presentation.get("members", {})
        annotations = annotations if isinstance(annotations, dict) else {}
        members = [describe_member(document, member,
                                   annotations.get(member["id"]) if isinstance(annotations.get(member["id"]), dict) else {},
                                   index, items) for member in document["contributions"]]
        identifier = "module:" + uuid5(NAMESPACE_URL, identity).hex
        modules.append({"id": identifier, "name": document_name(document), "kind": "module",
                        "summary": document.get("metadata", {}).get("description", ""),
                        "path": origin["path"], "scope": origin["scope"], "status": "组合声明",
                        "importable": False, "managedIds": [identity],
                        "details": {"documentId": identity, "pluginId": document["id"],
                                    "version": document["release"]["version"], "catalogScope": origin["scope"],
                                    "members": members, "memberCounts": dict(Counter(member["kind"] for member in members)),
                                    "entrypointIds": [entry["id"] for entry in document.get("entrypoints", [])]}})
        for member in members:
            if "cardId" in member:
                edges.append({"from": identifier, "to": member["cardId"], "label": "使用成员",
                              "relation": "module-member", "ref": member["ref"]})
    return modules, edges, [*view.diagnostics, *index.diagnostics]
