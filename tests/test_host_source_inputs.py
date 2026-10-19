# audience: internal
# # host-source-inputs-tests
# 来源与宿主写入均使用隔离临时目录, Git 夹具沿用已隔离的本地命令入口.

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.content_plan import INSTRUCTION_POINT
from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.service import Manager
from harness_manager.sources import SourceReader
from harness_manager.usage import usage_document
from tests.test_source_boundaries import git


# //// 核对编译来源在宿主提交期间的读取边界 [@x380kkm 2026-09-08] ////
class HostSourceInputTests(unittest.TestCase):
    # //// 创建已授权来源与独立宿主目录 [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.library = self.root / "user", self.root / "library"
        self.host = self.user / ".codex"
        self.host.mkdir(parents=True)
        self.library.mkdir()
        self.source = self.library / "rules.md"
        self.source.write_text("Old rule body.\n", encoding="utf-8")
        self.override = self.host / "AGENTS.override.md"
        self.manager = Manager(read_roots=[self.library], user_root=self.user)

    # //// 保存文件型规则声明并取得宿主预览 [@x380kkm 2026-09-08] ////
    def preview(self, resolver="manager.source/path", constraint=None):
        source = {"resolver": {"id": resolver, "range": "^1.0.0"}, "locator": str(self.library)}
        if constraint is not None:
            source["constraint"] = constraint
        document = {
            "apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:rule/files",
            "release": {"version": "local"}, "metadata": {"name": "Rule"},
            "sources": [{"id": "files", "source": source}],
            "contributions": [{"id": "rule", "point": INSTRUCTION_POINT,
                               "contract": {"id": INSTRUCTION_POINT, "range": "^1.0.0"},
                               "source": "source:files", "payload": {"name": "Rule", "entry": "rules.md"}}],
        }
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        usage = usage_document(document, {"state": "enabled"}, None, "user")
        self.manager.apply_document(self.manager.preview_document(usage)["plan"])
        return self.manager.host.preview()

    # //// 待提交记录之后的来源变化阻止旧正文写入 [@x380kkm 2026-09-08] ////
    def test_source_change_after_pending_keeps_host_unchanged(self):
        preview = self.preview()
        commit = HostStorage._commit

        # //// 保存待提交记录后更新来源正文 [@x380kkm 2026-09-08] ////
        def change_source(storage, documents):
            commit(storage, documents)
            if any(item.get("status") == "pending" for item in documents):
                self.source.write_text("Current rule body.\n", encoding="utf-8")

        with patch.object(HostStorage, "_commit", change_source):
            with self.assertRaises(HostTransactionError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse(self.override.exists())
        self.assertEqual(self.source.read_text(encoding="utf-8"), "Current rule body.\n")
        self.assertEqual(self.manager.host.storage("user").read_ownership(), {})

    # //// 宿主写入后的来源变化回滚目标并保留新来源 [@x380kkm 2026-09-08] ////
    def test_source_change_after_write_rolls_back_target(self):
        preview = self.preview()
        write = HostStorage._write_target

        # //// 完成目标写入后更新来源正文 [@x380kkm 2026-09-08] ////
        def change_source(storage, name, content, expected):
            write(storage, name, content, expected)
            if content is not None:
                self.source.write_text("Current rule body.\n", encoding="utf-8")

        with patch.object(HostStorage, "_write_target", change_source):
            with self.assertRaises(HostTransactionError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse(self.override.exists())
        self.assertEqual(self.source.read_text(encoding="utf-8"), "Current rule body.\n")
        self.assertFalse(self.manager.host.status()["recoveryRequired"])

    # //// 固定 Git 提交沿对象读取且保留工作树独立编辑 [@x380kkm 2026-09-08] ////
    def test_fixed_git_commit_ignores_worktree_change_during_commit(self):
        git(self.library, "init")
        git(self.library, "add", "rules.md")
        git(self.library, "commit", "-m", "保存规则来源")
        revision = git(self.library, "rev-parse", "HEAD")
        preview = self.preview("manager.source/git", revision)
        commit = HostStorage._commit

        # //// 待提交记录保存后编辑 Git 工作树正文 [@x380kkm 2026-09-08] ////
        def change_worktree(storage, documents):
            commit(storage, documents)
            if any(item.get("status") == "pending" for item in documents):
                self.source.write_text("Private worktree edit.\n", encoding="utf-8")

        with patch.object(HostStorage, "_commit", change_worktree):
            result = self.manager.host.apply(preview["planId"])
        self.assertTrue(result["changed"])
        self.assertIn("Old rule body.", self.override.read_text(encoding="utf-8"))
        self.assertEqual(self.source.read_text(encoding="utf-8"), "Private worktree edit.\n")

    # //// Git 引用移动到另一提交时拒绝预览中的旧规则 [@x380kkm 2026-09-08] ////
    def test_git_reference_change_after_pending_blocks_write(self):
        git(self.library, "init")
        git(self.library, "add", "rules.md")
        git(self.library, "commit", "-m", "保存规则来源")
        preview = self.preview("manager.source/git", "HEAD")
        commit = HostStorage._commit

        # //// 保存待提交记录后提交另一份来源正文 [@x380kkm 2026-09-08] ////
        def move_reference(storage, documents):
            commit(storage, documents)
            if any(item.get("status") == "pending" for item in documents):
                self.source.write_text("Current committed rule.\n", encoding="utf-8")
                git(self.library, "add", "rules.md")
                git(self.library, "commit", "-m", "保存当前规则")

        with patch.object(HostStorage, "_commit", move_reference):
            with self.assertRaises(HostTransactionError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse(self.override.exists())

    # //// 提交期间授权改变时沿当前读取边界拒绝来源 [@x380kkm 2026-09-08] ////
    def test_revoked_source_access_after_pending_blocks_write(self):
        preview = self.preview()
        commit = HostStorage._commit

        # //// 保存待提交记录后切换当前读取授权 [@x380kkm 2026-09-08] ////
        def revoke_source(storage, documents):
            commit(storage, documents)
            if any(item.get("status") == "pending" for item in documents):
                self.manager.host.reader = SourceReader(self.user, [])

        with patch.object(HostStorage, "_commit", revoke_source):
            with self.assertRaises(HostTransactionError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse(self.override.exists())
