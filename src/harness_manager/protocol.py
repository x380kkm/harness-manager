# audience: internal
# # harness-protocol
"""声明沿用 Manager Schema, Plugin 的逻辑身份与具体发布的存储身份分别保留."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable
from semantic_version import NpmSpec, Version

from .json_codec import encode_json
from .card_collections import validate_collection


# //// 返回随管理核心分发的声明契约 [@x380kkm 2026-09-06] ////
@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("protocol.schema.json").read_text(encoding="utf-8"))


# //// 校验结构并给出可定位的字段错误 [@x380kkm 2026-09-06] ////
def validate_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ValueError("声明需要 JSON 对象.")
    encode_json(document)
    if not isinstance(document.get("kind"), str):
        raise ValueError("kind 字段需要声明种类的文本.")
    validator = document_validator(document.get("kind", ""))
    errors = sorted(validator.iter_errors(document), key=lambda error: str(list(error.path)))
    if errors:
        details = []
        for error in errors[:8]:
            leaves = error.context or [error]
            relevant = [leaf for leaf in leaves if leaf.validator != "const"] or [error]
            for leaf in relevant[:4]:
                location = "/" + "/".join(str(part) for part in leaf.absolute_path)
                details.append(f"{location}: {leaf.message}")
        raise ValueError("声明不符合 Schema. " + " | ".join(details))
    document_identity(document)
    if document["kind"] == "CardCollection":
        validate_collection(document)


# //// 选择对应声明种类的校验器 [@x380kkm 2026-09-06] ////
@lru_cache(maxsize=None)
def document_validator(kind: str) -> Draft202012Validator:
    contract = schema()
    definitions = contract["$defs"]
    for name, definition in definitions.items():
        if definition.get("properties", {}).get("kind", {}).get("const") == kind:
            return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": definitions})
    raise ValueError(f"未知声明种类: {kind}")


# //// 按声明提供的本地 Schema 核对使用值 [@x380kkm 2026-09-06] ////
def value_diagnostics(value: Any, contract: dict) -> list[str]:
    try:
        Draft202012Validator.check_schema(contract)
        validator = Draft202012Validator(contract, registry=Registry())
        return [f"/{'/'.join(str(part) for part in error.path)}: {error.message}" for error in validator.iter_errors(value)]
    except (SchemaError, Unresolvable) as error:
        raise ValueError("值 Schema 需要可本地解释的结构与引用.") from error


# //// 区分逻辑对象和可并存的发布身份 [@x380kkm 2026-09-06] ////
def document_identity(document: dict[str, Any]) -> str:
    kind = document.get("kind")
    if kind == "Plugin":
        return f"{document['id']}@{document['release']['version']}"
    if kind == "PointContract":
        return f"point:{document['point']}@{document['contract']['version']}"
    identity = document.get("id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("声明需要稳定身份.")
    return identity


# //// 提取声明的显示名称 [@x380kkm 2026-09-06] ////
def document_name(document: dict[str, Any]) -> str:
    metadata = document.get("metadata", {})
    return metadata.get("name") or document.get("point") or document.get("id", "")


# //// 匹配精确来源版本或语义版本约束 [@x380kkm 2026-09-06] ////
def matches_version(version: str, constraint: str | None) -> bool:
    if not constraint or constraint == "*" or constraint == version:
        return True
    if not isinstance(version, str) or not isinstance(constraint, str):
        raise ValueError("版本与约束需要字符串.")
    if version.startswith(("git:", "local:")) or version == "local":
        if constraint.startswith(("git:", "local:")) or constraint == "local":
            return False
        raise ValueError(f"来源版本 {version} 需要精确版本约束.")
    return NpmSpec(constraint).match(Version(version))
