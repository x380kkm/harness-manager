# audience: internal
# # project-location-tests
"""隔离目录验证项目内外路径、相对范围和共享原文位置的归属."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.catalogs import external_project_locations, portable_project_document, resolve_document
from harness_manager.project_relocation import ProjectRelocation, RelocationError, relocate_document
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.usage import usage_document


# //// 验证具有明确路径语义的声明字段 [@x380kkm 2026-09-08] ////
class ProjectLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.user = self.root / "user"
        self.user.mkdir()
        self.document = {
            "apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:locations", "release": {"version": "local"},
            "metadata": {"name": "Locations"},
            "sources": [{"id": "local", "source": {"resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": "../outside"}}],
            "contributions": [{"id": "rule", "point": "context.x380kkm/instruction", "contract": {"id": "context.x380kkm/instruction", "range": "^1.0.0"},
                               "payload": {"text": "Use the declared convention.", "path": str(self.project / "literal")},
                               "scope": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"path": "../outside"}}}],
            "extensions": [{"contract": {"id": "manager.card/source", "range": "^1.0.0"},
                            "payload": {"path": "../outside/AGENTS.md"}}],
        }

    # //// 外部相对来源、范围和原文位置保留明确诊断 [@x380kkm 2026-09-08] ////
    def test_relative_external_locations_are_reported(self):
        result = external_project_locations([self.document], self.project)
        self.assertEqual(result["source"], [str(self.root / "outside")])
        self.assertEqual(result["scope"], [str(self.root / "outside")])
        self.assertEqual(result["receipt"], [str(self.root / "outside/AGENTS.md")])

    # //// 相对化只转换项目内字段并保留任意正文中的路径 [@x380kkm 2026-09-08] ////
    def test_portable_documents_preserve_external_paths_and_payload_text(self):
        document = deepcopy(self.document)
        document["sources"][0]["source"]["locator"] = str(self.project / "../outside")
        document["extensions"][0]["payload"]["path"] = str(self.project / "AGENTS.md")
        updated = portable_project_document(document, self.project)
        self.assertEqual(updated["sources"][0]["source"]["locator"], document["sources"][0]["source"]["locator"])
        self.assertEqual(updated["extensions"][0]["payload"]["path"], "AGENTS.md")
        self.assertEqual(updated["contributions"][0]["payload"], document["contributions"][0]["payload"])
        moved = relocate_document(document, self.project, self.root / "moved", portable=True)
        self.assertEqual(moved["sources"][0]["source"]["locator"], document["sources"][0]["source"]["locator"])

    # //// 相对目录范围和原文路径按当前项目解析 [@x380kkm 2026-09-08] ////
    def test_relative_locations_resolve_without_reinterpreting_current_scope(self):
        manager = Manager(self.project, user_root=self.user)
        document = deepcopy(self.document)
        document["contributions"][0]["scope"]["selector"]["path"] = "current"
        document["extensions"][0]["payload"]["path"] = "current"
        resolved = resolve_document(document, manager.catalogs.project, "project", [], project=self.project)
        self.assertEqual(resolved["contributions"][0]["scope"]["selector"]["path"], "current")
        self.assertEqual(resolved["extensions"][0]["payload"]["path"], str(self.project / "current"))
        self.assertEqual(resolved["sources"][0]["source"]["locator"], str(self.root / "outside"))


    # //// 项目模块沿引用链找到用户级的实际来源位置 [@x380kkm 2026-09-08] ////
    def test_relocation_follows_nested_user_module_sources(self):
        skill_path = self.project / "skills/method/SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("---\nname: method\ndescription: Project method.\n---\nRead symbols.\n", encoding="utf-8")
        manager = Manager(self.project, [self.project], user_root=self.user)
        skill = manager.import_file(str(skill_path))["document"]
        old = self.root / "old"
        skill["sources"][0]["source"]["locator"] = "../old/skills/method"
        inner = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:module/inner", "release": {"version": "local"},
                 "metadata": {"name": "Inner"}, "contributions": [{"id": "method", "ref": skill["id"] + "#method", "constraint": "local"}]}
        outer = {**deepcopy(inner), "id": "plugin:module/outer", "metadata": {"name": "Outer"},
                 "contributions": [{"id": "method", "ref": inner["id"] + "#method", "constraint": "local"}]}
        for document in (skill, inner, outer):
            manager.apply_document(manager.preview_document(document)["plan"])
        binding = usage_document(outer, {"state": "enabled"}, None, "project")
        manager.apply_document(manager.preview_document(binding, scope="project")["plan"])
        relocation = ProjectRelocation(manager.catalogs)
        preview = relocation.preview(str(old))
        self.assertEqual(preview["plan"]["sourceIds"], [document_identity(skill)])
        relocation.apply(preview["plan"])
        result = manager.read_content(outer["id"] + "#method", "local", scope="project-local")
        self.assertIn("Read symbols.", result["units"][0]["content"])

    # //// 旧位置的目录链接保持实际配置来源可确认 [@x380kkm 2026-09-08] ////
    def test_relocation_rejects_redirected_old_workspace(self):
        old, destination = self.root / "old", self.root / "other"
        destination.mkdir()
        try:
            old.symlink_to(destination, target_is_directory=True)
        except OSError:
            self.skipTest("当前用户无法创建目录符号链接.")
        manager = Manager(self.project, user_root=self.user)
        with self.assertRaises(RelocationError) as caught:
            ProjectRelocation(manager.catalogs).preview(str(old))
        self.assertEqual(caught.exception.code, "relocation_path")
        self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
