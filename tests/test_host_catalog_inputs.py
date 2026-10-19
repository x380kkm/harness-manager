# audience: internal
# # host-catalog-input-tests
"""隔离宿主事务核对内联声明和使用绑定的并发变化, 并保留外部提交的目录内容."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError
from harness_manager.usage import usage_document


# //// 在临时用户目录中核对声明与宿主提交的一致性 [@x380kkm 2026-09-10] ////
class HostCatalogInputTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name) / "user"
        self.codex = self.user / ".codex"
        self.codex.mkdir(parents=True)
        self.target = self.codex / "AGENTS.override.md"
        self.manager = Manager(user_root=self.user)
        self.document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:rule/example",
                         "release": {"version": "local"}, "contributions": [{"id": "rule",
                             "point": "context.x380kkm/instruction",
                             "contract": {"id": "context.x380kkm/instruction", "range": "^1.0.0"},
                             "payload": {"text": "Queued rule."}}]}
        self.binding = usage_document(self.document, {"state": "enabled"}, None, "user")
        for document in (self.document, self.binding):
            self.manager.apply_document(self.manager.preview_document(document)["plan"])

    # //// 在另一目录客户端中提交真实声明更新 [@x380kkm 2026-09-10] ////
    def change_document(self, before, after):
        writer = Manager(user_root=self.user)
        writer.apply_document(writer.preview_document(after, before)["plan"])

    # //// 事务记录落盘后的声明变化阻止旧正文写入 [@x380kkm 2026-09-10] ////
    def test_inline_change_after_pending_preserves_host(self):
        preview = self.manager.host.preview()
        updated = deepcopy(self.document)
        updated["contributions"][0]["payload"]["text"] = "Concurrent rule."
        commit = HostStorage._commit
        changed = []

        def mutate(storage, documents):
            commit(storage, documents)
            if not changed and any(record.get("status") == "pending" for record in documents):
                self.change_document(self.document, updated)
                changed.append(True)

        with patch.object(HostStorage, "_commit", mutate):
            with self.assertRaises(HostTransactionError) as raised:
                self.manager.host.apply(preview["planId"])
        self.assertIsInstance(raised.exception.__cause__, StorageConflictError)
        self.assertFalse(self.target.exists())
        self.assertIn(updated, self.manager.store.snapshot())

    # //// 宿主写入后的绑定变化回滚旧正文并保留外部选择 [@x380kkm 2026-09-10] ////
    def test_binding_change_after_write_rolls_back_host(self):
        preview = self.manager.host.preview()
        updated = {**self.binding, "enabled": False}
        write = HostStorage._write_target
        changed = []

        def mutate(storage, name, content, expected):
            write(storage, name, content, expected)
            if name == "AGENTS.override.md" and not changed:
                self.change_document(self.binding, updated)
                changed.append(True)

        with patch.object(HostStorage, "_write_target", mutate):
            with self.assertRaises(HostTransactionError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse(self.target.exists())
        self.assertIn(updated, self.manager.store.snapshot())
        self.assertFalse(self.manager.host.status()["recoveryRequired"])

    # //// 编译使用固定声明后拒绝期间新增的有效绑定 [@x380kkm 2026-09-10] ////
    def test_new_binding_during_compilation_rejects_preview(self):
        from harness_manager.host_control import compile_host
        added = {**self.binding, "id": "binding:other", "enabled": False}

        def mutate(*args, **kwargs):
            result = compile_host(*args, **kwargs)
            self.change_document(None, added)
            return result

        with patch("harness_manager.host_control.compile_host", mutate):
            with self.assertRaises(StorageConflictError):
                self.manager.host.preview()
        self.assertFalse(self.target.exists())

    # //// 用户宿主预览保持与项目私有目录的独立性 [@x380kkm 2026-09-10] ////
    def test_user_preview_ignores_project_only_changes(self):
        project = self.user.parent / "project"
        project.mkdir()
        manager = Manager(project, user_root=self.user)
        preview = manager.host.preview()
        manager.apply_document(manager.preview_document(deepcopy(self.document), scope="project-local")["plan"])
        self.assertTrue(manager.host.apply(preview["planId"])["changed"])
        self.assertIn("Queued rule.", self.target.read_text(encoding="utf-8"))
