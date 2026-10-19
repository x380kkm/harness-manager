# audience: internal
# # module-release-selection-tests
"""模块卡片按当前宿主的实际发布选择显示状态, 版本切换保留绑定的选项与范围."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.service import Manager
from harness_manager.protocol import document_identity


# //// 在隔离目录中维护多个版本和独立宿主绑定 [@x380kkm 2026-09-10] ////
class ModuleReleaseSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.project = root / "project"
        self.project.mkdir()
        self.manager = Manager(self.project, user_root=root)
        self.plugins = []
        for version, channel in (("1.0.0", "stable"), ("2.0.0", "preview")):
            plugin = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:module/methods",
                      "release": {"version": version, "channel": channel}, "contributions": [
                          {"id": name, "point": "context.x380kkm/instruction",
                           "contract": {"id": "context.x380kkm/instruction", "range": "^1.0.0"},
                           "payload": {"name": name, "text": version + name}}
                          for name in ("naming", "format")]}
            self.manager.store.apply(self.manager.store.preview_put(plugin))
            self.plugins.append(plugin)
        self.binding = {"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": "binding:custom/codex",
                        "plugin": {"id": self.plugins[0]["id"], "constraint": "2.0.0", "channel": "preview"},
                        "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": "codex"}},
                        "enabled": True, "options": {"mode": "personal"}}
        self.manager_binding = deepcopy(self.binding)
        self.manager_binding["id"] = "binding:custom/manager"
        self.manager_binding["target"]["selector"]["host"] = "harness-manager"
        for binding in (self.binding, self.manager_binding):
            self.manager.store.apply(self.manager.store.preview_put(binding))

    # //// 读取版本卡片的当前配置状态 [@x380kkm 2026-09-10] ////
    def states(self, scope: str = "user") -> dict:
        return {item["details"]["version"]: item["details"]["configuredState"]
                for item in self.manager.snapshot_cards(scope)["items"] if item["kind"] == "module"}

    # //// 精确版本和渠道只使对应卡片显示开启 [@x380kkm 2026-09-10] ////
    def test_selected_release_and_switch_preserve_other_host_binding(self) -> None:
        first = document_identity(self.plugins[0])
        second = document_identity(self.plugins[1])
        self.assertEqual(self.states(), {"1.0.0": "inherit", "2.0.0": "enabled"})
        usage = self.manager.describe_usage(first, context={"host": "codex"})
        self.assertEqual(usage["selectedPlugin"], second)
        self.assertEqual(usage["contextBindings"], [self.binding])
        plan = self.manager.preview_usage(first, {"state": "enabled", "constraint": "1.0.0", "channel": "stable"}, self.binding)["plan"]
        self.manager.apply_document(plan)
        selected = self.manager.describe_usage(first, context={"host": "codex"})
        updated = selected["contextBindings"][0]
        self.assertEqual(selected["selectedPlugin"], first)
        self.assertEqual(updated["id"], self.binding["id"])
        self.assertEqual(updated["options"], self.binding["options"])
        self.assertEqual(updated["target"], self.binding["target"])
        self.assertIn(self.manager_binding, self.manager.store.snapshot())
        self.assertEqual(self.states(), {"1.0.0": "enabled", "2.0.0": "inherit"})
        self.manager.store.apply(self.manager.store.preview_remove(updated["id"], updated))
        self.assertEqual(self.states(), {"1.0.0": "inherit", "2.0.0": "inherit"})

    # //// 项目版本覆盖用户选择而用户卡片继续显示自己的版本 [@x380kkm 2026-09-10] ////
    def test_project_release_selection_obeys_binding_layers(self) -> None:
        first = document_identity(self.plugins[0])
        project = self.manager.catalogs.project
        binding = deepcopy(self.binding)
        binding["id"] = "binding:project-version"
        binding["plugin"].update(constraint="1.0.0", channel="stable")
        binding["target"]["selector"]["project"] = "current"
        project.apply(project.preview_put(binding))
        usage = self.manager.describe_usage(first, "project", context={"host": "codex"})
        self.assertEqual(usage["selectedPlugin"], first)
        self.assertEqual(usage["contextBindings"], [binding])
        self.assertEqual(self.states("project"), {"1.0.0": "enabled", "2.0.0": "inherit"})
        self.assertEqual(self.states(), {"1.0.0": "inherit", "2.0.0": "enabled"})
