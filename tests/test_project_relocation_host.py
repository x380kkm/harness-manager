# audience: internal
# # project-relocation-host-tests
"""已应用的项目在目录搬移后继续管理规则与 Skill, 并恢复首次使用前的宿主文件."""
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest

from harness_manager.card_subjects import source_document
from harness_manager.project_relocation import ProjectRelocation
from harness_manager.service import Manager


# //// 在真实宿主输出中验证位置重连和解除管理 [@x380kkm 2026-09-08] ////
class RelocatedHostTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.old, self.current = [self.root / name for name in ("user", "old", "current")]
        self.user.mkdir()
        self.old.mkdir()
        self.original = b"# Shell\nUse PowerShell.\n\n# Notes\nKeep these notes.\n"
        (self.old / "AGENTS.md").write_bytes(self.original)
        skill = self.old / "skills/method/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: method\ndescription: Project method.\n---\nRead symbols.\n", encoding="utf-8")
        manager = Manager(self.old, [self.old], user_root=self.user)
        rule = source_document({"id": "rule:shell", "name": "Shell", "kind": "rule", "summary": "Shell method.",
                                "content": "# Shell\nUse PowerShell.\n", "path": str(self.old / "AGENTS.md"), "scope": "project"})
        rule["contributions"][0]["payload"]["text"] = "# Shell\nUse pwsh 7.\n"
        imported = manager.import_file(str(skill))["document"]
        for document in (rule, imported):
            manager.apply_document(manager.preview_document(document, scope="project")["plan"])
        self.skill_id = next(item["id"] for item in manager.cards.inventory("project-local")["items"] if item["kind"] == "skill")
        for identity in ("rule:shell", self.skill_id):
            baseline = manager.cards.describe(identity, "project-local")["configBaseline"]
            manager.cards.configure(identity, "enabled", "project-local", baseline)
        preview = manager.host.preview("project-local")
        self.assertIsNotNone(preview["planId"], preview["diagnostics"])
        manager.host.apply(preview["planId"])
        self.old_host = manager.host.storage("project-local")
        self.saved_host = self.old_host.store.snapshot()
        self.assertTrue(self.old_host.read_ownership())
        self.assertIn("Use pwsh 7", (self.old / "AGENTS.override.md").read_text(encoding="utf-8"))
        self.old.rename(self.current)
        self.manager = Manager(self.current, [self.current], user_root=self.user)

    # //// 搬移后重新输出来源路径并解除规则和恢复最初配置 [@x380kkm 2026-09-08] ////
    def test_relocated_host_can_apply_release_rule_and_restore_initial_files(self):
        relocation = ProjectRelocation(self.manager.catalogs)
        relocation.apply(relocation.preview(str(self.old))["plan"])
        preview = self.manager.host.preview("project-local")
        self.assertIsNotNone(preview["planId"], preview["diagnostics"])
        self.manager.host.apply(preview["planId"])
        configuration = self.current / ".codex/config.toml"
        config = tomllib.loads(configuration.read_text(encoding="utf-8"))
        self.assertEqual(config["skills"]["config"][0]["path"], (self.current / "skills/method").as_posix())
        rendered = (self.current / "AGENTS.override.md").read_text(encoding="utf-8")
        self.assertIn("Use pwsh 7", rendered)
        self.assertIn("Keep these notes", rendered)
        baseline = self.manager.cards.describe("rule:shell", "project-local")["configBaseline"]
        self.manager.cards.configure("rule:shell", "inherit", "project-local", baseline)
        preview = self.manager.host.preview("project-local")
        self.assertIsNotNone(preview["planId"], preview["diagnostics"])
        self.manager.host.apply(preview["planId"])
        self.assertFalse((self.current / "AGENTS.override.md").exists())
        self.assertEqual((self.current / "AGENTS.md").read_bytes(), self.original)
        self.assertTrue(configuration.exists())
        restore = self.manager.host.preview_restore("initial", "project-local")
        self.manager.host.apply(restore["planId"])
        self.assertFalse(configuration.exists())
        self.assertFalse(self.manager.host.status("project-local")["enabled"])
        self.assertEqual((self.current / "AGENTS.md").read_bytes(), self.original)
        self.assertEqual(self.old_host.store.snapshot(), self.saved_host)


if __name__ == "__main__":
    unittest.main()
