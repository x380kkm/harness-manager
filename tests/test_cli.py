# audience: internal
# # cli-interface-tests
"""隔离目录中的真实 CLI 进程验证跨调用计划, 同进程宿主确认与可用连接描述."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
import unittest

from harness_manager.card_subjects import source_document
from harness_manager.service import Manager


# //// 用真实子进程操作隔离的用户配置 [@x380kkm 2026-09-08] ////
class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name) / "用户"
        self.user.mkdir()
        self.manager = Manager(user_root=self.user)
        self.rule = source_document({"id": "rule:cli", "name": "命令规范", "kind": "rule", "summary": "命令约束.",
                                     "path": str(self.user / ".codex/AGENTS.md"), "scope": "user", "content": "使用 pwsh.\n"})

    # //// 运行一条具有明确根目录的 CLI 调用 [@x380kkm 2026-09-08] ////
    def run_cli(self, *arguments: str, stdin: str = "", success: bool = True):
        result = subprocess.run([sys.executable, "-m", "harness_manager.cli", "--user-root", str(self.user), *arguments],
                                input=stdin, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr or result.stdout)
        return result

    # //// 能力输入取自真实方法且连接生成保持宿主原位 [@x380kkm 2026-09-08] ////
    def test_api_help_and_connection_are_read_only(self) -> None:
        description = json.loads(self.run_cli("api", "module.preview").stdout)
        self.assertIn("members", description["inputSchema"]["properties"])
        self.assertIn("members", description["inputSchema"]["required"])
        self.assertTrue(description["readOnly"])
        help_topic = json.loads(self.run_cli("guide", "host").stdout)
        self.assertIn("planId", help_topic["text"])
        connection = tomllib.loads(self.run_cli("connection").stdout)["mcp_servers"]["harness_manager"]
        self.assertEqual(connection["command"], sys.executable)
        self.assertEqual(connection["args"][-2:], ["mcp", "--compact"])
        self.assertIn(str(self.user), connection["args"])
        self.assertFalse((self.user / ".harness").exists())
        self.assertFalse((self.user / ".codex").exists())

    # //// 声明计划可跨 CLI 调用保存且冲突保留协作修改 [@x380kkm 2026-09-08] ////
    def test_document_plan_survives_process_exit_and_checks_baseline(self) -> None:
        prepared = json.loads(self.run_cli("call", "document.preview", "--params", "-",
                                          stdin=json.dumps({"document": self.rule})).stdout)["result"]
        applied = json.loads(self.run_cli("call", "document.apply", "--params", "-",
                                         stdin=json.dumps({"plan": prepared["plan"]})).stdout)["result"]
        current = json.loads(self.run_cli("call", "document.read", "--params", "-",
                                         stdin=json.dumps({"id": applied["id"]})).stdout)["result"]
        changed = deepcopy(current["document"])
        changed["metadata"]["name"] = "独立编辑"
        preview = self.manager.preview_document(changed, current["baseline"])
        self.manager.apply_document(preview["plan"])
        stale = json.loads(self.run_cli("call", "document.apply", "--params", "-",
                                       stdin=json.dumps({"plan": prepared["plan"]}), success=False).stdout)
        self.assertEqual(stale["error"]["code"], "catalog-conflict")
        self.assertEqual(self.manager.read_document(applied["id"])["document"]["metadata"]["name"], "独立编辑")

    # //// 同进程确认完成宿主应用并恢复首次备份 [@x380kkm 2026-09-08] ////
    def test_host_confirmation_applies_and_restores_in_one_process(self) -> None:
        self.manager.apply_document(self.manager.preview_document(self.rule)["plan"])
        self.manager.cards.configure("rule:cli", "enabled")
        target = self.user / ".codex/AGENTS.override.md"
        cancelled = [json.loads(line) for line in self.run_cli("host", "apply", stdin="cancel\n").stdout.splitlines()]
        self.assertEqual(cancelled[-1]["result"]["status"], "cancelled")
        self.assertFalse(target.exists())
        applied = [json.loads(line) for line in self.run_cli("host", "apply", stdin="apply\n").stdout.splitlines()]
        self.assertIn("planId", applied[0]["result"])
        self.assertIn("使用 pwsh", target.read_text(encoding="utf-8"))
        self.assertTrue(applied[-1]["result"]["enabled"])
        restored = [json.loads(line) for line in self.run_cli("host", "restore", "initial", stdin="restore\n").stdout.splitlines()]
        self.assertFalse(restored[-1]["result"]["enabled"])
        self.assertFalse(target.exists())

    # //// 单次调用明确报告宿主令牌的进程边界 [@x380kkm 2026-09-08] ////
    def test_one_shot_host_calls_explain_process_lifetime(self) -> None:
        preview = json.loads(self.run_cli("call", "host.preview").stdout)
        self.assertEqual(preview["cli"]["planLifetime"], "process")
        rejected = self.run_cli("call", "host.apply", "--params", "-", stdin='{"plan_id":"expired"}', success=False)
        self.assertIn("MCP/RPC", json.loads(rejected.stderr)["error"]["message"])

    # //// 搬移预览可跨进程提交且重新关联私人开关 [@x380kkm 2026-09-08] ////
    def test_project_relocation_plan_survives_cli_process_exit(self) -> None:
        old, current = self.user.parent / "project-old", self.user.parent / "project-current"
        old.mkdir()
        manager = Manager(old, user_root=self.user)
        manager.apply_document(manager.preview_document(self.rule)["plan"])
        manager.cards.configure("rule:cli", "disabled", "project-local")
        old.rename(current)
        previous = manager.catalogs.project_local.snapshot()
        command = [sys.executable, "-m", "harness_manager.cli", "--user-root", str(self.user), "--workspace", str(current), "call"]
        preview = subprocess.run([*command, "project.relocate_preview", "--params", "-"],
                                 input=json.dumps({"old_workspace": str(old)}), text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(preview.returncode, 0, preview.stderr or preview.stdout)
        plan = json.loads(preview.stdout)["result"]["plan"]
        applied = subprocess.run([*command, "project.relocate_apply", "--params", "-"], input=json.dumps({"plan": plan}),
                                 text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(applied.returncode, 0, applied.stderr or applied.stdout)
        result = json.loads(applied.stdout)["result"]
        self.assertFalse(result["hostFilesChanged"])
        self.assertEqual(result["hostApplyRequired"], ["project-local"])
        relocated = Manager(current, user_root=self.user)
        self.assertFalse(relocated.cards.describe("rule:cli", "project-local")["management"]["effectiveEnabled"])
        self.assertEqual(manager.catalogs.project_local.snapshot(), previous)


if __name__ == "__main__":
    unittest.main()
