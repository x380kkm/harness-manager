# audience: internal
# # json-codec
"""管理接口与声明使用可按 UTF-8 编码的 JSON, 数值与文本保持跨入口可解析."""
from __future__ import annotations

import json
from typing import Any


# //// 按 JSON 类型与数值递归比较配置值 [@x380kkm 2026-09-10] ////
def json_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_values_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_values_equal(a, b) for a, b in zip(left, right))
    return left == right


# //// 序列化可传输的 JSON 文本 [@x380kkm 2026-09-06] ////
def encode_json(value: Any, indent: int | None = None) -> str:
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("JSON 文本包含孤立的 Unicode 代理码位.") from error
    return text


# //// 拒绝 JSON 之外的数值常量 [@x380kkm 2026-09-06] ////
def reject_constant(value: str) -> None:
    raise ValueError(f"JSON 数值常量无效: {value}")


# //// 解析有限数值与完整 Unicode 文本 [@x380kkm 2026-09-06] ////
def decode_json(text: str) -> Any:
    value = json.loads(text, parse_constant=reject_constant)
    encode_json(value)
    return value
