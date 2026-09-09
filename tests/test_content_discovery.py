# audience: internal
# # content-discovery-tests
# 临时目录中的实际来源核对候选分页, 宿主规则范围和必需内容的完整读取.

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.card_subjects import source_document
from harness_manager.content_plan import SKILL_POINT, TASK_CONTEXT_POINT
from harness_manager.service import Manager
from harness_manager.usage import usage_document
from test_content_snapshots import binding, member, package


# //// 验证精简候选仍能选择和读取完整内容 [@x380kkm 2026-09-08] ////
class ContentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "sources"
        self.source.mkdir()
        self.user = self.root / "user"
        self.user.mkdir()
        self.manager = Manager(read_roots=[self.source], user_root=self.user)
        (self.source / "SKILL.md").write_text("# 方法\n\n按原始输入整理证据.\n", encoding="utf-8")
        (self.source / "notes.md").write_text("保留任务的专用术语.", encoding="utf-8")
        self.plugin = package("plugin:methods", self.source, [
            member("first", SKILL_POINT, "SKILL.md", name="First", description="整理证据"),
            member("second", SKILL_POINT, "SKILL.md", name="Second", description="整理数据"),
            member("notes", TASK_CONTEXT_POINT, "notes.md", name="A notes", skill="plugin:methods#first"),
        ])
        self.save(self.plugin)
        self.save(binding("binding:methods", self.plugin, {}, options={"terminology": "项目术语"}))

    # //// 保存独立配置层中的当前声明 [@x380kkm 2026-09-08] ////
    def save(self, document, baseline=None):
        self.manager.apply_document(self.manager.preview_document(document, baseline)["plan"])

    # //// 内容类型筛选在分页前完成且摘要保留可用读取入口 [@x380kkm 2026-09-08] ////
    def test_filtered_summary_pages_keep_readable_selection_and_full_details(self):
        first = self.manager.discover_content(point=SKILL_POINT, limit=1)
        second = self.manager.discover_content(point=SKILL_POINT, limit=1, cursor=first["next_cursor"])
        self.assertEqual([first["candidates"][0]["name"], second["candidates"][0]["name"]], ["First", "Second"])
        self.assertIsNone(second["next_cursor"])
        candidate = first["candidates"][0]
        self.assertFalse({"binding_ids", "options", "source", "entry", "reference_chain"} & candidate.keys())
        complete = self.manager.invoke(candidate["read"]["method"], candidate["read"]["params"])
        self.assertEqual(complete["readiness"], "ready")
        contents = [unit["content"] for unit in complete["units"]]
        self.assertIn((self.source / "SKILL.md").read_bytes().decode("utf-8"), contents)
        self.assertIn("保留任务的专用术语.", contents)
        details = self.manager.discover_content(point=SKILL_POINT, detail="full")["candidates"][0]
        self.assertEqual(details["ref"], candidate["ref"])
        self.assertEqual(details["options"], {"terminology": "项目术语"})
        self.assertEqual(details["entry"], "SKILL.md")
        self.assertEqual(details["read"], candidate["read"])
        self.assertEqual(self.manager.discover_content(query="数据", point=SKILL_POINT)["candidates"][0]["name"], "Second")

    # //// 全局规则的宿主范围保持原生输出且保留任务配套 [@x380kkm 2026-09-08] ////
    def test_host_scoped_rules_keep_host_output_and_content_continuation(self):
        original_usage = next(document for document in self.manager.store.snapshot() if document["id"] == "binding:methods")
        method_usage = deepcopy(original_usage)
        method_usage["target"]["selector"]["host"] = "harness-manager"
        self.save(method_usage, original_usage)
        agents = self.user / ".codex/AGENTS.md"
        agents.parent.mkdir()
        agents.write_text("# 全局规则\n\n保留命名和文档契约.\n", encoding="utf-8")
        rule = source_document({"id": "rule:global", "kind": "rule", "name": "全局规则", "summary": "命名约束",
                                "path": str(agents), "scope": "user", "content": agents.read_text(encoding="utf-8")})
        self.save(rule)
        original = usage_document(rule, {"state": "enabled"}, None, "user")
        self.save(original)
        full = self.manager.open_content("plugin:methods#first", "1.0.0", context={"host": "harness-manager"})
        self.assertIn(rule["contributions"][0]["payload"], [unit["content"] for unit in full["units"]])
        before = self.manager.host.compile("user")
        self.assertEqual(before["diagnostics"], [])
        scoped = deepcopy(original)
        scoped["target"]["selector"]["host"] = "codex"
        self.save(scoped, original)
        after = self.manager.host.compile("user")
        self.assertEqual(after["diagnostics"], [])
        self.assertEqual(after["targets"], before["targets"])
        pending = self.manager.open_content("plugin:methods#first", "1.0.0", context={"host": "harness-manager"}, budget=1)
        restarted = Manager(read_roots=[self.source], user_root=self.user)
        complete = restarted.continue_content(pending["continuation"])
        self.assertEqual(complete["readiness"], "ready")
        contents = [unit["content"] for unit in complete["units"]]
        self.assertNotIn(rule["contributions"][0]["payload"], contents)
        self.assertIn("保留任务的专用术语.", contents)
        self.assertIn((self.source / "SKILL.md").read_bytes().decode("utf-8"), contents)
        self.assertEqual(len(complete["units"]), 3)

    # //// 筛选候选保留显式必需内容的缺口诊断 [@x380kkm 2026-09-08] ////
    def test_summary_filter_does_not_discard_required_context(self):
        changed = deepcopy(self.plugin)
        notes = changed["contributions"][2]
        notes["criticality"] = {"default": "required"}
        notes["source"] = "source:files"
        notes["payload"]["entry"] = "missing.md"
        self.save(changed, self.plugin)
        candidate = self.manager.discover_content(point=SKILL_POINT, query="First")["candidates"][0]
        result = self.manager.invoke(candidate["read"]["method"], candidate["read"]["params"])
        self.assertEqual(result["readiness"], "needs-content")
        self.assertTrue(any(reference.endswith("/missing.md") for reference in result["missing"]))
        self.assertTrue(any(note["code"] == "content.required-unit-unavailable" for note in result["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
