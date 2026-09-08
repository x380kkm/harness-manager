# audience: internal
# # host-migration-api-tests
# 共享管理接口在隔离用户目录中执行迁移, 并核对宿主原文与恢复后的继续管理.

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import source_document
from harness_manager.host_storage import HostStorage
from harness_manager.service import Manager


# //// 通过 Agent 与面板共用接口验证宿主迁移交互 [@x380kkm 2026-09-08] ////
class HostMigrationApiTests(unittest.TestCase):
    # //// 建立具有原始规则和独立事件组的隔离用户目录 [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name) / "user"
        self.host = self.user / ".codex"
        self.host.mkdir(parents=True)
        self.source = self.host / "AGENTS.md"
        self.original = b"# Rules\nOriginal name rule.\n\nIndependent rule.\n"
        self.source.write_bytes(self.original)
        self.override = self.host / "AGENTS.override.md"
        self.hooks = self.host / "hooks.json"
        self.groups = [{"matcher": "Edit", "hooks": [{"type": "command", "command": name}]} for name in ("A", "B", "C")]
        self.hooks.write_text(json.dumps({"extra": "retained", "hooks": {"PreToolUse": self.groups}}), encoding="utf-8")
        self.manager = Manager(user_root=self.user)

    # //// 预览声明并通过公共保存接口写入内容库 [@x380kkm 2026-09-08] ////
    def register(self, document):
        preview = self.manager.invoke("document.preview", {"document": document})
        return self.manager.invoke("document.apply", {"plan": preview["plan"]})

    # //// 将明确的原文片段登记为可开关规则 [@x380kkm 2026-09-08] ////
    def register_rule(self):
        item = {"id": "rule:naming", "kind": "rule", "name": "Naming", "summary": "", "path": str(self.source),
                "scope": "user", "content": "Original name rule.", "line": 2, "details": {"endLine": 2}}
        self.register(source_document(item))
        return item["id"]

    # //// 使用当前卡片基线保存启用状态 [@x380kkm 2026-09-08] ////
    def configure(self, identity, state):
        detail = self.manager.invoke("card.describe", {"id": identity})
        return self.manager.invoke("card.configure", {"id": identity, "state": state, "baseline": detail["configBaseline"]})

    # //// 预览并恢复用户选中的宿主存档 [@x380kkm 2026-09-08] ////
    def restore(self, identity):
        preview = self.manager.invoke("host.preview_restore", {"id": identity})
        return self.manager.invoke("host.apply", {"plan_id": preview["planId"]})

    # //// 分次关闭与解除 Hook 卡片保持原始执行次序 [@x380kkm 2026-09-08] ////
    def test_hook_cards_preserve_sequential_migration_and_release(self):
        identities = []
        point = "hook.x380kkm/lifecycle"
        for name, group in zip(("a", "b"), self.groups):
            document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:hook/" + name,
                        "release": {"version": "local"}, "metadata": {"name": name.upper()},
                        "contributions": [{"id": name, "point": point, "contract": {"id": point, "range": "^1.0.0"},
                                           "payload": {"name": name.upper(), "event": "PreToolUse",
                                                       "matcher": group["matcher"], "handlers": group["hooks"]}}]}
            self.register(document)
            inventory = self.manager.invoke("card.inventory")
            identities.append(next(item["id"] for item in inventory["items"]
                                   if item.get("details", {}).get("sourceDeclaration") == document["id"] + "@local"))
            self.assertEqual(self.configure(identities[-1], "disabled")["hostSync"]["status"], "applied")
        self.assertEqual(json.loads(self.hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"], self.groups[2:])
        self.assertEqual(self.configure(identities[0], "inherit")["hostSync"]["status"], "applied")
        self.assertEqual(json.loads(self.hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"], [self.groups[0], self.groups[2]])
        self.assertEqual(self.configure(identities[1], "inherit")["hostSync"]["status"], "applied")
        self.assertEqual(json.loads(self.hooks.read_text(encoding="utf-8")), {"extra": "retained", "hooks": {"PreToolUse": self.groups}})
        self.assertNotIn("hooks", self.manager.host.storage("user").read_ownership())

    # //// 保存期间原文被外部修改则保留编辑并显示宿主阻止结果 [@x380kkm 2026-09-08] ////
    def test_saved_card_reports_source_conflict_without_masking_private_edit(self):
        identity = self.register_rule()
        commit = HostStorage._commit
        edited = self.original + b"External private instruction.\n"

        def change_source(storage, documents):
            commit(storage, documents)
            if any(item.get("status") == "pending" for item in documents):
                self.source.write_bytes(edited)

        with patch.object(HostStorage, "_commit", change_source):
            result = self.configure(identity, "disabled")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertEqual(self.source.read_bytes(), edited)
        self.assertFalse(self.override.exists())
        self.assertFalse(self.manager.invoke("host.status")["recoveryRequired"])
        preview = self.manager.invoke("host.preview")
        self.manager.invoke("host.apply", {"plan_id": preview["planId"]})
        self.assertIn(b"External private instruction.", self.override.read_bytes())
        self.assertNotIn(b"Original name rule.", self.override.read_bytes())

    # //// 中断输出恢复后仍能解除规则且保留首次存档与已有保护副本 [@x380kkm 2026-09-08] ////
    def test_interrupted_rule_can_be_restored_and_released_through_public_api(self):
        identity = self.register_rule()
        self.configure(identity, "disabled")
        initial = deepcopy(next(item for item in self.manager.host.storage("user").store.snapshot() if item["id"] == "initial"))
        self.restore("initial")
        status = self.manager.invoke("host.status")
        self.manager.invoke("host.set_enabled", {"enabled": True, "baseline": status["baseline"]})
        protection = deepcopy(next(item for item in self.manager.host.storage("user").store.snapshot() if item["id"] == "before-restore"))
        write = HostStorage._write_target

        def interrupt_output(storage, name, content, expected):
            write(storage, name, content, expected)
            raise SystemExit()

        with patch.object(HostStorage, "_write_target", interrupt_output):
            with self.assertRaises(SystemExit):
                self.configure(identity, "enabled")
        self.manager = Manager(user_root=self.user)
        self.assertTrue(self.manager.invoke("host.status")["recoveryRequired"])
        self.assertEqual(self.restore("initial")["backupId"], "interrupted")
        documents = self.manager.host.storage("user").store.snapshot()
        self.assertEqual(next(item for item in documents if item["id"] == "initial"), initial)
        self.assertEqual(next(item for item in documents if item["id"] == "before-restore"), protection)
        self.assertFalse(self.restore("interrupted")["enabled"])
        self.assertEqual(self.override.read_bytes(), self.original)
        status = self.manager.invoke("host.status")
        self.manager.invoke("host.set_enabled", {"enabled": True, "baseline": status["baseline"]})
        self.assertEqual(self.configure(identity, "inherit")["hostSync"]["status"], "applied")
        self.assertFalse(self.override.exists())
        self.assertEqual(self.source.read_bytes(), self.original)


# //// 执行公共接口的宿主迁移用例 [@x380kkm 2026-09-08] ////
if __name__ == "__main__":
    unittest.main()
