# audience: internal
# # host-storage-records
# 宿主存档保留固定逻辑文件名, 完整字节与各目标根目录.
# 项目 Hook 状态使用独立文件名绑定用户配置, 仅含项目文件的存档按原目标恢复.

from __future__ import annotations

import base64

from .storage import _copy_json
from .storage_errors import StorageBoundaryError, StorageValidationError

HOOK_STATE_NAME = "hook-state.toml"
SOURCE_NAMES = ("AGENTS.md", "AGENTS.override.md", "config.toml", "hooks.json")
TARGET_NAMES = frozenset({"AGENTS.override.md", "config.toml", "hooks.json", HOOK_STATE_NAME})
MAX_FILE_BYTES = 4 * 1024 * 1024
RECOVERY_STATES = frozenset({"pending", "recovery-required"})


# //// 为存档编码文件存在性与完整字节 [@x380kkm 2026-09-07] ////
def encode_file(content: bytes | None) -> str | None:
    return None if content is None else base64.b64encode(content).decode("ascii")


# //// 核对存档字节范围并解码文件内容 [@x380kkm 2026-09-07] ////
def decode_file(content: str | None) -> bytes | None:
    if content is None:
        return None
    if not isinstance(content, str) or len(content) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise StorageValidationError("宿主文件存档超出内容范围.")
    try:
        decoded = base64.b64decode(content, validate=True)
    except (ValueError, UnicodeError) as error:
        raise StorageValidationError("宿主文件存档的字节编码无效.") from error
    if len(decoded) > MAX_FILE_BYTES:
        raise StorageValidationError("宿主文件大小上限为 4 MiB.")
    return decoded


# //// 核对受管文件集合及其字节内容 [@x380kkm 2026-09-10] ////
def validate_files(files: dict, names: tuple[str, ...], *, encoded: bool) -> None:
    if not isinstance(files, dict) or not files.keys() <= set(names):
        raise StorageValidationError("宿主内容需要来自作用域内的固定文件集合.")
    for content in files.values():
        value = decode_file(content) if encoded else content
        if value is not None and (not isinstance(value, bytes) or len(value) > MAX_FILE_BYTES):
            raise StorageValidationError("宿主文件需要不超过 4 MiB 的字节内容.")


# //// 核对存档绑定的项目目录与可选用户配置目录 [@x380kkm 2026-09-10] ////
def record_context(value: dict, context: dict) -> dict:
    required = {key: expected for key, expected in context.items() if key != "hookStateRoot"}
    if any(value.get(key) != expected for key, expected in required.items()):
        raise StorageBoundaryError("宿主存档所属目录或作用域不匹配.")
    if "hookStateRoot" in value:
        if value["hookStateRoot"] != context.get("hookStateRoot") or not value["hookStateRoot"]:
            raise StorageBoundaryError("Hook 状态存档所属用户目录不匹配.")
        required["hookStateRoot"] = value["hookStateRoot"]
    return required


# //// 校验操作存档的作用域与固定目标集合 [@x380kkm 2026-09-10] ////
def validate_record(value: dict, context: dict) -> None:
    required_context = record_context(value, context)
    names = SOURCE_NAMES + ((HOOK_STATE_NAME,) if "hookStateRoot" in required_context else ())
    kind = value.get("kind")
    if kind == "state" and value.get("id") == "state" and type(value.get("enabled")) is bool:
        if not value.keys() <= {*required_context, "id", "kind", "enabled", "lastApplied", "ownership"}:
            raise StorageValidationError("宿主控制记录包含未知字段.")
        if "lastApplied" in value and not isinstance(value["lastApplied"], str):
            raise StorageValidationError("宿主应用记录身份无效.")
        if not isinstance(value.get("ownership", {}), dict):
            raise StorageValidationError("宿主字段归属需要 JSON 对象.")
        _copy_json(value.get("ownership", {}))
        return
    if not isinstance(value.get("createdAt"), str) or not value["createdAt"] or len(value["createdAt"]) > 160:
        raise StorageValidationError("宿主记录需要创建时间.")
    if kind == "backup" and value.get("id") in {"initial", "before-restore", "interrupted", "restore-target"}:
        required = {*required_context, "id", "kind", "createdAt", "files"}
        if not required <= value.keys() <= required | {"ownership", "restoreError"}:
            raise StorageValidationError("宿主恢复点字段不完整或包含未知字段.")
        if value.get("ownership", {}) is not None and not isinstance(value.get("ownership", {}), dict):
            raise StorageValidationError("宿主恢复点的字段归属需要 JSON 对象.")
        _copy_json(value.get("ownership", {}))
        if "restoreError" in value and (not isinstance(value["restoreError"], str) or not value["restoreError"]):
            raise StorageValidationError("宿主恢复点需要明确的归属诊断.")
        if value.get("ownership", {}) is None and not value.get("restoreError"):
            raise StorageValidationError("归属未确定的恢复点需要说明原因.")
        if value["id"] == "initial" and (value.get("ownership") or value.get("ownership", {}) is None or value.get("restoreError")):
            raise StorageValidationError("原始恢复点需要保持接管前的字段归属.")
        validate_files(value.get("files"), names, encoded=True)
        if value["files"].keys() != set(names):
            raise StorageValidationError("宿主恢复点需要包含完整的原始配置.")
        return
    if kind != "transaction" or value.get("id") != "transaction":
        raise StorageValidationError("宿主操作记录身份无效.")
    required = {*required_context, "id", "kind", "status", "createdAt", "reason", "before", "after"}
    if not required <= value.keys() <= required | {"beforeOwnership", "afterOwnership", "beforeOwnershipError"}:
        raise StorageValidationError("宿主事务字段不完整或包含未知字段.")
    for key in ("beforeOwnership", "afterOwnership"):
        if not isinstance(value.get(key, {}), dict):
            raise StorageValidationError("宿主事务的字段归属需要 JSON 对象.")
        _copy_json(value.get(key, {}))
    if "beforeOwnershipError" in value and (not isinstance(value["beforeOwnershipError"], str) or not value["beforeOwnershipError"]):
        raise StorageValidationError("宿主事务需要明确的归属诊断.")
    if value.get("status") not in {*RECOVERY_STATES, "applied", "rolled-back"}:
        raise StorageValidationError("宿主事务状态无效.")
    if not isinstance(value.get("reason"), str) or not value["reason"] or len(value["reason"]) > 160:
        raise StorageValidationError("宿主事务需要操作原因.")
    validate_files(value.get("before"), names, encoded=True)
    validate_files(value.get("after"), names, encoded=True)
    if value["before"].keys() != value["after"].keys():
        raise StorageValidationError("宿主备份前后的目标集合需要一致.")
