# audience: internal
# # harness-rpc
"""本地子进程通过逐行 JSON 调用固定管理用例, 输出仅包含对应请求的结果或错误."""
from __future__ import annotations

import subprocess
import sqlite3
from typing import Any, TextIO

from .service import Manager, ServiceError
from .sources import SourceError
from .storage_errors import StorageError
from .json_codec import decode_json, encode_json

MAX_REQUEST_CHARACTERS = 4 * 1024 * 1024


# //// 将管理失败转换为稳定错误信息 [@x380kkm 2026-09-06] ////
def error_record(error: Exception) -> dict[str, Any]:
    record = {"code": getattr(error, "code", type(error).__name__), "message": str(error)}
    details = getattr(error, "details", None)
    if details is not None:
        record["details"] = details
    if getattr(error, "retryable", False):
        record["retryable"] = True
    return record


# //// 校验单个调用并返回结构化结果 [@x380kkm 2026-09-06] ////
def handle_request(manager: Manager, request: Any) -> dict:
    identity = None
    try:
        encode_json(request)
        identity = request.get("id") if isinstance(request, dict) else None
        if identity is not None and type(identity) not in (str, int):
            identity = None
            raise ServiceError("invalid_request_id", "请求 id 需要整数或字符串.")
        if not isinstance(request, dict) or not isinstance(request.get("method"), str):
            raise ServiceError("invalid_request", "调用需要 id, method 和 params 字段.")
        return {"id": identity, "result": manager.invoke(request["method"], request.get("params"))}
    except (StorageError, SourceError, ValueError, TypeError, KeyError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        return {"id": identity, "error": error_record(error)}


# //// 顺序处理受限长度的 JSON 行 [@x380kkm 2026-09-06] ////
def serve(manager: Manager, source: TextIO, target: TextIO) -> None:
    while line := source.readline(MAX_REQUEST_CHARACTERS + 1):
        if len(line) > MAX_REQUEST_CHARACTERS:
            response = {"id": None, "error": {"code": "request_too_large", "message": "请求超过单次读取上限."}}
            target.write(encode_json(response) + "\n")
            target.flush()
            return
        if not line.strip():
            continue
        try:
            response = handle_request(manager, decode_json(line))
        except ValueError as error:
            response = {"id": None, "error": error_record(error)}
        target.write(encode_json(response) + "\n")
        target.flush()
