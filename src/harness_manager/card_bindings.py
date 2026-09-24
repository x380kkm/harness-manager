# audience: internal
# # card-bindings
"""卡片快捷操作选择当前宿主与目录中唯一适用的绑定, 保存时沿用原始身份和范围字段."""
from __future__ import annotations

from uuid import uuid4

from .card_subjects import CardError, CardSubjects
from .catalogs import resolve_document
from .projection import resolve_scope
from .protocol import document_identity
from .usage import usage_document


# //// 收集指定发布在一层中的使用绑定 [@x380kkm 2026-09-10] ////
def plugin_bindings(documents: list[dict], plugin: str) -> list[dict]:
    return [document for document in documents
            if document["kind"] == "PluginBinding" and document["plugin"]["id"] == plugin]


# //// 筛出当前宿主与目录适用的原始绑定 [@x380kkm 2026-09-10] ////
def applicable_bindings(frame: CardSubjects, plugin: str, scope: str, documents: list[dict] | None = None) -> list[dict]:
    if scope not in frame.layer_stores:
        return []
    context = {**frame.context, **frame.catalogs.context(scope)}
    project = frame.catalogs.project.workspace if frame.catalogs.project else None
    bindings = []
    for document in plugin_bindings(frame.layer_documents[scope] if documents is None else documents, plugin):
        resolved = resolve_document(document, frame.layer_stores[scope], scope, [], project=project)
        if resolved is not None and resolve_scope(resolved["target"], context, document["id"], []) is not None:
            bindings.append(document)
    return bindings


# //// 选择当前上下文中唯一可快捷编辑的原始绑定 [@x380kkm 2026-09-10] ////
def card_binding(frame: CardSubjects, plugin: str, scope: str, documents: list[dict] | None = None) -> dict | None:
    bindings = applicable_bindings(frame, plugin, scope, documents)
    if len(bindings) > 1:
        raise CardError("card_binding_ambiguous", "当前范围有多个适用的使用绑定, 请在使用设置中选择具体绑定.")
    return bindings[0] if bindings else None


# //// 沿用所选绑定或创建独立的当前宿主使用记录 [@x380kkm 2026-09-10] ////
def configured_binding(frame: CardSubjects, plugin: dict, state: str, scope: str, previous: dict | None) -> dict:
    binding = usage_document(plugin, {"state": state}, previous, scope)
    if previous is not None:
        return binding
    documents = frame.layer_documents[scope]
    host = frame.context["host"]
    if plugin_bindings(documents, plugin["id"]):
        binding["target"]["selector"]["host"] = host
        binding["id"] = f"binding:{scope}/{host}/{plugin['id']}"
    occupied = {document_identity(document) for document in documents}
    while binding["id"] in occupied:
        binding["id"] = f"binding:{scope}/{host}/{plugin['id']}/{uuid4().hex}"
    return binding
