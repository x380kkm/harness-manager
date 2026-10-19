# toolkit-rules-tests
"""隔离用户目录验证规则安装, 宿主投放, 独立选择和迁移后的来源读取."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.service import Manager
from harness_manager.toolkit import BUNDLE


# //// 在隔离目录使用真实的规则安装与宿主应用 [@x380kkm 2026-10-19] ////
class ToolkitRulesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.user.mkdir()
        self.manager = Manager(user_root=self.user)

    # //// 按宿主读取模块的实际可用正文 [@x380kkm 2026-10-19] ////
    def rules(self, manager, host):
        result = manager.invoke("catalog.discover", {
            "context": {"host": host}, "scope": "user", "point": "context.x380kkm/instruction",
        })
        self.assertEqual(result["diagnostics"], [])
        content = {}
        for candidate in result["candidates"]:
            read = candidate["read"]
            response = manager.invoke(read["method"], read["params"])
            self.assertEqual(response["unit_status"], "ready")
            content[candidate["ref"].split("#")[-1]] = response["units"][0]["content"].replace("\r\n", "\n")
        return content

    # //// 预览并实际写入所选宿主 [@x380kkm 2026-10-19] ////
    def apply_host(self, manager, host):
        preview = manager.invoke("host.preview", {"host": host})
        self.assertIsNotNone(preview.get("planId"), preview)
        result = manager.invoke("host.apply", {"host": host, "plan_id": preview["planId"], "once": True})
        self.assertTrue(result["changed"], result)
        return result

    # //// 默认规则在缺少可选工具与个人选项时投放到两个宿主 [@x380kkm 2026-10-19] ////
    def test_默认规则读取和宿主应用保留用户配置(self):
        installed = self.manager.toolkit.install(["agent-manager"])
        self.assertEqual(installed["status"], "installed", installed)
        self.assertFalse((self.user / ".harness/toolkit/sources").exists())
        manager = Manager(user_root=self.user)
        for host, rule_file, config_file, native in (
            ("codex", "AGENTS.override.md", "config.toml", 'model = "user-choice"\n'),
            ("claude", "CLAUDE.md", "settings.json", '{"model": "user-choice", "permissions": {"deny": ["custom"]}}\n'),
        ):
            with self.subTest(host=host):
                directory = self.user / ("." + host)
                directory.mkdir(exist_ok=True)
                (directory / config_file).write_text(native, encoding="utf-8")
                (directory / rule_file).write_text("# 用户约定\n\n保留现有约定.\n", encoding="utf-8")
                rules = self.rules(manager, host)
                self.assertEqual(len(rules), 9)
                self.apply_host(manager, host)
                actual = (directory / rule_file).read_text(encoding="utf-8")
                for body in rules.values():
                    self.assertIn(body.strip(), actual)
                self.assertIn("保留现有约定", actual)
                self.assertEqual((directory / config_file).read_text(encoding="utf-8"), native)
                self.assertNotIn(str(self.user), actual)
                preview = manager.invoke("host.preview", {"host": host})
                self.assertEqual(preview["files"], [])
                self.assertFalse(preview["ownershipChanged"])

    # //// 独立宿主成员选择在来源更新后保持且规则正文随来源更新 [@x380kkm 2026-10-19] ////
    def test_规则更新保留宿主选择(self):
        release = self.root / "release"
        (release / "manager").mkdir(parents=True)
        shutil.copytree(BUNDLE / "rules", release / "rules")
        manifest = release / "manager/components.json"
        raw = json.loads((BUNDLE / "manager/components.json").read_text(encoding="utf-8"))
        raw["components"] = [component for component in raw["components"] if component["id"] == "agent-rules"]
        raw["defaults"] = ["agent-rules"]
        manifest.write_text(json.dumps(raw), encoding="utf-8")
        manager = Manager(read_roots=[self.root], user_root=self.user)
        with patch("harness_manager.toolkit.DEFAULT_MANIFEST", manifest):
            self.assertEqual(manager.toolkit.install()["status"], "installed")
            plugin = "plugin:toolkit/user/agent-rules@local"
            described = manager.invoke("usage.describe", {"plugin": plugin})
            baseline = next(binding for binding in described["bindings"] if binding["target"]["selector"]["host"] == "claude")
            preview = manager.invoke("usage.preview", {"plugin": plugin, "baseline": baseline, "settings": {
                "id": baseline["id"], "members": {"documentation": "exclude"},
            }})
            manager.invoke("document.apply", {"plan": preview["plan"]})
            before = deepcopy(manager.invoke("usage.describe", {"plugin": plugin})["bindings"])
            revised = "# 代码设计\n\n使用项目确定的接口约束.\n"
            (release / "rules/code-design.md").write_text(revised, encoding="utf-8")
            self.assertEqual(manager.toolkit.update()["status"], "updated")
            self.assertEqual(manager.invoke("usage.describe", {"plugin": plugin})["bindings"], before)
            self.assertIn("documentation", self.rules(manager, "codex"))
            self.assertNotIn("documentation", self.rules(manager, "claude"))
            self.assertEqual(self.rules(manager, "claude")["code-design"], revised)

    # //// 仅搬移配置后恢复来源且 Git 忽略本机运行记录 [@x380kkm 2026-10-19] ////
    def test_配置迁移重建规则并排除本机记录(self):
        self.assertEqual(self.manager.toolkit.install(["agent-rules"])["status"], "installed")
        target = self.root / "moved"
        (target / ".harness").mkdir(parents=True)
        for name in ("catalog.json", "toolkit.json", ".gitignore"):
            shutil.copy2(self.user / ".harness" / name, target / ".harness" / name)
        shutil.rmtree(self.user)
        moved = Manager(user_root=target)
        self.assertEqual(moved.toolkit.install()["status"], "installed")
        self.assertEqual(len(self.rules(moved, "codex")), 9)
        self.apply_host(moved, "codex")
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)
        paths = [".harness/catalog.json", ".harness/toolkit.json", ".harness/.gitignore",
                 ".harness/toolkit/rules/catalog.json", ".harness/backups/private.json",
                 ".harness/hosts/state.json", ".harness/observations/log.json"]
        ignored = subprocess.run(["git", "-C", str(target), "check-ignore", "--stdin", "-z"],
                                 input="\0".join(paths) + "\0", text=True, capture_output=True, check=True)
        self.assertEqual(set(ignored.stdout.rstrip("\0").split("\0")), set(paths[3:]))
