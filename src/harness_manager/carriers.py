# audience: internal
# # content-carriers
"""载体读取保留完整文本或完整 JSON 文档. 结构模型的领域校验与执行由其所属工具提供."""
from __future__ import annotations

from typing import Any, Protocol

from .content import ContentError
from .declarations import matches_version
from .json_codec import decode_json


# //// 定义完整单元的载体解释接口 [@x380kkm 2026-09-06] ////
class ContentCarrier(Protocol):
    def decode(self, text: str) -> Any: ...


# //// 保留完整的 Markdown 或文本单元 [@x380kkm 2026-09-06] ////
class TextCarrier:
    def decode(self, text: str) -> str:
        return text


# //// 保留 JSON 载体的完整值 [@x380kkm 2026-09-06] ////
class JsonCarrier:
    def decode(self, text: str) -> Any:
        return decode_json(text)


# //// 读取完整的结构模型文档 [@x380kkm 2026-09-06] ////
class ArchifyCarrier:
    def decode(self, text: str) -> dict:
        model = decode_json(text)
        if not isinstance(model, dict) or not isinstance(model.get("diagram_type"), str) or type(model.get("schema_version")) is not int:
            raise ContentError("content_carrier_format", "Archify 载体需要包含 diagram_type 和 schema_version 的完整结构文档.")
        return model


# //// 按声明载体选择内容解释器 [@x380kkm 2026-09-06] ////
def content_carrier(member: dict, media_type: str) -> tuple[ContentCarrier, str]:
    payload = member["payload"]
    declaration = payload.get("carrier") if isinstance(payload, dict) else None
    if declaration is None:
        return (JsonCarrier(), media_type) if media_type == "application/json" else (TextCarrier(), media_type)
    if not isinstance(declaration, dict) or not isinstance(declaration.get("contract"), dict):
        raise ContentError("content_carrier_contract", "内容载体需要明确的契约与媒体类型.")
    contract = declaration["contract"]
    implementations = {"carrier.x380kkm/markdown": TextCarrier, "carrier.x380kkm/json": JsonCarrier,
                       "carrier.x380kkm/archify-ir": ArchifyCarrier}
    implementation = implementations.get(contract.get("id"))
    if implementation is None or matches_version("1.0.0", contract.get("range")) is not True:
        raise ContentError("content_carrier_contract", "当前内容载体需要对应的读取实现.", contract)
    media_type = declaration.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise ContentError("content_carrier_contract", "内容载体需要 mediaType.")
    return implementation(), media_type
