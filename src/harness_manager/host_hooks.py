# audience: internal
# # host-hooks
# 完整事件组以事件名和处理器内容标识, 归属保存接管前的位置和最近启用状态.
# 多个来源声明同一事件组时, 任一来源启用即可保留一份事件组.
# 原生事件组的归属沿贡献引用或完整组身份交接, 最后一个引用解除时恢复原组.

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json

from .json_codec import encode_json, reject_constant
from .storage_errors import StorageConflictError, StorageValidationError

HOOK_POINT = "hook.x380kkm/lifecycle"


# //// 保留 Hook JSON 中唯一且明确的对象字段 [@x380kkm 2026-09-08] ////
def hook_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StorageValidationError("Hook 配置包含重复对象字段.")
        result[key] = value
    return result


# //// 解析完整 Hook 文件并保留其他顶层字段 [@x380kkm 2026-09-08] ////
def hooks_document(content: bytes | None) -> dict:
    try:
        document = json.loads((content or b"{}").decode("utf-8-sig"), object_pairs_hook=hook_object, parse_constant=reject_constant)
    except (UnicodeError, ValueError) as error:
        raise StorageValidationError("Hook 归属调和需要可解析的 JSON 对象.") from error
    hooks = document.get("hooks", {}) if isinstance(document, dict) else None
    if not isinstance(hooks, dict) or any(not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups) for groups in hooks.values()):
        raise StorageValidationError("Hook 配置需要按事件排列的完整对象组.")
    return document


# //// 按字段与值类型编码完整 Hook 对象 [@x380kkm 2026-09-08] ////
def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


# //// 取得完整事件组的稳定内容身份 [@x380kkm 2026-09-08] ////
def group_identity(value: dict) -> str:
    return canonical({"event": value["event"], "group": value["group"]})


# //// 以内容指纹记录事件组顺序 [@x380kkm 2026-09-08] ////
def order_key(group: dict) -> str:
    return sha256(canonical(group).encode("utf-8")).hexdigest()


# //// 合并同一事件组的独立来源与启用状态 [@x380kkm 2026-09-08] ////
def desired_groups(contributions: list) -> dict:
    result = {}
    for entry in contributions:
        if entry.get("point") != HOOK_POINT:
            continue
        hook = entry.get("hook")
        if (not isinstance(hook, dict) or set(hook) != {"event", "group"} or not isinstance(hook["event"], str)
                or not isinstance(hook["group"], dict) or not isinstance(entry.get("ref"), str)
                or not entry["ref"] or type(entry.get("enabled")) is not bool):
            raise StorageValidationError("受管 Hook 需要完整事件组, 来源引用和启用状态.")
        identity = group_identity(hook)
        if identity not in result:
            result[identity] = {**deepcopy(hook), "refs": [], "enabled": False}
        group = result[identity]
        if entry["ref"] not in group["refs"]:
            group["refs"].append(entry["ref"])
        group["enabled"] = group["enabled"] or entry["enabled"]
    return result


# //// 定位当前文件中完整匹配的事件组 [@x380kkm 2026-09-08] ////
def matching_positions(hooks: dict, value: dict) -> list[int]:
    expected = canonical(value["group"])
    return [index for index, group in enumerate(hooks.get(value["event"], [])) if canonical(group) == expected]


# //// 核对最近写入的事件组与启用状态 [@x380kkm 2026-09-08] ////
def owned_groups(values: list, hooks: dict) -> tuple[dict, dict]:
    result, positions = {}, {}
    for value in values:
        required = {"event", "group", "refs"}
        if (not isinstance(value, dict) or set(value) not in (required, required | {"beforeIndex", "enabled"})
                or not isinstance(value.get("event"), str) or not isinstance(value.get("group"), dict)
                or not isinstance(value.get("refs"), list) or not value["refs"]
                or any(not isinstance(ref, str) or not ref for ref in value["refs"])):
            raise StorageValidationError("Hook 归属需要完整事件组与来源身份.")
        record = {"beforeIndex": None, "enabled": True, **deepcopy(value)}
        if (type(record["enabled"]) is not bool or record["beforeIndex"] is not None
                and (type(record["beforeIndex"]) is not int or record["beforeIndex"] < 0)):
            raise StorageValidationError("Hook 归属需要有效的原始位置和最近启用状态.")
        identity = group_identity(record)
        if identity in result:
            raise StorageValidationError("Hook 归属中的事件组需要唯一.")
        matches = matching_positions(hooks, record)
        if len(matches) != int(record["enabled"]):
            raise StorageConflictError("host-hook-group")
        result[identity] = record
        positions[identity] = matches[0] if matches else None
    return result, positions


# //// 保存原生事件组及持续管理它的贡献引用 [@x380kkm 2026-09-08] ////
def original_groups(record: dict, owned: dict, hooks: dict, positions: dict) -> dict:
    values = record.get("originals", [{key: value[key] for key in ("event", "group", "refs", "beforeIndex")}
                                     for value in owned.values() if value["beforeIndex"] is not None])
    if not isinstance(values, list):
        raise StorageValidationError("Hook 原始归属需要事件组数组.")
    result = {}
    for value in values:
        if (not isinstance(value, dict) or set(value) != {"event", "group", "refs", "beforeIndex"}
                or not isinstance(value["event"], str) or not isinstance(value["group"], dict)
                or not isinstance(value["refs"], list) or not value["refs"]
                or any(not isinstance(ref, str) or not ref for ref in value["refs"])
                or type(value["beforeIndex"]) is not int or value["beforeIndex"] < 0):
            raise StorageValidationError("Hook 原始归属需要完整事件组, 来源引用和原始位置.")
        identity = group_identity(value)
        if identity in result:
            raise StorageValidationError("Hook 原始归属中的事件组需要唯一.")
        matches = matching_positions(hooks, value)
        if len(matches) != int(owned.get(identity, {}).get("enabled", False)):
            raise StorageConflictError("host-hook-group")
        positions[identity] = matches[0] if matches else None
        result[identity] = deepcopy(value)
    return result


# //// 按贡献引用和完整组身份延续原始归属 [@x380kkm 2026-09-08] ////
def retained_originals(originals: dict, owned: dict, desired: dict) -> tuple[dict, dict]:
    retained, released = {}, {}
    for identity, value in originals.items():
        previous_refs = set(value["refs"])
        identities = {identity} | {key for key, group in owned.items() if previous_refs & set(group["refs"])}
        refs = {}
        for key, group in desired.items():
            if key in identities or previous_refs & set(group["refs"]):
                refs.update(dict.fromkeys(group["refs"]))
        current = {**value, "refs": list(refs)}
        if current["refs"]:
            retained[identity] = current
        else:
            released[identity] = {**value, "enabled": True}
    return retained, released


# //// 为显式声明采用现有事件组并保存其原始位置 [@x380kkm 2026-09-08] ////
def adopt_groups(desired: dict, previous: dict, originals: dict, positions: dict, hooks: dict, orders: dict) -> dict:
    result = {}
    for identity, value in desired.items():
        if identity in previous:
            before = previous[identity]["beforeIndex"]
        else:
            matches = matching_positions(hooks, value)
            if len(matches) > 1:
                raise StorageConflictError("host-hook-group")
            before = originals[identity]["beforeIndex"] if identity in originals else (
                orders[value["event"]].index(order_key(value["group"])) if matches else None)
            positions[identity] = matches[0] if matches else None
        result[identity] = {**value, "beforeIndex": before}
        if before is not None and identity not in originals:
            originals[identity] = {key: deepcopy(result[identity][key]) for key in ("event", "group", "refs", "beforeIndex")}
    return result


# //// 合成包含已移出原始组的事件顺序 [@x380kkm 2026-09-08] ////
def original_orders(record: dict, hooks: dict, owned: dict, originals: dict, desired: dict) -> dict:
    saved = record.get("orders", {})
    if not isinstance(saved, dict) or any(not isinstance(event, str) or not isinstance(order, list)
            or any(not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key) for key in order)
            for event, order in saved.items()):
        raise StorageValidationError("Hook 原序关系需要事件名和内容指纹数组.")
    result = {}
    for event in {item["event"] for item in [*owned.values(), *originals.values(), *desired.values()]}:
        entries = [item for item in originals.values() if item["event"] == event]
        added = {order_key(item["group"]) for identity, item in owned.items() if item["event"] == event and identity not in originals}
        current = [order_key(group) for group in hooks.get(event, []) if order_key(group) not in added]
        retained = set(current) | {order_key(item["group"]) for item in entries}
        order = [key for key in saved.get(event, current) if key in retained]
        missing = [item for item in entries if order_key(item["group"]) not in order]
        indices = [item["beforeIndex"] for item in missing]
        if len(set(indices)) != len(indices):
            raise StorageConflictError("host-hook-order")
        for item in sorted(missing, key=lambda value: value["beforeIndex"]):
            order.insert(min(item["beforeIndex"], len(order)), order_key(item["group"]))
        existing = set(order)
        numbered, present = numbered_order(order), numbered_order(current)
        for index, key in enumerate(present):
            if key[0] in existing:
                continue
            following = next((value for value in present[index + 1:] if value in numbered), None)
            numbered.insert(numbered.index(following) if following is not None else len(numbered), key)
        result[event] = [key for key, _ in numbered]
    return result


# //// 为内容相同的事件组按出现次数分配顺序身份 [@x380kkm 2026-09-08] ////
def numbered_order(keys: list[str]) -> list[tuple[str, int]]:
    counts = Counter()
    result = []
    for key in keys:
        counts[key] += 1
        result.append((key, counts[key]))
    return result


# //// 依据仍存在的原序邻居定位恢复组 [@x380kkm 2026-09-08] ////
def restored_position(groups: list, value: dict, order: list[str]) -> int:
    if value["beforeIndex"] is None:
        return len(groups)
    index = order.index(order_key(value["group"]))
    current = [order_key(group) for group in groups]
    original_counts, current_counts = Counter(order), Counter(current)
    if any(original_counts[key] != current_counts[key] and max(original_counts[key], current_counts[key]) > 1
           for key in original_counts.keys() & current_counts.keys()):
        raise StorageConflictError("host-hook-order")
    positions = {key: position for position, key in enumerate(numbered_order(current))}
    numbered = numbered_order(order)
    before = next((positions[key] for key in reversed(numbered[:index]) if key in positions), None)
    after = next((positions[key] for key in numbered[index + 1:] if key in positions), None)
    if before is not None and after is not None and before >= after:
        raise StorageConflictError("host-hook-order")
    return after if after is not None else before + 1 if before is not None else min(index, len(groups))


# //// 仅改变受管组的存在性并保留外部组的当前位置 [@x380kkm 2026-09-08] ////
def apply_groups(hooks: dict, previous: dict, desired: dict, positions: dict, orders: dict, created: set, originals: dict) -> None:
    removals, insertions = {}, {}
    for identity, value in {**previous, **desired}.items():
        enabled = value["enabled"] if identity in desired else False
        position = positions[identity]
        if position is not None and not enabled:
            removals.setdefault(value["event"], []).append(position)
        elif position is None and enabled:
            origins = [item for item in originals.values() if item["event"] == value["event"] and set(item["refs"]) & set(value["refs"])]
            anchor = originals.get(identity) or (min(origins, key=lambda item: item["beforeIndex"]) if origins else value)
            insertions.setdefault(value["event"], []).append((value, anchor))
    for event, indices in removals.items():
        for index in sorted(indices, reverse=True):
            del hooks[event][index]
    for event, entries in insertions.items():
        if event not in hooks:
            hooks[event] = []
            created.add(event)
        order = orders[event]
        for value, anchor in sorted(entries, key=lambda item: order.index(order_key(item[1]["group"])) if item[1]["beforeIndex"] is not None else len(order)):
            groups = hooks[event]
            groups.insert(restored_position(groups, anchor, order), deepcopy(value["group"]))


# //// 调和显式受管的事件组并保留外部事件与字段 [@x380kkm 2026-09-08] ////
def reconcile_hooks(targets: dict, contributions: list, content: bytes | None, ownership: dict,
                    hook_file: str = "hooks.json") -> None:
    previous = ownership.get("hooks")
    desired = desired_groups(contributions)
    if previous is None and not desired:
        if hook_file in targets:
            raise StorageValidationError("Hook 文件输出需要显式生命周期贡献.")
        return
    original = hooks_document(content)
    document = deepcopy(original)
    hooks = document.setdefault("hooks", {})
    record = previous or {"createdFile": content is None, "createdHooks": "hooks" not in original, "createdEvents": [], "groups": []}
    required = {"createdFile", "createdHooks", "createdEvents", "groups"}
    if (not isinstance(record, dict) or not required <= set(record) <= required | {"orders", "originals"}
            or type(record["createdFile"]) is not bool or type(record["createdHooks"]) is not bool
            or not isinstance(record["createdEvents"], list) or any(not isinstance(event, str) for event in record["createdEvents"])
            or not isinstance(record["groups"], list)):
        raise StorageValidationError("Hook 归属记录的文件与事件信息无效.")
    owned, positions = owned_groups(record["groups"], hooks)
    originals = original_groups(record, owned, hooks, positions)
    orders = original_orders(record, hooks, owned, originals, desired)
    desired = adopt_groups(desired, owned, originals, positions, hooks, orders)
    retained, released = retained_originals(originals, owned, desired)
    created = set(record["createdEvents"])
    apply_groups(hooks, owned, {**released, **desired}, positions, orders, created, {**originals, **retained})
    for event in created:
        if event in hooks and not hooks[event]:
            del hooks[event]
    if not hooks and record["createdHooks"]:
        del document["hooks"]
    if not document and record["createdFile"]:
        targets[hook_file] = None
    else:
        targets[hook_file] = content if canonical(document) == canonical(original) and content is not None else (encode_json(document, indent=2) + "\n").encode("utf-8")
    if desired:
        events = {item["event"] for item in [*desired.values(), *retained.values()]}
        ownership["hooks"] = {**record, "groups": list(desired.values()),
                              "originals": list(retained.values()), "createdEvents": sorted(created & events),
                              "orders": {event: order for event, order in orders.items() if event in events}}
    else:
        ownership.pop("hooks", None)


# //// 判断内嵌 Hook 是否取得明确的启用请求 [@x380kkm 2026-09-24] ////
def requested_enabled(entry: dict, requests: dict) -> bool:
    request = requests.get(entry.get("ref"))
    return bool(request.get("enabled")) if isinstance(request, dict) else False


# //// 按启用请求写出内嵌在主配置中的 Hook 定义 [@x380kkm 2026-09-24] ////
def reconcile_embedded_hooks(targets: dict, contributions: list, content: bytes | None,
                             ownership: dict, hook_file: str) -> None:
    requests = ownership.get("hookRequests", {})
    present = [entry for entry in contributions
               if entry.get("point") != HOOK_POINT or requested_enabled(entry, requests)]
    if any(entry.get("point") == HOOK_POINT for entry in present) or "hooks" in ownership:
        reconcile_hooks(targets, present, content, ownership, hook_file)
    else:
        targets.pop(hook_file, None)
    ownership.pop("hookState", None)
    ownership.pop("hookRequests", None)
