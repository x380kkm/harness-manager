# audience: internal
# # card-collections
"""卡片集合保留有序层级与说明. 集合归属随目录分层, 卡片引用沿用独立来源身份."""
from __future__ import annotations

from copy import deepcopy
from typing import Iterator

CARD_IDENTITY_CONTRACT = "manager.card/identity"


# //// 保存集合组织操作的结构化错误 [@x380kkm 2026-09-07] ////
class CollectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 按声明顺序遍历集合中的分组与卡片 [@x380kkm 2026-09-07] ////
def collection_nodes(document: dict) -> Iterator[dict]:
    pending = list(reversed(document["nodes"]))
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.get("children", [])))


# //// 核对集合内的节点身份与卡片引用唯一性 [@x380kkm 2026-09-07] ////
def validate_collection(document: dict) -> None:
    identities, references = set(), set()
    for node in collection_nodes(document):
        if node["id"] in identities:
            raise ValueError(f"集合节点身份重复: {node['id']}")
        identities.add(node["id"])
        if "itemId" in node:
            if node["itemId"] in references:
                raise ValueError(f"集合中的卡片重复: {node['itemId']}")
            references.add(node["itemId"])


# //// 从共享来源与使用记录取得卡片身份 [@x380kkm 2026-09-07] ////
def shared_card_ids(documents: list[dict]) -> set[str]:
    from .card_subjects import source_record

    bindings = {}
    for document in documents:
        if document["kind"] == "PluginBinding":
            bindings.setdefault(document["plugin"]["id"], []).append(document)
    result = set()
    for document in documents:
        if document["kind"] != "Plugin" or document["id"] not in bindings:
            continue
        record = source_record(document)
        if record is not None and isinstance(record.get("id"), str):
            result.add(record["id"])
        references = {f"{document['id']}#{member['id']}" for member in document["contributions"]}
        extensions = [extension for binding in bindings[document["id"]] for extension in binding.get("extensions", [])]
        for extension in extensions:
            payload = extension.get("payload")
            if (extension["contract"]["id"] == CARD_IDENTITY_CONTRACT and isinstance(payload, dict)
                    and isinstance(payload.get("itemId"), str) and payload["itemId"]
                    and isinstance(payload.get("ref"), str) and payload["ref"] in references):
                result.add(payload["itemId"])
    return result


# //// 核对项目共享集合的卡片归属 [@x380kkm 2026-09-07] ////
def validate_shared_collections(documents: list[dict]) -> None:
    shared = shared_card_ids(documents)
    for document in documents:
        if document["kind"] != "CardCollection":
            continue
        for node in collection_nodes(document):
            if "itemId" in node and node["itemId"] not in shared:
                raise CollectionError("collection_private_reference",
                                      f"共享集合 {document['metadata']['name']} 中的卡片 {node['itemId']} 需要先设为项目共享.")


# //// 取得有效层级并保留当前编辑层的原文基线 [@x380kkm 2026-09-07] ////
def collection_inventory(catalogs, scope: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    view = catalogs.for_scope(scope)
    return collections_from_view(view, scope, items)


# //// 使用已读取的声明层生成集合与缺失引用提示 [@x380kkm 2026-09-07] ////
def collections_from_view(view, scope: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    available = {item["id"] for item in items}
    records, diagnostics = [], list(view.diagnostics)
    for document in view.documents:
        if document["kind"] != "CardCollection":
            continue
        origins = view.origins[document["id"]]
        baseline = next((origin["document"] for origin in origins if origin["scope"] == scope), None)
        records.append({"document": deepcopy(document), "scope": origins[-1]["scope"], "baseline": deepcopy(baseline)})
        for node in collection_nodes(document):
            if "itemId" in node and node["itemId"] not in available:
                diagnostics.append({"code": "collection_missing_card", "subject": document["id"],
                                    "nodeId": node["id"], "itemId": node["itemId"],
                                    "message": f"集合引用的卡片当前无法读取: {node['itemId']}"})
    return records, diagnostics
