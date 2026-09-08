# audience: internal
# # host-storage
# 首次使用前的完整配置绑定作用域与宿主根目录, 其恢复点保持原文与不存在记录.
# 宿主文件替换在独立 Store 协作锁中执行, 最近事务与恢复前保护点分别使用固定记录.
# 所选恢复目标独立于最近事务, 保留完整文件集与对应的字段归属.

from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from collections.abc import Callable
import re
import stat
import tempfile

from .storage import Store, _copy_json
from .storage_errors import (
    StorageBoundaryError, StorageConflictError, StorageIOError, StorageValidationError,
)

TARGET_NAMES = frozenset({"AGENTS.override.md", "config.toml", "hooks.json"})
SOURCE_NAMES = ("AGENTS.md", "AGENTS.override.md", "config.toml", "hooks.json")
MAX_FILE_BYTES = 4 * 1024 * 1024
RECOVERY_STATES = frozenset({"pending", "recovery-required"})


# //// 保存宿主事务的备份身份和恢复状态 [@x380kkm 2026-09-07] ////
class HostTransactionError(StorageIOError):
    code = "host-transaction"

    def __init__(self, backup_id: str, recovery_required: bool) -> None:
        message = "宿主设置保存失败, 原文已经存档."
        if recovery_required:
            message += " 部分目标需要从备份恢复."
        super().__init__(message)
        self.details = {"backupId": backup_id, "recoveryRequired": recovery_required}


# //// 拒绝文件链中的重解析点与非普通入口 [@x380kkm 2026-09-07] ////
def _check_chain(path: Path, *, directory: bool = False) -> None:
    for entry in (*reversed(path.parents), path):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        expected = stat.S_ISDIR if entry != path or directory else stat.S_ISREG
        if stat.S_ISLNK(info.st_mode) or reparse or not expected(info.st_mode):
            raise StorageBoundaryError(f"宿主存储需要普通文件系统入口: {entry}.")


# //// 为存档编码文件存在性与完整字节 [@x380kkm 2026-09-07] ////
def _encode(content: bytes | None) -> str | None:
    return None if content is None else base64.b64encode(content).decode("ascii")


# //// 核对存档字节范围并解码文件内容 [@x380kkm 2026-09-07] ////
def _decode(content: str | None) -> bytes | None:
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


# //// 核对受管文件集合及其字节内容 [@x380kkm 2026-09-07] ////
def _validate_files(files: dict, *, encoded: bool) -> None:
    if not isinstance(files, dict) or not files.keys() <= set(SOURCE_NAMES):
        raise StorageValidationError("宿主内容需要来自说明文件, 说明覆盖文件, 配置文件或 hooks 文件.")
    for content in files.values():
        value = _decode(content) if encoded else content
        if value is not None and (not isinstance(value, bytes) or len(value) > MAX_FILE_BYTES):
            raise StorageValidationError("宿主文件需要不超过 4 MiB 的字节内容.")


# //// 管理固定宿主文件与独立操作存档 [@x380kkm 2026-09-07] ////
class HostStorage:
    # //// 固定宿主路径映射与个人存档位置 [@x380kkm 2026-09-07] ////
    def __init__(self, user_root: Path, target_root: Path, scope_id: str, *, config_subdir: str = "") -> None:
        if not isinstance(scope_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}", scope_id):
            raise StorageValidationError("宿主作用域身份需要由字母, 数字, 下划线或连字符组成.")
        if config_subdir not in {"", ".codex"}:
            raise StorageBoundaryError("宿主配置目录需要位于根目录或其 .codex 子目录.")
        self.user_root = Path(os.path.abspath(user_root))
        self.target_root = Path(os.path.abspath(target_root))
        self.scope_id, self.config_subdir = scope_id, config_subdir
        self.context = {"scopeId": scope_id, "targetRoot": str(self.target_root), "configSubdir": config_subdir}
        _check_chain(self.user_root, directory=True)
        self.store = Store(self.user_root, lambda value: value["id"], self._validate_document,
                           catalog_directory=Path(".harness/hosts") / scope_id)
        self._check_boundaries()

    # //// 生成固定逻辑文件名对应的宿主路径 [@x380kkm 2026-09-07] ////
    def _target(self, name: str) -> Path:
        if name not in SOURCE_NAMES:
            raise StorageBoundaryError("文件名称超出宿主受管范围.")
        directory = self.target_root if name in {"AGENTS.md", "AGENTS.override.md"} else self.target_root / self.config_subdir
        return directory / name

    # //// 核对个人存档与全部宿主文件入口 [@x380kkm 2026-09-07] ////
    def _check_boundaries(self) -> None:
        _check_chain(self.store.catalog)
        _check_chain(self.store.lock)
        for name in SOURCE_NAMES:
            _check_chain(self._target(name))

    # //// 校验操作存档的作用域与固定目标集合 [@x380kkm 2026-09-07] ////
    def _validate_document(self, value: dict) -> None:
        if any(value.get(key) != expected for key, expected in self.context.items()):
            raise StorageBoundaryError("宿主存档所属目录或作用域不匹配.")
        kind = value.get("kind")
        if kind == "state" and value.get("id") == "state" and type(value.get("enabled")) is bool:
            if not value.keys() <= {*self.context, "id", "kind", "enabled", "lastApplied", "ownership"}:
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
            required = {*self.context, "id", "kind", "createdAt", "files"}
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
            _validate_files(value.get("files"), encoded=True)
            if value["files"].keys() != set(SOURCE_NAMES):
                raise StorageValidationError("宿主恢复点需要包含完整的原始配置.")
            return
        if kind != "transaction" or value.get("id") != "transaction":
            raise StorageValidationError("宿主操作记录身份无效.")
        required = {*self.context, "id", "kind", "status", "createdAt", "reason", "before", "after"}
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
        _validate_files(value.get("before"), encoded=True)
        _validate_files(value.get("after"), encoded=True)
        if value["before"].keys() != value["after"].keys():
            raise StorageValidationError("宿主备份前后的目标集合需要一致.")

    # //// 读取有界文件字节并保留不存在状态 [@x380kkm 2026-09-07] ////
    def _read_target(self, name: str) -> bytes | None:
        self._check_boundaries()
        path = self._target(name)
        return self._read_file(path)

    # //// 读取根目录中的说明来源并限定普通文件名 [@x380kkm 2026-09-08] ////
    def capture_instructions(self, names: list[str]) -> dict:
        self._check_boundaries()
        result = {}
        for name in names:
            if not isinstance(name, str) or not name or name in {".", ".."} or any(value in name for value in ("/", "\\", ":")):
                raise StorageBoundaryError("说明来源需要宿主根目录中的普通文件名.")
            path = self.target_root / name
            _check_chain(path)
            result[name] = _encode(self._read_file(path))
        return result

    # //// 取得影响说明候选选择的配置文件基线 [@x380kkm 2026-09-08] ////
    def capture_configuration(self) -> dict:
        return {"config.toml": _encode(self._read_target("config.toml"))}

    # //// 从固定路径取得有界普通文件字节 [@x380kkm 2026-09-08] ////
    def _read_file(self, path: Path) -> bytes | None:
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            self._check_boundaries()
            _check_chain(path)
            if not os.path.samestat(info, path.stat()):
                raise StorageConflictError(path.name)
            if not stat.S_ISREG(info.st_mode):
                raise StorageBoundaryError("宿主内容需要来自普通文件.")
            if info.st_size > MAX_FILE_BYTES:
                raise StorageValidationError("宿主文件大小上限为 4 MiB.")
            content = source.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise StorageValidationError("宿主文件大小上限为 4 MiB.")
        return content

    # //// 读取控制状态和受管目标的比较基线 [@x380kkm 2026-09-07] ////
    def capture(self, targets: dict[str, bytes | None]) -> dict:
        _validate_files(targets, encoded=False)
        self._check_boundaries()
        documents = self.store.snapshot()
        return {**self._baseline(documents), "files": {name: _encode(self._read_target(name)) for name in targets}}

    # //// 读取静态字段归属副本供宿主编译器合成配置 [@x380kkm 2026-09-07] ////
    def read_ownership(self) -> dict:
        self._check_boundaries()
        return _copy_json((self._state(self.store.snapshot()) or {}).get("ownership", {}))

    # //// 为控制状态生成不含归属原文的并发基线 [@x380kkm 2026-09-07] ////
    def _baseline(self, documents: list[dict]) -> dict:
        state = self._state(documents)
        ownership = (state or {}).get("ownership", {})
        encoded = json.dumps(ownership, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        public_state = {key: value for key, value in state.items() if key != "ownership"} if state else None
        return {**self.context, "state": public_state, "ownershipToken": sha256(encoded).hexdigest(), "files": {}}

    # //// 幂等保存首次使用前的完整宿主配置 [@x380kkm 2026-09-07] ////
    def initialize(self) -> dict:
        self._check_boundaries()
        with self.store._locked_catalog():
            self._initialize(self.store._read_catalog())
        return self.status()

    # //// 首次接管前固定说明, 配置与 hooks 的原始字节 [@x380kkm 2026-09-07] ////
    def _initialize(self, documents: list[dict]) -> list[dict]:
        if any(item["id"] == "initial" for item in documents):
            return documents
        documents = self._put(documents, self._snapshot("initial"))
        self._commit(documents)
        return documents

    # //// 捕获完整的宿主恢复点并保留文件存在性 [@x380kkm 2026-09-07] ////
    def _snapshot(self, identity: str, ownership: dict | None = None) -> dict:
        result = {**self.context, "id": identity, "kind": "backup",
                  "createdAt": datetime.now(timezone.utc).isoformat(),
                  "files": {name: _encode(self._read_target(name)) for name in SOURCE_NAMES}}
        if ownership is not None:
            result["ownership"] = _copy_json(ownership)
        return result

    # //// 返回控制状态及不含文件正文的备份摘要 [@x380kkm 2026-09-08] ////
    def status(self) -> dict:
        self._check_boundaries()
        documents = self.store.snapshot()
        state = self._state(documents)
        backups = [self._summary(item) for item in self._restorable_records(documents)]
        for summary in backups:
            record = self._backup(documents, summary["id"])
            try:
                self._restore_ownership(record, documents)
                summary["restorable"] = True
            except StorageValidationError as error:
                summary.update(restorable=False, restoreError=str(error))
        pending = [item["id"] for item in documents if item.get("status") in RECOVERY_STATES]
        initialized = any(item["id"] == "initial" for item in documents)
        return {"enabled": state["enabled"] if state else True, "backups": backups,
                "initialized": initialized, "initialBackupId": "initial" if initialized else None,
                "lastApplied": state.get("lastApplied") if state else None,
                "recoveryRequired": bool(pending), "pending": pending,
                "baseline": self._baseline(documents)}

    # //// 生成原始恢复点与异常事务的用户可读摘要 [@x380kkm 2026-09-08] ////
    @staticmethod
    def _summary(record: dict) -> dict:
        labels = {"initial": "首次使用前的原始配置", "before-restore": "恢复前的配置",
                  "transaction": "未完成操作的恢复记录", "interrupted": "中断时的配置", "restore-target": "所选恢复目标"}
        purposes = {"initial": "initial", "before-restore": "restore-protection", "transaction": "recovery",
                    "interrupted": "recovery", "restore-target": "recovery"}
        return {"id": record["id"], "createdAt": record["createdAt"], "label": labels[record["id"]],
                "purpose": purposes[record["id"]], "files": list(record.get("files", record.get("before", {}))),
                "reason": record.get("reason", labels[record["id"]]), "status": record.get("status", "available")}

    # //// 提取唯一宿主控制记录 [@x380kkm 2026-09-07] ////
    @staticmethod
    def _state(documents: list[dict]) -> dict | None:
        return next((item for item in documents if item["id"] == "state"), None)

    # //// 将完整操作记录在协作锁内原子持久化 [@x380kkm 2026-09-07] ////
    def _commit(self, documents: list[dict]) -> None:
        self._check_boundaries()
        for document in documents:
            self._validate_document(document)
        self.store._write_catalog(documents)

    # //// 替换独立操作记录并保留其余存档 [@x380kkm 2026-09-07] ////
    @staticmethod
    def _put(documents: list[dict], record: dict) -> list[dict]:
        return [item for item in documents if item["id"] != record["id"]] + [record]

    # //// 核对预览的作用域, 控制状态与文件字节 [@x380kkm 2026-09-07] ////
    def _check_baseline(self, baseline: dict, targets: dict, documents: list[dict]) -> None:
        if not isinstance(baseline, dict) or any(baseline.get(key) != value for key, value in self.context.items()):
            raise StorageBoundaryError("宿主操作基线所属目录或作用域不匹配.")
        _validate_files(baseline.get("files"), encoded=True)
        if baseline["files"].keys() != targets.keys():
            raise StorageValidationError("宿主操作的预览目标与应用目标需要一致.")
        current = self._baseline(documents)
        if baseline.get("state") != current["state"] or baseline.get("ownershipToken") != current["ownershipToken"]:
            raise StorageConflictError("host-state")
        for name, before in baseline["files"].items():
            if self._read_target(name) != _decode(before):
                raise StorageConflictError(name)
        self._check_instruction_reads(baseline, {})

    # //// 按预览原文或本次已写目标核对说明来源 [@x380kkm 2026-09-08] ////
    def _check_instruction_reads(self, baseline: dict, written_targets: dict) -> None:
        reads = baseline.get("instructionReads", {})
        if not isinstance(reads, dict):
            raise StorageValidationError("说明来源基线需要文件与原文字节的映射.")
        expected = {name: _encode(written_targets[name]) if name in written_targets else before for name, before in reads.items()}
        if self.capture_instructions(list(reads)) != expected:
            raise StorageConflictError("host-instruction-source")

    # //// 设置接管开关并保留当前宿主文件 [@x380kkm 2026-09-07] ////
    def enable(self, enabled: bool, baseline: dict | None = None) -> dict:
        if type(enabled) is not bool:
            raise StorageValidationError("接管开关需要布尔值.")
        baseline = self.capture({}) if baseline is None else baseline
        self._check_boundaries()
        with self.store._locked_catalog():
            documents = self.store._read_catalog()
            self._check_baseline(baseline, {}, documents)
            if enabled and any(item.get("status") in RECOVERY_STATES for item in documents):
                raise StorageValidationError("请先恢复未完成的宿主设置操作.")
            state = {**(self._state(documents) or {}), **self.context, "id": "state", "kind": "state", "enabled": enabled}
            if state != self._state(documents):
                self._commit(self._put(documents, state))
        return self.status()

    # //// 在最后一次基线核对后原子替换单个宿主目标 [@x380kkm 2026-09-07] ////
    def _write_target(self, name: str, content: bytes | None, expected: bytes | None) -> None:
        if self._read_target(name) != expected:
            raise StorageConflictError(name)
        path = self._target(name)
        if content is None:
            if expected is not None:
                path.unlink()
            return
        for directory in (*reversed(path.parent.parents), path.parent):
            _check_chain(directory, directory=True)
            directory.mkdir(exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
        temporary = Path(filename)
        committed = False
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if self._read_target(name) != expected:
                raise StorageConflictError(name)
            os.replace(temporary, path)
            committed = True
        finally:
            if not committed:
                temporary.unlink(missing_ok=True)

    # //// 仅回滚仍保持本次写入内容的宿主目标 [@x380kkm 2026-09-07] ////
    def _rollback(self, written: list[str], before: dict, targets: dict) -> bool:
        recovery_required = False
        for name in reversed(written):
            try:
                self._write_target(name, _decode(before[name]), targets[name])
            except Exception:
                recovery_required = True
        return recovery_required

    # //// 为已预览的目标存档原文并提交宿主配置 [@x380kkm 2026-09-07] ////
    def apply(self, targets: dict[str, bytes | None], baseline: dict, reason: str = "apply", *, ownership: dict | None = None,
              verify_inputs: Callable[[dict], None] | None = None) -> dict:
        if not isinstance(targets, dict) or not targets.keys() <= TARGET_NAMES:
            raise StorageValidationError("宿主应用只能写入说明覆盖文件, 配置文件和 hooks 文件.")
        if ownership is not None and not isinstance(ownership, dict):
            raise StorageValidationError("宿主字段归属需要 JSON 对象.")
        return self._apply(targets, baseline, reason, ownership=_copy_json(ownership), verify_inputs=verify_inputs)

    # //// 在存档与文件写入之间维持协作锁及失败恢复记录 [@x380kkm 2026-09-08] ////
    def _apply(self, targets: dict, baseline: dict, reason: str, restoring_id: str | None = None, ownership: dict | None = None,
               verify_inputs: Callable[[dict], None] | None = None) -> dict:
        _validate_files(targets, encoded=False)
        if not isinstance(reason, str) or not reason or len(reason) > 160:
            raise StorageValidationError("宿主操作需要简短的存档原因.")
        self._check_boundaries()
        with self.store._locked_catalog():
            documents = self.store._read_catalog()
            self._check_baseline(baseline, targets, documents)
            if verify_inputs is not None:
                verify_inputs({})
            had_pending = any(item.get("status") in RECOVERY_STATES for item in documents)
            if not restoring_id and had_pending:
                raise StorageValidationError("请先恢复未完成的宿主设置操作.")
            if restoring_id:
                previous = self._backup(documents, restoring_id)
                if baseline.get("backup") != previous:
                    raise StorageConflictError(restoring_id)
                if targets != self._backup_targets(previous):
                    raise StorageConflictError(restoring_id)
                restored_ownership = self._restore_ownership(previous, documents)
            documents = self._initialize(documents)
            protection = self._protection(documents) if restoring_id else None
            retained = next((item for item in self._restorable_records(documents) if item["id"] == "restore-target"), None)
            if retained is not None and not any(item["id"] == "restore-target" for item in documents):
                documents = self._retain_restore_target(documents, retained, protection or self._snapshot("before-restore"))
            if restoring_id:
                documents = self._retain_restore_target(documents, previous, protection)
                if protection["id"] != restoring_id:
                    documents = self._put(documents, protection)
            changed = [name for name in targets if targets[name] != _decode(baseline["files"][name])]
            state = {**(self._state(documents) or {}), **self.context, "id": "state", "kind": "state",
                     "enabled": restoring_id is None}
            if restoring_id:
                state["ownership"] = restored_ownership
            elif ownership is not None:
                state["ownership"] = ownership
            if not changed:
                self._check_instruction_reads(baseline, {})
                if verify_inputs is not None:
                    verify_inputs({})
                if restoring_id:
                    documents = [item for item in documents if item["id"] != "transaction"]
                    documents = self._put(documents, protection)
                if state != self._state(documents) or restoring_id:
                    self._commit(self._put(documents, state))
                return {"changed": False, "backupId": protection["id"] if protection else "initial",
                        "enabled": state["enabled"]}
            backup = {**self.context, "id": "transaction", "kind": "transaction", "status": "pending",
                      "createdAt": datetime.now(timezone.utc).isoformat(), "reason": reason,
                      "before": baseline["files"], "after": {name: _encode(value) for name, value in targets.items()},
                      "beforeOwnership": _copy_json(protection.get("ownership") or {}) if protection else _copy_json((self._state(documents) or {}).get("ownership", {})),
                      "afterOwnership": _copy_json(state.get("ownership", {}))}
            if protection and protection.get("restoreError"):
                backup["beforeOwnershipError"] = protection["restoreError"]
            pending = self._put(documents, backup)
            self._commit(pending)
            written = []
            try:
                self._check_instruction_reads(baseline, {})
                if verify_inputs is not None:
                    verify_inputs({})
                for name in changed:
                    self._write_target(name, targets[name], _decode(backup["before"][name]))
                    written.append(name)
                for name, content in targets.items():
                    if self._read_target(name) != content:
                        raise StorageConflictError(name)
                self._check_instruction_reads(baseline, targets)
                if verify_inputs is not None:
                    verify_inputs(targets)
                state["lastApplied"] = backup["createdAt"]
                completed = self._put(pending, {**backup, "status": "applied"})
                if protection is not None:
                    completed = self._put(completed, protection)
                self._commit(self._put(completed, state))
            except Exception as error:
                recovery_required = self._rollback(written, backup["before"], targets) or had_pending
                try:
                    status = "recovery-required" if recovery_required else "rolled-back"
                    self._commit(self._put(pending, {**backup, "status": status}))
                except Exception:
                    recovery_required = True
                failure = HostTransactionError(backup["id"], recovery_required)
                if restoring_id:
                    failure.details["restoreTargetId"] = "initial" if restoring_id == "initial" else "restore-target"
                raise failure from error
        return {"changed": True, "backupId": protection["id"] if protection else "initial",
                "transactionId": "transaction", "enabled": state["enabled"]}

    # //// 为中断状态保存与实际文件匹配的归属或保留明确诊断 [@x380kkm 2026-09-08] ////
    def _protection(self, documents: list[dict]) -> dict:
        transaction = next((item for item in documents if item.get("status") in RECOVERY_STATES), None)
        state = (self._state(documents) or {}).get("ownership", {})
        snapshot = self._snapshot("interrupted" if transaction else "before-restore", state)
        if transaction is None:
            return snapshot
        current = snapshot["files"]
        if all(current[name] == value for name, value in transaction["before"].items()) and not transaction.get("beforeOwnershipError"):
            snapshot["ownership"] = _copy_json(transaction.get("beforeOwnership", state))
        elif all(current[name] == value for name, value in transaction["after"].items()) and "afterOwnership" in transaction:
            snapshot["ownership"] = _copy_json(transaction["afterOwnership"])
        elif transaction.get("beforeOwnership") == {} and transaction.get("afterOwnership") == {} and not transaction.get("beforeOwnershipError"):
            snapshot["ownership"] = {}
        else:
            snapshot.update(ownership=None, restoreError="中断时的文件与事务前后配置均不一致, 原始字节已保存, 请先确认各文件的字段归属.")
        return snapshot

    # //// 将易被替换的所选目标保存为独立恢复点 [@x380kkm 2026-09-08] ////
    def _retain_restore_target(self, documents: list[dict], selected: dict, current: dict) -> list[dict]:
        if selected["id"] == "initial":
            return documents
        target = {**self.context, "id": "restore-target", "kind": "backup", "createdAt": selected["createdAt"],
                  "files": {**current["files"], **selected.get("files", selected.get("before", {}))}}
        try:
            target["ownership"] = self._restore_ownership(selected, documents)
        except StorageValidationError as error:
            target.update(ownership=None, restoreError=str(error))
        return self._put(documents, target)

    # //// 收集固定备份和恢复事务保存的目标 [@x380kkm 2026-09-08] ////
    @staticmethod
    def _restorable_records(documents: list[dict]) -> list[dict]:
        records = [item for item in documents if item["kind"] == "backup" or item.get("status") in RECOVERY_STATES]
        transaction = next((item for item in documents if item["kind"] == "transaction" and item["reason"] == "restore"
                            and item["status"] in {*RECOVERY_STATES, "rolled-back"}), None)
        if transaction is not None and not any(item["id"] == "restore-target" for item in records):
            target = {**transaction, "id": "restore-target", "kind": "backup", "status": "available",
                      "files": transaction["after"], "ownership": transaction.get("afterOwnership")}
            if target["ownership"] is None:
                target["restoreError"] = "所选恢复目标缺少对应的字段归属, 请先确认原始文件与受管内容."
            records.append(target)
        return records

    # //// 根据身份选择完整备份或事务中的恢复目标 [@x380kkm 2026-09-08] ////
    @staticmethod
    def _backup(documents: list[dict], backup_id: str) -> dict:
        result = next((item for item in HostStorage._restorable_records(documents) if item["id"] == backup_id), None)
        if result is None:
            raise StorageValidationError("所选宿主备份不存在.")
        return result

    # //// 提取所选恢复点或未完成事务的原始文件 [@x380kkm 2026-09-07] ////
    @staticmethod
    def _backup_targets(backup: dict) -> dict:
        return {name: _decode(value) for name, value in backup.get("files", backup.get("before", {})).items()}

    # //// 取得与恢复点字节对应的字段归属 [@x380kkm 2026-09-08] ////
    def _restore_ownership(self, backup: dict, documents: list[dict]) -> dict:
        if backup["id"] == "initial":
            return {}
        if backup["id"] == "transaction":
            if backup.get("beforeOwnershipError"):
                raise StorageValidationError(backup["beforeOwnershipError"])
            return _copy_json(backup.get("beforeOwnership", (self._state(documents) or {}).get("ownership", {})))
        if "ownership" in backup:
            if backup["ownership"] is None:
                raise StorageValidationError(backup["restoreError"])
            return _copy_json(backup["ownership"])
        original = next((item for item in documents if item["id"] == "initial"), None)
        if original is not None and original["files"] == backup["files"]:
            return {}
        raise StorageValidationError("所选恢复点缺少对应的字段归属, 请使用原始配置恢复点.")

    # //// 返回明确选择的备份正文和当前宿主基线 [@x380kkm 2026-09-07] ////
    def preview_restore(self, backup_id: str) -> dict:
        self._check_boundaries()
        documents = self.store.snapshot()
        backup = self._backup(documents, backup_id)
        self._restore_ownership(backup, documents)
        targets = self._backup_targets(backup)
        return {"id": backup_id, "targets": targets, "baseline": {**self.capture(targets), "backup": backup}}

    # //// 恢复备份前存档现状并关闭接管 [@x380kkm 2026-09-07] ////
    def restore(self, backup_id: str, baseline: dict) -> dict:
        self._check_boundaries()
        backup = self._backup(self.store.snapshot(), backup_id)
        targets = self._backup_targets(backup)
        return self._apply(targets, baseline, "restore", restoring_id=backup_id)
