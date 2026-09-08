# audience: internal
# # content-units
"""单元读取保留选择引用与原始内容身份. unit_status 描述本次单元传输, 修订观察只比较同一来源入口的当前正文."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .declarations import DeclarationIndex
from .projection import normalize_member
from .sources import SourceError, SourceReader, text_revision


# //// 保存内容解析与修订比较的诊断 [@x380kkm 2026-09-06] ////
class ContentError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code, self.details = code, details


# //// 在原始内容所属发布中解析来源 [@x380kkm 2026-09-06] ////
def resolve_source(plugin: dict, reference: str | dict) -> dict:
    if isinstance(reference, dict):
        return reference
    if reference.startswith("source:"):
        sources = [item["source"] for item in plugin.get("sources", [])
                   if "source:" + item["id"] == reference]
        if len(sources) == 1:
            return sources[0]
    raise ContentError("unresolved_source", "内容来源需要可解析的定位器或包内唯一的 source 绑定.",
                       {"plugin": plugin["id"], "source": reference})


# //// 将内嵌载体转换为完整文本单元 [@x380kkm 2026-09-06] ////
def inline_unit(payload: Any, content_ref: str) -> dict:
    text = payload if isinstance(payload, str) else None
    media_type = "text/plain"
    if text is None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        media_type = "application/json"
    return {"ref": content_ref, "mediaType": media_type, "content": text,
            "revision": "inline:" + text_revision(content_ref, text)}


# //// 读取成员指定的正文或相对资源 [@x380kkm 2026-09-06] ////
def read_member_unit(reader: SourceReader, plugin: dict, contribution: dict, resource: str | None) -> dict:
    payload = contribution["payload"]
    content_ref = f"{plugin['id']}#{contribution['id']}"
    source_ref = contribution.get("source")
    entry = payload.get("entry") if isinstance(payload, dict) else None
    if source_ref is None or entry is None:
        if resource is not None:
            raise SourceError("相对资源读取需要来源文件入口.")
        return inline_unit(payload, content_ref)
    if not isinstance(entry, str) or not entry:
        raise SourceError("entry 字段需要来源内的文件路径文本.")
    if resource is not None:
        entry = (Path(entry).parent / resource).as_posix()
    source = resolve_source(plugin, source_ref)
    return {"ref": content_ref, "entry": entry, **reader.read(source, entry)}


# //// 按选择版本返回完整单元或读取预算缺口 [@x380kkm 2026-09-06] ////
def read_content_unit(index: DeclarationIndex, reader: SourceReader, ref: str, version: str | None,
                      resource: str | None, budget: int, expected_revision: str | None) -> dict:
    chain = index.resolve_reference(ref, version)
    if not chain:
        raise ContentError("unresolved_content", "内容引用没有唯一的已登记结果.", index.diagnostics)
    selected = chain[0][0]
    plugin, contribution = chain[-1]
    valid, contribution = normalize_member(contribution, index.point_contracts, index.diagnostics, ref)
    if not valid:
        raise ContentError("unresolved_content", "内容声明需要满足唯一匹配的点契约.", index.diagnostics)
    unit = read_member_unit(reader, plugin, contribution, resource)
    result = {
        "ref": ref, "version": selected["release"]["version"],
        "content_ref": unit["ref"], "content_version": plugin["release"]["version"],
        "references": contribution.get("relations", []),
        "reference_chain": [{"ref": f"{owner['id']}#{member['id']}", "version": owner["release"]["version"]}
                            for owner, member in chain],
    }
    if expected_revision is not None and expected_revision != unit["revision"]:
        raise ContentError("content_changed", "来源正文的修订观察已变化, 请重新读取当前内容.",
                           {"ref": ref, "version": result["version"], "resource": resource,
                            "expected_revision": expected_revision, "actual_revision": unit["revision"]})
    required = len(unit["content"].encode("utf-8"))
    if required > budget:
        return {**result, "unit_status": "pending", "units": [],
                "missing": [{"ref": unit["ref"], "entry": unit.get("entry"), "revision": unit["revision"],
                             "reason": "budget", "required_bytes": required}]}
    return {**result, "unit_status": "ready", "units": [unit]}
