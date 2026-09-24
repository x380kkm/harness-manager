# audience: internal
# # card-hook-state
"""卡片读取原生逐处理器开关, 读取基线覆盖当前事件组及其用户状态. 声明保留范围设置."""
from copy import deepcopy
import getpass
from pathlib import Path

from .codex_hook_state import group_states
from .host_hook_sync import previous_group
from .host_hooks import hooks_document
from .host_paths import host_storage
from .host_ownership import current_file
from .host_storage import HOOK_STATE_NAME
from .host_projection import managed_projection
from .storage_errors import StorageConflictError, StorageValidationError
from .card_subjects import CardError
from .json_codec import json_values_equal


# //// 核对开关请求对应的声明仍是当前保存值 [@x380kkm 2026-09-10] ////
def hook_request_is_current(catalogs, request: dict) -> bool:
    if (not isinstance(request, dict) or request.get("scope") not in {"user", "project", "project-local"}
            or not isinstance(request.get("binding"), dict) or not isinstance(request["binding"].get("id"), str)
            or type(request.get("enabled")) is not bool or not isinstance(request.get("native"), dict)):
        raise StorageValidationError("Hook 开关请求需要声明范围, 绑定和原生读取基线.")
    binding = request["binding"]
    documents = catalogs.select(request["scope"]).snapshot()
    return json_values_equal(next((item for item in documents if item.get("id") == binding["id"]), None), binding)


# //// 在宿主协作锁中保存对应声明的原生开关请求 [@x380kkm 2026-09-10] ////
def save_hook_request(catalogs, codex_root, scope: str, reference: str, enabled: bool | None,
                      native: dict, binding: dict | None, binding_id: str) -> None:
    request = None if enabled is None else {"enabled": enabled, "native": native, "binding": binding, "scope": scope}
    # //// 核对声明保存值与当前目录内容 [@x380kkm 2026-09-10] ////
    def verify():
        documents = catalogs.select(scope).snapshot()
        if not json_values_equal(next((item for item in documents if item.get("id") == binding_id), None), binding):
            raise StorageConflictError(binding_id)
    host_storage(catalogs, codex_root, "project-local" if scope == "project" else scope).save_hook_requests(
        {reference: request}, verify_inputs=verify)


# //// 核对共享涉及的 Hook 开关请求已经处理 [@x380kkm 2026-09-10] ////
def require_settled_hook(frame, identity: str) -> None:
    if frame.item(identity)["kind"] != "hook":
        return
    document = frame.definition(identity)
    scope = hook_scope(frame, document)
    requests = host_storage(frame.catalogs, frame.codex.root, scope).read_ownership().get("hookRequests", {})
    references = {document["id"] + "#" + member["id"] for member in document["contributions"]}
    if references & requests.keys():
        raise CardError("sharing_pending_hook", "请先应用或撤去待处理的 Hook 开关选择, 再更改共享范围.")


# //// 选择卡片实际投放的用户或项目范围 [@x380kkm 2026-09-10] ////
def hook_scope(frame, document: dict) -> str:
    if frame.scope == "user":
        return "user"
    user = frame.catalogs.for_scope("user")
    selected, _ = managed_projection(user.documents, {"user": getpass.getuser(), "host": "codex"}, user.layers)
    reference = document["id"] + "#" + next(member["id"] for member in document["contributions"]
                                             if member.get("point") == "hook.x380kkm/lifecycle")
    if any(entry.summary["content"] == reference and entry.member.get("point") == "hook.x380kkm/lifecycle" for entry in selected):
        return "user"
    return "project-local"


# //// 读取卡片定义对应的原生状态与未应用选择 [@x380kkm 2026-09-10] ////
def observe_hook(frame, document: dict) -> dict:
    member = next(member for member in document["contributions"] if member.get("point") == "hook.x380kkm/lifecycle")
    payload = member["payload"]
    group = {"hooks": payload["handlers"]}
    if payload.get("matcher") is not None:
        group["matcher"] = payload["matcher"]
    reference = document["id"] + "#" + member["id"]
    value = {"event": payload["event"], "group": group, "refs": [reference]}
    scope = hook_scope(frame, document)
    storage = host_storage(frame.catalogs, frame.codex.root, scope)
    ownership = storage.read_ownership()
    name = "config.toml" if scope == "user" else HOOK_STATE_NAME
    files = storage.capture({"hooks.json": None, name: None})["files"]
    path = Path(ownership.get("hookState", {}).get("path", storage._target("hooks.json")))
    previous = previous_group(value, ownership.get("hooks", {}).get("groups", []))
    states = group_states(current_file(files, name), hooks_document(current_file(files, "hooks.json")), path, previous or value)
    flags = {state["enabled"] for state in states} if states is not None else set()
    state = "enabled" if flags == {True} else "disabled" if flags == {False} else "mixed" if flags else "absent"
    request = ownership.get("hookRequests", {}).get(reference)
    if request is not None and not hook_request_is_current(frame.catalogs, request):
        request = None
    return {"scope": scope, "ref": reference, "state": state,
            "enabled": next(iter(flags)) if len(flags) == 1 else None,
            "handlers": [{"key": item["key"], "enabled": item["enabled"],
                          "trustRecorded": bool((item["saved"] or {}).get("trusted_hash"))} for item in states or []],
            "request": deepcopy(request), "baseline": {"path": str(path), "states": states}}
