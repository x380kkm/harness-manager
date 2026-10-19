# execution-preferences-tests
"""隔离宿主输出验证执行偏好的范围, 选项覆盖和安装后保留."""
from copy import deepcopy
import tempfile
from pathlib import Path
import unittest

from harness_manager.codex_inventory import CodexInventory
from harness_manager.execution_preferences import register_execution_preferences
from harness_manager.host_profiles import CODEX, CLAUDE
from harness_manager.host_projection import compile_host
from harness_manager.service import Manager
from harness_manager.usage import usage_document


# //// 核对宿主隔离与用户选项保留 [@x380kkm 2026-10-19] ////
class ExecutionPreferencesTests(unittest.TestCase):
    # //// 在隔离用户目录登记两个宿主的执行偏好 [@x380kkm 2026-10-19] ////
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(user_root=self.root)
        register_execution_preferences(self.manager, "user")

    # //// 保存对应宿主的显式用户选项 [@x380kkm 2026-10-19] ////
    def configure(self, host, options):
        store = self.manager.catalogs.user
        plugin = self.manager.read_document(f"plugin:toolkit/user/execution-{host}@local")["document"]
        original = next(value for value in store.snapshot()
                        if value["kind"] == "PluginBinding" and value["plugin"]["id"] == plugin["id"])
        updated = usage_document(plugin, {"state": "enabled", "options": options}, original, "user")
        store.apply(store.preview_put(updated, original))

    # //// 宿主输出只含自身字段并保留原生主会话设置 [@x380kkm 2026-10-19] ////
    def test_host_preferences_do_not_cross_hosts_or_rewrite_native_model(self):
        self.configure("codex", {"tasks": {"review": {"model": "chosen-gpt", "model_reasoning_effort": "high"}}})
        self.configure("claude", {"defaults": {"model": "opus", "effort": "high"}})
        for profile, present, absent in ((CODEX, "chosen-gpt", "opus"), (CLAUDE, "opus", "chosen-gpt")):
            result = compile_host(self.manager.catalogs, CodexInventory(self.root), self.manager.reader, profile=profile)
            self.assertEqual(result["diagnostics"], [])
            text = result["targets"][profile.rule_file].decode("utf-8")
            self.assertIn(present, text)
            self.assertNotIn(absent, text)
            self.assertNotIn(profile.config_file, result["targets"])
        before = deepcopy(self.manager.catalogs.user.snapshot())
        register_execution_preferences(self.manager, "user")
        self.assertEqual(before, self.manager.catalogs.user.snapshot())

    # //// 错用另一宿主的推理字段使预览阻断而保持文件原样 [@x380kkm 2026-10-19] ////
    def test_incompatible_host_options_block_projection(self):
        self.configure("codex", {"defaults": {"effort": "high"}})
        result = compile_host(self.manager.catalogs, CodexInventory(self.root), self.manager.reader)
        self.assertTrue(any(item["code"] == "invalid_options" for item in result["diagnostics"]))
        self.assertEqual(result["targets"], {})
