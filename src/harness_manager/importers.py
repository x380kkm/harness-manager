# audience: internal
# # declaration-import
"""导入把已有文件描述为 Plugin 草稿. 来源原文保持原位, 内容主题复用相同的声明结构."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import yaml

from .protocol import validate_document
from .json_codec import decode_json
from .sources import SourceReader

POINTS = {
    "skill": "skill.x380kkm/deployment",
    "instruction": "context.x380kkm/instruction",
    "preference": "preference.x380kkm/method",
    "tool": "tool.x380kkm/endpoint",
    "model": "structure.x380kkm/model",
}


# //// 提取 Skill 的名称与摘要 [@x380kkm 2026-09-06] ////
def skill_metadata(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for end, line in enumerate(lines[1:], 1):
        if line.strip() in ("---", "..."):
            try:
                parsed = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError as error:
                raise ValueError(f"Skill 元数据解析失败: {error}") from error
            if parsed is None:
                return {}
            if not isinstance(parsed, dict):
                raise ValueError("Skill 的头部元数据需要键值对象.")
            return parsed
    raise ValueError("Skill 元数据缺少结束分隔线.")


# //// 从已有名称取得可读的局部身份 [@x380kkm 2026-09-06] ////
def slug(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    readable = re.sub(r"[^a-z0-9._-]+", "-", ascii_name.lower()).strip("-._")
    return readable or "u-" + value.encode("utf-8").hex()


# //// 按文件载体生成合法声明草稿 [@x380kkm 2026-09-06] ////
def import_document(reader: SourceReader, path: str, kind: str | None = None) -> dict[str, Any]:
    location, text = reader.read_file(path)
    if location.suffix.lower() == ".json":
        parsed = decode_json(text)
        if isinstance(parsed, dict) and "apiVersion" in parsed:
            validate_document(parsed)
            return parsed
        if not kind and isinstance(parsed, dict) and "diagram_type" in parsed:
            kind = "model"
        if not kind:
            raise ValueError("JSON 文件需要已有 Manager 声明, 或明确的内容类型.")
    selected_kind = kind or ("skill" if location.name.casefold() == "skill.md" else "instruction")
    if selected_kind not in POINTS:
        raise ValueError("未知导入类型.")
    metadata = skill_metadata(text) if selected_kind == "skill" else {}
    fallback = location.parent.name if selected_kind == "skill" else location.stem
    name = metadata.get("name", fallback)
    description = metadata.get("description", "")
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        raise ValueError("内容名称与摘要需要文本.")
    identifier = slug(name)
    point = POINTS[selected_kind]
    document = {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
        "id": f"plugin:local/{identifier}", "release": {"version": "local"},
        "metadata": {"name": name, "description": description.strip()[:500]},
        "sources": [{"id": "local", "source": {
            "resolver": {"id": "manager.source/path", "range": "^1.0.0"},
            "locator": str(location.parent),
        }}],
        "contributions": [{"id": identifier, "point": point,
            "contract": {"id": point, "range": "^1.0.0"}, "source": "source:local",
            "payload": {"name": name, "description": description.strip()[:500], "entry": location.name}}],
    }
    validate_document(document)
    return document
