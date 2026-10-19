# audience: internal
# # codex-hook-state
# 原生 Hook 身份包含文件路径和事件内的组与处理器序号.
# 完整处理器与组属性相同时, 状态随其位置迁移, 重复项按出现顺序对应.
# 输入与输出均为配置快照, 调用者负责原生文件的读取和原子提交.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
import json
from pathlib import Path

import tomlkit

from .storage_errors import StorageConflictError, StorageValidationError

EVENT_KEYS = {
    "SessionStart": "session_start", "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt_submit", "Stop": "stop",
    "SubagentStart": "subagent_start", "SubagentStop": "subagent_stop",
    "PreToolUse": "pre_tool_use", "PostToolUse": "post_tool_use",
    "PermissionRequest": "permission_request", "PreCompact": "pre_compact",
    "PostCompact": "post_compact", "Interrupt": "interrupt",
}


# //// 生成原生文件处理器的配置键 [@x380kkm 2026-09-10] ////
def hook_state_key(path: Path, event: str, group_index: int, handler_index: int) -> str:
    if event not in EVENT_KEYS or any(type(index) is not int or index < 0 for index in (group_index, handler_index)):
        raise StorageValidationError("原生 Hook 身份需要支持的事件和非负序号.")
    return f"{path}:{EVENT_KEYS[event]}:{group_index}:{handler_index}"


# //// 解析原生开关配置并保留 TOML 排版 [@x380kkm 2026-09-10] ////
def state_document(config: bytes | None):
    try:
        document = tomlkit.parse((config or b"").decode("utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise StorageValidationError("原生 Hook 状态需要可解析的 UTF-8 TOML 配置.") from error
    hooks = document.get("hooks", {})
    if not isinstance(hooks, Mapping):
        raise StorageValidationError("hooks 配置需要 TOML 表.")
    states = hooks.get("state", {})
    if not isinstance(states, Mapping):
        raise StorageValidationError("hooks.state 配置需要 TOML 表.")
    for row in states.values():
        validate_state_row(row)
    return document, states


# //// 验证原生状态行中开关与信任字段的类型 [@x380kkm 2026-09-10] ////
def validate_state_row(row) -> None:
    if not isinstance(row, Mapping):
        raise StorageValidationError("原生 Hook 状态需要配置对象.")
    if "enabled" in row and type(row["enabled"]) is not bool:
        raise StorageValidationError("原生 Hook enabled 需要布尔值.")
    if "trusted_hash" in row and not isinstance(row["trusted_hash"], str):
        raise StorageValidationError("原生 Hook trusted_hash 需要文本.")


# //// 读取各原生处理器的完整状态行 [@x380kkm 2026-09-10] ////
def parse_hook_states(config: bytes | None) -> dict:
    _, states = state_document(config)
    return {key: deepcopy(row.unwrap()) for key, row in states.items()}


# //// 编码完整定义以区分处理器内容和字段类型 [@x380kkm 2026-09-10] ////
def definition_key(value: dict) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise StorageValidationError("原生 Hook 定义需要完整 JSON 对象.") from error


# //// 取得原生事件组及其完整处理器 [@x380kkm 2026-09-10] ////
def event_groups(document: dict) -> dict:
    hooks = document.get("hooks", {}) if isinstance(document, dict) else None
    if not isinstance(hooks, dict):
        raise StorageValidationError("原生 Hook 文件需要事件对象.")
    for event, groups in hooks.items():
        if event not in EVENT_KEYS:
            continue
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            raise StorageValidationError("原生 Hook 事件需要对象组数组.")
        for group in groups:
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list) or any(not isinstance(handler, dict) for handler in handlers):
                raise StorageValidationError("原生 Hook 事件组需要处理器对象数组.")
    return hooks


# //// 按完整组读取每个处理器的开关与保存基线 [@x380kkm 2026-09-10] ////
def group_states(config: bytes | None, document: dict, path: Path, hook: dict) -> list[dict] | None:
    states = parse_hook_states(config)
    event, group = hook["event"], hook["group"]
    expected = definition_key(group)
    positions = [index for index, value in enumerate(event_groups(document).get(event, []))
                 if definition_key(value) == expected]
    if not positions:
        return None
    if len(positions) != 1:
        raise StorageConflictError(f"hooks:{path}:{event}")
    result = []
    for index, _ in enumerate(group.get("hooks", [])):
        key = hook_state_key(path, event, positions[0], index)
        saved = deepcopy(states.get(key))
        result.append({"key": key, "enabled": (saved or {}).get("enabled", True), "saved": saved})
    return result


# //// 按完整定义汇集包含外部组的原生处理器位置 [@x380kkm 2026-09-10] ////
def handler_positions(document: dict, path: Path) -> dict[str, list[str]]:
    positions = defaultdict(list)
    for event, groups in event_groups(document).items():
        if event not in EVENT_KEYS:
            continue
        for group_index, group in enumerate(groups):
            attributes = {key: value for key, value in group.items() if key != "hooks"}
            for handler_index, handler in enumerate(group.get("hooks", [])):
                identity = definition_key({"event": event, "group": attributes, "handler": handler})
                positions[identity].append(hook_state_key(path, event, group_index, handler_index))
    return dict(positions)


# //// 对应完整定义并拒绝有状态重复项的数量歧义 [@x380kkm 2026-09-10] ////
def state_sources(before: dict, after: dict, states: Mapping, path: Path) -> dict[str, str]:
    sources = {}
    for identity, old_keys in before.items():
        new_keys = after.get(identity, [])
        if (new_keys and len(old_keys) != len(new_keys) and max(len(old_keys), len(new_keys)) > 1
                and any(key in states for key in old_keys)):
            raise StorageConflictError(f"hooks.state:{path}")
        sources.update({new: old for old, new in zip(old_keys, new_keys) if old in states})
    return sources


# //// 移动原状态行并应用恢复行与明确的启用值 [@x380kkm 2026-09-10] ////
def remapped_states(states: Mapping, before: dict, after: dict, path: Path, overrides: dict, restored: dict) -> dict:
    sources = state_sources(before, after, states, path)
    old_keys = {key for values in before.values() for key in values}
    new_keys = {key for values in after.values() for key in values}
    result = {key: deepcopy(row) for key, row in states.items() if key not in old_keys | new_keys}
    result.update({key: deepcopy(states[source]) for key, source in sources.items()})
    for key, row in restored.items():
        if key not in new_keys:
            raise StorageValidationError("原生 Hook 恢复状态需要当前文件处理器的身份.")
        if row is None:
            result.pop(key, None)
        else:
            validate_state_row(row)
            result[key] = deepcopy(row)
    for key, enabled in overrides.items():
        if key not in new_keys or type(enabled) is not bool:
            raise StorageValidationError("原生 Hook 开关需要当前文件处理器的身份和布尔值.")
        if result.get(key, {}).get("enabled", True) != enabled:
            row = result.setdefault(key, tomlkit.table())
            row["enabled"] = enabled
    return result


# //// 随处理器位置迁移原生状态并保留配置其余内容 [@x380kkm 2026-09-10] ////
def remap_hook_states(config: bytes | None, before: dict, after: dict, path: Path,
                      overrides: dict[str, bool] | None = None, *,
                      restored: dict[str, dict | None] | None = None,
                      source_path: Path | None = None) -> bytes | None:
    document, states = state_document(config)
    previous = handler_positions(before, path if source_path is None else source_path)
    current = handler_positions(after, path)
    result = remapped_states(states, previous, current, path, overrides or {}, restored or {})
    if source_path is not None and str(source_path) != str(path):
        for key in (key for keys in current.values() for key in keys):
            if key in states and states[key] != result.get(key):
                raise StorageConflictError(f"hooks.state:{key}")
    return write_state_rows(config, document, states, result)


# //// 按来源文件路径迁移恢复快照中的原生状态行 [@x380kkm 2026-09-10] ////
def relocate_hook_state(config: bytes | None, source_path: Path, path: Path) -> bytes | None:
    document, states = state_document(config)
    if str(source_path) == str(path):
        return config
    prefix, destination = f"{source_path}:", f"{path}:"
    sources = {destination + key[len(prefix):]: key for key in states if key.startswith(prefix)}
    for key, source in sources.items():
        if key in states and states[key] != states[source]:
            raise StorageConflictError(f"hooks.state:{key}")
    result = {key: deepcopy(row) for key, row in states.items() if key not in sources.values()}
    result.update({key: deepcopy(states[source]) for key, source in sources.items()})
    return write_state_rows(config, document, states, result)


# //// 写出变动状态行并保留其余 TOML 字节格式 [@x380kkm 2026-09-10] ////
def write_state_rows(config: bytes | None, document, states: Mapping, result: dict) -> bytes | None:
    if dict(states) == result:
        return config
    hooks = document.get("hooks")
    if hooks is None:
        hooks = document["hooks"] = tomlkit.table()
    rows = hooks.get("state")
    if rows is None:
        rows = hooks["state"] = tomlkit.table()
    for key in list(rows):
        if key not in result:
            del rows[key]
    for key, row in result.items():
        if key not in rows or rows[key] != row:
            rows[key] = row
    prefix = b"\xef\xbb\xbf" if config and config.startswith(b"\xef\xbb\xbf") else b""
    return prefix + tomlkit.dumps(document).encode("utf-8")
