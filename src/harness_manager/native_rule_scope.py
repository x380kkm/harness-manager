# audience: internal
# # native-rule-scope
"""项目规则的撤销能力取决于 Codex 当前全局原文. 规则身份通过明确来源片段核对."""
from __future__ import annotations

from .host_instructions import fragment_spans, normalized, source_fragments, source_path_key
from .storage_errors import StorageError


# //// 取得当前全局候选原文并按宿主顺序选择有效内容 [@x380kkm 2026-09-08] ////
def global_instructions(codex, global_texts: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    paths = [source_path_key(codex.root / name) for name in ("AGENTS.override.md", "AGENTS.md")]
    if global_texts is not None:
        if not isinstance(global_texts, dict) or any(not isinstance(path, str) or not isinstance(text, str) for path, text in global_texts.items()):
            raise ValueError("全局说明输入需要文件路径与完整文本的映射.")
        texts = {}
        for path, text in global_texts.items():
            key = source_path_key(path)
            if key in texts and texts[key] != text:
                raise ValueError("同一全局说明来源包含不同的输入文本.")
            texts[key] = text
        texts = {path: texts[path] for path in paths if path in texts}
        return texts, next((texts[path] for path in paths if texts.get(path, "").strip()), "")
    snapshot = codex.snapshot([])
    items = {source_path_key(item["path"]): item for item in snapshot["items"]
             if item["kind"] == "instruction" and source_path_key(item["path"]) in paths}
    texts, selected = {}, None
    for path in paths:
        item = items.get(path)
        if item is None:
            continue
        text = item.get("content")
        if not isinstance(text, str):
            raise ValueError("当前全局说明包含无法核对的来源文件.")
        texts[path] = text
        if selected is None and text.strip():
            selected = text
    if any(note.get("code") == "redirected-scan-root" and source_path_key(note.get("path", "")) == source_path_key(codex.root)
           for note in snapshot["diagnostics"]):
        raise ValueError("当前全局说明根目录需要重新确认.")
    return texts, selected or ""


# //// 核对项目关闭的规则是否仍存在于全局实际原文 [@x380kkm 2026-09-08] ////
def inactive_global_rules(codex, instructions: list[dict], *, global_texts: dict[str, str] | None = None) -> tuple[set[str], list[dict]]:
    paths = {source_path_key(codex.root / name) for name in ("AGENTS.override.md", "AGENTS.md")}
    candidates = [entry for entry in instructions if not entry["enabled"] and isinstance(entry.get("source"), dict)
                  and isinstance(entry["source"].get("path"), str) and source_path_key(entry["source"]["path"]) in paths]
    if not candidates:
        return set(), []
    inactive, diagnostics = set(), []
    try:
        texts, active = global_instructions(codex, global_texts)
    except (OSError, ValueError) as error:
        return set(), [{"severity": "error", "code": "host_global_instruction_source", "subject": "AGENTS.override.md", "message": str(error)}]
    for entry in candidates:
        try:
            source = entry["source"]
            if not active.strip():
                inactive.add(entry["ref"])
                continue
            fragments = source_fragments(source, texts.get(source_path_key(source["path"]), ""), entry["text"])
            if not fragments:
                original = source.get("originalText", entry["text"])
                fragments = [original] if isinstance(original, str) and normalized(original) and normalized(original) in normalized(active) else []
            remaining = False
            comparable = active.replace("\r\n", "\n")
            for fragment in fragments:
                if fragment.replace("\r\n", "\n") not in comparable:
                    continue
                fragment_spans(active, [fragment])
                remaining = True
            if remaining:
                diagnostics.append({"severity": "error", "code": "host_inherited_rule_scope", "subject": entry["ref"],
                                    "message": "此规则仍在 Codex 当前选择的全局原文中. 项目文件无法撤去全局内容, 请调整用户级配置或确认项目独立来源."})
            else:
                inactive.add(entry["ref"])
        except (StorageError, ValueError, TypeError):
            diagnostics.append({"severity": "error", "code": "host_global_instruction_source", "subject": entry["ref"],
                                "message": "规则的全局原文片段存在变化或歧义, 请重新确认来源."})
    return inactive, diagnostics
