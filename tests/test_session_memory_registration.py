# session-memory-registration-tests
# 隔离配置验证会话 Hook 的关闭默认值, 路径迁移和宿主输出边界.
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from harness_manager.codex_inventory import CodexInventory
from harness_manager.host_projection import compile_host
from harness_manager.host_profiles import CLAUDE, CODEX
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.session_memory import register_session_memory


# //// 在临时配置目录检查会话 Hook 声明与实际编译结果 [@x380kkm 2026-10-19] ////
class SessionMemoryRegistrationTests(unittest.TestCase):
    # //// 创建个人目录和两处工具安装位置 [@x380kkm 2026-10-19] ////
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.project = self.root / "user", self.root / "project"
        self.user.mkdir()
        self.project.mkdir()
        self.manager = Manager(self.project, user_root=self.user)
        self.original, self.moved = self.root / "original", self.root / "moved"

    # //// 保存用户对声明或绑定的明确修改 [@x380kkm 2026-10-19] ////
    def save(self, before, after):
        store = self.manager.catalogs.user
        store.apply(store.preview_put(after, before))

    # //// 获取指定宿主的编译输出 [@x380kkm 2026-10-19] ////
    def compile(self, profile):
        result = compile_host(self.manager.catalogs, CodexInventory(self.user), self.manager.reader, profile=profile)
        self.assertEqual(result["diagnostics"], [])
        return result

    # //// 初装为两个宿主登记关闭的独立 Hook [@x380kkm 2026-10-19] ////
    def test_初装关闭且宿主输出隔离(self):
        registered = register_session_memory(self.manager, self.original, "user")
        self.assertEqual(len(registered), 4)
        for profile in (CODEX, CLAUDE):
            compiled = self.compile(profile)
            self.assertEqual(json.loads(compiled["targets"][profile.hook_file])["hooks"], {})
            self.assertEqual(len(compiled["contributions"]), 2)
            self.assertTrue(all(not item["enabled"] for item in compiled["contributions"]))
            self.assertTrue(all(f"/memory-{profile.id}-" in item["ref"] for item in compiled["contributions"]))
        documents = self.manager.catalogs.user.snapshot()
        for before in documents:
            if before["kind"] == "PluginBinding":
                after = deepcopy(before)
                after["enabled"] = True
                self.save(before, after)
        for profile in (CODEX, CLAUDE):
            compiled = self.compile(profile)
            hooks = json.loads(compiled["targets"][profile.hook_file])["hooks"]
            self.assertEqual(set(hooks), {"UserPromptSubmit", "Stop"})
            for groups in hooks.values():
                self.assertEqual(len(groups), 1)
                self.assertIn(f'" {profile.id} ', groups[0]["hooks"][0]["command"])

    # //// 搬移仅改写对应宿主和动作的内置命令并保留用户选择 [@x380kkm 2026-10-19] ////
    def test_搬移保留启停与自定义命令(self):
        register_session_memory(self.manager, self.original, "user")
        custom_commands = {
            "plugin:toolkit/user/memory-claude-record": "node custom-memory.mjs",
            "plugin:toolkit/user/memory-claude-recap": f'node "{self.original.as_posix()}/tools/session-memory/session-memory.mjs" codex record',
        }
        for before in self.manager.catalogs.user.snapshot():
            after = deepcopy(before)
            if before["kind"] == "PluginBinding":
                after["enabled"] = before["plugin"]["id"].endswith("-record")
            elif before["id"] in custom_commands:
                after["contributions"][0]["payload"]["handlers"][0]["command"] = custom_commands[before["id"]]
            self.save(before, after)
        original = {document_identity(item): item for item in self.manager.catalogs.user.snapshot()}
        register_session_memory(self.manager, self.moved, "user")
        for after in self.manager.catalogs.user.snapshot():
            before = original[document_identity(after)]
            if after["kind"] == "PluginBinding":
                self.assertEqual(after, before)
                continue
            command = after["contributions"][0]["payload"]["handlers"][0]["command"]
            if after["id"] in custom_commands:
                self.assertEqual(command, custom_commands[after["id"]])
            else:
                self.assertIn(self.moved.as_posix(), command)
                self.assertNotIn(self.original.as_posix(), command)
        register_session_memory(self.manager, self.moved, "user")
        self.assertEqual(len(self.manager.catalogs.user.snapshot()), len(original))


if __name__ == "__main__":
    unittest.main()
