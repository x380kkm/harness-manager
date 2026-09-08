# audience: internal
# # module-inventory-tests
# 组合卡片使用明确的发布引用, 个人内容与共享目录按实际范围读取.

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from harness_manager.card_subjects import source_document
from harness_manager.catalogs import Catalogs
from harness_manager.module_inventory import PRESENTATION_CONTRACT, module_inventory
from harness_manager.protocol import document_identity
from test_projection import make_plugin


# //// 在独立目录核对模块投影与来源边界 [@x380kkm 2026-09-07] ////
class ModuleInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.project = self.root / "user", self.root / "project"
        self.user.mkdir()
        self.project.mkdir()
        self.catalogs = Catalogs(self.user, self.project)

    # //// 保存测试中的独立声明 [@x380kkm 2026-09-07] ////
    def save(self, document: dict, scope: str = "user") -> None:
        store = self.catalogs.select(scope)
        store.apply(store.preview_put(document))

    # //// 创建独立规则卡片的来源观察 [@x380kkm 2026-09-07] ////
    def rule(self, identity: str = "rule:powershell") -> dict:
        return {"id": identity, "kind": "rule", "name": "PowerShell 使用规范", "summary": "统一命令环境.",
                "path": str(self.user / ".codex/AGENTS.md"), "scope": "user", "content": "使用 pwsh.", "details": {}}

    # //// 单一内容保持原类型且显式单载体模块保留模块身份 [@x380kkm 2026-09-07] ////
    def test_single_sources_require_explicit_module_presentation(self) -> None:
        rule = source_document(self.rule())
        skill = make_plugin()
        self.save(rule)
        self.save(skill)
        self.assertEqual(module_inventory(self.catalogs, "user", [self.rule()])[0], [])
        module = deepcopy(rule)
        module["id"] = "plugin:powershell/module"
        module["extensions"].append({"contract": {"id": PRESENTATION_CONTRACT, "range": "^1.0.0"},
                                     "payload": {"members": {"rule": {"role": "命令执行约束", "carrier": "AGENTS.md"}}}})
        self.save(module)
        modules, _, _ = module_inventory(self.catalogs, "user", [self.rule()])
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]["details"]["documentId"], document_identity(module))
        self.assertEqual(modules[0]["details"]["members"][0]["carrier"], "AGENTS.md")

    # //// 组合引用保留独立版本且成员边只连接准确来源 [@x380kkm 2026-09-07] ////
    def test_references_resolve_members_without_merging_same_named_cards(self) -> None:
        rule = self.rule()
        source = source_document(rule)
        self.save(source)
        skill = make_plugin("plugin:tools/shell", ("shell",))
        self.save(skill)
        module = make_plugin("plugin:workflow/shell")
        module["contributions"] = [{"id": "policy", "ref": source["id"] + "#rule", "constraint": "local"},
                                   {"id": "method", "ref": skill["id"] + "#shell", "constraint": "1.0.0"}]
        self.save(module)
        unrelated = {**rule, "id": "rule:other", "path": str(self.user / "other.md")}
        before = deepcopy([rule, unrelated])
        modules, edges, diagnostics = module_inventory(self.catalogs, "user", [rule, unrelated])
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(modules), 1)
        members = modules[0]["details"]["members"]
        self.assertEqual(modules[0]["details"]["memberCounts"], {"rule": 1, "skill": 1})
        self.assertEqual(members[0]["originalRef"], source["id"] + "#rule")
        self.assertEqual(members[0]["originalDocumentId"], document_identity(source))
        self.assertEqual(members[1]["version"], "1.0.0")
        self.assertEqual([edge["to"] for edge in edges], [rule["id"]])
        self.assertEqual([rule, unrelated], before)

    # //// 缺失和循环引用保留成员位置与诊断 [@x380kkm 2026-09-07] ////
    def test_unresolved_members_remain_visible_with_reference_evidence(self) -> None:
        module = make_plugin("plugin:workflow/check")
        module["contributions"] = [{"id": "missing", "ref": "plugin:absent#method"},
                                   {"id": "cycle", "ref": module["id"] + "#cycle"}]
        self.save(module)
        modules, edges, diagnostics = module_inventory(self.catalogs, "user", [])
        self.assertEqual(edges, [])
        self.assertEqual([member["id"] for member in modules[0]["details"]["members"]], ["missing", "cycle"])
        self.assertTrue(all(member["resolved"] is False for member in modules[0]["details"]["members"]))
        self.assertEqual({note["code"] for note in diagnostics}, {"missing_plugin", "reference_cycle"})

    # //// 用户视图隔离项目组合且个人层保留实际目录归属 [@x380kkm 2026-09-07] ////
    def test_project_modules_obey_catalog_scope_and_classify_independent_carriers(self) -> None:
        module = make_plugin(names=("first", "second"))
        module["contributions"][1] = {"id": "lifecycle", "point": "example/hook",
                                      "contract": {"id": "example/hook", "range": "^1.0.0"}, "payload": {"name": "执行准备"}}
        module["extensions"] = [{"contract": {"id": PRESENTATION_CONTRACT, "range": "^1.0.0"},
                                  "payload": {"members": {"lifecycle": {"kind": "hook", "carrier": "hooks.json"}}}}]
        self.save(module, "project-local")
        self.assertEqual(module_inventory(self.catalogs, "user", [])[0], [])
        self.assertEqual(module_inventory(self.catalogs, "project", [])[0], [])
        modules, _, _ = module_inventory(self.catalogs, "project-local", [])
        self.assertEqual(modules[0]["scope"], "project-local")
        self.assertEqual(modules[0]["path"], str(self.catalogs.project_local.catalog))
        self.assertEqual(modules[0]["details"]["memberCounts"], {"skill": 1, "hook": 1})


if __name__ == "__main__":
    unittest.main()
