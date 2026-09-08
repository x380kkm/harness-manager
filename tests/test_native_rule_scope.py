# audience: internal
# # native-rule-scope-tests
"""隔离宿主目录验证实际全局原文, 候选来源差异与项目规则撤销边界."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import source_document
from harness_manager.host_projection import compile_host
from harness_manager.service import Manager
import test_host_instruction_sources as source_tests


# //// 使用有明确原文章节的真实宿主输入 [@x380kkm 2026-09-08] ////
class NativeRuleScopeTests(unittest.TestCase):
    # //// 构造规则声明与未接管的原始用户说明 [@x380kkm 2026-09-08] ////
    def setUp(self) -> None:
        source_tests.HostInstructionSourceTests.setUp(self)

    # //// 通过生产卡片接口改变选定层的规则状态 [@x380kkm 2026-09-08] ////
    def configure(self, state: str, scope: str = "user") -> dict:
        return source_tests.HostInstructionSourceTests.configure(self, state, scope)

    # //// 将同一用户来源置于独立项目上下文 [@x380kkm 2026-09-08] ////
    def open_project(self):
        project = self.root / "project"
        project.mkdir()
        self.manager = Manager(project, user_root=self.user)
        return project

    # //// 项目关闭未绑定但仍在全局原文中的规则时明确阻断 [@x380kkm 2026-09-08] ////
    def test_unbound_native_global_rule_cannot_be_disabled_from_project(self) -> None:
        project = self.open_project()
        result = self.configure("disabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertIn("host_inherited_rule_scope", {note["code"] for note in result["hostSync"]["diagnostics"]})
        self.assertFalse((project / "AGENTS.override.md").exists())
        self.assertIn("Keep original names.", self.agents.read_text(encoding="utf-8"))

    # //// 用户已移除的原文片段不被误判为全局生效 [@x380kkm 2026-09-08] ////
    def test_user_disabled_rule_does_not_block_project_setting(self) -> None:
        self.configure("disabled")
        before = self.override.read_bytes()
        self.assertNotIn(b"Keep original names.", before)
        project = self.open_project()
        result = self.configure("disabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "unchanged")
        self.assertFalse((project / "AGENTS.override.md").exists())
        self.assertEqual(self.override.read_bytes(), before)

    # //// 用户关闭原规则后可在项目中独立开启并保留全局文件 [@x380kkm 2026-09-08] ////
    def test_user_disabled_rule_can_be_enabled_in_specific_project(self) -> None:
        self.configure("disabled")
        before = self.override.read_bytes()
        original = self.agents.read_bytes()
        project = self.open_project()
        project_source = project / "AGENTS.md"
        project_source.write_text("# Project\nKeep project policy.\n", encoding="utf-8")
        result = self.configure("enabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "applied")
        text = (project / "AGENTS.override.md").read_text(encoding="utf-8")
        self.assertIn("Keep project policy.", text)
        self.assertIn("Keep original names.", text)
        self.assertEqual(self.override.read_bytes(), before)
        self.assertEqual(self.agents.read_bytes(), original)

    # //// 注入的全局原文作为唯一判断输入并保持候选优先级 [@x380kkm 2026-09-08] ////
    def test_explicit_global_texts_avoid_second_inventory_read(self) -> None:
        self.open_project()
        self.manager.cards.configure(self.item["id"], "disabled", "project-local")
        inputs = {str(self.agents): self.original, str(self.override): "# Current guidance\nUse current comments.\n"}
        with patch.object(self.manager.codex, "snapshot", side_effect=AssertionError("second instruction read")):
            inactive = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader,
                                    "project-local", global_texts=inputs)
            inputs[str(self.override)] = ""
            active = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader,
                                  "project-local", global_texts=inputs)
        self.assertEqual(inactive["diagnostics"], [])
        self.assertEqual(inactive["targets"], {})
        self.assertIn("host_inherited_rule_scope", {note["code"] for note in active["diagnostics"]})

    # //// 优先候选未包含的低优先级原文不会被当成当前规则 [@x380kkm 2026-09-08] ////
    def test_effective_override_controls_global_rule_presence(self) -> None:
        self.override.write_text("# Current guidance\nUse current comments.\n", encoding="utf-8")
        before = self.override.read_bytes()
        project = self.open_project()
        result = self.configure("disabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "unchanged")
        self.assertFalse((project / "AGENTS.override.md").exists())
        self.assertEqual(self.override.read_bytes(), before)

    # //// 全局片段重复导致归属歧义时保持宿主文件原位 [@x380kkm 2026-09-08] ////
    def test_ambiguous_global_fragment_blocks_project_write(self) -> None:
        self.agents.write_text(self.original + "\nKeep original names.\n", encoding="utf-8")
        project = self.open_project()
        result = self.configure("disabled", "project-local")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertIn("host_global_instruction_source", {note["code"] for note in result["hostSync"]["diagnostics"]})
        self.assertFalse((project / "AGENTS.override.md").exists())

    # //// 明确章节来自另一候选文件时要求重新确认 [@x380kkm 2026-09-08] ////
    def test_anchored_rule_cannot_silently_switch_to_existing_override(self) -> None:
        self.override.write_text(self.original + "Override-only policy.\n", encoding="utf-8")
        before = self.override.read_bytes()
        result = self.configure("disabled")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertTrue(any("host-rule-source-file" in note["message"] for note in result["hostSync"]["diagnostics"]))
        self.assertEqual(self.override.read_bytes(), before)

    # //// 跨候选文件的相同正文以来源冲突报告 [@x380kkm 2026-09-08] ////
    def test_matching_text_in_another_candidate_requires_source_confirmation(self) -> None:
        item = {**deepcopy(self.item), "id": "rule:without-range", "details": {}}
        document = source_document(item)
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.item = item
        self.override.write_text(self.original, encoding="utf-8")
        before = self.override.read_bytes()
        result = self.configure("disabled")
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertEqual(self.override.read_bytes(), before)

    # //// 新增正文保留已有优先候选的完整内容 [@x380kkm 2026-09-08] ////
    def test_new_unmapped_rule_can_append_to_existing_override(self) -> None:
        item = {**deepcopy(self.item), "id": "rule:new-body", "content": "Use complete evidence.", "details": {}}
        document = source_document(item)
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.item = item
        self.override.write_text("# Current guidance\nKeep current guidance.\n", encoding="utf-8")
        result = self.configure("enabled")
        self.assertEqual(result["hostSync"]["status"], "applied")
        text = self.override.read_text(encoding="utf-8")
        self.assertIn("Keep current guidance.", text)
        self.assertIn(item["content"], text)
