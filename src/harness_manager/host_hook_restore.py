# audience: internal
# # host-hook-restore
# 项目恢复只替换其 hooks.json 对应的原生状态行, 用户配置的其他字段和注释保持当前内容.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import tomlkit

from .codex_hook_state import remap_hook_states, state_document
from .host_hooks import hooks_document
from .storage_errors import StorageConflictError, StorageValidationError


# //// 区分恢复点与当前配置中的原生状态诊断 [@x380kkm 2026-09-10] ////
def restore_state_document(content: bytes | None, label: str):
    try:
        return state_document(content)
    except StorageValidationError as error:
        raise StorageValidationError(f"{label}无法用于项目 Hook 状态恢复: {error}") from error


# //// 按项目 hooks.json 的绝对路径筛选原生状态行 [@x380kkm 2026-09-10] ////
def project_state_rows(states, hooks_path: Path) -> dict:
    prefixes = (f"{hooks_path}:", f"{hooks_path.as_posix()}:")
    return {key: row for key, row in states.items() if key.startswith(prefixes)}


# //// 创建缺失的 Hook 状态表并保留配置的其他字段 [@x380kkm 2026-09-10] ////
def ensure_hook_state_table(document):
    hooks = document.get("hooks")
    if hooks is None:
        hooks = document["hooks"] = tomlkit.table()
    states = hooks.get("state")
    if states is None:
        states = hooks["state"] = tomlkit.table()
    return states


# //// 更新状态行的字段并保留当前字段注释 [@x380kkm 2026-09-10] ////
def restore_state_row(states, key: str, saved) -> None:
    if key not in states:
        states[key] = deepcopy(saved)
        return
    row = states[key]
    for field in list(row):
        if field not in saved:
            del row[field]
    for field, value in saved.items():
        if field not in row or row[field] != value:
            row[field] = deepcopy(value)


# //// 合并项目自己的原生状态并保持无变化时的当前字节 [@x380kkm 2026-09-10] ////
def merge_project_hook_state(current: bytes | None, snapshot: bytes | None, hooks_path: Path) -> bytes | None:
    _, snapshot_states = restore_state_document(snapshot, "恢复点")
    current_document, current_states = restore_state_document(current, "当前用户配置")
    current_rows = project_state_rows(current_states, hooks_path)
    snapshot_rows = project_state_rows(snapshot_states, hooks_path)
    if current_rows == snapshot_rows:
        return current
    states = ensure_hook_state_table(current_document)
    for key in list(current_rows):
        if key not in snapshot_rows:
            del states[key]
    for key, row in snapshot_rows.items():
        restore_state_row(states, key, row)
    prefix = b"\xef\xbb\xbf" if current and current.startswith(b"\xef\xbb\xbf") else b""
    result = prefix + tomlkit.dumps(current_document).encode("utf-8")
    return None if not result and snapshot is None else result


# //// 按恢复前后的完整定义保持项目当前开关与信任 [@x380kkm 2026-09-10] ////
def restore_project_hook_definitions(config: bytes | None, current: bytes | None,
                                     snapshot: bytes | None, hooks_path: Path) -> bytes | None:
    _, states = restore_state_document(config, "当前用户配置")
    if current == snapshot or not project_state_rows(states, hooks_path):
        return config
    try:
        return remap_hook_states(config, hooks_document(current), hooks_document(snapshot), hooks_path)
    except StorageConflictError as error:
        raise StorageValidationError("项目 Hook 恢复需要逐处理器状态与完整定义唯一对应.") from error
