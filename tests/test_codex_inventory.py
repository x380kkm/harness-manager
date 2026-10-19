# audience: internal
# # codex-inventory-tests
# 独立用户目录承载配置与缓存, 核对来源边界, 安全摘要和登记语义.

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.codex_inventory import CodexInventory
from harness_manager.projection import resolve_scope


# //// 在独立用户目录验证盘点与来源登记 [@x380kkm 2026-09-06] ////
class CodexInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "profile"
        self.codex = self.user / ".codex"
        self.codex.mkdir(parents=True)
        self.inventory = CodexInventory(self.user)

    # //// 写入测试专用来源载体 [@x380kkm 2026-09-06] ////
    def write(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # //// 创建具有完整名称与正文的 Skill [@x380kkm 2026-09-06] ////
    def skill(self, path: Path, name: str = "sample") -> Path:
        return self.write(path, f"---\nname: {name}\ndescription: Read scoped task content.\n---\n# Method\nRead the input.\n")

    # //// 配置摘要保留可用字段并遮蔽命令与认证内容 [@x380kkm 2026-09-06] ////
    def test_configuration_returns_only_safe_fields(self) -> None:
        self.write(self.codex / "config.toml", '''model = "sample-model"
approval_policy = "never"
notify = ["private-notify-command"]
[mcp_servers.local]
command = "private-command"
args = ["--token", "private-argument"]
[mcp_servers.local.env]
ACCESS_TOKEN = "private-environment"
[mcp_servers.remote]
url = "https://private-user:private-password@example.com/private-path?token=private-query"
http_headers = {Authorization = "private-header"}
[mcp_servers.off]
enabled = false
url = "http://127.0.0.1/private-route"
[plugins."cached@market"]
enabled = false
[projects."ignored-project"]
trust_level = "trusted"
''')
        self.write(self.codex / "hooks.json", json.dumps({"hooks": {"Start": [{"hooks": [{"command": "private-hook"}]}]}}))
        self.write(self.codex / "auth.json", '{"token":"private-auth-file"}')
        snapshot = self.inventory.snapshot([])
        rendered = json.dumps(snapshot)
        for value in ("private-notify-command", "private-command", "private-argument", "private-environment", "private-user",
                      "private-password", "private-path", "private-query", "private-header", "private-route", "private-hook", "private-auth-file"):
            self.assertNotIn(value, rendered)
        nodes = {item["name"]: item for item in snapshot["items"]}
        self.assertEqual(nodes["Codex 用户配置"]["details"]["model"], "sample-model")
        self.assertEqual(nodes["local"]["details"]["environmentKeys"], ["ACCESS_TOKEN"])
        self.assertEqual(nodes["off"]["status"], "配置禁用")
        self.assertEqual(nodes["Codex 生命周期设置"]["details"]["handlerCount"], 1)
        self.assertEqual(nodes["Codex 生命周期设置"]["kind"], "hook")
        self.assertFalse(any("content" in item for item in snapshot["items"]))

    # //// 插件开关, 安装记录与缓存版本保留独立状态 [@x380kkm 2026-09-06] ////
    def test_plugin_cache_does_not_claim_installation_or_activation(self) -> None:
        self.write(self.codex / "config.toml", '[plugins."sample@market"]\nenabled = false\n')
        plugin = self.codex / "plugins/cache/market/sample"
        version = plugin / "1.0.0"
        manifest = {"name": "sample", "version": "1.0.0", "mcpServers": "./.mcp.json"}
        self.write(version / ".codex-plugin/plugin.json", json.dumps(manifest))
        self.write(version / ".mcp.json", json.dumps({"mcpServers": {"plugin-tool": {"command": "private-runtime", "args": ["private-token"]}}}))
        self.skill(version / "skills/sample/SKILL.md")
        snapshot = self.inventory.snapshot([])
        cached = next(item for item in snapshot["items"] if item["details"].get("observation") == "缓存版本")
        method = next(item for item in snapshot["items"] if item["kind"] == "skill")
        self.assertFalse(cached["details"]["hasInstallationRecord"])
        self.assertEqual(method["status"], "所属插件配置禁用; Skill 缓存存在")
        self.assertEqual(method["details"]["pluginId"], cached["id"])
        self.assertIn({"from": cached["id"], "to": method["id"], "label": "包含 Skill"}, snapshot["edges"])
        self.assertNotIn("private-token", json.dumps(snapshot))
        self.write(plugin / ".codex-remote-plugin-install.json", '{"remote_plugin_id":"record-id"}')
        refreshed = self.inventory.snapshot([])
        observations = [item["details"].get("observation") for item in refreshed["items"]]
        self.assertIn("安装记录", observations)
        self.assertIn("缓存版本", observations)
        self.assertTrue(any(item["status"] == "配置禁用" for item in refreshed["items"]))

    # //// 同名 Skill 单独登记并准确匹配已有来源 [@x380kkm 2026-09-06] ////
    def test_duplicate_names_keep_separate_imports_and_managed_origins(self) -> None:
        first = self.skill(self.user / ".agents/skills/same/SKILL.md")
        second = self.skill(self.codex / "skills/same/SKILL.md")
        before = {path: path.read_text(encoding="utf-8") for path in (first, second)}
        nodes = [item for item in self.inventory.snapshot([])["items"] if item["kind"] == "skill"]
        drafts = [self.inventory.prepare_import(item["id"]) for item in nodes]
        self.assertNotEqual(drafts[0]["id"], drafts[1]["id"])
        managed = [item for item in self.inventory.snapshot([drafts[0]])["items"] if item["managedIds"]]
        self.assertEqual([item["id"] for item in managed], [nodes[0]["id"]])
        self.assertEqual(before, {path: path.read_text(encoding="utf-8") for path in (first, second)})
        self.assertFalse((self.user / ".harness").exists())
        self.assertEqual(self.inventory.prepare_import(nodes[0]["id"]), drafts[0])

    # //// 按完整文件统计登记并保留宿主配置的独立归属 [@x380kkm 2026-09-07] ////
    def test_coverage_counts_files_without_transferring_host_ownership(self) -> None:
        config = self.write(self.codex / "config.toml", '''[mcp_servers.one]
command = "one"
[mcp_servers.two]
command = "two"
[plugins."sample@market"]
enabled = true
''')
        instruction = self.write(self.codex / "AGENTS.md", "# Rules\nBase.\n")
        originals = {path: path.read_text(encoding="utf-8") for path in (config, instruction)}
        before = self.inventory.snapshot([])
        file = next(item for item in before["items"] if item["kind"] == "instruction")
        draft = self.inventory.prepare_import(file["id"])
        registered = self.inventory.snapshot([draft])
        coverage = registered["coverage"]
        self.assertEqual(before["coverage"]["registeredFiles"], 0)
        self.assertEqual(coverage["registeredFiles"], 1)
        self.assertEqual(coverage["importableFiles"], 1)
        self.assertEqual(coverage["hostConfigurationFiles"], 1)
        self.assertEqual(coverage["hostConfigurationFiles"], before["coverage"]["hostConfigurationFiles"])
        self.assertEqual(coverage["observedItems"], before["coverage"]["observedItems"])
        self.assertEqual({item["id"]: item["status"] for item in registered["items"]},
                         {item["id"]: item["status"] for item in before["items"]})
        self.assertFalse(coverage["capabilities"]["hostDeployment"])
        self.assertFalse(coverage["capabilities"]["hostConfigurationWriteback"])
        self.assertEqual(originals, {path: path.read_text(encoding="utf-8") for path in originals})
        self.write(instruction, originals[instruction] + "## Input\nRead.\n## Output\nWrite.\n")
        expanded = self.inventory.snapshot([draft])["coverage"]
        self.assertEqual(expanded["counts"], coverage["counts"])
        self.assertEqual(expanded["fileCounts"]["instruction"], 1)
        self.assertEqual(expanded["registeredFiles"], 1)
        self.assertEqual(expanded["importableFiles"], 1)
        self.assertEqual(expanded["hostConfigurationFiles"], 1)
        self.assertEqual(expanded["observedItems"], coverage["observedItems"])

    # //// 保持完整原文与目录说明的适用边界 [@x380kkm 2026-09-07] ////
    def test_instruction_preserves_full_document_and_directory_scope(self) -> None:
        self.write(self.codex / "AGENTS.md", "# Codex default\nBase.\n")
        self.write(self.codex / "AGENTS.override.md", "# Codex override\nActive candidate.\n")
        content = "# Working rules\nBody.\n```md\n# Sample heading\n```\n## Detail\n\nMovement:\n\n- Preserve order.\n- Keep context.\n"
        path = self.write(self.user / "AGENTS.md", content)
        nodes = self.inventory.snapshot([])["items"]
        self.assertEqual([item["kind"] for item in nodes], ["instruction"] * 3)
        local = next(item for item in nodes if item["kind"] == "instruction" and item["scope"] == "directory")
        self.assertEqual(local["content"], path.read_bytes().decode("utf-8"))
        draft = self.inventory.prepare_import(local["id"])
        scope = draft["contributions"][0]["scope"]
        self.assertIsNotNone(resolve_scope(scope, {"path": str(self.user / "work/file.py")}, "scope", []))
        self.assertIsNone(resolve_scope(scope, {"path": str(self.root / "another/file.py")}, "scope", []))
        fallback = next(item for item in nodes if item["path"] == str(self.codex / "AGENTS.md") and item["kind"] == "instruction")
        self.assertEqual(fallback["details"]["selection"], "此位置有更高优先级候选")
        self.write(self.codex / "AGENTS.override.md", " \n")
        fallback = next(item for item in self.inventory.snapshot([])["items"] if item["id"] == fallback["id"])
        self.assertEqual(fallback["details"]["selection"], "此位置的优先候选")

    # //// 显式隔离用户根优先于进程中的宿主位置 [@x380kkm 2026-09-06] ////
    def test_isolated_user_root_ignores_codex_home(self) -> None:
        external = self.root / "external-codex"
        self.write(external / "AGENTS.md", "# External scope\nOutside.\n")
        with patch.dict("os.environ", {"CODEX_HOME": str(external)}):
            inventory = CodexInventory(self.user)
            self.assertEqual(inventory.root, self.codex)
            self.assertEqual(inventory.snapshot([])["items"], [])
            self.assertTrue(CodexInventory(self.user, external).snapshot([])["items"])

    # //// 损坏配置保留安全诊断并继续展示有效 Skill [@x380kkm 2026-09-06] ////
    def test_malformed_inputs_keep_safe_diagnostics_and_other_content(self) -> None:
        self.write(self.codex / "config.toml", 'model = "parse-secret\n')
        self.write(self.codex / "hooks.json", '{"parse-secret":')
        self.write(self.codex / "skills/broken/SKILL.md", '---\nname: ["yaml-secret"\n---\nBody.\n')
        self.skill(self.codex / "skills/readable/SKILL.md", "readable")
        snapshot = self.inventory.snapshot([])
        notes = json.dumps(snapshot["diagnostics"])
        self.assertNotIn("parse-secret", notes)
        self.assertNotIn("yaml-secret", notes)
        self.assertEqual(len(snapshot["diagnostics"]), 3)
        nodes = {item["name"]: item for item in snapshot["items"] if item["kind"] == "skill"}
        self.assertFalse(nodes["broken"]["importable"])
        self.assertTrue(nodes["readable"]["importable"])

    # //// 读取 Skill 正文前核对链接仍在授权目录内 [@x380kkm 2026-09-06] ////
    def test_linked_content_cannot_escape_known_roots(self) -> None:
        external = self.skill(self.root / "external/SKILL.md", "outside")
        inside = self.codex / "skills/linked"
        inside.parent.mkdir()
        try:
            inside.symlink_to(external.parent, target_is_directory=True)
            (self.user / "AGENTS.md").symlink_to(external)
        except OSError as error:
            self.skipTest(f"系统提供的链接权限不足: {error.__class__.__name__}")
        snapshot = self.inventory.snapshot([])
        self.assertFalse(any(item.get("content") for item in snapshot["items"]))
        self.assertTrue(any(note["code"] == "outside-scan-root" for note in snapshot["diagnostics"]))
        self.assertFalse(any(item["importable"] for item in snapshot["items"]))

    # //// Skill 包的资源目录保留在正文载体之外 [@x380kkm 2026-09-06] ////
    def test_skill_discovery_stops_at_package_and_preserves_disabled_setting(self) -> None:
        first = self.skill(self.codex / "skills/first/SKILL.md", "first")
        self.skill(first.parent / "resources/example/SKILL.md", "example-only")
        self.skill(self.codex / "skills/second/SKILL.md", "second")
        setting_path = str(first.parent).replace("\\", "\\\\")
        self.write(self.codex / "config.toml", f'[[skills.config]]\npath = "{setting_path}"\nenabled = false\n')
        methods = [item for item in self.inventory.snapshot([])["items"] if item["kind"] == "skill"]
        self.assertEqual({item["name"] for item in methods}, {"first", "second"})
        self.assertEqual(next(item for item in methods if item["name"] == "first")["status"], "配置禁用; 文件存在")


if __name__ == "__main__":
    unittest.main()
