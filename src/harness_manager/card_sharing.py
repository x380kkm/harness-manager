# audience: internal
# # card-sharing
"""项目共享由卡片记录决定. 参考关系与适配正文随两端共同归属移动, 私人覆盖保存在用户目录."""
from __future__ import annotations

from copy import deepcopy

from .card_relations import CardRelations, relation_document, relation_id, relation_payload
from .card_collections import CARD_IDENTITY_CONTRACT, validate_shared_collections
from .card_subjects import CardError, CardSubjects
from .catalogs import external_project_locations, portable_project_document
from .protocol import document_identity
from .sharing_storage import apply_sharing, check_shared_relations
from .storage_errors import StorageConflictError
from .usage import usage_document


# //// 从引用定位所属逻辑发布 [@x380kkm 2026-09-07] ////
def reference_plugin(reference: str) -> str:
    return reference.partition("#")[0]


# //// 取得卡片在一层中的明确使用记录 [@x380kkm 2026-09-07] ////
def source_binding(frame: CardSubjects, identity: str, scope: str) -> dict | None:
    plugin = frame.definition(identity)["id"]
    return next((value for value in frame.layer_documents.get(scope, [])
                 if value["kind"] == "PluginBinding" and value["id"] == f"binding:{scope}/{plugin}"), None)


# //// 按两端共享记录确定项目关系的唯一归属 [@x380kkm 2026-09-07] ////
def relation_scope(frame: CardSubjects, source: str, target: str) -> str:
    return "project" if all(source_binding(frame, identity, "project") is not None
                            for identity in (source, target)) else "project-local"


# //// 汇集卡片和相邻关系涉及的发布身份 [@x380kkm 2026-09-07] ////
def related_plugins(frame: CardSubjects, identity: str) -> set[str]:
    result = {frame.definition(identity)["id"]}
    for documents in frame.layer_documents.values():
        for document in documents:
            payload = relation_payload(document)
            if payload is not None and identity in (payload["adapter"]["from"], payload["adapter"]["to"]):
                result.update((document["id"], reference_plugin(payload.get("skill", payload.get("subject", ""))),
                               reference_plugin(payload["adapter"].get("targetRef", ""))))
    return result


# //// 保留指定发布与相邻关系的原始对象基线 [@x380kkm 2026-09-07] ////
def related_records(documents: list[dict], plugins: set[str]) -> list[dict]:
    result = []
    for document in documents:
        match = document.get("id") in plugins or document.get("plugin", {}).get("id") in plugins
        payload = relation_payload(document)
        if payload is not None:
            match = match or reference_plugin(payload.get("skill", payload.get("subject", ""))) in plugins
            match = match or reference_plugin(payload["adapter"].get("targetRef", "")) in plugins
        if match:
            result.append(document)
    return sorted(result, key=document_identity)


# //// 描述共享动作涉及的三个来源层 [@x380kkm 2026-09-07] ////
def sharing_baseline(frame: CardSubjects, identity: str) -> dict:
    plugins = related_plugins(frame, identity)
    return deepcopy({"id": identity, "source": frame.definition(identity),
                     "documents": {scope: related_records(documents, plugins)
                                   for scope, documents in frame.layer_documents.items()}})


# //// 将使用设置复制到指定项目层 [@x380kkm 2026-09-07] ////
def move_binding(document: dict, scope: str) -> dict:
    result = deepcopy(document)
    result["id"] = f"binding:{scope}/{document['plugin']['id']}"
    result["target"]["selector"]["project"] = "current"
    return result


# //// 为项目保存当前层选定的卡片使用设置 [@x380kkm 2026-09-07] ////
def effective_source_binding(frame: CardSubjects, identity: str, scope: str) -> dict:
    document = frame.definition(identity)
    selected = next((source_binding(frame, identity, layer) for layer in ("project-local", "project", "user")
                     if source_binding(frame, identity, layer) is not None), None)
    binding = usage_document(document, {"state": "disabled"}, None, scope) if selected is None else move_binding(selected, scope)
    subject = frame.subject(identity)
    extensions = [extension for extension in binding.get("extensions", [])
                  if extension["contract"]["id"] != CARD_IDENTITY_CONTRACT]
    extensions.append({"contract": {"id": CARD_IDENTITY_CONTRACT, "range": "^1.0.0"},
                       "payload": {"itemId": identity, "ref": subject["ref"]}})
    binding["extensions"] = extensions
    return binding


# //// 将一条参考关系的正文和绑定放到同一项目层 [@x380kkm 2026-09-07] ////
def edge_documents(frame: CardSubjects, edge: dict, scope: str) -> tuple[dict, dict]:
    source, target = frame.subject(edge["from"]), frame.subject(edge["to"])
    document = relation_document(source, target, edge["text"], scope)
    binding = usage_document(document, {"state": "enabled" if edge["enabled"] else "disabled"}, None, scope)
    return document, binding


# //// 把对象对放入一次原子写入目标 [@x380kkm 2026-09-07] ////
def put_documents(targets: dict, documents) -> None:
    for document in documents:
        targets[document_identity(document)] = document


# //// 把项目内部来源保存为相对项目的位置 [@x380kkm 2026-09-07] ////
def relative_project_sources(frame: CardSubjects, targets: dict) -> None:
    root = frame.catalogs.project.workspace
    for identity, document in list(targets.items()):
        if document is not None:
            targets[identity] = portable_project_document(document, root)


# //// 说明共享声明仍依赖的本机来源位置 [@x380kkm 2026-09-07] ////
def sharing_portability(frame: CardSubjects, identity: str) -> dict:
    plugins = related_plugins(frame, identity)
    documents = [document for document in frame.project
                 if document.get("id") in plugins or document.get("plugin", {}).get("id") in plugins]
    locations = external_project_locations(documents, frame.catalogs.project.workspace)
    paths, scope_paths, receipt_paths = (locations[kind] for kind in ("source", "scope", "receipt"))
    warnings = []
    if paths:
        warnings.append("共享声明引用本机安装位置, 其他设备需要提供对应来源或调整位置映射.")
    if scope_paths:
        warnings.append("共享声明含固定目录范围, 移动项目或更换设备时需要确认范围映射.")
    if receipt_paths:
        warnings.append("共享来源记录指向项目外的位置, 接收者可查看这些位置; 原文定位需要对应来源.")
    return {"portability": "local-source-required" if paths else "local-context-required" if scope_paths or receipt_paths else "project-contained",
            "sourcePathsNeeded": sorted(paths),
            "scopePathsNeeded": sorted(scope_paths), "sourceReceiptPaths": sorted(receipt_paths), "warnings": warnings}


# //// 从指定项目层移出关系的正文与绑定 [@x380kkm 2026-09-07] ////
def remove_edge(targets: dict, source: str, target: str, scope: str) -> None:
    plugin = relation_id(source, target)
    targets[plugin + "@local:" + scope] = None
    targets[f"binding:{scope}/{plugin}"] = None


# //// 核对共享保存中的并发变化与引用闭合 [@x380kkm 2026-09-07] ////
def sharing_guard(plugins: set[str]):
    def guard(scope: str | None, expected: dict, current: dict, targets: dict) -> None:
        for name, documents in expected.items():
            if related_records(documents, plugins) != related_records(current[name], plugins):
                raise StorageConflictError(next(iter(sorted(plugins))))
        if scope in {"project", None}:
            after = {document_identity(value): value for value in current["project"]}
            removed = {value["id"] for value in current["project"] if value["kind"] == "Plugin"
                       and not value["id"].startswith("plugin:adapter/")
                       and document_identity(value) in targets and targets[document_identity(value)] is None}
            for identity, value in targets.items():
                if value is None:
                    after.pop(identity, None)
                else:
                    after[identity] = value
            check_shared_relations(list(after.values()))
            validate_shared_collections(list(after.values()))
            for document in after.values():
                references = [document.get("plugin", {}).get("id", "")]
                for carrier in [document, *document.get("contributions", [])]:
                    references.append(carrier.get("ref", ""))
                    references.extend(value.get("target", "") for key in ("requirements", "relations")
                                      for value in carrier.get(key, []))
                if any(reference_plugin(reference) in removed for reference in references):
                    raise CardError("sharing_dependencies", "其他共享声明仍引用此卡片, 请先调整它们的使用关系.")
    return guard


# //// 迁移项目卡片及相邻关系的共享归属 [@x380kkm 2026-09-07] ////
def set_shared(frame: CardSubjects, identity: str, shared: bool, baseline: dict | None) -> bool:
    if type(shared) is not bool:
        raise CardError("card_shared", "共享设置需要布尔值.")
    current = sharing_baseline(frame, identity)
    if baseline is None:
        if source_binding(frame, identity, "project") or source_binding(frame, identity, "project-local"):
            raise StorageConflictError(frame.definition(identity)["id"])
    elif baseline != current:
        raise StorageConflictError(frame.definition(identity)["id"])
    definition = frame.definition(identity)
    source_key = document_identity(definition)
    private, project, cleanup = {}, {}, {}
    put_documents(private, (definition, effective_source_binding(frame, identity, "project-local")))
    if shared:
        put_documents(project, (definition, effective_source_binding(frame, identity, "project")))
        cleanup[f"binding:project-local/{definition['id']}"] = None
    else:
        project[source_key] = None
        project[f"binding:project/{definition['id']}"] = None
    move_incident_relations(frame, identity, shared, private, project, cleanup)
    relative_project_sources(frame, project)
    plugins = related_plugins(frame, identity)
    return apply_sharing(frame, [("project-local", private), ("project", project), ("project-local", cleanup)],
                         sharing_guard(plugins))


# //// 按变更后的共同归属安排相邻关系 [@x380kkm 2026-09-07] ////
def move_incident_relations(frame: CardSubjects, identity: str, shared: bool, private: dict, project: dict, cleanup: dict) -> None:
    edges = [edge for edge in CardRelations(frame).edges() if identity in (edge["from"], edge["to"])]
    for edge in edges:
        put_documents(private, edge_documents(frame, edge, "project-local"))
        other = edge["to"] if edge["from"] == identity else edge["from"]
        destination_shared = shared and source_binding(frame, other, "project") is not None
        if destination_shared:
            put_documents(project, (frame.definition(edge["from"]), frame.definition(edge["to"])))
            put_documents(project, edge_documents(frame, edge, "project"))
            remove_edge(cleanup, edge["from"], edge["to"], "project-local")
        else:
            remove_edge(project, edge["from"], edge["to"], "project")
            for end in (edge["from"], edge["to"]):
                put_documents(private, (frame.definition(end),))


# //// 修改共享层默认值并同步相邻关系的归属 [@x380kkm 2026-09-07] ////
def configure_project(frame: CardSubjects, identity: str, state: str) -> bool:
    definition = frame.definition(identity)
    private, project, cleanup = {}, {}, {}
    if state == "inherit":
        put_documents(private, (definition,))
        project[document_identity(definition)] = None
        project[f"binding:project/{definition['id']}"] = None
    else:
        binding = usage_document(definition, {"state": state}, source_binding(frame, identity, "project"), "project")
        put_documents(project, (definition, binding))
    move_incident_relations(frame, identity, state != "inherit", private, project, cleanup)
    relative_project_sources(frame, project)
    return apply_sharing(frame, [("project-local", private), ("project", project), ("project-local", cleanup)],
                         sharing_guard(related_plugins(frame, identity)))


# //// 在项目范围按两端共同归属保存参考关系 [@x380kkm 2026-09-07] ////
def set_project_relation(frame: CardSubjects, source: str, target: str, text: str | None,
                         baseline: dict | None, enabled: bool) -> dict:
    relations = CardRelations(frame)
    edge = relations._edge(source, target)
    current = edge["baseline"] if edge and not edge["inherited"] else None
    if current != baseline:
        raise StorageConflictError(relation_id(source, target))
    source_subject, target_subject = frame.subject(source), frame.subject(target)
    if source == target or type(enabled) is not bool:
        raise CardError("relation_target", "参考关系需要另一张卡片与明确启用状态.")
    if text is None:
        text = f"使用本 skill 时可以参考使用 **{target_subject['name']}** skill"
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 1048576:
        raise CardError("relation_text", "参考关系需要完整适配说明, 正文上限为 1 MiB.")
    edge = {"from": source, "to": target, "text": text, "enabled": enabled}
    destination = relation_scope(frame, source, target)
    private, project, cleanup = {}, {}, {}
    put_documents(private, (frame.definition(source), frame.definition(target)))
    put_documents(private, edge_documents(frame, edge, "project-local"))
    if destination == "project":
        put_documents(project, (frame.definition(source), frame.definition(target)))
        put_documents(project, edge_documents(frame, edge, "project"))
        remove_edge(cleanup, source, target, "project-local")
    else:
        remove_edge(project, source, target, "project")
    plugins = related_plugins(frame, source) | related_plugins(frame, target)
    relative_project_sources(frame, project)
    changed = apply_sharing(frame, [("project-local", private), ("project", project), ("project-local", cleanup)],
                            sharing_guard(plugins))
    return {"changed": changed, "scope": destination, "from": source, "to": target}


# //// 采用用户级关系或移出项目独立关系 [@x380kkm 2026-09-07] ////
def remove_project_relation(frame: CardSubjects, source: str, target: str, baseline: dict | None) -> dict:
    edge = CardRelations(frame)._edge(source, target)
    if edge is None or edge["inherited"]:
        raise CardError("relation_scope", "当前项目没有可移出的参考关系.")
    if edge["baseline"] != baseline:
        raise StorageConflictError(relation_id(source, target))
    project, private = {}, {}
    user_frame = CardSubjects(frame.catalogs, frame.codex, "user")
    user_edge = CardRelations(user_frame)._edge(source, target)
    owner = relation_scope(frame, source, target)
    if user_edge is not None:
        if owner == "project":
            put_documents(project, edge_documents(frame, user_edge, "project"))
            remove_edge(private, source, target, "project-local")
        else:
            put_documents(private, edge_documents(frame, user_edge, "project-local"))
            remove_edge(project, source, target, "project")
    else:
        remove_edge(project, source, target, "project")
        remove_edge(private, source, target, "project-local")
    plugins = related_plugins(frame, source) | related_plugins(frame, target)
    changed = apply_sharing(frame, [("project", project), ("project-local", private)], sharing_guard(plugins))
    return {"changed": changed, "scope": owner if user_edge is not None else frame.scope,
            "from": source, "to": target, "removed": user_edge is None,
            "derivedFromUser": user_edge is not None}
