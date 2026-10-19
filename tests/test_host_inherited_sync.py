# audience: internal
# # host-inherited-sync-tests
# 临时用户和项目目录验证自动接管的范围, 继承配置与部分失败反馈.

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

from harness_manager.host_storage import HostStorage
from harness_manager.service import Manager


# //// 通过公开管理接口核对用户设置和项目宿主文件 [@x380kkm 2026-09-08] ////
class HostInheritedSyncTests(unittest.TestCase):
    # //// 创建用户来源并启用单个 Skill [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.project, self.library = [self.root / name for name in ("user", "project", "library")]
        for directory in (self.user, self.project, self.library):
            directory.mkdir()
        skill = self.library / "method" / "SKILL.md"
        skill.parent.mkdir()
        skill.write_text("---\nname: method\ndescription: Read source.\n---\n# Method\nRead symbols.\n", encoding="utf-8")
        self.manager = Manager(self.project, [self.library], user_root=self.user)
        document = self.manager.invoke("document.import", {"path": str(skill)})["document"]
        plan = self.manager.invoke("document.preview", {"document": document})["plan"]
        self.manager.invoke("document.apply", {"plan": plan})
        self.card_id = next(item["id"] for item in self.manager.invoke("card.inventory")["items"] if item["kind"] == "skill")
        self.configure_user("enabled")

    # //// 根据当前基线保存用户层启用状态 [@x380kkm 2026-09-08] ////
    def configure_user(self, state):
        baseline = self.manager.invoke("card.describe", {"id": self.card_id})["configBaseline"]
        return self.manager.invoke("card.configure", {"id": self.card_id, "state": state, "baseline": baseline})

    # //// 预览并应用指定管理器的项目宿主文件 [@x380kkm 2026-09-08] ////
    def apply_project(self, manager):
        preview = manager.invoke("host.preview", {"scope": "project-local"})
        self.assertIsNotNone(preview["planId"], preview["diagnostics"])
        manager.invoke("host.apply", {"plan_id": preview["planId"]})

    # //// 读取实际宿主配置中的 Skill 启用值 [@x380kkm 2026-09-08] ////
    def enabled_in(self, root):
        config = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
        return config["skills"]["config"][0]["enabled"]

    # //// 用户默认变化同步当前已接管项目的继承结果 [@x380kkm 2026-09-08] ////
    def test_user_change_updates_the_active_project(self):
        self.apply_project(self.manager)
        result = self.configure_user("disabled")["hostSync"]
        self.assertEqual(result["status"], "applied")
        self.assertEqual({scope: value["status"] for scope, value in result["scopes"].items()},
                         {"user": "applied", "project-local": "applied"})
        self.assertFalse(self.enabled_in(self.user))
        self.assertFalse(self.enabled_in(self.project))
        self.assertEqual(self.manager.invoke("host.preview", {"scope": "project-local"})["files"], [])

    # //// 用户重新开启接管时应用关闭期间的默认变化 [@x380kkm 2026-09-08] ////
    def test_enabling_user_control_updates_saved_project_inheritance(self):
        self.apply_project(self.manager)
        self.manager.invoke("host.set_enabled", {"enabled": False})
        result = self.configure_user("disabled")
        self.assertEqual(result["hostSync"]["status"], "disabled")
        self.assertTrue(self.enabled_in(self.user))
        self.assertTrue(self.enabled_in(self.project))
        baseline = self.manager.invoke("host.status")["baseline"]
        enabled = self.manager.invoke("host.set_enabled", {"enabled": True, "baseline": baseline})
        self.assertEqual(enabled["hostSync"]["status"], "applied")
        self.assertFalse(self.enabled_in(self.user))
        self.assertFalse(self.enabled_in(self.project))

    # //// 项目失败保留用户应用结果并支持再次同步 [@x380kkm 2026-09-08] ////
    def test_project_failure_reports_its_scope_and_can_be_retried(self):
        self.apply_project(self.manager)
        write = HostStorage._write_target

        # //// 对当前项目的配置文件模拟写入占用 [@x380kkm 2026-09-08] ////
        def locked_project(storage, name, content, expected):
            if storage.target_root == self.project and name == "config.toml":
                raise PermissionError("project configuration is locked")
            return write(storage, name, content, expected)

        with patch.object(HostStorage, "_write_target", new=locked_project):
            result = self.configure_user("disabled")["hostSync"]
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["scopes"]["user"]["status"], "applied")
        self.assertEqual(result["scopes"]["project-local"]["status"], "blocked")
        self.assertEqual({note["scope"] for note in result["diagnostics"]}, {"project-local"})
        self.assertFalse(self.enabled_in(self.user))
        self.assertTrue(self.enabled_in(self.project))
        retried = self.configure_user("disabled")["hostSync"]
        self.assertEqual(retried["status"], "applied")
        self.assertEqual(retried["scopes"]["user"]["status"], "unchanged")
        self.assertFalse(self.enabled_in(self.project))

    # //// 自动应用尊重当前项目的初始化和接管开关 [@x380kkm 2026-09-08] ////
    def test_automatic_sync_preserves_unselected_and_disabled_projects(self):
        self.assertFalse((self.project / ".codex").exists())
        self.assertFalse(self.manager.host.storage("project-local").store.directory.exists())
        other_project = self.root / "other-project"
        other_project.mkdir()
        other = Manager(other_project, [self.library], user_root=self.user)
        self.apply_project(other)
        self.apply_project(self.manager)
        self.manager.invoke("host.set_enabled", {"enabled": False, "scope": "project-local"})
        self.configure_user("disabled")
        self.assertFalse(self.enabled_in(self.user))
        self.assertTrue(self.enabled_in(self.project))
        self.assertTrue(self.enabled_in(other_project))

    # //// 显式宿主应用保持预览选择的写入范围 [@x380kkm 2026-09-08] ////
    def test_explicit_user_apply_preserves_project_files(self):
        self.apply_project(self.manager)
        baseline = self.manager.cards.describe(self.card_id)["configBaseline"]
        self.manager.cards.configure(self.card_id, "disabled", "user", baseline)
        preview = self.manager.invoke("host.preview")
        self.manager.invoke("host.apply", {"plan_id": preview["planId"]})
        self.assertFalse(self.enabled_in(self.user))
        self.assertTrue(self.enabled_in(self.project))

    # //// 重开项目时只读发现继承差异并保留已有确认预览 [@x380kkm 2026-09-08] ////
    def test_inspect_reopened_project_preserves_files_archives_and_previews(self):
        self.apply_project(self.manager)
        other_project = self.root / "other-project"
        other_project.mkdir()
        other = Manager(other_project, [self.library], user_root=self.user)
        self.apply_project(other)
        baseline = other.invoke("card.describe", {"id": self.card_id})["configBaseline"]
        other.invoke("card.configure", {"id": self.card_id, "state": "disabled", "baseline": baseline})
        self.manager = Manager(self.project, [self.library], user_root=self.user)
        storage = self.manager.host.storage("project-local")
        records = storage.store.snapshot()
        previews = [self.manager.host.preview("project-local") for _ in range(4)]
        saved = deepcopy(self.manager.host.previews)
        inspected = self.manager.host.inspect("project-local")
        self.assertEqual(inspected["status"], "pending")
        self.assertEqual([entry["name"] for entry in inspected["files"]], ["config.toml"])
        self.assertEqual(inspected["diagnostics"], [])
        self.assertTrue(self.enabled_in(self.project))
        self.assertFalse(self.enabled_in(other_project))
        self.assertEqual(storage.store.snapshot(), records)
        self.assertEqual(self.manager.host.previews, saved)
        self.manager.invoke("host.apply", {"plan_id": previews[0]["planId"]})
        self.assertFalse(self.enabled_in(self.project))
        self.assertEqual(self.manager.host.inspect("project-local")["status"], "unchanged")

    # //// 未初始化和关闭接管的项目保持文件原样并直接返回状态 [@x380kkm 2026-09-08] ////
    def test_inspect_inactive_project_skips_compilation(self):
        storage = self.manager.host.storage("project-local")
        with patch.object(self.manager.host, "compile", side_effect=AssertionError("inactive host compilation")):
            uninitialized = self.manager.host.inspect("project-local")
            self.assertEqual(uninitialized["status"], "uninitialized")
            self.assertEqual(uninitialized["files"], [])
            self.assertFalse(storage.store.directory.exists())
            self.assertFalse((self.project / ".codex").exists())
            self.manager.invoke("host.set_enabled", {"scope": "project-local", "enabled": False})
            records = storage.store.snapshot()
            disabled = self.manager.host.inspect("project-local")
            self.assertEqual(disabled["status"], "disabled")
            self.assertFalse(disabled["ownershipChanged"])
            self.assertEqual(storage.store.snapshot(), records)
        self.assertEqual(self.manager.host.previews, {})

    # //// 无法编译的现有宿主配置返回诊断并保留原文 [@x380kkm 2026-09-08] ////
    def test_inspect_reports_blocked_configuration_without_writing(self):
        self.apply_project(self.manager)
        config = self.project / ".codex" / "config.toml"
        malformed = b"[[skills.config]\n"
        config.write_bytes(malformed)
        storage = self.manager.host.storage("project-local")
        records = storage.store.snapshot()
        inspected = self.manager.host.inspect("project-local")
        self.assertEqual(inspected["status"], "blocked")
        self.assertTrue(inspected["diagnostics"])
        self.assertEqual(inspected["files"], [])
        self.assertEqual(config.read_bytes(), malformed)
        self.assertEqual(storage.store.snapshot(), records)
        self.assertEqual(self.manager.host.previews, {})

    # //// 未完成宿主事务在编译前返回恢复诊断 [@x380kkm 2026-09-08] ////
    def test_inspect_pending_recovery_skips_compilation(self):
        preview = self.manager.host.preview("project-local")
        with patch.object(HostStorage, "_write_target", side_effect=SystemExit()):
            with self.assertRaises(SystemExit):
                self.manager.invoke("host.apply", {"plan_id": preview["planId"]})
        storage = self.manager.host.storage("project-local")
        records = storage.store.snapshot()
        saved = deepcopy(self.manager.host.previews)
        with patch.object(self.manager.host, "compile", side_effect=AssertionError("pending recovery compilation")):
            inspected = self.manager.host.inspect("project-local")
        self.assertEqual(inspected["status"], "blocked")
        self.assertEqual([note["code"] for note in inspected["diagnostics"]], ["host_recovery_required"])
        self.assertEqual(inspected["files"], [])
        self.assertEqual(storage.store.snapshot(), records)
        self.assertEqual(self.manager.host.previews, saved)

    # //// 文件相同而字段归属待确认时仍返回待应用状态 [@x380kkm 2026-09-08] ////
    def test_inspect_reports_ownership_only_changes(self):
        config = self.project / ".codex" / "config.toml"
        config.parent.mkdir()
        native = self.manager.host.compile("project-local")["targets"]["config.toml"]
        config.write_bytes(native)
        self.manager.invoke("host.initialize", {"scope": "project-local"})
        storage = self.manager.host.storage("project-local")
        records = storage.store.snapshot()
        inspected = self.manager.host.inspect("project-local")
        self.assertEqual(inspected["status"], "pending")
        self.assertEqual(inspected["files"], [])
        self.assertTrue(inspected["ownershipChanged"])
        self.assertEqual(config.read_bytes(), native)
        self.assertEqual(storage.store.snapshot(), records)
        self.assertEqual(self.manager.host.previews, {})


# //// 执行继承配置的宿主同步测试 [@x380kkm 2026-09-08] ////
if __name__ == "__main__":
    unittest.main()
