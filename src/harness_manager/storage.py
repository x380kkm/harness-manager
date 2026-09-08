# audience: internal
# # declaration-storage
# 工作目录由调用者明确提供, 受管目录和文件使用普通文件系统入口.
# identity 与 validate 是纯回调, validate 正常返回表示有效, ValueError 表示输入被拒绝.
# 协作写入者共同使用 catalog.lock, 锁文件保持原位, catalog.json 通过单文件替换提交.

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from .storage_errors import (
    StorageBoundaryError,
    StorageBusyError,
    StorageConflictError,
    StorageFormatError,
    StorageIOError,
    StorageValidationError,
)

Document = dict[str, Any]


# //// 复制 JSON 值并保留其数据类型 [@x380kkm 2026-09-06] ////
def _copy_json(value: Any) -> Any:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise StorageValidationError("JSON 对象的字段名必须是字符串.")
        return {_copy_json(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise StorageValidationError("JSON 文本必须可以使用 UTF-8 表示.") from error
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise StorageValidationError("声明与计划必须由有限的 JSON 值组成.")


# //// 按 JSON 类型与数值比较两个值 [@x380kkm 2026-09-06] ////
def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


# //// 生成供界面阅读的字段差异 [@x380kkm 2026-09-06] ////
def _field_changes(before: Document | None, after: Document | None, path: str = "") -> list[Document]:
    previous, current = before or {}, after or {}
    changes = []
    for key in sorted(previous.keys() | current.keys()):
        field = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
        if key not in previous:
            changes.append({"path": field, "operation": "add", "after": current[key]})
        elif key not in current:
            changes.append({"path": field, "operation": "remove", "before": previous[key]})
        elif isinstance(previous[key], dict) and isinstance(current[key], dict):
            changes.extend(_field_changes(previous[key], current[key], field))
        elif not _json_equal(previous[key], current[key]):
            changes.append({
                "path": field,
                "operation": "replace",
                "before": previous[key],
                "after": current[key],
            })
    return changes


# //// 拒绝目录中的重复字段 [@x380kkm 2026-09-06] ////
def _unique_object(pairs: list[tuple[str, Any]]) -> Document:
    document = {}
    for key, value in pairs:
        if key in document:
            raise StorageFormatError(f"目录包含重复字段: {key}.")
        document[key] = value
    return document


# //// 拒绝目录中的非有限数字 [@x380kkm 2026-09-06] ////
def _reject_constant(value: str) -> None:
    raise StorageFormatError(f"目录包含非有限数字: {value}.")


# //// 取得平台文件锁并返回释放动作 [@x380kkm 2026-09-06] ////
def _acquire_lock(descriptor: int) -> Callable[[], None]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return lambda: msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lambda: fcntl.flock(descriptor, fcntl.LOCK_UN)


# //// 管理单个工作目录中的声明目录 [@x380kkm 2026-09-06] ////
class Store:
    # //// 固定工作目录与声明校验入口 [@x380kkm 2026-09-06] ////
    def __init__(
        self,
        workspace: Path,
        identity: Callable[[Document], str],
        validate: Callable[[Document], None],
        *,
        catalog_directory: Path | None = None,
    ) -> None:
        try:
            self.workspace = Path(workspace).resolve(strict=True)
        except OSError as error:
            raise StorageBoundaryError("工作目录必须是已经存在的目录.") from error
        if not self.workspace.is_dir():
            raise StorageBoundaryError("工作目录必须是已经存在的目录.")
        directory = Path(catalog_directory) if catalog_directory is not None else Path(".harness")
        self.directory = directory if directory.is_absolute() else self.workspace / directory
        if ".." in self.directory.parts or self.directory == self.workspace or not self.directory.is_relative_to(self.workspace):
            raise StorageBoundaryError("声明目录需要位于工作目录内部.")
        self.catalog = self.directory / "catalog.json"
        self.lock = self.directory / "catalog.lock"
        self.identity = identity
        self.validate = validate

    # //// 返回当前目录内容并保持文件系统只读 [@x380kkm 2026-09-06] ////
    def snapshot(self) -> list[Document]:
        try:
            return self._read_catalog()
        except OSError as error:
            raise StorageIOError(f"读取目录失败: {error}.") from error

    # //// 核对受管路径并创建声明目录 [@x380kkm 2026-09-07] ////
    def ensure_directory(self) -> None:
        self._check_location()
        self._create_directory()
        self._check_location()

    # //// 预览新增或替换一个声明 [@x380kkm 2026-09-06] ////
    def preview_put(self, document: Document, baseline: Document | None = None) -> Document:
        after, document_id = self._validate_document(document)
        before = self._validate_baseline(document_id, baseline)
        self._check_baseline(self.snapshot(), document_id, before)
        return self._plan("put", document_id, before, after)

    # //// 预览移除一个已登记声明 [@x380kkm 2026-09-06] ////
    def preview_remove(self, identity: str, baseline: Document) -> Document:
        if not isinstance(identity, str) or not identity or baseline is None:
            raise StorageValidationError("移除计划需要对象身份及原始声明.")
        before = self._validate_baseline(identity, baseline)
        self._check_baseline(self.snapshot(), identity, before)
        return self._plan("remove", identity, before, None)

    # //// 在协作锁内重新核对并提交单个对象变化 [@x380kkm 2026-09-06] ////
    def apply(self, plan: Document) -> Document:
        return self.apply_many([plan])[0]

    # //// 在同一目录提交相互依赖的声明变化 [@x380kkm 2026-09-06] ////
    def apply_many(self, plans: list[Document], *, verify_current: Callable[[list[Document]], None] | None = None) -> list[Document]:
        if not isinstance(plans, list) or not plans:
            raise StorageValidationError("联合提交需要非空计划列表.")
        checked = [self._validate_plan(plan) for plan in plans]
        if len({plan["id"] for plan in checked}) != len(checked):
            raise StorageValidationError("联合提交中的每项声明身份需要唯一.")
        try:
            with self._locked_catalog():
                documents = self._read_catalog()
                for plan in checked:
                    self._check_baseline(documents, plan["id"], plan["before"])
                if verify_current is not None:
                    verify_current(documents)
                results = []
                for plan in checked:
                    after = plan["after"]
                    changed = not _json_equal(plan["before"], after)
                    if changed:
                        documents = self._replace_document(documents, plan["id"], after)
                    results.append({"operation": plan["operation"], "id": plan["id"], "changed": changed, "document": after})
                if any(result["changed"] for result in results):
                    self._write_catalog(documents)
                return results
        except OSError as error:
            raise StorageIOError(f"提交目录失败: {error}.") from error

    # //// 校验声明并提取稳定身份 [@x380kkm 2026-09-06] ////
    def _validate_document(self, value: Any) -> tuple[Document, str]:
        document = _copy_json(value)
        if not isinstance(document, dict):
            raise StorageValidationError("每项声明必须是 JSON 对象.")
        try:
            self.validate(document)
        except ValueError as error:
            raise StorageValidationError(str(error)) from error
        return document, self._document_identity(document)

    # //// 核对身份回调的结果 [@x380kkm 2026-09-06] ////
    def _document_identity(self, document: Document) -> str:
        try:
            document_id = self.identity(document)
        except ValueError as error:
            raise StorageValidationError(str(error)) from error
        if not isinstance(document_id, str) or not document_id:
            raise StorageValidationError("声明身份必须是非空字符串.")
        return document_id

    # //// 核对原始声明与计划目标的身份 [@x380kkm 2026-09-06] ////
    def _validate_baseline(self, document_id: str, baseline: Document | None) -> Document | None:
        if baseline is None:
            return None
        document, baseline_id = self._validate_document(baseline)
        if baseline_id != document_id:
            raise StorageValidationError("原始声明的身份与计划目标不同; 身份变化需要新增声明.")
        return document

    # //// 构造可序列化的对象变更计划 [@x380kkm 2026-09-06] ////
    def _plan(
        self, operation: str, document_id: str, before: Document | None, after: Document | None
    ) -> Document:
        return {
            "operation": operation,
            "id": document_id,
            "before": before,
            "after": after,
            "changes": _field_changes(before, after),
        }

    # //// 核对计划形状与全部声明内容 [@x380kkm 2026-09-06] ////
    def _validate_plan(self, value: Any) -> Document:
        plan = _copy_json(value)
        if not isinstance(plan, dict) or plan.keys() != {"operation", "id", "before", "after", "changes"}:
            raise StorageValidationError("计划必须包含 operation, id, before, after 与 changes.")
        if plan["operation"] == "put":
            after, document_id = self._validate_document(plan["after"])
        elif plan["operation"] == "remove" and plan["before"] is not None and plan["after"] is None:
            after, document_id = None, plan["id"]
        else:
            raise StorageValidationError("计划操作与前后声明不匹配.")
        if not isinstance(document_id, str) or not document_id or plan["id"] != document_id:
            raise StorageValidationError("计划身份与目标声明不同.")
        before = self._validate_baseline(document_id, plan["before"])
        expected = self._plan(plan["operation"], document_id, before, after)
        if not _json_equal(plan["changes"], expected["changes"]):
            raise StorageValidationError("计划字段差异与前后声明不同.")
        return expected

    # //// 按目标对象检查当前声明基线 [@x380kkm 2026-09-06] ////
    def _check_baseline(self, documents: list[Document], document_id: str, before: Document | None) -> None:
        current = next(
            (document for document in documents if self._document_identity(document) == document_id),
            None,
        )
        if not _json_equal(current, before):
            raise StorageConflictError(document_id)

    # //// 替换目标声明并保留其他对象及其顺序 [@x380kkm 2026-09-06] ////
    def _replace_document(
        self, documents: list[Document], document_id: str, after: Document | None
    ) -> list[Document]:
        updated = []
        found = False
        for document in documents:
            if self._document_identity(document) == document_id:
                found = True
                if after is not None:
                    updated.append(after)
            else:
                updated.append(document)
        if not found and after is not None:
            updated.append(after)
        return updated

    # //// 核对受管路径的普通文件类型与范围 [@x380kkm 2026-09-06] ////
    def _check_path(self, path: Path, expected: Callable[[int], bool]) -> None:
        try:
            details = path.lstat()
        except FileNotFoundError:
            return
        reparse = getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(details.st_mode) or reparse or not path.resolve().is_relative_to(self.workspace):
            raise StorageBoundaryError(f"受管路径必须位于工作目录中的普通入口: {path}.")
        if not expected(details.st_mode):
            raise StorageBoundaryError(f"受管路径类型不匹配: {path}.")

    # //// 核对工作目录和声明目录的位置 [@x380kkm 2026-09-06] ////
    def _check_location(self) -> None:
        if self.workspace.resolve() != self.workspace or not self.workspace.is_dir():
            raise StorageBoundaryError("工作目录的位置或类型已经变化.")
        for directory in self._directory_chain():
            self._check_path(directory, stat.S_ISDIR)
        self._check_path(self.catalog, stat.S_ISREG)

    # //// 按父子顺序列出受管目录的各级入口 [@x380kkm 2026-09-07] ////
    def _directory_chain(self) -> list[Path]:
        current, directories = self.workspace, []
        for part in self.directory.relative_to(self.workspace).parts:
            current = current / part
            directories.append(current)
        return directories

    # //// 逐级核对并创建声明目录 [@x380kkm 2026-09-07] ////
    def _create_directory(self) -> None:
        for directory in self._directory_chain():
            self._check_location()
            directory.mkdir(exist_ok=True)
            self._check_path(directory, stat.S_ISDIR)

    # //// 读取唯一的声明列表并拒绝目录歧义 [@x380kkm 2026-09-06] ////
    def _read_catalog(self) -> list[Document]:
        self._check_location()
        try:
            content = self.catalog.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except UnicodeError as error:
            raise StorageFormatError(f"目录必须使用 UTF-8 编码: {error}.") from error
        try:
            catalog = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except ValueError as error:
            raise StorageFormatError(f"目录 JSON 无法读取: {error}.") from error
        if not isinstance(catalog, dict) or catalog.keys() != {"documents"}:
            raise StorageFormatError("目录必须只包含 documents 数组.")
        if not isinstance(catalog["documents"], list):
            raise StorageFormatError("目录必须只包含 documents 数组.")
        documents, identities = [], set()
        for item in catalog["documents"]:
            document, document_id = self._validate_document(item)
            if document_id in identities:
                raise StorageFormatError(f"目录包含重复声明身份: {document_id}.")
            identities.add(document_id)
            documents.append(document)
        return documents

    # //// 获取非阻塞的协作文件锁 [@x380kkm 2026-09-06] ////
    @contextmanager
    def _locked_catalog(self) -> Iterator[None]:
        self.ensure_directory()
        self._check_path(self.lock, stat.S_ISREG)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock, flags, 0o600)
        try:
            self._check_path(self.lock, stat.S_ISREG)
            try:
                release = _acquire_lock(descriptor)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise StorageBusyError("声明目录正在由另一个写入者使用, 可以重试.") from error
                raise
            try:
                yield
            finally:
                release()
        finally:
            os.close(descriptor)

    # //// 将完整目录写入临时文件后原子提交 [@x380kkm 2026-09-06] ////
    def _write_catalog(self, documents: list[Document]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                json.dump({"documents": documents}, output, ensure_ascii=False, allow_nan=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            self._check_location()
            os.replace(temporary, self.catalog)
        finally:
            temporary.unlink(missing_ok=True)
