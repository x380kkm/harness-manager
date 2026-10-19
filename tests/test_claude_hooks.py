# audience: internal
# # claude-hooks-tests
# Claude Code 的 Hook 定义内嵌在 settings.json, 没有独立的逐处理器开关, 启停因此以写入与否表达.
# 这里核对未取得启用请求的 Hook 不写入, 取得请求后写入, 以及两种情况下用户其余设置逐字段保留.

from pathlib import Path
import json
import tempfile
import unittest

from harness_manager.host_hooks import HOOK_POINT
from harness_manager.host_ownership import encode_file, reconcile
from harness_manager.host_profiles import CLAUDE
from harness_manager.codex_hook_state import hook_state_key
from harness_manager.service import Manager
from harness_manager.usage import usage_document
from unittest.mock import patch

ORIGINAL = {
    "model": "opus",
    "permissions": {"defaultMode": "acceptEdits"},
    "enabledPlugins": {"example@marketplace": True},
    "env": {"SAMPLE": "1"},
}


# //// 核对内嵌 Hook 的启停与用户设置保留 [@x380kkm 2026-09-24] ////
class ClaudeEmbeddedHookTests(unittest.TestCase):
    # //// 准备带用户设置的主配置基线 [@x380kkm 2026-09-24] ////
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()
        self.settings = (json.dumps(ORIGINAL, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.baseline = {CLAUDE.rule_file: encode_file(None), CLAUDE.config_file: encode_file(self.settings)}
        self.contribution = {"ref": "plugin:sample#hook", "name": "Sample", "point": HOOK_POINT,
                             "enabled": True, "target": CLAUDE.config_file,
                             "hook": {"event": "Stop",
                                      "group": {"hooks": [{"type": "command", "command": "node check.mjs"}]}}}

    # //// 按给定归属调和一次 Hook 输出 [@x380kkm 2026-09-24] ////
    def reconcile(self, ownership: dict, targets: dict | None = None) -> tuple[dict, dict]:
        return reconcile(dict(targets or {}), [self.contribution], self.baseline, ownership, self.root, CLAUDE)

    # //// 读取输出的主配置文档 [@x380kkm 2026-09-24] ////
    def document(self, targets: dict) -> dict:
        return json.loads(targets[CLAUDE.config_file].decode("utf-8"))

    # //// 没有启用请求时完全不触碰主配置 [@x380kkm 2026-09-24] ////
    def test_hook_stays_out_without_an_enable_request(self) -> None:
        targets, ownership = self.reconcile({})
        self.assertNotIn(CLAUDE.config_file, targets)
        self.assertNotIn("hooks", ownership)

    # //// 取得启用请求后写入 Hook 且保留用户设置 [@x380kkm 2026-09-24] ////
    def test_requested_hook_is_written_beside_existing_settings(self) -> None:
        targets, ownership = self.reconcile({"hookRequests": {"plugin:sample#hook": {"enabled": True}}})
        document = self.document(targets)
        self.assertEqual({key: document[key] for key in ORIGINAL}, ORIGINAL)
        self.assertEqual([group["hooks"][0]["command"] for group in document["hooks"]["Stop"]],
                         ["node check.mjs"])
        self.assertIn("hooks", ownership)

    # //// 请求关闭后移除 Hook 并保留用户设置 [@x380kkm 2026-09-24] ////
    def test_disabling_removes_the_hook_and_keeps_settings(self) -> None:
        enabled_targets, enabled_ownership = self.reconcile(
            {"hookRequests": {"plugin:sample#hook": {"enabled": True}}})
        applied = {CLAUDE.rule_file: encode_file(None),
                   CLAUDE.config_file: encode_file(enabled_targets[CLAUDE.config_file])}
        self.baseline = applied
        targets, ownership = self.reconcile({**enabled_ownership,
                                             "hookRequests": {"plugin:sample#hook": {"enabled": False}}})
        document = self.document(targets)
        self.assertEqual({key: document[key] for key in ORIGINAL}, ORIGINAL)
        self.assertEqual(document.get("hooks", {}), {})
        self.assertFalse(ownership["hooks"]["groups"][0]["enabled"])

    # //// 接管前已有的 Hook 事件组在写入后保持原位 [@x380kkm 2026-09-24] ////
    def test_existing_native_hooks_survive(self) -> None:
        existing = {**ORIGINAL, "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
        self.settings = (json.dumps(existing, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.baseline = {CLAUDE.rule_file: encode_file(None), CLAUDE.config_file: encode_file(self.settings)}
        targets, _ = self.reconcile({"hookRequests": {"plugin:sample#hook": {"enabled": True}}})
        document = self.document(targets)
        self.assertEqual({key: document[key] for key in ORIGINAL}, ORIGINAL)
        self.assertEqual(document["hooks"]["SessionStart"][0]["hooks"][0]["command"], "mine")
        self.assertIn("Stop", document["hooks"])


# //// 在隔离用户目录核对 Hook 卡片的宿主选择和启停请求 [@x380kkm 2026-10-19] ////
class HookCardHostTests(unittest.TestCase):
    # //// 准备两个宿主的相同命令与独立开关 [@x380kkm 2026-10-19] ////
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.group = {"hooks": [{"type": "command", "command": "inspect"}]}
        for name in (".claude", ".codex"):
            (self.root / name).mkdir()
        self.settings = self.root / ".claude/settings.json"
        self.settings.write_text(json.dumps({"model": "chosen", "hooks": {"Stop": [self.group]}}), encoding="utf-8")
        self.hooks = self.root / ".codex/hooks.json"
        self.hooks.write_text(json.dumps({"hooks": {"Stop": [self.group]}}), encoding="utf-8")
        key = hook_state_key(self.hooks, "Stop", 0, 0)
        self.config = self.root / ".codex/config.toml"
        self.config.write_text(f"[hooks.state.'{key}']\nenabled = false\n", encoding="utf-8")
        self.manager = Manager(user_root=self.root)
        self.document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:sample/hook",
                         "release": {"version": "local"}, "metadata": {"name": "请求记录"},
                         "contributions": [{"id": "hook", "point": HOOK_POINT,
                                            "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
                                            "payload": {"event": "Stop", "handlers": self.group["hooks"]}}]}
        store = self.manager.catalogs.user
        store.apply(store.preview_put(self.document))
        binding = usage_document(self.document, {"state": "enabled"}, None, "user")
        binding["target"]["selector"]["host"] = "claude"
        store.apply(store.preview_put(binding))
        self.identity = next(item["id"] for item in self.manager.cards.inventory()["items"]
                             if item.get("details", {}).get("sourceDeclaration") == "plugin:sample/hook@local")

    # //// Claude 读取其主配置而 Codex 读取自己的逐处理器开关 [@x380kkm 2026-10-19] ////
    def test_native_states_and_requests_use_selected_host(self):
        claude = self.manager.cards.describe(self.identity)
        self.assertEqual(claude["management"]["host"], "claude")
        self.assertEqual(claude["management"]["userState"], "enabled")
        self.assertTrue(claude["management"]["nativeEnabled"])
        self.assertEqual(claude["configBaseline"]["native"]["path"], str(self.settings))
        codex = self.manager.cards.describe(self.identity, host="codex")
        self.assertFalse(codex["management"]["nativeEnabled"])
        self.assertEqual(codex["configBaseline"]["native"]["path"], str(self.hooks))
        originals = self.hooks.read_bytes(), self.config.read_bytes()
        control = self.manager.hosts.control("claude")
        for state in ("disabled", "enabled"):
            current = self.manager.cards.describe(self.identity)
            with patch.object(self.manager.host, "synchronize_active_scopes", side_effect=AssertionError("跨宿主同步")):
                result = self.manager.invoke("card.configure", {"id": self.identity, "state": state,
                                             "baseline": current["configBaseline"]})
            self.assertEqual(result["hostSync"]["status"], "pending")
            requests = control.storage("user").read_ownership()["hookRequests"]
            self.assertIs(requests["plugin:sample/hook#hook"]["enabled"], state == "enabled")
            self.assertNotIn("hookRequests", self.manager.host.storage("user").read_ownership())
            preview = control.preview()
            self.assertEqual(preview["diagnostics"], [])
            control.apply(preview["planId"])
            settings = json.loads(self.settings.read_text(encoding="utf-8"))
            self.assertEqual(settings["model"], "chosen")
            self.assertEqual(bool(settings.get("hooks", {}).get("Stop")), state == "enabled")
            self.assertEqual((self.hooks.read_bytes(), self.config.read_bytes()), originals)
            repeated = control.preview()
            control.apply(repeated["planId"])
            self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), settings)

    # //// 继承移除绑定时回执保留选定宿主并清理相应请求 [@x380kkm 2026-10-19] ////
    def test_inherit_keeps_host_in_receipt(self):
        current = self.manager.cards.describe(self.identity)
        result = self.manager.invoke("card.configure", {"id": self.identity, "state": "inherit",
                                      "baseline": current["configBaseline"]})
        self.assertEqual(result["management"]["host"], "claude")
        self.assertIsNone(result["configBaseline"]["binding"])
        self.assertEqual(result["configBaseline"]["native"]["path"], str(self.settings))
        self.assertNotIn("hookRequests", self.manager.hosts.control("claude").storage("user").read_ownership())

    # //// 双宿主声明通过显式参数选择各自的绑定与原生状态 [@x380kkm 2026-10-19] ////
    def test_shared_declaration_uses_explicit_host(self):
        binding = usage_document(self.document, {"state": "disabled"}, None, "user")
        binding["id"] = "binding:sample/codex"
        binding["target"]["selector"]["host"] = "codex"
        store = self.manager.catalogs.user
        store.apply(store.preview_put(binding))
        for host, enabled in (("claude", True), ("codex", False)):
            current = self.manager.invoke("card.describe", {"id": self.identity, "host": host})
            self.assertEqual(current["configBaseline"]["binding"]["target"]["selector"]["host"], host)
            self.assertIs(current["management"]["nativeEnabled"], enabled)

    # //// 宿主集合限定同一绑定并保留单宿主推断和多宿主选择 [@x380kkm 2026-10-19] ////
    def test_host_list_selector_uses_native_scope_matching(self):
        store = self.manager.catalogs.user
        binding = self.manager.cards.describe(self.identity)["configBaseline"]["binding"]
        for hosts, expected in ((["claude"], "claude"), (["codex", "claude"], "codex")):
            changed = json.loads(json.dumps(binding))
            changed["target"]["selector"]["host"] = hosts
            store.apply(store.preview_put(changed, binding))
            binding = changed
            current = self.manager.cards.describe(self.identity)
            self.assertEqual(current["management"]["host"], expected)
            claude = self.manager.cards.describe(self.identity, host="claude")
            self.assertEqual(claude["configBaseline"]["binding"], binding)
            self.assertTrue(claude["management"]["nativeEnabled"])
