# audience: internal
# # host-hook-sync
"""Hook 定义与启用状态分别同步. 原生逐项选择与上次投影共同决定普通同步的结果.
命令组保留位置, 结构变化按完整处理器定义搬移已有信任. 显式启停沿用读取时的原生基线.
"""
from copy import deepcopy
import json
from pathlib import Path

from .codex_hook_state import group_states, hook_state_key, remap_hook_states
from .host_hooks import HOOK_POINT, desired_groups, group_identity, hooks_document, matching_positions, reconcile_hooks
from .storage_errors import StorageConflictError, StorageValidationError


# //// 按贡献身份选择唯一的上次受管组 [@x380kkm 2026-09-10] ////
def previous_group(value: dict, groups: list[dict]) -> dict | None:
    exact = [group for group in groups if group_identity(group) == group_identity(value)]
    matches = exact or [group for group in groups if set(group["refs"]) & set(value["refs"])]
    if len(matches) > 1:
        raise StorageConflictError("host-hook-state-identity")
    return matches[0] if matches else None


# //// 从原生逐项开关取得布尔数组 [@x380kkm 2026-09-10] ////
def enabled_values(states: list[dict] | None) -> list[bool] | None:
    return [state["enabled"] for state in states] if states is not None else None


# //// 为编辑后的处理器保留其原生启停选择 [@x380kkm 2026-09-10] ////
def edited_values(value: dict, previous: dict, flags: list[bool]) -> list[bool]:
    if len(set(flags)) == 1:
        return flags[:1] * len(value["group"]["hooks"])
    old = previous["group"]["hooks"]
    new = value["group"]["hooks"]
    if old == new:
        return list(flags)
    result = []
    used, unresolved = set(), []
    for handler in new:
        matches = [index for index, item in enumerate(old) if item == handler]
        if len(matches) == 1 and matches[0] not in used:
            used.add(matches[0])
            result.append(flags[matches[0]])
        else:
            unresolved.append(len(result))
            result.append(value["enabled"])
    remaining = [index for index in range(len(old)) if index not in used]
    if len(remaining) == len(unresolved) == 1:
        result[unresolved[0]] = flags[remaining[0]]
    elif remaining and unresolved:
        raise StorageConflictError("host-hook-mixed-edit")
    return result


# //// 合并显式请求与两端启用状态 [@x380kkm 2026-09-10] ////
def choose_enabled(value: dict, previous: dict | None, states: list[dict] | None,
                   requests: dict, path: Path) -> list[bool]:
    count = len(value["group"]["hooks"])
    selected = [requests[ref] for ref in value["refs"] if ref in requests]
    if selected:
        if any(request.get("native") != {"path": str(path), "states": states} for request in selected):
            raise StorageConflictError("host-hook-native-state")
        choices = {request["enabled"] for request in selected}
        if len(choices) != 1:
            raise StorageConflictError("host-hook-enable-request")
        return [choices.pop()] * count
    native = enabled_values(states)
    intended = [value["enabled"]] * count
    if previous is None:
        return native if native is not None and value["enabled"] else intended
    configured = previous["enabled"]
    if value["enabled"] != configured:
        observed = previous.get("nativeEnabled")
        if observed is not None and native is not None and native != observed and native != intended:
            raise StorageConflictError("host-hook-enable-conflict")
        return intended
    if native is not None:
        return edited_values(value, previous, native)
    return [previous.get("enabled", value["enabled"])] * count


# //// 验证用于双向跟随的原生状态记录 [@x380kkm 2026-09-10] ////
def state_record(ownership: dict) -> dict:
    record = ownership.get("hookState", {"groups": [], "originals": {}})
    if (not isinstance(record, dict) or not {"groups", "originals"} <= set(record) <= {"groups", "originals", "path"}
            or not isinstance(record["groups"], list) or not isinstance(record["originals"], dict)):
        raise StorageValidationError("Hook 同步记录需要受管组和原生状态.")
    if "path" in record and (not isinstance(record["path"], str) or not Path(record["path"]).is_absolute()):
        raise StorageValidationError("Hook 同步位置需要绝对来源路径.")
    for group in record["groups"]:
        if (not isinstance(group, dict) or type(group.get("enabled")) is not bool
                or not isinstance(group.get("nativeEnabled"), list)
                or any(type(flag) is not bool for flag in group["nativeEnabled"])):
            raise StorageValidationError("Hook 同步记录需要逐处理器启用状态.")
    return deepcopy(record)


# //// 以原始定义身份保存用户后来的原生选择 [@x380kkm 2026-09-10] ////
def observe_original_choices(record: dict, prior: list[dict], config: bytes | None, document: dict, path: Path) -> None:
    for identity, original_states in record["originals"].items():
        previous = next((group for group in prior if group_identity(group) == identity), None)
        observed = previous.get("nativeEnabled") if previous else None
        if original_states is None or observed is None:
            continue
        states = group_states(config, document, path, json.loads(identity))
        for index, state in enumerate(states or []):
            if index >= len(observed):
                continue
            original = original_states[index]
            updated = deepcopy(state)
            if state["enabled"] == observed[index]:
                updated["enabled"] = original["enabled"]
                if updated["saved"] is not None:
                    if "enabled" in (original["saved"] or {}):
                        updated["saved"]["enabled"] = original["saved"]["enabled"]
                    else:
                        updated["saved"].pop("enabled", None)
                    updated["saved"] = updated["saved"] or None
            original_states[index] = updated


# //// 保留定义并在用户配置中同步原生开关 [@x380kkm 2026-09-10] ////
def reconcile_hook_state(targets: dict, contributions: list, content: bytes | None,
                         config: bytes | None, ownership: dict, path: Path,
                         hook_file: str = "hooks.json") -> bytes | None:
    before = hooks_document(content)
    record = state_record(ownership)
    native_path = Path(record.get("path", path))
    prior = record["groups"] or ownership.get("hooks", {}).get("groups", [])
    observe_original_choices(record, prior, config, before, native_path)
    desired = desired_groups(contributions)
    requests = ownership.get("hookRequests", {})
    owned = ownership.get("hooks", {})
    managed = {group_identity(group) for group in owned.get("groups", [])}
    native = {group_identity(group) for group in owned.get("originals", [])}
    native.update(group_identity(group) for group in owned.get("groups", []) if group.get("beforeIndex") is not None)
    choices = {}
    for identity, value in desired.items():
        previous = previous_group(value, prior)
        states = group_states(config, before, native_path, previous or value)
        choices[identity] = choose_enabled(value, previous, states, requests, native_path)
        if (identity not in managed or identity in native) and matching_positions(before.get("hooks", {}), value):
            record["originals"].setdefault(identity, group_states(config, before, native_path, value))
    present = [{**entry, "enabled": True} if entry.get("point") == HOOK_POINT else entry for entry in contributions]
    reconcile_hooks(targets, present, content, ownership, hook_file)
    after = hooks_document(targets[hook_file])
    overrides = {}
    for identity, value in desired.items():
        index = matching_positions(after.get("hooks", {}), value)[0]
        for handler_index, enabled in enumerate(choices[identity]):
            overrides[hook_state_key(path, value["event"], index, handler_index)] = enabled
    restored = {}
    retained = {group_identity(group) for group in ownership.get("hooks", {}).get("originals", [])}
    for identity, states in record["originals"].items():
        if states is None:
            continue
        original = json.loads(identity)
        indices = matching_positions(after.get("hooks", {}), original)
        if not indices:
            continue
        current = group_states(config, before, native_path, original)
        previous = next((group for group in prior if group_identity(group) == identity), None)
        if current is not None and identity not in retained:
            observed = previous.get("nativeEnabled") if previous else None
            for handler_index, state in enumerate(states):
                native = current[handler_index]["enabled"]
                if observed is None or handler_index < len(observed) and native == observed[handler_index]:
                    restored[hook_state_key(path, original["event"], indices[0], handler_index)] = state["saved"]
        elif current is None:
            for handler_index, state in enumerate(states):
                restored[hook_state_key(path, original["event"], indices[0], handler_index)] = state["saved"]
    result = remap_hook_states(config, before, after, path, overrides, restored=restored, source_path=native_path)
    if desired:
        ownership["hookState"] = {
            "path": str(path),
            "groups": [{**value, "nativeEnabled": choices[identity]}
                       for identity, value in desired.items()],
            "originals": {identity: states for identity, states in record["originals"].items() if identity in retained}}
    else:
        ownership.pop("hookState", None)
    ownership.pop("hookRequests", None)
    return result
