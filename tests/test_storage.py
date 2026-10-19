# audience: internal
# # storage-tests
# 测试使用独立临时目录和真实平台文件锁, 检查目标对象并发与文件提交边界.

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.storage import Store
from harness_manager.storage_errors import (
    StorageBoundaryError,
    StorageBusyError,
    StorageConflictError,
    StorageFormatError,
    StorageIOError,
    StorageValidationError,
)


# //// 为测试声明提取稳定身份 [@x380kkm 2026-09-06] ////
def identity(document: dict) -> str:
    return document.get("id", document.get("point", ""))


# //// 拒绝测试中明确无效的声明 [@x380kkm 2026-09-06] ////
def validate(document: dict) -> None:
    if document.get("invalid"):
        raise ValueError("声明被校验器拒绝.")


# //// 检查声明存储的并发与路径边界 [@x380kkm 2026-09-06] ////
class StoreTests(unittest.TestCase):
    # //// 创建独立工作目录 [@x380kkm 2026-09-06] ////
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.store = Store(self.workspace, identity, validate)

    # //// 写入测试使用的原始目录内容 [@x380kkm 2026-09-06] ////
    def write_catalog(self, text: str):
        self.store.directory.mkdir(exist_ok=True)
        self.store.catalog.write_text(text, encoding="utf-8")

    # //// 读取与预览保持工作目录原样 [@x380kkm 2026-09-06] ////
    def test_read_and_preview_do_not_create_files(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.store.snapshot(), [])
            plan = self.store.preview_put({"id": "alpha", "label": "中文"})
        self.assertEqual(plan["changes"][1]["after"], "中文")
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(output.getvalue(), "")

    # //// 嵌套目录在提交时逐级创建且维持工作目录边界 [@x380kkm 2026-09-07] ////
    def test_nested_catalog_creates_only_its_managed_directory(self):
        store = Store(self.workspace, identity, validate, catalog_directory=Path(".harness/projects/example"))
        plan = store.preview_put({"id": "alpha"})
        self.assertEqual(list(self.workspace.iterdir()), [])
        store.apply(plan)
        self.assertEqual(store.snapshot(), [{"id": "alpha"}])
        self.assertEqual(store.workspace, self.workspace)
        self.assertFalse((self.workspace / ".harness/catalog.json").exists())

    # //// 自定义声明目录越界与中间普通文件阻止写入 [@x380kkm 2026-09-07] ////
    def test_nested_catalog_rejects_outside_and_file_ancestors(self):
        for directory in (self.workspace.parent, Path("../outside"), self.workspace):
            with self.assertRaises(StorageBoundaryError):
                Store(self.workspace, identity, validate, catalog_directory=directory)
        store = Store(self.workspace, identity, validate, catalog_directory=Path(".harness/projects/example"))
        plan = store.preview_put({"id": "alpha"})
        (self.workspace / ".harness").mkdir()
        ancestor = self.workspace / ".harness/projects"
        ancestor.write_text("原有文件", encoding="utf-8")
        with self.assertRaises(StorageBoundaryError):
            store.snapshot()
        with self.assertRaises(StorageBoundaryError):
            store.apply(plan)
        self.assertEqual(ancestor.read_text(encoding="utf-8"), "原有文件")

    # //// 同一对象的陈旧基线阻止覆盖 [@x380kkm 2026-09-06] ////
    def test_same_object_conflict_preserves_current_catalog(self):
        original = {"id": "alpha", "value": 1}
        self.store.apply(self.store.preview_put(original))
        stale = self.store.preview_put({"id": "alpha", "value": 2}, original)
        self.store.apply(self.store.preview_put({"id": "alpha", "value": 3}, original))
        current = self.store.catalog.read_bytes()
        with self.assertRaises(StorageConflictError):
            self.store.apply(stale)
        self.assertEqual(self.store.catalog.read_bytes(), current)

    # //// 不同对象的并发修改共同保留 [@x380kkm 2026-09-06] ////
    def test_different_object_changes_merge(self):
        first = self.store.preview_put({"id": "alpha", "value": 1})
        second_store = Store(self.workspace, identity, validate)
        second = second_store.preview_put({"id": "beta", "value": 2})
        second_store.apply(second)
        self.store.apply(first)
        self.assertEqual(self.store.snapshot(), [{"id": "beta", "value": 2}, {"id": "alpha", "value": 1}])

    # //// 注入身份支持多个发布版本并拒绝基线重命名 [@x380kkm 2026-09-06] ////
    def test_injected_identity_keeps_releases_independent(self):
        versioned_identity = lambda document: f"{document['id']}@{document['release']['version']}"
        store = Store(self.workspace, versioned_identity, validate)
        original = {"kind": "Plugin", "id": "plugin:example", "release": {"version": "1.0.0"}}
        newer = {"kind": "Plugin", "id": "plugin:example", "release": {"version": "2.0.0"}}
        store.apply(store.preview_put(original))
        with self.assertRaisesRegex(StorageValidationError, "身份变化需要新增声明"):
            store.preview_put(newer, original)
        result = store.apply(store.preview_put(newer))
        self.assertEqual(result["id"], "plugin:example@2.0.0")
        self.assertEqual(store.snapshot(), [original, newer])

    # //// JSON 基线忽略对象字段顺序并区分布尔值与数字 [@x380kkm 2026-09-06] ////
    def test_baseline_uses_json_semantics(self):
        original = {"id": "alpha", "value": 1}
        self.store.apply(self.store.preview_put(original))
        plan = self.store.preview_put({"id": "alpha", "value": 2}, {"value": 1.0, "id": "alpha"})
        with self.assertRaises(StorageConflictError):
            self.store.preview_put(original, {"id": "alpha", "value": True})
        self.store.apply(plan)

    # //// 非阻塞文件锁报告可重试错误 [@x380kkm 2026-09-06] ////
    def test_busy_lock_does_not_write_catalog(self):
        plan = self.store.preview_put({"id": "alpha"})
        second_store = Store(self.workspace, identity, validate)
        with self.store._locked_catalog():
            with self.assertRaises(StorageBusyError) as caught:
                second_store.apply(plan)
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(self.store.catalog.exists())
        self.store.apply(plan)

    # //// 回调拒绝与计划差异篡改保持原文件 [@x380kkm 2026-09-06] ////
    def test_validation_errors_preserve_catalog(self):
        original = {"id": "alpha", "value": 1}
        self.store.apply(self.store.preview_put(original))
        before = self.store.catalog.read_bytes()
        with self.assertRaises(StorageValidationError):
            self.store.preview_put({"id": "alpha", "invalid": True}, original)
        plan = self.store.preview_put({"id": "alpha", "value": 2}, original)
        plan["after"]["value"] = 9
        with self.assertRaises(StorageValidationError):
            self.store.apply(plan)
        self.assertEqual(self.store.catalog.read_bytes(), before)

    # //// 原子替换失败保持原文件并清除临时文件 [@x380kkm 2026-09-06] ////
    def test_failed_replace_preserves_catalog(self):
        original = {"id": "alpha", "value": 1}
        self.store.apply(self.store.preview_put(original))
        before = self.store.catalog.read_bytes()
        plan = self.store.preview_put({"id": "alpha", "value": 2}, original)
        with patch("harness_manager.storage.os.replace", side_effect=OSError("替换失败")):
            with self.assertRaises(StorageIOError):
                self.store.apply(plan)
        self.assertEqual(self.store.catalog.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob(".catalog-*.json")), [])

    # //// 目录格式歧义保持原始内容 [@x380kkm 2026-09-06] ////
    def test_invalid_catalog_is_preserved(self):
        contents = [
            '{"documents": [], "unknown": true}',
            '{"documents": [{"id": "alpha"}, {"id": "alpha"}]}',
            '{"documents": [], "documents": []}',
            '{"documents":',
        ]
        for content in contents:
            with self.subTest(content=content):
                self.write_catalog(content)
                with self.assertRaises(StorageFormatError):
                    self.store.snapshot()
                self.assertEqual(self.store.catalog.read_text(encoding="utf-8"), content)

    # //// 应用时重新读取目录并拒绝新出现的格式错误 [@x380kkm 2026-09-06] ////
    def test_apply_rechecks_catalog_format(self):
        plan = self.store.preview_put({"id": "alpha"})
        content = '{"documents": [], "external": "keep"}'
        self.write_catalog(content)
        with self.assertRaises(StorageFormatError):
            self.store.apply(plan)
        self.assertEqual(self.store.catalog.read_text(encoding="utf-8"), content)

    # //// 非 UTF-8 目录返回格式错误并保持原始字节 [@x380kkm 2026-09-06] ////
    def test_invalid_encoding_is_preserved(self):
        self.store.directory.mkdir()
        content = b'{"documents": [], "text": "\xff"}'
        self.store.catalog.write_bytes(content)
        with self.assertRaises(StorageFormatError):
            self.store.snapshot()
        self.assertEqual(self.store.catalog.read_bytes(), content)

    # //// 移除只改变登记列表并保留来源文件 [@x380kkm 2026-09-06] ////
    def test_remove_preserves_source_and_other_documents(self):
        source = self.workspace / "SKILL.md"
        source.write_text("来源正文", encoding="utf-8")
        original = {"id": "alpha", "source": str(source)}
        self.store.apply(self.store.preview_put(original))
        remove = self.store.preview_remove("alpha", original)
        self.store.apply(self.store.preview_put({"kind": "PointContract", "point": "example/tool"}))
        result = self.store.apply(remove)
        self.assertIsNone(result["document"])
        self.assertEqual(source.read_text(encoding="utf-8"), "来源正文")
        self.assertEqual(self.store.snapshot(), [{"kind": "PointContract", "point": "example/tool"}])

    # //// 受管目录越界链接保持外部文件原样 [@x380kkm 2026-09-06] ////
    def test_managed_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            destination = Path(outside)
            external = destination / "catalog.json"
            external.write_text('{"documents": []}', encoding="utf-8")
            try:
                self.store.directory.symlink_to(destination, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"当前宿主无法创建符号链接: {error}")
            with self.assertRaises(StorageBoundaryError):
                self.store.snapshot()
            self.assertEqual(external.read_text(encoding="utf-8"), '{"documents": []}')

    # //// 目录文件越界链接阻止计划提交 [@x380kkm 2026-09-06] ////
    def test_catalog_symlink_is_rejected_during_apply(self):
        plan = self.store.preview_put({"id": "alpha"})
        self.store.directory.mkdir()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "catalog.json"
            external.write_text('{"documents": []}', encoding="utf-8")
            try:
                self.store.catalog.symlink_to(external)
            except OSError as error:
                self.skipTest(f"当前宿主无法创建符号链接: {error}")
            with self.assertRaises(StorageBoundaryError):
                self.store.apply(plan)
            self.assertEqual(external.read_text(encoding="utf-8"), '{"documents": []}')

    # //// Windows 目录连接阻止工作目录外的写入 [@x380kkm 2026-09-06] ////
    @unittest.skipUnless(os.name == "nt", "Windows 目录连接使用原生重解析点.")
    def test_windows_junction_is_rejected(self):
        cases = ((".harness", ".harness"), (".harness/projects/example", ".harness/projects"))
        for directory, redirected in cases:
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as outside:
                store = Store(self.workspace, identity, validate, catalog_directory=Path(directory))
                plan = store.preview_put({"id": "alpha"})
                external = Path(outside) / "catalog.json"
                external.write_text('{"documents": []}', encoding="utf-8")
                junction = self.workspace / redirected
                junction.parent.mkdir(parents=True, exist_ok=True)
                source_path = str(junction).replace("'", "''")
                target_path = outside.replace("'", "''")
                command = (
                    "$ErrorActionPreference = 'Stop'\n"
                    f"New-Item -ItemType Junction -Path '{source_path}' -Target '{target_path}' | Out-Null"
                )
                subprocess.run(["pwsh", "-Command", command], check=True, capture_output=True,
                               text=True, encoding="utf-8")
                try:
                    with self.assertRaises(StorageBoundaryError):
                        store.snapshot()
                    with self.assertRaises(StorageBoundaryError):
                        store.apply(plan)
                    self.assertEqual(external.read_text(encoding="utf-8"), '{"documents": []}')
                    self.assertFalse((Path(outside) / "example").exists())
                finally:
                    junction.rmdir()


# //// 执行声明存储测试 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
