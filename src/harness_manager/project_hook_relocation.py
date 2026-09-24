# audience: internal
# # project-hook-relocation
"""项目搬移保留实际原生开关的来源位置, 恢复点中的状态键对应新的项目目录.
开关请求沿原绑定与搬移后的保存值转换, 当前有效请求保留其原生读取基线.
"""
from copy import deepcopy
from pathlib import Path

from .codex_hook_state import relocate_hook_state
from .host_ownership import decode_file, encode_file
from .host_storage import HOOK_STATE_NAME
from .json_codec import json_values_equal


# //// 将归属中的原生读取位置对应到恢复文件 [@x380kkm 2026-09-10] ////
def relocate_native_read(value: dict, source: Path, target: Path) -> None:
    previous = Path(value.get("path", source))
    prefixes = (f"{previous}:", f"{previous.as_posix()}:")
    value["path"] = str(target)
    for row in value.get("states") or []:
        key = row.get("key", "")
        prefix = next((prefix for prefix in prefixes if key.startswith(prefix)), None)
        if prefix is not None:
            row["key"] = f"{target}:" + key[len(prefix):]


# //// 对齐恢复点的归属位置和原生状态读取基线 [@x380kkm 2026-09-10] ////
def relocate_snapshot_ownership(ownership: dict | None, source: Path, target: Path) -> None:
    if not ownership:
        return
    state = ownership.get("hookState")
    if state is not None:
        previous = Path(state.get("path", source))
        for rows in state.get("originals", {}).values():
            relocate_native_read({"path": str(previous), "states": rows}, source, target)
        state["path"] = str(target)
    for request in ownership.get("hookRequests", {}).values():
        relocate_native_read(request["native"], source, target)


# //// 将恢复点状态键映射到新的项目位置 [@x380kkm 2026-09-10] ////
def relocate_host_hooks(record: dict, old: Path, current: Path, binding_moves: dict | None = None) -> dict:
    result = deepcopy(record)
    source = old / ".codex/hooks.json"
    target = current / ".codex/hooks.json"
    if binding_moves is not None:
        for name in ("ownership", "beforeOwnership", "afterOwnership"):
            relocate_hook_requests(result.get(name), binding_moves)
    for field, ownership in (("files", "ownership"), ("before", "beforeOwnership"), ("after", "afterOwnership")):
        files = result.get(field, {})
        if HOOK_STATE_NAME in files:
            snapshot_ownership = result.get(ownership) or {}
            previous = Path(snapshot_ownership.get("hookState", {}).get("path", source))
            content = relocate_hook_state(decode_file(files[HOOK_STATE_NAME]), source, target)
            files[HOOK_STATE_NAME] = encode_file(relocate_hook_state(content, previous, target))
            relocate_snapshot_ownership(snapshot_ownership, source, target)
    ownership = result.get("ownership", {})
    if result.get("kind") == "state" and ("hooks" in ownership or ownership.get("hookRequests")):
        state = result["ownership"].setdefault("hookState", {"groups": [], "originals": {}})
        state.setdefault("path", str(source))
    return result


# //// 以原绑定基线转换有效开关请求并清除过时选择 [@x380kkm 2026-09-10] ////
def relocate_hook_requests(ownership: dict | None, binding_moves: dict) -> None:
    if not ownership or "hookRequests" not in ownership:
        return
    requests = ownership["hookRequests"]
    for reference, request in list(requests.items()):
        binding = request.get("binding", {})
        movement = binding_moves.get(request.get("scope"), {}).get(binding.get("id"))
        if movement is None or not json_values_equal(movement[0], binding) or movement[1] is None:
            del requests[reference]
        else:
            request["binding"] = deepcopy(movement[1])
    if not requests:
        del ownership["hookRequests"]
