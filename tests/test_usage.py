# audience: internal
# # usage-settings-tests
# 使用设置通过真实目录操作验证成员隔离, 继承, 接受集合和冲突恢复.
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from harness_manager.content import ContentError
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError
from test_service import create_skill


# //// 验证面板与 Agent 共用的使用设置用例 [@x380kkm 2026-09-06] ////
class UsageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.project = self.root / "project"
        self.user.mkdir()
        self.project.mkdir()
        self.path = create_skill(self.root)
        self.manager = Manager(self.project, [self.path.parent], user_root=self.user)
        self.plugin = self.manager.import_file(str(self.path))["document"]
        self.plugin["contributions"].extend([
            {"id": "endpoint", "point": "tool.x380kkm/endpoint", "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"},
             "payload": {"name": "query", "command": {"runtime": "node", "entry": "query.mjs"}}},
            {"id": "preference", "point": "preference.x380kkm/method", "contract": {"id": "preference.x380kkm/method", "range": "^1.0.0"},
             "payload": {"name": "evidence", "guidance": "按当前问题使用证据."}},
        ])
        self.manager.apply_document(self.manager.preview_document(self.plugin)["plan"])
        self.identity = document_identity(self.plugin)

    # //// 保存独立的使用绑定 [@x380kkm 2026-09-06] ////
    def save_usage(self, settings: dict, scope="user", baseline=None) -> dict:
        preview = self.manager.preview_usage(self.identity, settings, baseline, scope)
        return self.manager.apply_document(preview["plan"])["document"]

    # //// 普通 Skill, 工具和偏好分别选择且来源发布保持原样 [@x380kkm 2026-09-06] ////
    def test_member_selection_preserves_independent_plugin_content(self) -> None:
        original = self.path.read_bytes()
        description = self.manager.describe_usage(self.identity)
        self.assertEqual(description["bindings"], [])
        self.assertEqual(len(description["members"]), 3)
        settings = {"members": {"example-skill": "include", "endpoint": "exclude", "preference": "exclude"}, "acceptance": "current"}
        preview = self.manager.preview_usage(self.identity, settings)
        self.assertEqual(len(self.manager.list_documents()["documents"]), 1)
        self.assertEqual([item["name"] for item in preview["effective"]], ["example-skill"])
        self.manager.apply_document(preview["plan"])
        self.assertEqual(self.manager.read_document(self.identity)["document"], self.plugin)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([item["point"] for item in self.manager.discover_content(scope="user")["candidates"]], ["skill.x380kkm/deployment"])

    # //// 仅确认表单实际列出的成员以保留新内容的接受边界 [@x380kkm 2026-09-06] ////
    def test_new_members_are_not_implicitly_accepted_by_old_form(self) -> None:
        settings = {"members": {item["id"]: "inherit" for item in self.plugin["contributions"]}, "acceptance": "current"}
        changed = deepcopy(self.plugin)
        changed["contributions"].append({**deepcopy(self.plugin["contributions"][0]), "id": "new-method"})
        self.manager.apply_document(self.manager.preview_document(changed, self.plugin)["plan"])
        usage = self.save_usage(settings)
        self.assertNotIn("new-method", usage["selectionBaseline"]["included"])
        self.assertNotIn(self.plugin["id"] + "#new-method", [item["ref"] for item in self.manager.discover_content(scope="user")["candidates"]])

    # //// 已确认集合只限制新增成员并保留上层的显式排除 [@x380kkm 2026-09-06] ////
    def test_project_acceptance_does_not_reenable_user_exclusions(self) -> None:
        self.save_usage({"members": {"endpoint": "exclude"}})
        self.save_usage({"members": {item["id"]: "inherit" for item in self.plugin["contributions"]}, "acceptance": "current"}, "project")
        references = [item["ref"] for item in self.manager.discover_content(scope="project")["candidates"]]
        self.assertNotIn(self.plugin["id"] + "#endpoint", references)
        self.assertIn(self.plugin["id"] + "#example-skill", references)

    # //// 移除本层绑定后恢复用户级选项 [@x380kkm 2026-09-06] ////
    def test_removing_project_settings_restores_user_values(self) -> None:
        user = self.save_usage({"options": {"mode": "user", "width": 80}})
        project = self.save_usage({"options": {"width": 100}}, "project")
        effective = self.manager.discover_content(scope="project")["candidates"][0]
        self.assertEqual(effective["options"], {"mode": "user", "width": 100})
        self.manager.apply_document(self.manager.preview_remove(project["id"], project, "project")["plan"])
        self.assertEqual(self.manager.discover_content(scope="project")["candidates"][0]["options"], user["options"])

    # //// 用户级预览独立于已选项目中的特殊关闭设置 [@x380kkm 2026-09-06] ////
    def test_user_preview_isolated_from_selected_project_settings(self) -> None:
        self.save_usage({})
        self.save_usage({"state": "disabled"}, "project")
        self.assertTrue(self.manager.describe_usage(self.identity, "user")["effective"])
        self.assertEqual(self.manager.describe_usage(self.identity, "project")["effective"], [])
        ref = self.plugin["id"] + "#example-skill"
        result = self.manager.open_content(ref, "local", scope="user")
        self.assertEqual(result["readiness"], "ready")
        snapshot = self.manager.content_snapshot(result["snapshot"])
        self.assertNotIn("project", snapshot["target"]["selector"])
        with self.assertRaises(ContentError):
            self.manager.open_content(ref, "local", scope="project")

    # //// 保存冲突仍返回可恢复的表单草稿 [@x380kkm 2026-09-06] ////
    def test_conflict_preserves_compiled_draft_for_json_comparison(self) -> None:
        baseline = self.save_usage({"options": {"mode": "initial"}})
        external = deepcopy(baseline)
        external["options"]["mode"] = "external"
        self.manager.apply_document(self.manager.preview_document(external, baseline)["plan"])
        with self.assertRaises(StorageConflictError) as caught:
            self.manager.preview_usage(self.identity, {"options": {"mode": "local"}}, baseline)
        self.assertEqual(caught.exception.details["document"]["options"]["mode"], "local")
        self.assertEqual(self.manager.read_document(baseline["id"])["document"], external)

    # //// 编辑局部使用字段时保留其他策略与成员意图 [@x380kkm 2026-09-06] ////
    def test_edit_preserves_fields_outside_usage_form(self) -> None:
        baseline = self.save_usage({"members": {"example-skill": "require"}})
        richer = deepcopy(baseline)
        richer["plugin"]["constraint"] = "*"
        richer["extensions"] = [{"contract": {"id": "example/host-policy", "range": "^1.0.0"}, "payload": {"mode": "selected"}}]
        self.manager.apply_document(self.manager.preview_document(richer, baseline)["plan"])
        result = self.save_usage({"options": {"width": 90}}, baseline=richer)
        self.assertEqual(result["extensions"], richer["extensions"])
        self.assertEqual(result["selection"], richer["selection"])
        self.assertEqual(result["plugin"], richer["plugin"])
        inherited = self.save_usage({"constraint": None}, baseline=result)
        self.assertNotIn("constraint", inherited["plugin"])
        self.assertEqual(inherited["extensions"], richer["extensions"])

    # //// 相同成员选择保留已有列表顺序 [@x380kkm 2026-09-06] ////
    def test_unchanged_member_order_is_preserved(self) -> None:
        baseline = self.save_usage({"members": {"preference": "include", "example-skill": "include"}})
        result = self.save_usage({"members": {"example-skill": "include", "preference": "include"}, "options": {"mode": "selected"}}, baseline=baseline)
        self.assertEqual(result["selection"], baseline["selection"])


# //// 运行使用设置验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
