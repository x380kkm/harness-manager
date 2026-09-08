# audience: internal
# # scope-editing-tests
# 用户来源与项目影响使用隔离目录, 共享副本按各自的发布身份保留.

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.card_subjects import CardError, source_document
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.usage import usage_document


# //// 核对来源编辑的范围隔离和保存前影响 [@x380kkm 2026-09-08] ////
class ScopeEditingTests(unittest.TestCase):
    # //// 创建用户规则及当前项目 [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project = root / "user", root / "project"
        self.project.mkdir()
        self.agents = self.user / ".codex/AGENTS.md"
        self.agents.parent.mkdir(parents=True)
        self.agents.write_text("# Names\nPreserve public names.\n", encoding="utf-8")
        self.manager = Manager(self.project, [self.user], user_root=self.user)
        self.document = self.rule("names")
        self.save(self.document)

    # //// 建立指向原始说明的独立规则发布 [@x380kkm 2026-09-08] ////
    def rule(self, name):
        return source_document({"id": "rule:" + name, "kind": "rule", "name": name,
                                "summary": "Public names.", "content": self.agents.read_text(encoding="utf-8"),
                                "path": str(self.agents), "scope": "user"})

    # //// 保存指定目录中的声明夹具 [@x380kkm 2026-09-08] ////
    def save(self, document, scope="user", baseline=None):
        preview = self.manager.preview_document(document, baseline, scope)
        self.manager.apply_document(preview["plan"])

    # //// 编辑共享规则后继续管理用户来源并保留项目冲突 [@x380kkm 2026-09-08] ////
    def test_shared_source_edit_reports_impact_and_preserves_user_controls(self):
        baseline = self.manager.invoke("card.describe", {"id": "rule:names"})["configBaseline"]
        self.manager.invoke("card.configure", {"id": "rule:names", "state": "enabled", "baseline": baseline})
        sharing = self.manager.invoke("card.describe", {"id": "rule:names", "scope": "project-local"})["sharingBaseline"]
        self.manager.invoke("card.set_shared", {"id": "rule:names", "shared": True, "baseline": sharing})
        preserved = {scope: self.manager.catalogs.select(scope).catalog.read_bytes() for scope in ("project", "project-local")}
        edited = deepcopy(self.document)
        edited["contributions"][0]["payload"]["text"] = "# Names\nPreserve public names and document interfaces.\n"
        preview = self.manager.invoke("document.preview", {"document": edited, "baseline": self.document})
        conflicts = [note for note in preview["diagnostics"] if note["code"] == "catalog_identity_conflict"]
        self.assertEqual([(note["scope"], note["subject"]) for note in conflicts], [("project", document_identity(edited))])
        applied = self.manager.invoke("document.apply", {"plan": preview["plan"]})
        self.assertTrue(applied["changed"])
        current = self.manager.invoke("card.describe", {"id": "rule:names"})
        self.assertEqual(current["configBaseline"]["source"], edited)
        toggled = self.manager.invoke("card.configure", {"id": "rule:names", "state": "disabled", "baseline": current["configBaseline"]})
        self.assertFalse(toggled["management"]["effectiveEnabled"])
        self.assertEqual(toggled["hostSync"]["scopes"]["user"]["status"], "applied")
        with self.assertRaises(CardError):
            self.manager.invoke("card.describe", {"id": "rule:names", "scope": "project-local"})
        for scope, content in preserved.items():
            self.assertEqual(self.manager.catalogs.select(scope).catalog.read_bytes(), content)

    # //// 无关项目错误保持原归属且私人冲突标记实际影响层 [@x380kkm 2026-09-08] ////
    def test_preview_excludes_existing_project_errors_and_identifies_private_impact(self):
        other = self.rule("other")
        self.save(other)
        conflicting = deepcopy(other)
        conflicting["metadata"]["name"] = "Project other"
        self.save(conflicting, "project")
        changed = deepcopy(self.document)
        changed["metadata"]["name"] = "User names"
        self.assertEqual(self.manager.preview_document(changed, self.document)["diagnostics"], [])
        self.save(self.document, "project-local")
        preview = self.manager.preview_document(changed, self.document)
        self.assertEqual([(note["code"], note["scope"], note["subject"]) for note in preview["diagnostics"]],
                         [("catalog_identity_conflict", "project-local", document_identity(changed))])

    # //// 既有版本缺失时仍报告新增断开的绑定并允许分次保存 [@x380kkm 2026-09-08] ////
    def test_remove_preview_reports_project_reference_impact_without_blocking_save(self):
        self.manager.apply_document(self.manager.preview_remove(document_identity(self.document), self.document)["plan"])
        self.document["release"]["version"] = "1.0.0"
        self.save(self.document)
        binding = usage_document(self.document, {"state": "enabled"}, None, "project")
        binding["target"]["selector"]["task"] = "working"
        self.save(binding, "project")
        other = deepcopy(binding)
        other["id"] += "/other"
        other["plugin"]["constraint"] = "2.0.0"
        other["target"]["selector"]["task"] = "other"
        self.save(other, "project")
        preview = self.manager.invoke("document.preview_remove", {"id": document_identity(self.document), "baseline": self.document})
        self.assertEqual([(note["code"], note["scope"]) for note in preview["diagnostics"]], [("missing_plugin", "project")])
        self.assertEqual(preview["diagnostics"][0]["origin"], binding["id"])
        result = self.manager.invoke("document.apply", {"plan": preview["plan"]})
        self.assertTrue(result["changed"])
        self.assertEqual(self.manager.catalogs.user.snapshot(), [])
        self.assertEqual(self.manager.catalogs.project.snapshot(), [binding, other])

    # //// 模块预览沿同一入口报告共享发布的内容冲突 [@x380kkm 2026-09-08] ////
    def test_module_preview_reports_current_project_impact(self):
        settings = {"name": "Names module", "description": "Names", "members": [{"itemId": "rule:names"}]}
        created = self.manager.invoke("module.preview", settings)
        self.manager.invoke("module.apply", {"plan": created["plan"]})
        self.save(created["document"], "project")
        preview = self.manager.invoke("module.preview", {**settings, "name": "Updated module", "id": created["documentId"],
                                                         "baseline": created["document"]})
        self.assertEqual([(note["code"], note["scope"]) for note in preview["diagnostics"]],
                         [("catalog_identity_conflict", "project")])

    # //// 下层目录不可读时保留用户计划并说明影响检查范围 [@x380kkm 2026-09-08] ////
    def test_unreadable_project_keeps_user_preview_available(self):
        self.manager.catalogs.project.ensure_directory()
        self.manager.catalogs.project.catalog.write_text("{", encoding="utf-8")
        changed = deepcopy(self.document)
        changed["metadata"]["name"] = "User names"
        preview = self.manager.preview_document(changed, self.document)
        self.assertEqual([(note["code"], note["scope"], note["severity"]) for note in preview["diagnostics"]],
                         [("scope_impact_unavailable", "project", "warning")])
        self.manager.apply_document(preview["plan"])
        self.assertEqual(self.manager.cards.describe("rule:names")["configBaseline"]["source"], changed)


# //// 执行跨层编辑行为验证 [@x380kkm 2026-09-08] ////
if __name__ == "__main__":
    unittest.main()
