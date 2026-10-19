# audience: internal
# # host-instruction-source-tests
"""隔离目录验证规则片段接管, 原文保留, 外部编辑与项目说明候选的实际生效结果."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import source_document
from harness_manager.service import Manager
from harness_manager.host_storage import HostStorage
from harness_manager.storage_errors import StorageConflictError


# //// 在独立用户目录中建立可区分的规则章节 [@x380kkm 2026-09-08] ////
class HostInstructionSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.host = self.user / ".codex"
        self.host.mkdir(parents=True)
        self.original = "# Policies\n\n## Naming\nKeep original names.\n\n## Comments\nKeep the comment format.\n"
        self.agents = self.host / "AGENTS.md"
        self.agents.write_text(self.original, encoding="utf-8")
        self.override = self.host / "AGENTS.override.md"
        self.manager = Manager(user_root=self.user)
        self.item = {"id": "rule:names", "kind": "rule", "name": "Names", "path": str(self.agents),
                     "scope": "user", "summary": "Naming policy.", "content": "Keep original names.",
                     "line": 4, "details": {"endLine": 4}}
        self.document = source_document(self.item)
        self.manager.apply_document(self.manager.preview_document(self.document)["plan"])

    # //// 通过共同管理接口修改卡片状态 [@x380kkm 2026-09-08] ////
    def configure(self, state, scope="user"):
        baseline = self.manager.cards.describe(self.item["id"], scope)["configBaseline"]
        return self.manager.invoke("card.configure", {"id": self.item["id"], "state": state, "scope": scope, "baseline": baseline})

    # //// 首次关闭只移出选中片段且保留其他章节 [@x380kkm 2026-09-08] ////
    def test_first_disable_preserves_other_rules_and_release_restores_fallback(self):
        result = self.configure("disabled")
        self.assertEqual(result["hostSync"]["status"], "applied")
        output = self.override.read_text(encoding="utf-8")
        self.assertNotIn("Keep original names.", output)
        self.assertIn("## Comments\nKeep the comment format.", output)
        self.assertEqual(self.agents.read_text(encoding="utf-8"), self.original)
        self.configure("enabled")
        self.assertEqual(self.override.read_text(encoding="utf-8"), self.original)
        changed = deepcopy(self.document)
        changed["contributions"][0]["payload"]["text"] = "Use domain names."
        self.manager.invoke("document.apply", {"plan": self.manager.preview_document(changed, self.document)["plan"]})
        self.assertIn("Use domain names.", self.override.read_text(encoding="utf-8"))
        self.assertNotIn("Keep original names.", self.override.read_text(encoding="utf-8"))
        self.configure("inherit")
        self.assertFalse(self.override.exists())
        self.assertEqual(self.agents.read_text(encoding="utf-8"), self.original)

    # //// 新规则追加到现有说明且关闭后保留原有内容 [@x380kkm 2026-09-08] ////
    def test_new_rule_preserves_unmanaged_base(self):
        new = deepcopy(self.item)
        new.update(id="rule:new", content="Read complete sources.", details={})
        document = source_document(new)
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.manager.invoke("card.configure", {"id": new["id"], "state": "enabled"})
        output = self.override.read_text(encoding="utf-8")
        self.assertTrue(output.startswith(self.original.rstrip()))
        self.assertIn(new["content"], output)
        self.item = new
        self.configure("disabled")
        self.assertEqual(self.override.read_text(encoding="utf-8"), self.original)

    # //// 原文的未接管编辑进入新预览且使旧计划失效 [@x380kkm 2026-09-08] ////
    def test_external_source_edits_are_preserved_and_guard_the_preview(self):
        self.configure("enabled")
        updated = self.original.replace("Keep the comment format.", "Use concise comments.")
        self.agents.write_text(updated, encoding="utf-8")
        preview = self.manager.host.preview()
        self.manager.host.apply(preview["planId"])
        self.assertIn("Use concise comments.", self.override.read_text(encoding="utf-8"))
        preview = self.manager.host.preview()
        self.agents.write_text(updated + "An external addition.\n", encoding="utf-8")
        with self.assertRaises(StorageConflictError):
            self.manager.host.apply(preview["planId"])
        self.assertNotIn("An external addition.", self.override.read_text(encoding="utf-8"))

    # //// 来源片段变化与重叠均阻止写入完整覆盖文件 [@x380kkm 2026-09-08] ////
    def test_invalid_source_mapping_blocks_without_masking_existing_instructions(self):
        self.agents.write_text(self.original.replace("Keep original names.", "External naming rule."), encoding="utf-8")
        result = self.configure("disabled")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertFalse(self.override.exists())
        self.agents.write_text(self.original, encoding="utf-8")
        duplicate = deepcopy(self.item)
        duplicate["id"] = "rule:overlap"
        document = source_document(duplicate)
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.manager.cards.configure(duplicate["id"], "enabled")
        preview = self.manager.host.preview()
        self.assertIsNone(preview["planId"])
        self.assertIn("host-rule-source-overlap", preview["diagnostics"][0]["message"])
        self.assertFalse(self.override.exists())

    # //// 项目说明候选内容在规则接管及解除后保持可读 [@x380kkm 2026-09-08] ////
    def test_project_fallback_file_is_preserved(self):
        project = self.root / "project"
        project.mkdir()
        source = project / "CLAUDE.md"
        source.write_text(self.original, encoding="utf-8")
        (self.host / "config.toml").write_text('project_doc_fallback_filenames = ["CLAUDE.md"]\n', encoding="utf-8")
        self.manager = Manager(project, user_root=self.user)
        self.item = {**self.item, "id": "rule:project-names", "path": str(source), "scope": "project"}
        document = source_document(self.item)
        self.manager.apply_document(self.manager.preview_document(document, scope="project-local")["plan"])
        result = self.configure("disabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "applied")
        override = project / "AGENTS.override.md"
        self.assertNotIn("Keep original names.", override.read_text(encoding="utf-8"))
        self.assertIn("Keep the comment format.", override.read_text(encoding="utf-8"))
        self.assertEqual(source.read_text(encoding="utf-8"), self.original)
        self.configure("inherit", "project-local")
        self.assertFalse(override.exists())

    # //// 提交前更换说明候选配置会拒绝旧来源的输出 [@x380kkm 2026-09-08] ////
    def test_fallback_configuration_change_at_storage_boundary_is_rejected(self):
        project = self.root / "fallback-project"
        project.mkdir()
        (project / "CLAUDE.md").write_text("Current source.\n", encoding="utf-8")
        (project / "OTHER.md").write_text("New source.\n", encoding="utf-8")
        config = self.host / "config.toml"
        config.write_text('project_doc_fallback_filenames = ["CLAUDE.md"]\n', encoding="utf-8")
        self.manager = Manager(project, user_root=self.user)
        item = {**self.item, "id": "rule:project-source", "path": str(project / "CLAUDE.md"), "content": "Current source.",
                "line": 1, "details": {"endLine": 1}, "scope": "project"}
        document = source_document(item)
        self.manager.apply_document(self.manager.preview_document(document, scope="project-local")["plan"])
        self.manager.cards.configure(item["id"], "enabled", "project-local")
        preview = self.manager.host.preview("project-local")
        original_apply = HostStorage.apply

        # //// 在存储接收预览后修改来源选择配置 [@x380kkm 2026-09-08] ////
        def changed_configuration(storage, *args, **kwargs):
            config.write_text('project_doc_fallback_filenames = ["OTHER.md"]\n', encoding="utf-8")
            return original_apply(storage, *args, **kwargs)

        with patch.object(HostStorage, "apply", changed_configuration):
            with self.assertRaises(StorageConflictError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse((project / "AGENTS.override.md").exists())

    # //// 全局说明在项目提交边界变化时拒绝旧判断 [@x380kkm 2026-09-08] ////
    def test_global_instruction_change_at_project_storage_boundary_is_rejected(self):
        project = self.root / "scoped-project"
        project.mkdir()
        self.manager = Manager(project, user_root=self.user)
        self.manager.cards.configure(self.item["id"], "enabled", "project-local")
        preview = self.manager.host.preview("project-local")
        self.assertIsNotNone(preview["planId"], preview)
        original_apply = HostStorage.apply

        # //// 在项目提交时改变用户全局说明 [@x380kkm 2026-09-08] ////
        def changed_global_source(storage, *args, **kwargs):
            self.agents.write_text(self.original + "Global policy changed.\n", encoding="utf-8")
            return original_apply(storage, *args, **kwargs)

        with patch.object(HostStorage, "apply", changed_global_source):
            with self.assertRaises(StorageConflictError):
                self.manager.host.apply(preview["planId"])
        self.assertFalse((project / "AGENTS.override.md").exists())

    # //// 保护副本恢复片段归属并保持后续解除可用 [@x380kkm 2026-09-08] ////
    def test_restore_keeps_fragment_ownership(self):
        self.configure("disabled")
        for identity in ("initial", "before-restore"):
            preview = self.manager.host.preview_restore(identity)
            self.manager.host.apply(preview["planId"])
        self.manager.host.set_enabled(True)
        self.configure("inherit")
        self.assertFalse(self.override.exists())
        self.assertEqual(self.agents.read_text(encoding="utf-8"), self.original)

    # //// Windows 行尾与显式片段保持相同来源定位 [@x380kkm 2026-09-08] ////
    def test_explicit_fragments_keep_crlf_sources_and_other_bytes(self):
        original = self.original.replace("\n", "\r\n").encode("utf-8")
        self.agents.write_bytes(original)
        document = deepcopy(self.document)
        record = document["extensions"][0]["payload"]
        record.pop("endLine")
        record["fragments"] = ["## Naming\nKeep original names."]
        self.manager.apply_document(self.manager.preview_document(document, self.document)["plan"])
        self.configure("disabled")
        output = self.override.read_bytes()
        self.assertNotIn(b"Naming", output)
        self.assertIn(b"## Comments\r\nKeep the comment format.\r\n", output)
        self.assertEqual(self.agents.read_bytes(), original)

    # //// 当前项目根目录的规则范围由同一宿主文件表达 [@x380kkm 2026-09-08] ////
    def test_project_root_path_scope_applies_to_its_own_instruction_file(self):
        project = self.root / "project-policy"
        project.mkdir()
        source = project / "AGENTS.md"
        source.write_text(self.original, encoding="utf-8")
        manager = Manager(project, user_root=self.user)
        item = {**self.item, "id": "rule:root-scope", "scope": "directory", "path": str(source)}
        document = source_document(item)
        manager.apply_document(manager.preview_document(document, scope="project-local")["plan"])
        result = manager.invoke("card.configure", {"id": item["id"], "state": "disabled", "scope": "project-local"})
        self.assertEqual(result["hostSync"]["status"], "applied")
        output = (project / "AGENTS.override.md").read_text(encoding="utf-8")
        self.assertNotIn("Keep original names.", output)
        self.assertIn("Keep the comment format.", output)


if __name__ == "__main__":
    unittest.main()
