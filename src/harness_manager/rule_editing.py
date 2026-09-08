# audience: internal
# # rule-editing
"""规则编辑与宿主输出共用来源映射. 聚合标题和片段间隔以原始字符串传递."""
from __future__ import annotations

from copy import deepcopy

from .card_subjects import source_record
from .host_instructions import aggregate_spans, mapping_for, normalized, replace_fragments
from .storage_errors import StorageConflictError, StorageValidationError


# //// 从明确的贡献引用取得内联规则正文 [@x380kkm 2026-09-08] ////
def rule_member(document: dict | None, reference: str) -> dict:
    if isinstance(document, dict) and document.get("kind") == "Plugin":
        for member in document.get("contributions", []):
            if reference == document["id"] + "#" + member["id"]:
                payload = member.get("payload")
                if (isinstance(payload, dict) and isinstance(payload.get("text"), str)
                        and not (member.get("source") is not None and payload.get("entry"))):
                    return member
    raise StorageValidationError("规则编辑需要当前声明中明确的内联正文引用.")


# //// 将聚合正文拆成固定文字与有序片段引用 [@x380kkm 2026-09-08] ////
def aggregate_parts(aggregate: str, spans: list[tuple[int, int]]) -> list[dict]:
    result, cursor = [], 0
    for index, (start, end) in sorted(enumerate(spans), key=lambda item: item[1][0]):
        if start > cursor:
            result.append({"text": aggregate[cursor:start]})
        result.append({"fragment": index})
        cursor = end
    if cursor < len(aggregate):
        result.append({"text": aggregate[cursor:]})
    return result


# //// 按实际宿主来源与持久化归属定位当前规则片段 [@x380kkm 2026-09-08] ////
def rule_mapping(document: dict, member: dict, host, scope: str) -> dict:
    source = source_record(document)
    if source is None:
        return {"fragments": []}
    storage = host.storage(scope)
    previous = storage.read_ownership().get("AGENTS.override.md")
    name, content, _, _ = host.instruction_source(scope, storage, previous)
    previous_source = previous.get("source") if previous else None
    if previous_source is not None and previous_source["file"] != name:
        raise StorageConflictError("host-rule-source-file")
    reference = document["id"] + "#" + member["id"]
    previous_mapping = previous_source["entries"].get(reference) if previous_source else None
    entry = {"ref": reference, "text": member["payload"]["text"], "source": source}
    return mapping_for(entry, previous_mapping, (content or b"").decode("utf-8-sig"), storage.target_root / name)


# //// 提供规则的独立编辑字段并保留尚未对应的已保存正文 [@x380kkm 2026-09-08] ////
def describe_rule(document: dict, reference: str, host, scope: str) -> dict:
    member = rule_member(document, reference)
    payload = member["payload"]
    mapping = rule_mapping(document, member, host, scope)
    fragments = mapping["fragments"]
    result = {"memberId": member["id"], "name": payload.get("name") or document.get("metadata", {}).get("name", "规则"),
              "texts": [payload["text"]], "parts": [{"fragment": 0}], "mapped": bool(fragments)}
    if len(fragments) < 2:
        return result
    source = source_record(document) or {}
    aggregate = mapping.get("aggregate", source.get("originalText", payload["text"]))
    if not isinstance(aggregate, str):
        raise StorageValidationError("多片段规则需要完整的原文聚合正文.")
    spans = aggregate_spans(aggregate, fragments)
    texts = payload.get("fragmentTexts", fragments)
    if not isinstance(texts, list) or len(texts) != len(fragments) or any(not isinstance(text, str) for text in texts):
        texts = fragments
    result.update(texts=deepcopy(texts), parts=aggregate_parts(aggregate, spans))
    if normalized(replace_fragments(aggregate, spans, texts)) != normalized(payload["text"]):
        result["unmappedText"] = payload["text"]
    return result
