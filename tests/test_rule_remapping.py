# audience: internal
# # rule-remapping-tests
"""真实声明预览与提交核对规则来源修正, 期望正文编辑和旧片段归属兼容."""
from copy import deepcopy
import unittest

from harness_manager.card_subjects import source_record
from harness_manager.host_instructions import compose_instructions
from harness_manager.protocol import document_identity
import test_host_instruction_sources as source_tests


# //// 在隔离宿主目录中维护显式片段与来源选择 [@x380kkm 2026-09-08] ////
class RuleRemappingTests(unittest.TestCase):
    # //// 构造原始章节与对应规则声明 [@x380kkm 2026-09-08] ////
    def setUp(self) -> None:
        source_tests.HostInstructionSourceTests.setUp(self)

    # //// 通过共同入口设置规则状态 [@x380kkm 2026-09-08] ////
    def configure(self, state: str) -> dict:
        return source_tests.HostInstructionSourceTests.configure(self, state)

    # //// 保存明确修改后的规则声明并读取宿主同步结果 [@x380kkm 2026-09-08] ////
    def update(self, text: str, selection: dict | None = None) -> dict:
        record = self.manager.read_document(document_identity(self.document))
        draft = deepcopy(record["document"])
        draft["contributions"][0]["payload"]["text"] = text
        if selection is not None:
            source_record(draft).update(selection)
        plan = self.manager.preview_document(draft, record["baseline"])["plan"]
        return self.manager.invoke("document.apply", {"plan": plan})

    # //// 显式改选片段时恢复旧位置并替换新位置 [@x380kkm 2026-09-08] ////
    def test_explicit_fragment_change_replaces_new_location(self) -> None:
        self.configure("enabled")
        result = self.update("Use concise comments.", {"fragments": ["Keep the comment format."], "originalText": "Keep the comment format."})
        self.assertEqual(result["hostSync"]["status"], "applied")
        output = self.override.read_text(encoding="utf-8")
        self.assertIn("## Naming\nKeep original names.", output)
        self.assertIn("## Comments\nUse concise comments.", output)
        self.assertNotIn("Keep the comment format.", output)

    # //// 来源改写后显式提供当前片段可修复重试链 [@x380kkm 2026-09-08] ////
    def test_changed_source_is_recovered_by_explicit_fragments(self) -> None:
        self.configure("enabled")
        self.agents.write_text(self.original.replace("Keep original names.", "Source naming revision."), encoding="utf-8")
        self.assertIsNone(self.manager.host.preview()["planId"])
        result = self.update("Use domain names.", {"fragments": ["Source naming revision."], "originalText": "Source naming revision."})
        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertIn("## Naming\nUse domain names.", self.override.read_text(encoding="utf-8"))
        self.assertIsNotNone(self.manager.host.preview()["planId"])

    # //// 只改期望正文及插入无关行时沿用已确认片段 [@x380kkm 2026-09-08] ////
    def test_text_edit_after_unrelated_insertion_keeps_fragment_anchor(self) -> None:
        self.configure("enabled")
        self.agents.write_text("# Added context\n\n" + self.original, encoding="utf-8")
        result = self.update("Use domain names.")
        self.assertEqual(result["hostSync"]["status"], "applied")
        output = self.override.read_text(encoding="utf-8")
        self.assertTrue(output.startswith("# Added context\n\n"))
        self.assertIn("## Naming\nUse domain names.", output)
        self.assertIn("Keep the comment format.", output)

    # //// 显式行区间修正需要对应原文验证 [@x380kkm 2026-09-08] ////
    def test_section_change_uses_updated_source_selection(self) -> None:
        self.configure("enabled")
        result = self.update("Use concise comments.", {"sections": [{"line": 7, "endLine": 7}], "originalText": "Keep the comment format."})
        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertIn("## Naming\nKeep original names.", self.override.read_text(encoding="utf-8"))
        self.assertIn("## Comments\nUse concise comments.", self.override.read_text(encoding="utf-8"))

    # //// 错误修正不会退回旧片段后误报成功 [@x380kkm 2026-09-08] ////
    def test_invalid_explicit_remap_preserves_current_output(self) -> None:
        self.configure("enabled")
        before = self.override.read_bytes()
        result = self.update("Use another rule.", {"fragments": ["Absent source text."]})
        self.assertEqual(result["hostSync"]["status"], "blocked")
        self.assertEqual(self.override.read_bytes(), before)

    # //// 旧片段记录保持定位并可由显式片段重新确认 [@x380kkm 2026-09-08] ////
    def test_legacy_mapping_keeps_anchor_and_accepts_explicit_repair(self) -> None:
        entry = {"ref": "rule:names", "text": "Use domain names.", "enabled": True,
                 "source": {"path": str(self.agents), "line": 4, "endLine": 4, "originalText": "Keep original names."}}
        previous = {"file": "AGENTS.md", "entries": {entry["ref"]: {"fragments": ["Keep original names."]}}}
        base = ("# Added\n\n" + self.original).encode()
        output, source = compose_instructions([entry], base, self.agents, previous)
        self.assertIn(b"Use domain names.", output)
        self.assertIn("selection", source["entries"][entry["ref"]])
        changed = self.original.replace("Keep original names.", "Revised source.").encode()
        entry["source"]["fragments"] = ["Revised source."]
        repaired, _ = compose_instructions([entry], changed, self.agents, previous)
        self.assertIn(b"Use domain names.", repaired)
        self.assertNotIn(b"Revised source.", repaired)

    # //// 旧归属记录通过真实保存入口接受来源片段修正 [@x380kkm 2026-09-08] ////
    def test_legacy_owned_record_repair_through_document_api(self) -> None:
        self.configure("enabled")
        storage = self.manager.host.storage("user")
        before = next(record for record in storage.store.snapshot() if record["id"] == "state")
        legacy = deepcopy(before)
        for record in legacy["ownership"]["AGENTS.override.md"]["source"]["entries"].values():
            record.pop("selection", None)
        storage.store.apply(storage.store.preview_put(legacy, before))
        self.agents.write_text(self.original.replace("Keep original names.", "Revised source names."), encoding="utf-8")
        result = self.update("Use verified names.", {"fragments": ["Revised source names."]})
        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertIn("## Naming\nUse verified names.", self.override.read_text(encoding="utf-8"))
        stored = storage.read_ownership()["AGENTS.override.md"]["source"]["entries"]
        self.assertEqual(next(iter(stored.values()))["selection"]["fragments"], ["Revised source names."])
