# audience: internal
# # module-operation-tests
# 隔离目录验证组合创建, 来源守卫和显式使用设置的交互边界.

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import source_document
from harness_manager.module_contexts import PRESENTATION_CONTRACT
from harness_manager.module_inventory import module_inventory, module_presentation
from harness_manager.module_operations import ModuleError, ModuleOperations
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.storage import Store
from harness_manager.storage_errors import StorageConflictError
from test_projection import make_plugin


# //// 使用真实目录完成模块组合与启用用例 [@x380kkm 2026-09-07] ////
class ModuleOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project = root / "user", root / "project"
        self.project.mkdir()
        self.skill = self.user / ".agents/skills/shell/SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("---\nname: shell\ndescription: Shell method.\n---\n# Shell\nUse PowerShell.\n", encoding="utf-8")
        self.manager = Manager(self.project, [self.user], user_root=self.user)
        self.operations = ModuleOperations(self.manager.catalogs, self.manager.codex)
        self.rule = {"id": "rule:shell", "name": "PowerShell 使用规范", "kind": "rule", "summary": "命令环境.",
                     "content": "Use pwsh.", "path": str(self.user / ".codex/AGENTS.md"), "scope": "user"}
        self.rule_document = source_document(self.rule)
        self.save(self.rule_document)
        self.choices = {choice["name"]: choice for choice in self.operations.describe()["candidates"]}
        self.members = [{"itemId": self.rule["id"], "role": "命令执行规则"}, {"itemId": self.choices["shell"]["itemId"]}]

    # //// 保存指定层的测试声明 [@x380kkm 2026-09-07] ////
    def save(self, document: dict, baseline: dict | None = None, scope: str = "user") -> None:
        store = self.manager.catalogs.select(scope)
        store.apply(store.preview_put(document, baseline))

    # //// 保存组合后显式开启并通过现有读取接口使用 [@x380kkm 2026-09-07] ////
    def test_create_module_preserves_sources_and_needs_explicit_binding(self) -> None:
        original = self.skill.read_bytes()
        preview = self.operations.preview("命令执行", "规则与方法配合.", self.members)
        self.assertEqual(len(self.manager.store.snapshot()), 1)
        result = self.operations.apply(preview["plan"])
        documents = self.manager.store.snapshot()
        self.assertEqual(len(documents), 3)
        self.assertTrue(all(document["kind"] == "Plugin" for document in documents))
        self.assertEqual(self.manager.discover_content(scope="user")["candidates"], [])
        module = result["document"]
        self.assertTrue(all("ref" in member and member["constraint"] == "local" for member in module["contributions"]))
        self.assertEqual(self.skill.read_bytes(), original)
        usage = self.manager.preview_usage(result["documentId"], {"state": "enabled", "acceptance": "current",
                                            "members": {member["id"]: "inherit" for member in module["contributions"]}})
        self.manager.apply_document(usage["plan"])
        candidates = self.manager.discover_content(scope="user")["candidates"]
        self.assertEqual({candidate["name"] for candidate in candidates}, {"shell", "PowerShell 使用规范"})
        modules, _, diagnostics = module_inventory(self.manager.catalogs, "user", [self.rule])
        self.assertEqual(diagnostics, [])
        self.assertEqual(modules[0]["id"], result["id"])

    # //// 自定义模块绑定在卡片和使用接口显示同一启用状态 [@x380kkm 2026-09-10] ////
    def test_custom_module_binding_is_visible_as_current_usage(self):
        result = self.operations.apply(self.operations.preview("命令执行", "", self.members)["plan"])
        preview = self.manager.preview_usage(result["documentId"], {"id": "binding:custom/module", "state": "enabled",
                                                                    "options": {"mode": "personal"}})
        self.manager.apply_document(preview["plan"])
        module = next(item for item in self.manager.snapshot_cards()["items"] if item["id"] == result["id"])
        self.assertEqual(module["details"]["configuredState"], "enabled")
        usage = self.manager.describe_usage(result["documentId"], context={"host": "codex"})
        self.assertEqual([binding["id"] for binding in usage["contextBindings"]], ["binding:custom/module"])
        self.assertEqual(usage["contextBindings"][0]["options"], {"mode": "personal"})

    # //// 编辑保持载体说明和发布中的其他字段 [@x380kkm 2026-09-07] ////
    def test_edit_preserves_annotations_member_scope_and_unrelated_fields(self) -> None:
        result = self.operations.apply(self.operations.preview("命令执行", "", self.members)["plan"])
        before = result["document"]
        custom = deepcopy(before)
        custom["metadata"]["homepage"] = "https://example.test/shell"
        custom["options"] = {"schema": {"type": "object"}, "defaults": {"shell": "pwsh"}}
        first = custom["contributions"][0]
        first["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"task": "coding"}}
        module_presentation(custom)["members"][first["id"]]["carrier"] = "AGENTS.md"
        self.save(custom, before)
        description = self.operations.describe(result["id"])
        members = list(reversed(description["settings"]["members"]))
        members[1]["role"] = "执行环境"
        updated = self.operations.apply(self.operations.preview("执行配置", "来源分别维护.", members,
                                                                 id=result["documentId"], baseline=description["baseline"])["plan"])["document"]
        self.assertEqual(updated["contributions"][1]["id"], first["id"])
        self.assertEqual(updated["contributions"][1]["scope"], first["scope"])
        self.assertEqual(module_presentation(updated)["members"][first["id"]]["carrier"], "AGENTS.md")
        self.assertEqual(module_presentation(updated)["members"][first["id"]]["role"], "执行环境")
        self.assertEqual(updated["metadata"]["homepage"], custom["metadata"]["homepage"])
        self.assertEqual(updated["options"], custom["options"])
        self.assertEqual(next(document for document in self.manager.store.snapshot() if document["id"] == self.rule_document["id"]), self.rule_document)

    # //// 工具与 Hook 的观察需要可引用的接口声明 [@x380kkm 2026-09-07] ////
    def test_tool_and_hook_choices_require_declared_contributions(self) -> None:
        root = self.user / ".codex"
        root.mkdir()
        (root / "hooks.json").write_text('{"hooks":{"Start":[{"hooks":[{"command":"private-hook"}]}]}}', encoding="utf-8")
        (root / "config.toml").write_text('[mcp_servers.shell]\ncommand="private-command"\n', encoding="utf-8")
        choices = self.operations.describe()["candidates"]
        observations = [choice for choice in choices if choice["kind"] in {"tool", "hook"}]
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(not choice["supported"] and choice["reason"] for choice in observations))
        with self.assertRaises(ModuleError):
            self.operations.preview("不可执行的观察", "", [{"itemId": observations[0]["itemId"]}])
        tool = make_plugin("plugin:shell/tool")
        tool["contributions"] = [{"id": "shell", "point": "tool.x380kkm/endpoint",
                                  "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"}, "payload": {"name": "shell"}}]
        self.save(tool)
        choice = next(choice for choice in self.operations.describe()["candidates"] if choice["kind"] == "tool" and choice["supported"])
        result = self.operations.apply(self.operations.preview("工具配置", "", [{"itemId": choice["itemId"]}])["plan"])
        self.assertEqual(result["document"]["contributions"][0]["ref"], tool["id"] + "#shell")

    # //// Hook 成员保留独立版本并通过模块表单维护用途及顺序 [@x380kkm 2026-09-07] ////
    def test_hook_members_preserve_versions_and_editable_roles(self) -> None:
        hook = make_plugin("plugin:events/start")
        hook["contributions"] = [{"id": "event", "point": "hook.x380kkm/lifecycle",
                                   "contract": {"id": "hook.x380kkm/lifecycle", "range": "^1.0.0"},
                                   "payload": {"name": "启动事件", "event": "Start"}}]
        self.save(hook)
        candidate = next(choice for choice in self.operations.describe()["candidates"] if choice["kind"] == "hook")
        self.assertTrue(candidate["supported"])
        members = [self.members[0], {"itemId": candidate["itemId"], "role": "执行准备"}, self.members[1]]
        result = self.operations.apply(self.operations.preview("命令执行", "", members)["plan"])
        before = result["document"]
        module = deepcopy(before)
        member = module["contributions"][1]
        module_presentation(module)["members"][member["id"]]["carrier"] = "hooks.json"
        self.save(module, before)
        description = self.operations.describe(result["id"])
        selected = description["settings"]["members"]
        self.assertEqual(len(selected), 3)
        selected[1]["role"] = "工作区准备"
        selected.insert(0, selected.pop(1))
        preview = self.operations.preview("命令配置", "", selected,
                                          id=result["id"], baseline=description["baseline"])
        updated = self.operations.apply(preview["plan"])["document"]
        self.assertEqual(updated["contributions"][0], member)
        annotation = module_presentation(updated)["members"][member["id"]]
        self.assertEqual(annotation["carrier"], "hooks.json")
        self.assertEqual(annotation["role"], "工作区准备")
        self.assertEqual(member["constraint"], "1.0.0")
        self.assertEqual(next(document for document in self.manager.store.snapshot() if document["id"] == hook["id"]), hook)
        private = self.operations.apply(self.operations.preview("项目准备", "", members, scope="project-local")["plan"])
        self.assertEqual(private["document"]["contributions"][1]["ref"], hook["id"] + "#event")
        self.save(hook, scope="project")
        shared = self.operations.apply(self.operations.preview("共享准备", "", [{"itemId": candidate["itemId"]}], scope="project")["plan"])
        self.assertEqual(shared["document"]["contributions"][0]["constraint"], "1.0.0")

    # //// 自定义接口可通过明确的成员类型参与组合 [@x380kkm 2026-09-07] ////
    def test_declared_custom_hook_kind_is_selectable(self) -> None:
        hook = make_plugin("plugin:events/custom")
        hook["contributions"] = [{"id": "event", "point": "example/event",
                                   "contract": {"id": "example/event", "range": "^1.0.0"}, "payload": {"name": "启动事件"}}]
        hook["extensions"] = [{"contract": {"id": PRESENTATION_CONTRACT, "range": "^1.0.0"},
                                "payload": {"members": {"event": {"kind": "hook"}}}}]
        self.save(hook)
        candidate = next(choice for choice in self.operations.describe()["candidates"] if choice["kind"] == "hook")
        result = self.operations.apply(self.operations.preview("事件组合", "", [{"itemId": candidate["itemId"]}])["plan"])
        self.assertEqual(result["document"]["contributions"][0]["ref"], hook["id"] + "#event")

    # //// 来源在目录锁前变化时联合提交保持原子性 [@x380kkm 2026-09-07] ////
    def test_source_change_during_apply_preserves_all_pending_entries(self) -> None:
        preview = self.operations.preview("执行配置", "", self.members)
        original_apply = Store.apply_many
        injected = False

        # //// 在模块提交前保存协作者的规则编辑 [@x380kkm 2026-09-07] ////
        def apply_with_change(store, plans, **options):
            nonlocal injected
            if not injected and any(entry["id"] == preview["documentId"] for entry in plans):
                injected = True
                changed = deepcopy(self.rule_document)
                changed["metadata"]["description"] = "协作者修改."
                original_apply(store, [store.preview_put(changed, self.rule_document)])
            return original_apply(store, plans, **options)

        with patch.object(Store, "apply_many", apply_with_change):
            with self.assertRaises(StorageConflictError):
                self.operations.apply(preview["plan"])
        documents = self.manager.store.snapshot()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["metadata"]["description"], "协作者修改.")

    # //// 项目个人组合核对继承来源基线并保持用户目录完整 [@x380kkm 2026-09-07] ////
    def test_project_private_module_detects_changed_user_source(self) -> None:
        with self.assertRaises(ModuleError) as raised:
            self.operations.preview("共享执行", "", self.members, scope="project")
        self.assertEqual(raised.exception.code, "module_shared_source")
        preview = self.operations.preview("项目执行", "", self.members, scope="project-local")
        changed = deepcopy(self.rule_document)
        changed["contributions"][0]["payload"]["text"] = "Changed rule."
        self.save(changed, self.rule_document)
        with self.assertRaises(StorageConflictError):
            self.operations.apply(preview["plan"])
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])
        self.assertEqual(self.manager.catalogs.project.snapshot(), [])

    # //// 目录和差异注入无法改变模块表单的写入集合 [@x380kkm 2026-09-07] ////
    def test_tampered_plan_and_duplicate_members_are_rejected(self) -> None:
        with self.assertRaises(ModuleError):
            self.operations.preview("重复", "", [self.members[0], self.members[0]])
        preview = self.operations.preview("执行配置", "", self.members)
        outside = deepcopy(preview["plan"])
        outside["catalog"] = str(self.project / "outside.json")
        with self.assertRaises(ModuleError):
            self.operations.apply(outside)
        altered = deepcopy(preview["plan"])
        altered["entries"][0]["after"]["metadata"]["name"] = "替换来源"
        with self.assertRaises(ModuleError):
            self.operations.apply(altered)
        self.assertEqual(self.manager.store.snapshot(), [self.rule_document])


if __name__ == "__main__":
    unittest.main()
