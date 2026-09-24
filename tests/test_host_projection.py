# audience: internal
# # host-projection-tests
"""隔离目录核对宿主编译的成员选择, 范围边界, 来源授权和配置保留."""
from copy import deepcopy
import base64
import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import json
import unittest
from unittest.mock import patch

from harness_manager.catalogs import Catalogs
from harness_manager.card_relations import relation_document
from harness_manager.codex_inventory import CodexInventory
from harness_manager.content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT, TASK_CONTEXT_POINT
from harness_manager.host_projection import HOOK_POINT, compile_host, selected_sources
from harness_manager.host_ownership import reconcile
from harness_manager.projection import project_content
from harness_manager.sources import SourceReader
from harness_manager.usage import usage_document


# //// 在隔离的用户与项目目录中构造组合声明 [@x380kkm 2026-09-07] ////
class HostProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project, self.library = root / "user", root / "project", root / "library"
        self.user.mkdir()
        self.project.mkdir()
        self.library.mkdir()
        self.codex = CodexInventory(self.user)
        self.codex.root.mkdir()
        self.catalogs = Catalogs(self.user, self.project)
        self.reader = SourceReader(self.project, [self.library])
        self.skill = self.library / "method/SKILL.md"
        self.skill.parent.mkdir()
        self.skill.write_text("---\nname: method\ndescription: Read source.\n---\n# Method\nComplete procedure.\n", encoding="utf-8")
        self.module = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:method",
                       "release": {"version": "local"}, "metadata": {"name": "Method"},
                       "sources": [{"id": "files", "source": {"resolver": {"id": "manager.source/path", "range": "^1.0.0"},
                                                                "locator": str(self.library)}}],
                       "contributions": [
                           {"id": "rule", "point": INSTRUCTION_POINT, "contract": {"id": INSTRUCTION_POINT, "range": "^1.0.0"},
                            "payload": {"name": "Naming", "text": "## Naming\n\nKeep existing names.\n"}},
                           {"id": "skill", "point": SKILL_POINT, "contract": {"id": SKILL_POINT, "range": "^1.0.0"},
                            "source": "source:files", "payload": {"name": "method", "description": "Read source.", "entry": "method/SKILL.md"}}]}

    # //// 保存独立声明并沿用同一对象基线 [@x380kkm 2026-09-07] ////
    def save(self, document: dict, scope: str = "user") -> None:
        store = self.catalogs.select(scope)
        baseline = next((value for value in store.snapshot() if value["id"] == document["id"]), None)
        store.apply(store.preview_put(document, baseline))

    # //// 为组合声明保存显式范围配置 [@x380kkm 2026-09-07] ////
    def configure(self, state: str = "enabled", scope: str = "user", members: dict | None = None) -> None:
        self.save(self.module)
        settings = {"state": state}
        if members:
            settings["members"] = members
        self.save(usage_document(self.module, settings, None, scope), scope)

    # //// 编译选定目录的输出并保留宿主文件原位 [@x380kkm 2026-09-07] ////
    def compile(self, scope: str = "user") -> dict:
        return compile_host(self.catalogs, self.codex, self.reader, scope)

    # //// 来源观察和未绑定声明保持宿主配置原状 [@x380kkm 2026-09-07] ////
    def test_unbound_sources_do_not_take_over_host_files(self) -> None:
        self.save(self.module)
        config = self.codex.root / "config.toml"
        config.write_text("invalid = [", encoding="utf-8")
        before = config.read_bytes()
        result = self.compile()
        self.assertEqual(result, {"targets": {}, "diagnostics": [], "contributions": []})
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.codex.root / "AGENTS.override.md").exists())

    # //// 组合成员输出保留未受管配置及其注释 [@x380kkm 2026-09-07] ////
    def test_module_compiles_rules_skills_and_preferences_without_writing(self) -> None:
        self.module["contributions"].append({"id": "preference", "point": PREFERENCE_POINT,
                                             "contract": {"id": PREFERENCE_POINT, "range": "^1.0.0"},
                                             "payload": {"name": "Evidence", "guidance": "Use current evidence."}})
        self.configure()
        config = self.codex.root / "config.toml"
        original = ('# user model\nmodel = "chosen-model"\n[mcp_servers.local]\ncommand = "private-command"\n'
                    '# preserved environment\n[mcp_servers.local.env]\nTOKEN = "private-value"\n'
                    '[[skills.config]]\n# independent skill\npath = "independent"\nenabled = false\n'
                    '[[skills.config]]\n# managed skill comment\npath = "' + self.skill.as_posix() + '"\nenabled = false\n')
        config.write_text(original, encoding="utf-8")
        result = self.compile()
        self.assertEqual(result["diagnostics"], [])
        generated = result["targets"]["config.toml"].decode("utf-8")
        parsed = tomllib.loads(generated)
        self.assertEqual(parsed["model"], "chosen-model")
        self.assertEqual(parsed["mcp_servers"]["local"]["env"], {"TOKEN": "private-value"})
        self.assertEqual(parsed["skills"]["config"], [{"path": "independent", "enabled": False},
                                                      {"path": self.skill.as_posix(), "enabled": True}])
        self.assertIn("# independent skill", generated)
        self.assertIn("# managed skill comment", generated)
        rules = result["targets"]["AGENTS.override.md"].decode("utf-8")
        self.assertIn(self.module["contributions"][0]["payload"]["text"], rules)
        self.assertIn("Use current evidence.", rules)
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        self.assertFalse((self.codex.root / "AGENTS.override.md").exists())
        self.assertNotIn("private-value", str(result["contributions"]))

    # //// 关闭全部规则保留有效覆盖文件并关闭受管 Skill [@x380kkm 2026-09-07] ////
    def test_disabled_module_generates_nonempty_instruction_override(self) -> None:
        self.configure("disabled")
        result = self.compile()
        self.assertEqual(result["diagnostics"], [])
        self.assertTrue(result["targets"]["AGENTS.override.md"].strip())
        self.assertNotIn(b"Keep existing names", result["targets"]["AGENTS.override.md"])
        self.assertFalse(tomllib.loads(result["targets"]["config.toml"].decode())["skills"]["config"][0]["enabled"])

    # //// 模块成员选择与项目个人覆盖沿用有效绑定 [@x380kkm 2026-09-07] ////
    def test_member_selection_and_project_override_drive_skill_state(self) -> None:
        self.configure(members={"skill": "exclude"})
        user = self.compile()
        self.assertIn(b"Keep existing names", user["targets"]["AGENTS.override.md"])
        self.assertFalse(tomllib.loads(user["targets"]["config.toml"].decode())["skills"]["config"][0]["enabled"])
        self.configure(scope="project-local", members={"skill": "include"})
        project = self.compile("project-local")
        self.assertEqual(project["diagnostics"], [])
        self.assertIn(b"Keep existing names", project["targets"]["AGENTS.override.md"])
        self.assertTrue(tomllib.loads(project["targets"]["config.toml"].decode())["skills"]["config"][0]["enabled"])
        self.assertFalse((self.project / ".codex/config.toml").exists())

    # //// 项目排除仍在全局启用的规则时保持宿主输出受阻 [@x380kkm 2026-09-07] ////
    def test_project_cannot_remove_globally_enabled_rule(self) -> None:
        self.configure()
        self.configure(scope="project-local", members={"rule": "exclude"})
        result = self.compile("project-local")
        self.assertEqual(result["targets"], {})
        self.assertIn("host_inherited_rule_scope", {item["code"] for item in result["diagnostics"]})

    # //// 多个组合别名引用同一正文时只输出一次 [@x380kkm 2026-09-07] ////
    def test_aliases_deduplicate_by_original_content_reference(self) -> None:
        self.configure()
        wrapper = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:wrapper", "release": {"version": "local"},
                   "contributions": [{"id": "one", "ref": "plugin:method#rule", "constraint": "local"},
                                     {"id": "two", "ref": "plugin:method#rule", "constraint": "local"}]}
        self.save(wrapper)
        self.save(usage_document(wrapper, {"state": "enabled"}, None, "user"))
        result = self.compile()
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["targets"]["AGENTS.override.md"].count(b"Keep existing names"), 1)

    # //// 同源引用的全部绑定参与宿主规则范围选择 [@x380kkm 2026-09-08] ////
    def test_instruction_aliases_keep_the_narrow_binding_until_selection(self) -> None:
        self.module["contributions"] = self.module["contributions"][:1]
        self.module["contributions"][0]["payload"]["slot"] = "execution"
        competing = deepcopy(self.module)
        competing["id"] = "plugin:competing"
        competing["contributions"][0]["payload"]["text"] = "Use another procedure."
        first = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:a", "release": {"version": "local"},
                 "contributions": [{"id": "rule", "ref": "plugin:method#rule", "constraint": "local"}]}
        second = {**deepcopy(first), "id": "plugin:z"}
        for document in (self.module, competing, first, second):
            self.save(document)
        for document in (competing, first):
            self.save(usage_document(document, {"state": "enabled"}, None, "user"))
        self.save(usage_document(second, {"state": "enabled"}, None, "project-local"), "project-local")
        view = self.catalogs.for_scope("project-local")
        context = {"user": getpass.getuser(), **self.catalogs.context("project-local")}
        entries, diagnostics = project_content(view.documents, context, layers=view.layers)
        hosted = selected_sources(entries, context, diagnostics)
        self.assertEqual(diagnostics, [])
        self.assertEqual(list(hosted), ["plugin:method#rule"])

    # //// 有向配套说明由来源选择控制并保持目标 Skill 独立 [@x380kkm 2026-09-07] ////
    def test_directional_adapter_is_static_and_does_not_enable_target(self) -> None:
        self.module["contributions"] = self.module["contributions"][1:]
        self.configure()
        target = deepcopy(self.module)
        target["id"] = "plugin:target"
        target["contributions"][0]["payload"].update(name="target", entry="target/SKILL.md")
        target_path = self.library / "target/SKILL.md"
        target_path.parent.mkdir()
        target_path.write_text("---\nname: target\ndescription: Optional method.\n---\nOptional procedure.\n", encoding="utf-8")
        self.save(target)
        source_subject = {"id": "card:method", "name": "method", "kind": "skill", "ref": "plugin:method#skill", "version": "local"}
        target_subject = {"id": "card:target", "name": "target", "kind": "skill", "ref": "plugin:target#skill", "version": "local"}
        text = "使用本 skill 时可以参考使用 **target** skill.\n\n保留原始方法."
        adapter = relation_document(source_subject, target_subject, text, "user")
        self.save(adapter)
        self.save(usage_document(adapter, {"state": "enabled"}, None, "user"))
        result = self.compile()
        self.assertEqual(result["diagnostics"], [])
        rules = result["targets"]["AGENTS.override.md"].decode("utf-8")
        self.assertIn("## 使用 method 时\n\n" + text, rules)
        self.assertNotIn("content.open", rules)
        self.assertNotIn("Manager", rules)
        skill_settings = tomllib.loads(result["targets"]["config.toml"].decode())["skills"]["config"]
        self.assertEqual(skill_settings, [{"path": self.skill.as_posix(), "enabled": True}])
        self.configure("disabled")
        source_off = self.compile()
        self.assertNotIn(text, source_off["targets"]["AGENTS.override.md"].decode("utf-8"))
        self.assertFalse(next(value for value in source_off["contributions"] if value["point"] == TASK_CONTEXT_POINT)["enabled"])
        self.save(usage_document(target, {"state": "enabled"}, None, "user"))
        target_on = self.compile()
        self.assertNotIn(text, target_on["targets"]["AGENTS.override.md"].decode("utf-8"))
        settings = {row["path"]: row["enabled"] for row in tomllib.loads(target_on["targets"]["config.toml"].decode())["skills"]["config"]}
        self.assertFalse(settings[self.skill.as_posix()])
        self.assertTrue(settings[target_path.as_posix()])

    # //// 规则来源的配套说明由 subject 引用选择 [@x380kkm 2026-09-07] ////
    def test_rule_subject_adapter_follows_rule_selection(self) -> None:
        self.module["contributions"] = self.module["contributions"][:1]
        self.configure()
        source = {"id": "card:rule", "name": "Naming", "kind": "rule", "ref": "plugin:method#rule", "version": "local"}
        target = {"id": "card:external", "name": "Method", "kind": "skill", "ref": "plugin:external#skill", "version": "local"}
        adapter = relation_document(source, target, "Keep the accompanying analysis.", "user")
        self.save(adapter)
        self.save(usage_document(adapter, {"state": "enabled"}, None, "user"))
        result = self.compile()
        self.assertEqual(result["diagnostics"], [])
        self.assertIn(b"Keep the accompanying analysis.", result["targets"]["AGENTS.override.md"])
        self.configure(members={"rule": "exclude"})
        self.assertNotIn(b"Keep the accompanying analysis.", self.compile()["targets"]["AGENTS.override.md"])

    # //// 任务与局部目录条件通过阻断保留原有范围 [@x380kkm 2026-09-07] ////
    def test_limited_rule_scope_cannot_become_global_guidance(self) -> None:
        self.configure()
        for selector in ({"task": "write"}, {"agent": "worker"}, {"path": str(self.project / "part")}):
            with self.subTest(selector=selector):
                self.module["contributions"][0]["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"},
                                                           "selector": selector}
                self.save(self.module)
                result = self.compile()
                self.assertEqual(result["targets"], {})
                self.assertIn("host_scope_adapter", {item["code"] for item in result["diagnostics"]})
                self.assertTrue(all(item["severity"] == "error" for item in result["diagnostics"]))

    # //// 其他宿主的限定绑定与成员保留当前宿主的独立编译 [@x380kkm 2026-09-10] ////
    def test_other_host_scopes_do_not_block_current_host(self) -> None:
        self.configure()
        expected = self.compile()
        binding = usage_document(self.module, {"state": "enabled"}, None, "user")
        binding["id"] = "binding:manager-only"
        for selector in ({"task": "review", "host": "harness-manager"},
                         {"host": "harness-manager", "task": "review"}):
            with self.subTest(selector=selector):
                binding["target"]["selector"] = selector
                self.save(binding)
                self.assertEqual(self.compile(), expected)
        member = deepcopy(self.module["contributions"][0])
        member["id"] = "manager-rule"
        member["scope"] = deepcopy(binding["target"])
        self.module["contributions"].append(member)
        self.save(self.module)
        self.assertEqual(self.compile(), expected)

    # //// 当前宿主的任务限定绑定保留载体适配诊断 [@x380kkm 2026-09-10] ////
    def test_current_host_task_scope_requires_adapter(self) -> None:
        self.configure()
        binding = usage_document(self.module, {"state": "enabled"}, None, "user")
        binding["target"]["selector"] = {"task": "review", "host": "codex"}
        self.save(binding)
        result = self.compile()
        self.assertEqual(result["targets"], {})
        self.assertIn("host_scope_adapter", {item["code"] for item in result["diagnostics"]})

    # //// 项目编译先排除属于其他项目的任务绑定 [@x380kkm 2026-09-10] ////
    def test_other_project_scopes_do_not_block_current_project(self) -> None:
        self.configure(scope="project-local")
        expected = self.compile("project-local")
        binding = usage_document(self.module, {"state": "enabled"}, None, "project-local")
        binding["id"] = "binding:other-project"
        for selector in ({"task": "review", "project": self.library.as_uri()},
                         {"project": self.library.as_uri(), "task": "review"}):
            with self.subTest(selector=selector):
                binding["target"]["selector"] = selector
                self.save(binding)
                self.assertEqual(self.compile("project-local"), expected)

    # //// 引用链的宿主排除同时覆盖下层任务范围 [@x380kkm 2026-09-10] ////
    def test_other_host_alias_does_not_require_target_adapter(self) -> None:
        self.configure()
        expected = self.compile()
        rule = deepcopy(self.module["contributions"][0])
        rule["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"task": "review"}}
        other = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:task-rule",
                 "release": {"version": "local"}, "contributions": [rule]}
        self.save(other)
        self.module["contributions"].append({"id": "manager-alias", "ref": "plugin:task-rule#rule", "scope": {
            "contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": "harness-manager"}}})
        self.save(self.module)
        self.assertEqual(self.compile(), expected)

    # //// Hook 与工具缺少载体映射时保留全部宿主文件 [@x380kkm 2026-09-07] ////
    def test_unsupported_carriers_block_partial_module_and_execute_nothing(self) -> None:
        hooks = self.codex.root / "hooks.json"
        hooks.write_text('{"hooks":{"SessionStart":[{"command":"private-hook"}]}}', encoding="utf-8")
        before = hooks.read_bytes()
        for point in ("lifecycle.example/hooks", "tool.x380kkm/endpoint", "unknown.example/content"):
            self.module["contributions"] = self.module["contributions"][:2] + [
                {"id": "carrier", "point": point, "contract": {"id": point, "range": "^1.0.0"},
                 "payload": {"command": "do-not-run", "name": "Carrier"}}]
            self.configure()
            with patch("subprocess.run", side_effect=AssertionError("unexpected execution")):
                result = self.compile()
            self.assertEqual(result["targets"], {})
            self.assertIn("host_carrier_adapter", {item["code"] for item in result["diagnostics"]})
            self.assertEqual(hooks.read_bytes(), before)

    # //// 明确排除的 Tool 保留规则与已管理载体的开闭输出 [@x380kkm 2026-09-10] ////
    def test_excluded_tool_does_not_block_supported_carrier_reconciliation(self) -> None:
        self.module["contributions"].append({"id": "tool", "point": "tool.x380kkm/endpoint",
                                             "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"},
                                             "payload": {"name": "Tool", "command": "inspect"}})
        self.module["contributions"].append({"id": "hook", "point": HOOK_POINT, "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
                                             "payload": {"event": "Stop", "handlers": [{"type": "command", "command": "inspect"}]}})
        for selector in ({}, {"task": "review"}, {"agent": "reviewer"}):
            with self.subTest(selector=selector):
                self.module["contributions"][2]["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": selector}
                self.configure(members={"tool": "exclude"})
                enabled = self.compile()
                self.assertEqual(enabled["diagnostics"], [])
                self.assertIn(b"Keep existing names.", enabled["targets"]["AGENTS.override.md"])
                self.assertEqual({item["ref"].partition("#")[2] for item in enabled["contributions"]}, {"rule", "skill", "hook"})
                self.configure("disabled", members={"tool": "exclude"})
                disabled = self.compile()
                self.assertEqual(disabled["diagnostics"], [])
                self.assertTrue(all(not item["enabled"] for item in disabled["contributions"]))
                self.assertNotIn(b"Keep existing names.", disabled["targets"]["AGENTS.override.md"])
                self.assertEqual(json.loads(disabled["targets"]["hooks.json"]), {"hooks": {}})

    # //// 必需 Tool 的排除和已选未知载体都保留编译诊断 [@x380kkm 2026-09-10] ////
    def test_required_excluded_tool_and_selected_unknown_contract_stay_blocked(self) -> None:
        tool = {"id": "tool", "point": "tool.x380kkm/endpoint", "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"},
                "payload": {"name": "Tool"}, "criticality": {"default": "required"}}
        self.module["contributions"].append(tool)
        self.configure(members={"tool": "exclude"})
        result = self.compile()
        self.assertEqual(result["targets"], {})
        self.assertIn("required_excluded", {item["code"] for item in result["diagnostics"]})
        self.module["contributions"].pop()
        self.module["contributions"][0]["contract"]["range"] = "^2.0.0"
        self.configure()
        result = self.compile()
        self.assertEqual(result["targets"], {})
        self.assertIn("host_carrier_adapter", {item["code"] for item in result["diagnostics"]})

    # //// 整体关闭的 Tool 保留其他包中规则的独立部署 [@x380kkm 2026-09-10] ////
    def test_disabled_tool_with_task_scope_preserves_other_rules(self) -> None:
        self.configure()
        expected = self.compile()
        tools = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:separate-tools", "release": {"version": "local"},
                 "contributions": [{"id": "tool", "point": "tool.x380kkm/endpoint",
                                    "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"},
                                    "scope": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"task": "review"}},
                                    "payload": {"name": "Tool"}}]}
        self.save(tools)
        self.save(usage_document(tools, {"state": "disabled"}, None, "user"))
        self.assertEqual(self.compile(), expected)

    # //// 显式生命周期成员输出完整事件组并保持信任由宿主管理 [@x380kkm 2026-09-07] ////
    def test_hooks_compile_for_user_and_project_without_execution(self) -> None:
        member = {"id": "hook", "point": HOOK_POINT, "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
                  "payload": {"name": "Inspect writes", "event": "PostToolUse", "matcher": "Edit|Write", "handlers": [
                      {"type": "command", "command": "pwsh -File inspect.ps1", "commandWindows": "pwsh -File windows.ps1",
                       "timeout": 30, "statusMessage": "Inspecting files", "async": True, "additionalContextLimit": 5000},
                      {"type": "mcp_tool", "server": "scanner", "tool": "scan_patch", "input": {"patch": "${tool_input.command}"}}]}}
        self.module["contributions"] = [member]
        self.save(self.module)
        binding = usage_document(self.module, {"state": "enabled"}, None, "project-local")
        self.save(binding, "project-local")
        with patch("subprocess.run", side_effect=AssertionError("unexpected hook execution")):
            project = self.compile("project-local")
        self.assertEqual(project["diagnostics"], [])
        group = {"matcher": "Edit|Write", "hooks": member["payload"]["handlers"]}
        self.assertEqual(json.loads(project["targets"]["hooks.json"]), {"hooks": {"PostToolUse": [group]}})
        self.assertEqual(project["contributions"][0]["hook"], {"event": "PostToolUse", "group": group})
        self.assertNotIn("config.toml", project["targets"])
        binding["enabled"] = False
        self.save(binding, "project-local")
        self.assertEqual(json.loads(self.compile("project-local")["targets"]["hooks.json"]), {"hooks": {}})
        self.configure()
        user = self.compile()
        self.assertEqual(user["diagnostics"], [])
        self.assertEqual(json.loads(user["targets"]["hooks.json"]), {"hooks": {"PostToolUse": [group]}})
        self.assertFalse((self.codex.root / "hooks.json").exists())

    # //// 项目继承全局 Hook 时沿用用户来源并阻断重复配置 [@x380kkm 2026-09-07] ////
    def test_project_inherits_user_hook_without_copying_or_disabling_it(self) -> None:
        self.module["contributions"] = [{"id": "hook", "point": HOOK_POINT, "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
                                         "payload": {"event": "SessionStart", "handlers": [{"type": "command", "command": "inspect"}]}}]
        self.configure()
        inherited = self.compile("project-local")
        self.assertEqual(inherited["diagnostics"], [])
        self.assertNotIn("hooks.json", inherited["targets"])
        self.configure("disabled", "project-local")
        disabled = self.compile("project-local")
        self.assertEqual(disabled["targets"], {})
        self.assertIn("host_inherited_hook_scope", {note["code"] for note in disabled["diagnostics"]})

    # //// 编译归属在最后一个模块绑定移除后清理全部新增输出 [@x380kkm 2026-09-07] ////
    def test_compiler_and_ownership_remove_outputs_after_last_binding(self) -> None:
        self.module["contributions"].append({"id": "hook", "point": HOOK_POINT, "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
                                             "payload": {"event": "SessionStart", "handlers": [{"type": "command", "command": "inspect"}]}})
        self.configure()
        compiled = self.compile()
        targets, ownership = reconcile(compiled["targets"], compiled["contributions"],
                                       {name: None for name in compiled["targets"]}, {}, self.codex.root)
        binding = next(document for document in self.catalogs.user.snapshot() if document["kind"] == "PluginBinding")
        self.catalogs.user.apply(self.catalogs.user.preview_remove(binding["id"], binding))
        current = {name: base64.b64encode(value).decode("ascii") for name, value in targets.items()}
        unbound = self.compile()
        cleared, final = reconcile(unbound["targets"], unbound["contributions"], current, ownership, self.codex.root)
        self.assertIsNone(cleared["AGENTS.override.md"])
        self.assertIsNone(cleared["hooks.json"])
        self.assertEqual(tomllib.loads(cleared["config.toml"].decode()), {})
        self.assertEqual(final, {})

    # //// 宿主跳过的处理器与匹配条件使编译明确阻断 [@x380kkm 2026-09-07] ////
    def test_unsupported_hook_handlers_and_ignored_matchers_are_errors(self) -> None:
        payloads = [
            {"event": "Stop", "handlers": [{"type": "prompt", "prompt": "Review"}]},
            {"event": "SessionEnd", "handlers": [{"type": "mcp_tool", "server": "one", "tool": "two"}]},
            {"event": "Stop", "matcher": "Edit", "handlers": [{"type": "command", "command": "inspect"}]},
            {"event": "Interrupt", "handlers": [{"type": "command", "command": "inspect", "timeout": 30}]},
            {"event": "SessionStart", "handlers": [{"type": "command", "command": "inspect", "trust": True}]},
        ]
        for payload in payloads:
            self.module["contributions"] = [{"id": "hook", "point": HOOK_POINT,
                                             "contract": {"id": HOOK_POINT, "range": "^1.0.0"}, "payload": payload}]
            self.configure()
            result = self.compile()
            self.assertEqual(result["targets"], {})
            self.assertTrue(result["diagnostics"])
            self.assertTrue(all(item["severity"] == "error" for item in result["diagnostics"]))

    # //// 来源正文沿用读取授权并完整保留文本 [@x380kkm 2026-09-07] ////
    def test_file_rules_use_authorized_complete_source(self) -> None:
        text = "# 完整规则\n\n第一段.\n\n第二段.\n"
        rule = self.library / "rule.md"
        rule.write_text(text, encoding="utf-8")
        self.module["contributions"] = self.module["contributions"][:1]
        self.module["contributions"][0].update(source="source:files", payload={"name": "Whole file", "entry": "rule.md"})
        self.configure()
        self.assertIn(rule.read_bytes().decode("utf-8"), self.compile()["targets"]["AGENTS.override.md"].decode("utf-8"))
        self.reader = SourceReader(self.project, [])
        refused = self.compile()
        self.assertEqual(refused["targets"], {})
        self.assertIn("host_source_access", {item["code"] for item in refused["diagnostics"]})

    # //// 未物化 Skill 与无效宿主 TOML 通过阻断保留内容 [@x380kkm 2026-09-07] ////
    def test_unresolved_skill_and_invalid_config_are_not_deployed(self) -> None:
        self.configure()
        original = deepcopy(self.module)
        self.module["contributions"][1].pop("source")
        self.save(self.module)
        self.assertIn("host_skill_source", {item["code"] for item in self.compile()["diagnostics"]})
        self.module = original
        self.save(self.module)
        config = self.codex.root / "config.toml"
        config.write_text("private_value = [", encoding="utf-8")
        result = self.compile()
        self.assertEqual(result["targets"], {})
        self.assertIn("host_config_parse", {item["code"] for item in result["diagnostics"]})
        self.assertNotIn("private_value", str(result["diagnostics"]))
