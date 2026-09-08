# audience: internal
# # sharing-storage
"""共享变更先保存私人副本, 再原子替换项目声明. 保存失败按对象基线回滚, 协作冲突保留恢复记录."""
from __future__ import annotations

import os
import stat
from uuid import uuid4

from .card_subjects import CardError
from .json_codec import encode_json
from .protocol import document_identity
from .storage_errors import StorageBoundaryError, StorageConflictError


# //// 从指定对象集合生成基于原始内容的存储计划 [@x380kkm 2026-09-07] ////
def mutation_plans(store, before: dict, after: dict) -> list[dict]:
    plans = []
    for identity, value in after.items():
        previous = before.get(identity)
        if value is None:
            if previous is not None:
                plans.append(store.preview_remove(identity, previous))
        else:
            plans.append(store.preview_put(value, previous))
    return plans


# //// 为一次分享操作保存无法自动恢复的对象差异 [@x380kkm 2026-09-07] ////
def save_recovery(store, stages: list[dict]) -> str:
    store.ensure_directory()
    directory = store.directory / "sharing-recovery"
    if directory.exists():
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise StorageBoundaryError("共享恢复目录需要直接位于私人配置目录中.")
    else:
        directory.mkdir()
    if directory.resolve().parent != store.directory.resolve():
        raise StorageBoundaryError("共享恢复目录需要保持在私人配置目录内.")
    path = directory / (uuid4().hex + ".json")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(encode_json({"operation": "share-card", "stages": stages}))
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


# //// 依次提交私人保留、共享替换和私人清理 [@x380kkm 2026-09-07] ////
def apply_sharing(frame, stages: list[tuple[str, dict]], guard) -> bool:
    current = {scope: {document_identity(value): value for value in documents}
               for scope, documents in frame.layer_documents.items()}

    # //// 在提交边界核对全部目录的实际对象 [@x380kkm 2026-09-08] ////
    def verify(scope=None, documents=None, targets=None):
        expected = {name: list(values.values()) for name, values in current.items()}
        actual = {name: documents if name == scope else frame.catalogs.select(name).snapshot() for name in current}
        guard(scope, expected, actual, targets or {})

    completed = []
    changed = False
    try:
        for scope, targets in stages:
            if not targets:
                continue
            store = frame.catalogs.select(scope)
            before = {identity: current[scope].get(identity) for identity in targets}
            plans = mutation_plans(store, before, targets)
            if not plans:
                continue
            results = store.apply_many(plans, verify_current=lambda documents: verify(scope, documents, targets))
            changed_ids = {result["id"] for result in results if result["changed"]}
            if changed_ids:
                completed.append({"scope": scope, "before": {key: before[key] for key in changed_ids},
                                  "after": {key: targets[key] for key in changed_ids}})
                changed = True
            for identity, value in targets.items():
                if value is None:
                    current[scope].pop(identity, None)
                else:
                    current[scope][identity] = value
        verify()
    except Exception as error:
        pending = list(completed)
        try:
            for stage in reversed(completed):
                store = frame.catalogs.select(stage["scope"])
                plans = mutation_plans(store, stage["after"], stage["before"])
                if plans:
                    store.apply_many(plans)
                pending.pop()
        except Exception as rollback_error:
            failure = CardError("sharing_recovery", "共享保存遇到协作变化, 私人副本与共享原文保留供恢复.")
            failure.details = {"cause": str(error), "rollback": str(rollback_error)}
            try:
                failure.details["recovery"] = save_recovery(frame.catalogs.select("project-local"), pending)
            except (OSError, StorageBoundaryError) as record_error:
                failure.details.update(recoveryError=str(record_error), stages=pending)
            raise failure from error
        raise
    return changed


# //// 核对共享关系的两端均有明确共享记录 [@x380kkm 2026-09-07] ////
def check_shared_relations(documents: list[dict]) -> None:
    bindings = {value["plugin"]["id"] for value in documents if value["kind"] == "PluginBinding"
                and value["id"] == f"binding:project/{value['plugin']['id']}"}
    definitions = {value["id"] for value in documents if value["kind"] == "Plugin"}
    for document in documents:
        if document["kind"] != "Plugin" or not document["id"].startswith("plugin:adapter/"):
            continue
        for member in document["contributions"]:
            payload = member.get("payload", {})
            adapter = payload.get("adapter", {}) if isinstance(payload, dict) else {}
            source = payload.get("skill", payload.get("subject", ""))
            target = adapter.get("targetRef", "")
            ends = {str(source).partition("#")[0], str(target).partition("#")[0]}
            if "" in ends or not ends <= bindings or not ends <= definitions:
                raise StorageConflictError(document_identity(document))
