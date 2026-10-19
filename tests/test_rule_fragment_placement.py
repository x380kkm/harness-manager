# audience: internal
# # rule-fragment-placement-tests
"""规则片段的作用范围由来源位置与完整正文映射共同约束."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.card_subjects import source_document
from harness_manager.host_instructions import compose_instructions
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError, StorageValidationError


# //// 构造分属代码与文档作用范围的规则片段 [@x380kkm 2026-09-08] ////
class RuleFragmentPlacementTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parent / "AGENTS.md"
        self.fragments = ["Keep comments concise.", "Keep documents complete."]
        self.base = ("# Writing\n\n<!-- precode:start -->\n## Comments\nKeep comments concise.\n"
                     "A separate code rule.\n<!-- precode:end -->\n\n<!-- predoc:start -->\n"
                     "## Documents\nKeep documents complete.\nA separate document rule.\n<!-- predoc:end -->\n")
        self.aggregate = "# Writing\n\n## Comments\nKeep comments concise.\n\n## Documents\nKeep documents complete."
        self.entry = {"ref": "rule:writing", "text": self.aggregate, "enabled": True,
                      "source": {"path": str(self.path), "fragments": self.fragments, "originalText": self.aggregate}}

    # //// 使用原始宿主正文计算一次规则投影 [@x380kkm 2026-09-08] ////
    def compose(self, previous=None):
        output, ownership = compose_instructions([self.entry], self.base.encode(), self.path, previous)
        return output.decode(), ownership

    # //// 开关在原位保留或移除片段并保留分栏与未接管规则 [@x380kkm 2026-09-08] ////
    def test_switches_preserve_fragment_locations_and_context(self):
        output, ownership = self.compose()
        self.assertEqual(output, self.base)
        self.assertEqual(ownership["entries"][self.entry["ref"]]["aggregate"], self.aggregate)
        self.entry["enabled"] = False
        disabled, ownership = self.compose(ownership)
        self.assertEqual(disabled, self.base.replace(self.fragments[0], "").replace(self.fragments[1], ""))
        self.entry["enabled"] = True
        restored, _ = self.compose(ownership)
        self.assertEqual(restored, self.base)

    # //// 逐段编辑将每段正文保留在对应分栏内 [@x380kkm 2026-09-08] ////
    def test_edits_require_matching_aggregate_and_keep_original_baseline(self):
        _, ownership = self.compose()
        self.entry["fragmentTexts"] = ["Use precise comments.", "Use complete explanations."]
        self.entry["text"] = self.aggregate.replace(self.fragments[0], self.entry["fragmentTexts"][0]).replace(
            self.fragments[1], self.entry["fragmentTexts"][1])
        output, updated = self.compose(ownership)
        self.assertEqual(output, self.base.replace(self.fragments[0], self.entry["fragmentTexts"][0]).replace(
            self.fragments[1], self.entry["fragmentTexts"][1]))
        self.assertEqual(updated["entries"][self.entry["ref"]]["aggregate"], self.aggregate)
        self.entry["enabled"] = False
        disabled, updated = self.compose(updated)
        self.assertNotIn("Use precise comments.", disabled)
        self.assertNotIn("Use complete explanations.", disabled)
        self.entry["enabled"] = True
        restored, _ = self.compose(updated)
        self.assertEqual(restored, output)

    # //// 缺失表格或适用条件的首次映射阻止规则投影 [@x380kkm 2026-09-08] ////
    def test_unmapped_content_blocks_first_projection(self):
        for remainder in ("Only for generated reports.", "| Tier | Scope |\n| --- | --- |\n| Note | Code |"):
            for enabled in (True, False):
                with self.subTest(remainder=remainder, enabled=enabled):
                    self.entry["text"] = self.aggregate + "\n\n" + remainder
                    self.entry["enabled"] = enabled
                    with self.assertRaisesRegex(StorageValidationError, "尚未映射"):
                        self.compose()

    # //// 已确认的正文或标题编辑需要明确逐段映射 [@x380kkm 2026-09-08] ////
    def test_text_changes_without_fragment_texts_are_blocked(self):
        _, ownership = self.compose()
        for text in (self.aggregate.replace("concise", "precise"), self.aggregate.replace("## Comments", "## All output")):
            with self.subTest(text=text):
                self.entry["text"] = text
                with self.assertRaisesRegex(StorageValidationError, "payload.fragmentTexts"):
                    self.compose(ownership)

    # //// 显式片段替换与完整正文共同限定改动范围 [@x380kkm 2026-09-08] ////
    def test_explicit_fragments_reject_unrepresented_body_and_heading_changes(self):
        _, ownership = self.compose()
        self.entry["fragmentTexts"] = ["Use precise comments.", self.fragments[1]]
        expected = self.aggregate.replace(self.fragments[0], self.entry["fragmentTexts"][0])
        for text in (expected + "\nAn extra rule.", expected.replace("## Documents", "## All output")):
            with self.subTest(text=text):
                self.entry["text"] = text
                with self.assertRaisesRegex(StorageValidationError, "标题范围"):
                    self.compose(ownership)

    # //// 来源声明顺序与原文排序独立且替换内容保持完整 [@x380kkm 2026-09-08] ////
    def test_declaration_order_controls_replacements_without_cascading(self):
        self.entry["source"]["fragments"] = list(reversed(self.fragments))
        _, ownership = self.compose()
        self.entry["fragmentTexts"] = ["Use complete documents.", "Keep documents complete. Explain comments."]
        self.entry["text"] = "# Writing\n\n## Comments\nKeep documents complete. Explain comments.\n\n## Documents\nUse complete documents."
        output, _ = self.compose(ownership)
        expected = self.base.replace(self.fragments[1], "Use complete documents.").replace(
            self.fragments[0], "Keep documents complete. Explain comments.")
        self.assertEqual(output, expected)

    # //// 空替换删除对应片段且保持其他规则与标题原位 [@x380kkm 2026-09-08] ////
    def test_empty_fragment_replacement_deletes_only_its_source(self):
        _, ownership = self.compose()
        self.entry["fragmentTexts"] = ["", self.fragments[1]]
        self.entry["text"] = self.aggregate.replace(self.fragments[0], "")
        output, _ = self.compose(ownership)
        self.assertEqual(output, self.base.replace(self.fragments[0], ""))

    # //// 首次逐段编辑由完整原始正文约束替换结果 [@x380kkm 2026-09-08] ////
    def test_initial_explicit_replacements_use_original_aggregate(self):
        self.entry["fragmentTexts"] = ["Use precise comments.", self.fragments[1]]
        self.entry["text"] = self.aggregate.replace(self.fragments[0], self.entry["fragmentTexts"][0])
        output, ownership = self.compose()
        self.assertEqual(output, self.base.replace(self.fragments[0], self.entry["fragmentTexts"][0]))
        self.assertEqual(ownership["entries"][self.entry["ref"]]["aggregate"], self.aggregate)

    # //// 原文修订后显式重映射重新建立完整的聚合基线 [@x380kkm 2026-09-08] ////
    def test_explicit_remapping_repairs_changed_source(self):
        _, ownership = self.compose()
        self.base = self.base.replace(self.fragments[0], "Revised comment policy.")
        with self.assertRaises(StorageConflictError):
            self.compose(ownership)
        self.entry["source"]["fragments"] = ["Revised comment policy.", self.fragments[1]]
        self.entry["source"]["originalText"] = self.aggregate.replace(self.fragments[0], "Revised comment policy.")
        self.entry["text"] = self.entry["source"]["originalText"]
        output, repaired = self.compose(ownership)
        self.assertEqual(output, self.base)
        self.assertEqual(repaired["entries"][self.entry["ref"]]["aggregate"], self.entry["text"])

    # //// 旧归属记录根据完整正文建立聚合基线并保留原位 [@x380kkm 2026-09-08] ////
    def test_legacy_mapping_accepts_complete_body_and_rejects_omitted_content(self):
        _, ownership = self.compose()
        record = ownership["entries"][self.entry["ref"]]
        record.pop("aggregate")
        for legacy in (ownership, {"file": self.path.name, "entries": {self.entry["ref"]: {"fragments": self.fragments}}}):
            with self.subTest(legacy=legacy):
                output, upgraded = self.compose(deepcopy(legacy))
                self.assertEqual(output, self.base)
                self.assertIn("aggregate", upgraded["entries"][self.entry["ref"]])
                self.entry["text"] = self.aggregate + "\nOnly for reports."
                with self.assertRaisesRegex(StorageValidationError, "尚未映射"):
                    self.compose(legacy)
                self.entry["text"] = self.aggregate

    # //// 明确的行区间按声明顺序定位并保留宿主行尾 [@x380kkm 2026-09-08] ////
    def test_sections_preserve_order_and_crlf(self):
        lines = self.base.splitlines()
        self.entry["source"].pop("fragments")
        self.entry["source"]["sections"] = [{"line": lines.index(fragment) + 1, "endLine": lines.index(fragment) + 1}
                                             for fragment in reversed(self.fragments)]
        self.base = self.base.replace("\n", "\r\n")
        self.entry["fragmentTexts"] = ["Use complete documents.\nInclude findings.", self.fragments[0]]
        self.entry["text"] = self.aggregate.replace(self.fragments[1], self.entry["fragmentTexts"][0])
        output, _ = self.compose()
        self.assertEqual(output, self.base.replace(self.fragments[1], "Use complete documents.\r\nInclude findings."))

    # //// 模糊与重叠来源均阻止逐段写入 [@x380kkm 2026-09-08] ////
    def test_ambiguous_and_overlapping_fragments_are_rejected(self):
        self.base += "\n" + self.fragments[0]
        with self.assertRaisesRegex(StorageConflictError, "host-rule-source-anchor"):
            self.compose()
        self.base = self.base[:-(len(self.fragments[0]) + 1)]
        self.entry["source"]["fragments"] = [self.fragments[0], "comments concise."]
        with self.assertRaisesRegex(StorageConflictError, "host-rule-source-overlap"):
            self.compose()

    # //// 明确的来源选择与替换列表覆盖全部片段 [@x380kkm 2026-09-08] ////
    def test_incomplete_source_and_replacement_lists_are_rejected(self):
        self.entry["source"]["fragments"] = []
        with self.assertRaisesRegex(StorageValidationError, "非空"):
            self.compose()
        self.entry["source"]["fragments"] = self.fragments
        self.entry["fragmentTexts"] = [self.fragments[0]]
        with self.assertRaisesRegex(StorageValidationError, "每个片段"):
            self.compose()

    # //// 声明保存入口传递分片编辑并持久化原始聚合基线 [@x380kkm 2026-09-08] ////
    def test_document_api_applies_fragment_edits_and_preserves_source(self):
        with TemporaryDirectory() as directory:
            user = Path(directory) / "user"
            host = user / ".codex"
            host.mkdir(parents=True)
            agents = host / "AGENTS.md"
            agents.write_text(self.base, encoding="utf-8")
            manager = Manager(user_root=user)
            item = {"id": self.entry["ref"], "kind": "rule", "name": "Writing", "path": str(agents),
                    "scope": "user", "summary": "", "content": self.aggregate, "details": {"fragments": self.fragments}}
            document = source_document(item)
            manager.apply_document(manager.preview_document(document)["plan"])
            enabled = manager.invoke("card.configure", {"id": item["id"], "state": "enabled"})
            self.assertEqual(enabled["hostSync"]["status"], "applied")
            revised = deepcopy(document)
            revised["contributions"][0]["payload"].update(
                text=self.aggregate.replace(self.fragments[0], "Use precise comments."),
                fragmentTexts=["Use precise comments.", self.fragments[1]])
            plan = manager.preview_document(revised, document)["plan"]
            saved = manager.invoke("document.apply", {"plan": plan})
            self.assertEqual(saved["hostSync"]["status"], "applied")
            self.assertEqual((host / "AGENTS.override.md").read_text(encoding="utf-8"),
                             self.base.replace(self.fragments[0], "Use precise comments."))
            self.assertEqual(agents.read_text(encoding="utf-8"), self.base)
            ownership = manager.host.storage("user").read_ownership()["AGENTS.override.md"]["source"]["entries"]
            self.assertEqual(next(iter(ownership.values()))["aggregate"], self.aggregate)
