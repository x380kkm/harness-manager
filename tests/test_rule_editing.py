# audience: internal
# # rule-editing-behavior
"""规则编辑经公开接口保存, 宿主结果在独立用户目录中读取."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.card_subjects import source_document, source_record
from harness_manager.service import Manager


# //// 验证片段表单保存后的原位输出与使用开关 [@x380kkm 2026-09-08] ////
class RuleEditingTests(unittest.TestCase):
    # //// 创建有独立片段和未接管正文的用户来源 [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name)
        self.source = self.user / ".codex/AGENTS.md"
        self.source.parent.mkdir()
        self.original = "# Policy 👀\r\n\r\nKeep names precise.\r\nUnmanaged guidance.\r\n\r\n## Comments\r\nKeep comments concise.\r\nUnmanaged comments.\r\n"
        self.source.write_bytes(self.original.encode("utf-8"))
        self.aggregate = "# Policy 👀\n\nKeep names precise.\n\n## Comments\nKeep comments concise."
        self.fragments = ["Keep comments concise.", "Keep names precise."]
        self.manager = Manager(user_root=self.user)
        self.document = source_document({"id": "rule:policy", "kind": "rule", "name": "规则正文", "summary": "规则正文.",
            "content": self.aggregate, "path": str(self.source), "scope": "user", "details": {"fragments": self.fragments}})
        self.manager.apply_document(self.manager.preview_document(self.document)["plan"])
        self.identity = self.document["id"] + "@local"
        self.reference = self.document["id"] + "#rule"
        self.override = self.source.parent / "AGENTS.override.md"

    # //// 从公开入口读取规则的编辑字段 [@x380kkm 2026-09-08] ////
    def describe(self):
        return self.manager.invoke("rule.describe", {"id": self.identity, "ref": self.reference})

    # //// 按表单片段与固定文字保存完整声明 [@x380kkm 2026-09-08] ////
    def save_fields(self, record, texts):
        draft = deepcopy(record["document"])
        payload = next(member for member in draft["contributions"] if member["id"] == record["memberId"])["payload"]
        payload["text"] = "".join(texts[part["fragment"]] if "fragment" in part else part["text"] for part in record["parts"])
        if record["mapped"]:
            payload["fragmentTexts"] = texts
        else:
            payload.pop("fragmentTexts", None)
        preview = self.manager.invoke("document.preview", {"document": draft, "baseline": record["baseline"], "scope": record["scope"]})
        return self.manager.invoke("document.apply", {"plan": preview["plan"]})

    # //// 以当前配置基线调整规则使用状态 [@x380kkm 2026-09-08] ////
    def configure(self, state):
        return self.manager.invoke("card.configure", {"id": "rule:policy", "state": state,
            "baseline": self.manager.cards.describe("rule:policy")["configBaseline"]})

    # //// 乱序片段和空替换保留原文结构及后续启停 [@x380kkm 2026-09-08] ////
    def test_fragment_edits_preserve_context_and_switches(self):
        self.configure("enabled")
        record = self.describe()
        self.assertEqual(record["texts"], self.fragments)
        self.assertEqual([part["fragment"] for part in record["parts"] if "fragment" in part], [1, 0])
        result = self.save_fields(record, ["", "Use explicit names 🧭."])
        self.assertEqual(result["hostSync"]["status"], "applied")
        expected = self.original.replace(self.fragments[0], "").replace(self.fragments[1], "Use explicit names 🧭.")
        self.assertEqual(self.override.read_bytes(), expected.encode("utf-8"))
        self.assertEqual(self.configure("disabled")["hostSync"]["status"], "applied")
        disabled = self.original.replace(self.fragments[0], "").replace(self.fragments[1], "")
        self.assertEqual(self.override.read_bytes(), disabled.encode("utf-8"))
        self.configure("enabled")
        self.assertEqual(self.override.read_bytes(), expected.encode("utf-8"))
        self.configure("inherit")
        self.assertFalse(self.override.exists())
        self.assertEqual(self.source.read_bytes(), self.original.encode("utf-8"))

    # //// 首次编辑按来源行区间取得各片段原文 [@x380kkm 2026-09-08] ////
    def test_section_mapping_supports_editing_before_adoption(self):
        document = deepcopy(self.document)
        source = source_record(document)
        source.pop("fragments")
        source["sections"] = [{"line": 7, "endLine": 7}, {"line": 3, "endLine": 3}]
        self.manager.apply_document(self.manager.preview_document(document, self.document)["plan"])
        record = self.describe()
        self.assertEqual(record["texts"], self.fragments)
        self.save_fields(record, ["Document behavior.", "Name the operation."])
        self.assertEqual(self.configure("enabled")["hostSync"]["status"], "applied")
        expected = self.original.replace(self.fragments[0], "Document behavior.").replace(self.fragments[1], "Name the operation.")
        self.assertEqual(self.override.read_bytes(), expected.encode("utf-8"))

    # //// 保存正文与片段失配时保留原文并允许明确修正 [@x380kkm 2026-09-08] ////
    def test_unmapped_saved_body_is_preserved_for_repair(self):
        self.configure("enabled")
        draft = deepcopy(self.document)
        draft["contributions"][0]["payload"]["text"] = self.aggregate.replace("precise", "explicit")
        result = self.manager.invoke("document.apply", {"plan": self.manager.preview_document(draft, self.document)["plan"]})
        self.assertEqual(result["hostSync"]["status"], "blocked")
        record = self.describe()
        self.assertEqual(record["unmappedText"], draft["contributions"][0]["payload"]["text"])
        repaired = self.save_fields(record, [self.fragments[0], "Keep names explicit."])
        self.assertEqual(repaired["hostSync"]["status"], "applied")
        self.assertNotIn("unmappedText", self.describe())
        self.assertEqual(self.configure("disabled")["hostSync"]["status"], "applied")

    # //// 相同替换正文按独立片段身份继续编辑 [@x380kkm 2026-09-08] ////
    def test_equal_replacements_keep_independent_fields(self):
        self.configure("enabled")
        self.save_fields(self.describe(), ["Shared wording.", "Shared wording."])
        record = self.describe()
        self.assertEqual(record["texts"], ["Shared wording.", "Shared wording."])
        self.save_fields(record, ["Comment wording.", "Name wording."])
        expected = self.original.replace(self.fragments[0], "Comment wording.").replace(self.fragments[1], "Name wording.")
        self.assertEqual(self.override.read_bytes(), expected.encode("utf-8"))

    # //// 单片段空正文只移除对应原文 [@x380kkm 2026-09-08] ////
    def test_single_mapped_fragment_can_be_cleared(self):
        document = deepcopy(self.document)
        document["contributions"][0]["payload"]["text"] = self.fragments[0]
        source_record(document).update(fragments=[self.fragments[0]], originalText=self.fragments[0])
        self.manager.apply_document(self.manager.preview_document(document, self.document)["plan"])
        record = self.describe()
        self.assertTrue(record["mapped"])
        self.assertEqual(record["texts"], [self.fragments[0]])
        self.save_fields(record, [""])
        self.assertEqual(self.configure("enabled")["hostSync"]["status"], "applied")
        self.assertEqual(self.override.read_bytes(), self.original.replace(self.fragments[0], "").encode("utf-8"))

    # //// 项目相对来源按项目位置编辑并保留共享路径 [@x380kkm 2026-09-08] ////
    def test_project_relative_source_uses_project_root(self):
        project = self.user / "workspace"
        project.mkdir()
        (project / "AGENTS.md").write_bytes(self.original.encode("utf-8"))
        self.manager = Manager(project, user_root=self.user)
        document = deepcopy(self.document)
        document["id"] = "plugin:project/policy"
        source_record(document).update(id="rule:project-policy", path="AGENTS.md", scope="project")
        self.manager.apply_document(self.manager.preview_document(document, scope="project-local")["plan"])
        record = self.manager.invoke("rule.describe", {"id": document["id"] + "@local", "ref": document["id"] + "#rule", "scope": "project-local"})
        self.assertTrue(record["mapped"])
        self.assertEqual(record["texts"], self.fragments)
        self.assertEqual(source_record(record["baseline"])["path"], "AGENTS.md")
        self.save_fields(record, ["New comment policy.", self.fragments[1]])
        result = self.manager.invoke("card.configure", {"id": "rule:project-policy", "state": "enabled", "scope": "project-local"})
        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertEqual((project / "AGENTS.override.md").read_bytes(), self.original.replace(self.fragments[0], "New comment policy.").encode("utf-8"))
