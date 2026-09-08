# audience: internal
# # module-context-tests
"""模块说明的派生贡献沿用有效选择, 并保持上游内容与版本独立."""
from copy import deepcopy
import asyncio
import getpass
import json
import unittest

from mcp import Client

from harness_manager.content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT, TASK_CONTEXT_POINT
from harness_manager.host_projection import compile_host
from harness_manager.mcp_server import create_server
from harness_manager.module_contexts import module_contexts
from harness_manager.module_inventory import module_presentation
from harness_manager.projection import project_content
import test_module_operations as operations_tests


# //// 用真实模块声明核对派生说明的读取与退出条件 [@x380kkm 2026-09-08] ////
class ModuleContextTests(unittest.TestCase):
    # //// 复用隔离模块来源的构造方法 [@x380kkm 2026-09-08] ////
    def setUp(self) -> None:
        operations_tests.ModuleOperationTests.setUp(self)

    # //// 在隔离配置层保存用于比较的来源 [@x380kkm 2026-09-08] ////
    def save(self, document: dict, baseline: dict | None = None, scope: str = "user") -> None:
        operations_tests.ModuleOperationTests.save(self, document, baseline, scope)

    # //// 创建具有独立说明的模块并开启其绑定 [@x380kkm 2026-09-08] ////
    def create_context_module(self):
        role = "仅在人类化中文写作或技术报告中参考. 保留当前来源的核对过程."
        members = [{"itemId": self.choices["shell"]["itemId"], "role": role}, {"itemId": self.rule["id"]}]
        result = self.operations.apply(self.operations.preview("技术表达", "", members)["plan"])
        usage = self.manager.preview_usage(result["documentId"], {"state": "enabled"})
        self.manager.apply_document(usage["plan"])
        return result, role

    # //// 取得当前有效投影与包含模块别名的 Skill 入口 [@x380kkm 2026-09-08] ////
    def projected(self):
        view = self.manager.catalogs.for_scope("user")
        context = {"user": getpass.getuser(), "host": "codex"}
        projected, diagnostics = project_content(view.documents, context, layers=view.layers)
        self.assertEqual(diagnostics, [])
        selected = next((entry for entry in projected if entry.member["point"] == SKILL_POINT), None)
        return projected, selected

    # //// 旧 role 声明产生独立上下文并保留原始文件与固定版本 [@x380kkm 2026-09-08] ////
    def test_legacy_role_projects_context_without_copying_upstream(self) -> None:
        before = self.skill.read_bytes()
        result, role = self.create_context_module()
        projected, selected = self.projected()
        contexts = module_contexts(projected)
        self.assertEqual(len(contexts), 1)
        context = contexts[0]
        self.assertEqual(context.member["point"], TASK_CONTEXT_POINT)
        self.assertEqual(context.member["payload"]["skill"], selected.summary["ref"])
        self.assertIn(role, context.member["payload"]["text"])
        self.assertEqual(context.owner["id"], result["document"]["id"])
        self.assertEqual(self.skill.read_bytes(), before)
        self.assertEqual(json.dumps(result["document"], ensure_ascii=False).count(role), 1)
        self.assertTrue(all(member.get("constraint") == "local" for member in result["document"]["contributions"]))

    # //// 生产宿主编译器输出完整模块使用说明 [@x380kkm 2026-09-08] ////
    def test_host_compiler_outputs_module_context(self) -> None:
        _, role = self.create_context_module()
        compiled = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader)
        self.assertEqual(compiled["diagnostics"], [])
        self.assertIn(role, compiled["targets"]["AGENTS.override.md"].decode("utf-8"))

    # //// 真实 MCP 完整读取返回所选模块的使用说明 [@x380kkm 2026-09-08] ////
    def test_mcp_complete_method_read_includes_module_context(self) -> None:
        result, role = self.create_context_module()
        _, selected = self.projected()

        # //// 通过协议客户端调用模块方法的完整读取入口 [@x380kkm 2026-09-08] ////
        async def open_method():
            async with Client(create_server(self.manager)) as client:
                response = await client.call_tool("content_open", {"ref": selected.summary["ref"],
                                                                   "version": selected.summary["version"], "scope": "user"})
                self.assertFalse(response.is_error, response.content)
                return response.structured_content

        opened = asyncio.run(open_method())
        texts = [unit["content"].get("text", "") for unit in opened["units"] if isinstance(unit["content"], dict)]
        self.assertTrue(any(role in text for text in texts))
        self.assertEqual(opened["readiness"], "ready")
        self.assertEqual(self.operations.describe(result["id"])["settings"]["members"][0]["role"], role)

    # //// 共用规则的模块在单元读取和完整续读中保留各自说明 [@x380kkm 2026-09-08] ////
    def test_modules_share_rule_content_and_preserve_contexts(self) -> None:
        first, first_role = self.create_context_module()
        projected, selected = self.projected()
        rule = next(entry for entry in projected if entry.member["point"] == INSTRUCTION_POINT)
        unit = self.manager.read_content(rule.summary["ref"], rule.summary["version"])
        rule_payload = json.loads(unit["units"][0]["content"])
        before = self.manager.open_content(selected.summary["ref"], selected.summary["version"])
        second_role = "核对命令中的来源名称."
        second = self.operations.apply(self.operations.preview("命令检查", "", [
            {"itemId": self.rule["id"], "role": second_role}])["plan"])
        usage = self.manager.preview_usage(second["documentId"], {"state": "enabled"})
        self.manager.apply_document(usage["plan"])
        pending = self.manager.open_content(selected.summary["ref"], selected.summary["version"], budget=1)
        complete = self.manager.continue_content(pending["continuation"])
        self.assertEqual(complete["readiness"], "ready")
        self.assertEqual(complete["selection"], before["selection"])
        contents = [item["content"] for item in complete["units"]]
        self.assertEqual(contents.count(rule_payload), 1)
        texts = [value.get("text", "") for value in contents if isinstance(value, dict)]
        self.assertTrue(any(first_role in text for text in texts))
        self.assertTrue(any(second_role in text for text in texts))
        snapshot = self.manager.content_snapshot(complete["snapshot"])
        references = [entry["ref"] for entry in snapshot["configurations"]]
        self.assertTrue(any(reference.startswith(first["document"]["id"] + "#") for reference in references))
        self.assertTrue(any(reference.startswith(second["document"]["id"] + "#") for reference in references))
        compiled = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader)
        self.assertEqual(compiled["diagnostics"], [])
        self.assertEqual(compiled["targets"]["AGENTS.override.md"].decode("utf-8").count(self.rule["content"]), 1)

    # //// 模块关闭与成员排除会移除相应派生说明 [@x380kkm 2026-09-08] ////
    def test_module_disable_and_member_exclusion_remove_context(self) -> None:
        result, _ = self.create_context_module()
        member = result["document"]["contributions"][0]
        usage = self.manager.describe_usage(result["documentId"])
        binding = next(value for value in usage["bindings"] if value["id"] == usage["preferred"])
        preview = self.manager.preview_usage(result["documentId"], {"members": {member["id"]: "exclude"}}, binding)
        self.manager.apply_document(preview["plan"])
        projected, _ = self.projected()
        self.assertEqual(module_contexts(projected), [])
        usage = self.manager.describe_usage(result["documentId"])
        binding = next(value for value in usage["bindings"] if value["id"] == usage["preferred"])
        preview = self.manager.preview_usage(result["documentId"], {"state": "disabled"}, binding)
        self.manager.apply_document(preview["plan"])
        self.assertEqual(module_contexts(self.projected()[0]), [])

    # //// 清空说明与移出成员后只保留当前模块内容 [@x380kkm 2026-09-08] ////
    def test_role_edit_and_member_removal_update_context(self) -> None:
        result, role = self.create_context_module()
        description = self.operations.describe(result["id"])
        members = deepcopy(description["settings"]["members"])
        members[0]["role"] = "仅在技术报告中引用."
        preview = self.operations.preview("技术表达", "", members, id=result["id"], baseline=description["baseline"])
        self.operations.apply(preview["plan"])
        contexts = module_contexts(self.projected()[0])
        self.assertIn(members[0]["role"], contexts[0].member["payload"]["text"])
        self.assertNotIn(role, contexts[0].member["payload"]["text"])
        description = self.operations.describe(result["id"])
        preview = self.operations.preview("技术表达", "", members[1:], id=result["id"], baseline=description["baseline"])
        self.operations.apply(preview["plan"])
        self.assertEqual(module_contexts(self.projected()[0]), [])

    # //// Hook 说明只补充已使用模块的 Agent 上下文 [@x380kkm 2026-09-08] ////
    def test_hook_guidance_is_limited_to_included_module(self) -> None:
        result, _ = self.create_context_module()
        module = deepcopy(result["document"])
        module["contributions"].append({"id": "event", "point": "hook.x380kkm/lifecycle",
                                         "contract": {"id": "hook.x380kkm/lifecycle", "range": "^1.0.0"},
                                         "payload": {"name": "检查输出", "event": "PostToolUse", "handlers": [{"type": "command", "command": "inspect"}]}})
        source = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:event", "release": {"version": "local"},
                  "contributions": [module["contributions"].pop()]}
        self.save(source)
        module["contributions"].append({"id": "event", "ref": "plugin:event#event", "constraint": "local"})
        module_presentation(module)["members"]["event"] = {"kind": "hook", "role": "解释检查结果后再修改相关规则."}
        self.save(module, result["document"])
        projected, selected = self.projected()
        all_contexts = module_contexts(projected)
        self.assertTrue(any(entry.member["point"] == PREFERENCE_POINT for entry in all_contexts))
        unrelated = deepcopy(selected)
        unrelated.chain = [selected.chain[-1]]
        self.assertFalse(any(entry.member["point"] == PREFERENCE_POINT for entry in module_contexts(projected, included=[unrelated])))
        self.assertTrue(any(entry.member["point"] == PREFERENCE_POINT for entry in module_contexts(projected, included=[selected])))
