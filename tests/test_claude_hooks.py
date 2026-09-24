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
        self.assertNotIn("hooks", ownership)

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
