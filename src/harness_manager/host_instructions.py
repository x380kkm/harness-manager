# audience: internal
# # host-instructions
"""规则以明确的原文片段接管宿主说明. 未匹配内容保持原位, 原文位置发生歧义时由调用方重新确认来源."""
from __future__ import annotations

from copy import deepcopy
from bisect import bisect_left
from pathlib import Path
import os
import re

from .storage_errors import StorageConflictError, StorageValidationError

SELECTION_FIELDS = frozenset({"path", "fragments", "sections", "line", "endLine", "originalText"})


# //// 比较正文时统一行尾而保留输出原文 [@x380kkm 2026-09-08] ////
def normalized(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


# //// 比较声明中的来源位置而保持链接归属由宿主核对 [@x380kkm 2026-09-08] ////
def source_path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


# //// 识别声明明确提供的原文映射依据 [@x380kkm 2026-09-08] ////
def has_source_mapping(source: dict) -> bool:
    return any(key in source for key in ("sections", "endLine")) or bool(source.get("fragments"))


# //// 从明确的来源行区间取得原文片段 [@x380kkm 2026-09-08] ////
def source_fragments(source: dict, base: str, text: str) -> list[str]:
    explicit = source.get("fragments")
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit or any(not isinstance(value, str) or not value.strip() for value in explicit):
            raise StorageValidationError("规则 fragments 需要完整且非空的原文片段数组.")
        return explicit
    sections = source.get("sections")
    if sections is None and "endLine" in source:
        sections = [{"line": source.get("line"), "endLine": source["endLine"]}]
    if sections is None:
        original = source.get("originalText", text)
        if not isinstance(original, str):
            raise StorageValidationError("规则 originalText 需要原始正文.")
        original = original.strip()
        return [original] if original and normalized(original) in normalized(base) else []
    if not isinstance(sections, list) or not sections:
        raise StorageValidationError("规则来源需要明确的非空行区间.")
    lines, result = base.splitlines(keepends=True), []
    expected = source.get("originalText", text)
    if not isinstance(expected, str):
        raise StorageValidationError("规则 originalText 需要原始正文.")
    for section in sections:
        if not isinstance(section, dict):
            raise StorageValidationError("规则来源区间需要 line 和 endLine.")
        start, end = section.get("line"), section.get("endLine")
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise StorageValidationError("规则来源行区间已失效, 请重新确认原文片段.")
        fragment = "".join(lines[start - 1:end]).rstrip("\r\n")
        if not fragment.strip() or normalized(fragment) not in normalized(expected):
            raise StorageConflictError("host-rule-source-anchor")
        result.append(fragment)
    return result


# //// 保存来源定位输入并保持期望正文独立 [@x380kkm 2026-09-08] ////
def source_selection(source: dict | None) -> dict:
    selection = {key: deepcopy(source[key]) for key in SELECTION_FIELDS if isinstance(source, dict) and key in source}
    if isinstance(selection.get("path"), str):
        selection["path"] = source_path_key(selection["path"])
    if isinstance(selection.get("originalText"), str):
        selection["originalText"] = normalized(selection["originalText"])
    if isinstance(selection.get("fragments"), list):
        selection["fragments"] = [value.replace("\r\n", "\n") if isinstance(value, str) else value for value in selection["fragments"]]
    return selection


# //// 从首次或显式修正的来源选择取得完整映射 [@x380kkm 2026-09-08] ////
def selected_mapping(entry: dict, base: str, source_path: Path, selection: dict) -> dict:
    source = entry.get("source")
    fragments = []
    if isinstance(source, dict) and isinstance(source.get("path"), str) and source_path_key(source["path"]) == source_path_key(source_path):
        fragments = source_fragments(source, base, entry["text"])
    return {"fragments": fragments, "selection": selection}


# //// 核对持久化来源片段及首次接管正文 [@x380kkm 2026-09-08] ////
def mapping_for(entry: dict, previous: dict | None, base: str, source_path: Path) -> dict:
    source = entry.get("source")
    selection = source_selection(source)
    if (isinstance(source, dict) and isinstance(source.get("path"), str)
            and source_path_key(Path(source["path"]).parent) == source_path_key(source_path.parent)
            and source_path_key(source["path"]) != source_path_key(source_path)):
        original = source.get("originalText", entry["text"])
        matches = isinstance(original, str) and normalized(original) and normalized(original) in normalized(base)
        if has_source_mapping(source) or matches:
            raise StorageConflictError("host-rule-source-file")
    if previous is not None:
        if (not isinstance(previous, dict) or not {"fragments"} <= set(previous) <= {"fragments", "selection", "aggregate"}
                or not isinstance(previous["fragments"], list)
                or any(not isinstance(value, str) or not value.strip() for value in previous["fragments"])
                or "selection" in previous and (not isinstance(previous["selection"], dict) or set(previous["selection"]) - SELECTION_FIELDS)
                or "aggregate" in previous and not isinstance(previous["aggregate"], str)):
            raise StorageValidationError("指令归属需要有效的原文片段.")
        if "selection" in previous:
            if previous["selection"] != selection:
                return selected_mapping(entry, base, source_path, selection)
        elif isinstance(source, dict) and "fragments" in source:
            current = selected_mapping(entry, base, source_path, selection)
            if current["fragments"] != previous["fragments"]:
                return current
        return {**deepcopy(previous), "selection": selection}
    return selected_mapping(entry, base, source_path, selection)


# //// 在当前原文中定位唯一且互不覆盖的片段 [@x380kkm 2026-09-08] ////
def fragment_spans(base: str, fragments: list[str]) -> list[tuple[int, int]]:
    spans, comparable = [], base.replace("\r\n", "\n")
    removed = [match.start() - index for index, match in enumerate(re.finditer("\r\n", base))]
    for fragment in fragments:
        fragment = fragment.replace("\r\n", "\n")
        start = comparable.find(fragment)
        if start < 0 or comparable.find(fragment, start + 1) >= 0:
            raise StorageConflictError("host-rule-source-anchor")
        end = start + len(fragment)
        spans.append((start + bisect_left(removed, start), end + bisect_left(removed, end)))
    ordered = sorted(spans)
    if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
        raise StorageConflictError("host-rule-source-overlap")
    return spans


# //// 同时替换已定位片段并保持声明顺序与原文位置对应 [@x380kkm 2026-09-08] ////
def replace_fragments(text: str, spans: list[tuple[int, int]], replacements: list[str]) -> str:
    for (start, end), replacement in sorted(zip(spans, replacements), reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


# //// 核对聚合正文由完整来源片段与显示标题组成 [@x380kkm 2026-09-08] ////
def aggregate_spans(text: str, fragments: list[str]) -> list[tuple[int, int]]:
    try:
        spans = fragment_spans(text, fragments)
    except StorageConflictError as error:
        raise StorageValidationError("多片段规则需要正文中的唯一且完整片段, 请补齐 source.fragments 或 source.sections.") from error
    remainder = replace_fragments(text, spans, [""] * len(spans))
    if any(line.strip() and not re.fullmatch(r" {0,3}#{1,6}(?:[ \t]+.*)?", line) for line in remainder.splitlines()):
        raise StorageValidationError("多片段规则正文包含尚未映射的内容, 请为完整正文补齐 source.fragments 或 source.sections.")
    return spans


# //// 根据聚合正文基线验证各片段的完整替换内容 [@x380kkm 2026-09-08] ////
def fragment_replacements(entry: dict, mapping: dict) -> list[str]:
    fragments = mapping["fragments"]
    explicit = entry.get("fragmentTexts")
    if "fragmentTexts" in entry and (not isinstance(explicit, list) or len(explicit) != len(fragments)
                                      or any(not isinstance(value, str) for value in explicit)):
        raise StorageValidationError("fragmentTexts 需要按来源声明顺序为每个片段提供一项文本, 空文本表示删除该片段.")
    if len(fragments) < 2:
        if fragments and explicit is not None and normalized(explicit[0]) != normalized(entry["text"]):
            raise StorageValidationError("单片段的 fragmentTexts 需要与完整 text 正文一致.")
        return (explicit if explicit is not None else [entry["text"]]) if fragments else []
    aggregate = mapping.get("aggregate")
    if aggregate is None:
        source = entry.get("source")
        aggregate = source.get("originalText", entry["text"]) if explicit is not None and isinstance(source, dict) else entry["text"]
        if not isinstance(aggregate, str):
            raise StorageValidationError("多片段规则需要 originalText 提供完整的聚合正文基线.")
        mapping["aggregate"] = aggregate
    spans = aggregate_spans(aggregate, fragments)
    if explicit is None:
        if normalized(entry["text"]) != normalized(aggregate):
            raise StorageValidationError("多片段规则正文已编辑, 请在 payload.fragmentTexts 中逐项提供替换文本.")
        return fragments
    expected = replace_fragments(aggregate, spans, explicit)
    if normalized(expected) != normalized(entry["text"]):
        raise StorageValidationError("text 需要与聚合正文基线逐段替换 fragmentTexts 后的结果一致, 请核对正文及标题范围.")
    return explicit


# //// 将规则片段写入各自来源位置并保留其他内容 [@x380kkm 2026-09-08] ////
def compose_instructions(entries: list[dict], base: bytes | None, source_path: Path, previous: dict | None) -> tuple[bytes, dict]:
    try:
        text = (base or b"").decode("utf-8-sig")
    except UnicodeError as error:
        raise StorageValidationError("宿主说明需要完整 UTF-8 文本.") from error
    previous = previous or {"file": source_path.name, "entries": {}}
    if (not isinstance(previous, dict) or set(previous) != {"file", "entries"}
            or previous["file"] != source_path.name or not isinstance(previous["entries"], dict)):
        raise StorageConflictError("host-rule-source-file")
    patches, appended, mappings = [], [], {}
    newline = "\r\n" if "\r\n" in text else "\n"
    for entry in entries:
        mapping = mapping_for(entry, previous["entries"].get(entry["ref"]), text, source_path)
        mappings[entry["ref"]] = mapping
        spans = fragment_spans(text, mapping["fragments"])
        replacements = fragment_replacements(entry, mapping)
        replacement = entry["text"] if entry["enabled"] else ""
        replacement = replacement.replace("\r\n", "\n").replace("\n", newline)
        if spans:
            patches.extend((start, end, value.replace("\r\n", "\n").replace("\n", newline) if entry["enabled"] else "")
                           for (start, end), value in zip(spans, replacements))
        elif replacement:
            appended.append(replacement)
    patches.sort()
    for left, right in zip(patches, patches[1:]):
        if left[1] > right[0]:
            raise StorageConflictError("host-rule-source-overlap")
    for start, end, replacement in reversed(patches):
        text = text[:start] + replacement + text[end:]
    if appended:
        text = text.rstrip("\r\n") + (newline * 2 if text.strip() else "") + (newline * 2).join(appended) + newline
    if not text.strip():
        text = "# Instructions" + newline
    return text.encode("utf-8"), {"file": source_path.name, "entries": mappings}
