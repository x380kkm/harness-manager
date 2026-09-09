# audience: internal
# # host-control-tests
"""隔离宿主目录核对首次恢复点, 默认接管和关闭后的直接文件使用."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import source_document
from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError


# //// 在隔离的宿主中执行完整管理用例 [@x380kkm 2026-09-07] ////
class HostControlTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.codex = self.user / ".codex"
        self.codex.mkdir(parents=True)
        (self.codex / "AGENTS.md").write_text("# Original\nExisting guidance.\n", encoding="utf-8")
        (self.codex / "config.toml").write_text('# Kept comment\nmodel = "selected"\n', encoding="utf-8")
        (self.codex / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
        self.manager = Manager(user_root=self.user)
        self.item = {"id": "rule:theme", "kind": "rule", "name": "Theme", "summary": "A complete rule theme.",
                     "path": str(self.codex / "AGENTS.md"), "scope": "user", "content": "## Theme\nKeep complete context.\n"}
        self.document = source_document(self.item)
        self.manager.apply_document(self.manager.preview_document(self.document)["plan"])

    # //// 通过外部共用接口修改一项完整规则 [@x380kkm 2026-09-07] ////
    def configure(self, state):
        baseline = self.manager.cards.describe(self.item["id"])["configBaseline"]
        return self.manager.invoke("card.configure", {"id": self.item["id"], "state": state, "baseline": baseline})

    # //// 默认接管写出静态文件且关闭保留当前文件 [@x380kkm 2026-09-07] ////
    def test_initial_backup_and_native_files_survive_disabled_manager(self):
        original = {path.name: path.read_bytes() for path in self.codex.iterdir()}
        initialized = self.manager.invoke("host.initialize")
        self.assertTrue(initialized["enabled"])
        self.assertEqual(initialized["initialBackupId"], "initial")
        result = self.configure("enabled")
        self.assertEqual(result["hostSync"]["status"], "applied")
        target = self.codex / "AGENTS.override.md"
        applied = target.read_bytes()
        self.assertIn(b"Keep complete context", applied)
        status = self.manager.host.status()
        self.manager.invoke("host.set_enabled", {"enabled": False, "baseline": status["baseline"]})
        result = self.configure("disabled")
        self.assertEqual(result["hostSync"]["status"], "disabled")
        self.assertEqual(target.read_bytes(), applied)
        restarted = Manager(user_root=self.user)
        self.assertFalse(restarted.host.status()["enabled"])
        self.assertEqual(target.read_bytes(), applied)
        preview = restarted.host.preview_restore("initial")
        self.assertIn("AGENTS.override.md", [file["name"] for file in preview["files"]])
        restarted.host.apply(preview["planId"])
        self.assertFalse(target.exists())
        self.assertEqual({path.name: path.read_bytes() for path in self.codex.iterdir()}, original)
        self.assertFalse(restarted.host.status()["enabled"])
        self.assertEqual({backup["id"] for backup in restarted.host.status()["backups"]}, {"initial", "before-restore"})

    # //// 恢复目标重试失败后保持用户原文可跨普通应用恢复 [@x380kkm 2026-09-08] ////
    def test_restore_target_survives_retry_failure_and_regular_application(self):
        self.configure("enabled")
        config = self.codex / "config.toml"
        custom = b'model = "saved-choice"\n'
        config.write_bytes(custom)
        initial = self.manager.invoke("host.preview_restore", {"id": "initial"})
        self.manager.invoke("host.apply", {"plan_id": initial["planId"]})
        target_id = "before-restore"
        write = HostStorage._write_target

        # //// 对配置文件模拟写入占用并保留其他目标的真实写入 [@x380kkm 2026-09-08] ////
        def locked_config(storage, name, content, expected):
            if name == "config.toml":
                raise PermissionError("configuration is locked")
            return write(storage, name, content, expected)

        for _ in range(2):
            preview = self.manager.invoke("host.preview_restore", {"id": target_id})
            with patch.object(HostStorage, "_write_target", new=locked_config):
                with self.assertRaises(HostTransactionError) as caught:
                    self.manager.invoke("host.apply", {"plan_id": preview["planId"]})
            target_id = caught.exception.details["restoreTargetId"]
        self.manager.invoke("host.set_enabled", {"enabled": True})
        self.manager = Manager(user_root=self.user)
        target = self.manager.invoke("host.preview_restore", {"id": target_id})
        self.manager.invoke("host.apply", {"plan_id": target["planId"]})
        self.assertEqual(config.read_bytes(), custom)

    # //// 开启后发生归属冲突时返回已保存开关与应用诊断 [@x380kkm 2026-09-08] ////
    def test_enable_reports_saved_state_when_host_synchronization_conflicts(self):
        self.configure("enabled")
        self.manager.invoke("host.set_enabled", {"enabled": False})
        target = self.codex / "AGENTS.override.md"
        target.write_text("Private edited guidance.\n", encoding="utf-8")
        result = self.manager.invoke("host.set_enabled", {"enabled": True})
        self.assertTrue(result["enabled"])
        self.assertTrue(self.manager.host.status()["enabled"])
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertTrue(result["hostSync"]["diagnostics"])
        self.assertEqual(target.read_text(encoding="utf-8"), "Private edited guidance.\n")

    # //// 预览后内容变更使旧计划失效并保留文件 [@x380kkm 2026-09-07] ////
    def test_changed_catalog_rejects_stale_host_preview(self):
        self.manager.cards.configure(self.item["id"], "enabled")
        preview = self.manager.host.preview()
        changed = deepcopy(self.document)
        changed["contributions"][0]["payload"]["text"] = "Changed content."
        self.manager.apply_document(self.manager.preview_document(changed, self.document)["plan"])
        with self.assertRaises(StorageConflictError):
            self.manager.host.apply(preview["planId"])
        self.assertFalse((self.codex / "AGENTS.override.md").exists())

    # //// 已安装 Skill 的启停使用入口文件并保留配置原值 [@x380kkm 2026-09-08] ////
    def test_installed_skill_is_directly_configured_without_runtime_service(self):
        path = self.user / ".agents/skills/example/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: example\ndescription: Example method.\n---\nUse the source.\n", encoding="utf-8")
        item = next(item for item in self.manager.snapshot_cards()["items"] if item["kind"] == "skill")
        config = self.codex / "config.toml"
        original = config.read_bytes()
        result = self.manager.invoke("card.configure", {"id": item["id"], "state": "disabled"})
        self.assertEqual(result["hostSync"]["status"], "applied")
        text = (self.codex / "config.toml").read_text(encoding="utf-8")
        self.assertIn('# Kept comment\nmodel = "selected"', text)
        self.assertIn('path = "' + path.as_posix() + '"', text)
        self.assertIn("enabled = false", text)
        self.assertNotIn("mcp_servers", text)
        self.assertEqual(len(self.manager.host.status()["backups"]), 1)
        restarted = Manager(user_root=self.user)
        self.assertEqual(restarted.host.inspect()["status"], "unchanged")
        current = restarted.cards.describe(item["id"])
        removed = restarted.invoke("card.configure", {"id": item["id"], "state": "inherit", "baseline": current["configBaseline"]})
        self.assertEqual(removed["hostSync"]["status"], "applied")
        self.assertEqual(config.read_bytes().rstrip(), original.rstrip())

    # //// 独立 Hook 与模块引用均保留其他宿主处理项 [@x380kkm 2026-09-07] ////
    def test_hook_card_and_module_use_static_host_configuration(self):
        import json
        original = {"description": "Existing lifecycle settings", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "user-owned-hook"}]}]}}
        path = self.codex / "hooks.json"
        path.write_text(json.dumps(original), encoding="utf-8")
        point = "hook.x380kkm/lifecycle"
        document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:hook/check", "release": {"version": "local"},
                    "metadata": {"name": "Source check"}, "contributions": [{"id": "check", "point": point,
                      "contract": {"id": point, "range": "^1.0.0"}, "payload": {"name": "Source check", "event": "PreToolUse", "matcher": "Bash",
                      "handlers": [{"type": "command", "command": "declared-check"}]}}]}
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        card = next(item for item in self.manager.snapshot_cards()["items"] if item["kind"] == "hook" and item.get("details", {}).get("sourceDeclaration") == "plugin:hook/check@local")
        candidates = self.manager.modules.describe()["candidates"]
        self.assertTrue(next(choice for choice in candidates if choice["itemId"] == card["id"])["supported"])
        result = self.manager.invoke("card.configure", {"id": card["id"], "state": "enabled"})
        self.assertEqual(result["hostSync"]["status"], "applied")
        current = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(current["hooks"]["Stop"], original["hooks"]["Stop"])
        self.assertEqual(current["hooks"]["PreToolUse"][0]["matcher"], "Bash")
        baseline = self.manager.cards.describe(card["id"])["configBaseline"]
        result = self.manager.invoke("card.configure", {"id": card["id"], "state": "inherit", "baseline": baseline})
        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
