# audience: internal
# # host-hook-storage-tests
# 项目 Hook 定义与用户开关使用同一宿主事务, 共享配置的协作写入使用用户宿主锁.

import base64
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import tomlkit

from harness_manager.codex_hook_state import hook_state_key, parse_hook_states
from harness_manager.host_storage import HOOK_STATE_NAME, HostStorage, HostTransactionError
from harness_manager.storage_errors import (
    StorageBoundaryError, StorageBusyError, StorageConflictError, StorageValidationError,
)


# //// 检查跨目录 Hook 文件的事务与恢复边界 [@x380kkm 2026-09-10] ////
class HostHookStorageTests(unittest.TestCase):
    # //// 创建独立的个人目录和项目配置目录 [@x380kkm 2026-09-10] ////
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.native = self.user / ".codex"
        self.native.mkdir(parents=True)
        self.project = self.root / "project"
        (self.project / ".codex").mkdir(parents=True)
        self.user_config = self.native / "config.toml"
        self.user_config.write_bytes(b"user = true\n")
        self.project_config = self.project / ".codex/config.toml"
        self.project_config.write_bytes(b"project = true\n")
        self.hooks = self.project / ".codex/hooks.json"
        self.hooks.write_bytes(b'{"hooks":{}}\n')
        self.storage = self.open_project()
        self.user_storage = HostStorage(self.user, self.native, "codex-user")

    # //// 打开绑定相同用户配置目录的项目存档 [@x380kkm 2026-09-10] ////
    def open_project(self):
        return HostStorage(self.user, self.project, "codex-project", config_subdir=".codex",
                           hook_state_root=self.native)

    # //// 构造当前项目的原生开关配置并保留指定用户正文 [@x380kkm 2026-09-10] ////
    def hook_configuration(self, enabled: bool, prefix: bytes = b"user = true\n") -> bytes:
        key = json.dumps(f"{self.hooks}:stop:0:0")
        return prefix + f"[hooks.state.{key}]\nenabled = {str(enabled).lower()}\n".encode("utf-8")

    # //// 项目配置与用户开关分别保存并按项目范围恢复 [@x380kkm 2026-09-10] ////
    def test_alias_applies_and_restores_with_project_files(self):
        original = self.hook_configuration(True)
        self.user_config.write_bytes(original)
        targets = {"hooks.json": b'{"hooks":{"Stop":[]}}\n',
                   "config.toml": b"project = false\n", HOOK_STATE_NAME: self.hook_configuration(False)}
        result = self.storage.apply(targets, self.storage.capture(targets))
        self.assertEqual(self.user_config.read_bytes(), targets[HOOK_STATE_NAME])
        self.assertEqual(self.project_config.read_bytes(), targets["config.toml"])
        preview = self.open_project().preview_restore(result["backupId"])
        self.assertEqual(preview["targets"][HOOK_STATE_NAME], original)
        self.storage.restore(preview["id"], preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), original)
        self.assertEqual(self.project_config.read_bytes(), b"project = true\n")
        self.assertEqual(self.hooks.read_bytes(), b'{"hooks":{}}\n')

    # //// 项目目标写入失败时回滚已写入的用户开关 [@x380kkm 2026-09-10] ////
    def test_project_write_failure_rolls_back_user_file(self):
        targets = {HOOK_STATE_NAME: b"user = false\n", "hooks.json": b"changed"}
        write = self.storage._write_target

        def fail_project(name, content, expected):
            if name == "hooks.json":
                raise PermissionError("project locked")
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=fail_project):
            with self.assertRaises(HostTransactionError) as caught:
                self.storage.apply(targets, self.storage.capture(targets))
        self.assertFalse(caught.exception.details["recoveryRequired"])
        self.assertEqual(self.user_config.read_bytes(), b"user = true\n")
        self.assertEqual(self.hooks.read_bytes(), b'{"hooks":{}}\n')

    # //// 跨目录写入中断后通过事务恢复用户开关 [@x380kkm 2026-09-10] ////
    def test_interrupted_project_transaction_restores_user_file(self):
        original = self.hook_configuration(True)
        self.user_config.write_bytes(original)
        targets = {HOOK_STATE_NAME: self.hook_configuration(False), "hooks.json": b"changed"}
        write = self.storage._write_target

        def interrupt_project(name, content, expected):
            if name == "hooks.json":
                raise SystemExit()
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=interrupt_project):
            with self.assertRaises(SystemExit):
                self.storage.apply(targets, self.storage.capture(targets))
        restarted = self.open_project()
        self.assertTrue(restarted.status()["recoveryRequired"])
        preview = restarted.preview_restore("transaction")
        restarted.restore("transaction", preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), original)
        self.assertFalse(restarted.status()["recoveryRequired"])

    # //// 项目恢复保留后改的用户设置与其他项目开关 [@x380kkm 2026-09-10] ////
    def test_project_restore_preserves_other_user_changes(self):
        original = self.hook_configuration(True, b'model = "original"\n') + b'trusted_hash = "original-trust"\n'
        self.user_config.write_bytes(original)
        targets = {"AGENTS.override.md": b"project rule", HOOK_STATE_NAME: self.hook_configuration(False)}
        self.storage.apply(targets, self.storage.capture(targets))
        other = str(self.root / "other/.codex/hooks.json") + ":stop:0:0"
        current = self.hook_configuration(False, b'\xef\xbb\xbf# current user settings\nmodel = "recent" # model note\n')
        current = current.replace(b"enabled = false\n", b"enabled = false # switch note\n")
        current += (f'trusted_hash = "recent-trust"\n[hooks.state.{json.dumps(other)}]\n'
                    'enabled = false # external note\n').encode("utf-8")
        self.user_config.write_bytes(current)
        preview = self.storage.preview_restore("initial")
        saved = base64.b64decode(preview["baseline"]["backup"]["files"][HOOK_STATE_NAME])
        self.assertEqual(saved, original)
        self.storage.restore("initial", preview["baseline"])
        restored = self.user_config.read_bytes()
        parsed = tomllib.loads(restored.decode("utf-8-sig"))
        self.assertEqual(parsed["model"], "recent")
        self.assertEqual(parsed["hooks"]["state"][other], {"enabled": False})
        self.assertEqual(parsed["hooks"]["state"][f"{self.hooks}:stop:0:0"],
                         {"enabled": True, "trusted_hash": "original-trust"})
        self.assertTrue(restored.startswith(b"\xef\xbb\xbf# current user settings\n"))
        self.assertIn(b'# model note', restored)
        self.assertIn(b'enabled = true # switch note', restored)
        self.assertIn(b'enabled = false # external note', restored)
        self.assertFalse((self.project / "AGENTS.override.md").exists())

    # //// 规则恢复保持无本项目状态变化的用户配置原字节 [@x380kkm 2026-09-10] ////
    def test_rule_only_restore_keeps_current_user_bytes(self):
        targets = {"AGENTS.override.md": b"project rule"}
        self.storage.apply(targets, self.storage.capture(targets))
        other = str(self.root / "other/.codex/hooks.json") + ":stop:0:0"
        current = (b'\xef\xbb\xbf# current settings\r\nmodel = "recent"\r\n'
                   + f'[hooks.state.{json.dumps(other)}]\r\nenabled = false\r\n'.encode("utf-8"))
        self.user_config.write_bytes(current)
        preview = self.storage.preview_restore("initial")
        self.assertEqual(preview["targets"][HOOK_STATE_NAME], current)
        self.storage.restore("initial", preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), current)

    # //// 恢复保护点仅还原本项目开关并保留新的用户字段 [@x380kkm 2026-09-10] ////
    def test_before_restore_snapshot_reapplies_only_project_state(self):
        self.user_config.write_bytes(self.hook_configuration(True))
        targets = {HOOK_STATE_NAME: self.hook_configuration(False)}
        self.storage.apply(targets, self.storage.capture(targets))
        self.user_config.write_bytes(self.hook_configuration(False, b'model = "recent"\n'))
        initial = self.storage.preview_restore("initial")
        self.storage.restore("initial", initial["baseline"])
        self.user_config.write_bytes(self.hook_configuration(True, b'model = "newer"\n'))
        preview = self.storage.preview_restore("before-restore")
        self.storage.restore("before-restore", preview["baseline"])
        parsed = tomllib.loads(self.user_config.read_text(encoding="utf-8"))
        self.assertEqual(parsed["model"], "newer")
        self.assertFalse(parsed["hooks"]["state"][f"{self.hooks}:stop:0:0"]["enabled"])

    # //// 无本项目状态时恢复保持用户配置的空文件或不存在状态 [@x380kkm 2026-09-10] ////
    def test_no_project_state_preserves_absent_and_empty_user_config(self):
        self.storage.initialize()
        for current in (None, b"", b"# keep comment\n"):
            with self.subTest(current=current):
                if current is None:
                    self.user_config.unlink(missing_ok=True)
                else:
                    self.user_config.write_bytes(current)
                preview = self.storage.preview_restore("initial")
                self.assertEqual(preview["targets"][HOOK_STATE_NAME], current)
                self.storage.restore("initial", preview["baseline"])
                actual = self.user_config.read_bytes() if self.user_config.exists() else None
                self.assertEqual(actual, current)

    # //// 清理唯一项目状态后恢复用户配置的原始存在性 [@x380kkm 2026-09-10] ////
    def test_removing_last_project_state_restores_empty_config_presence(self):
        for index, original in enumerate((None, b"")):
            with self.subTest(original=original):
                if original is None:
                    self.user_config.unlink(missing_ok=True)
                else:
                    self.user_config.write_bytes(original)
                storage = HostStorage(self.user, self.project, f"codex-empty-{index}",
                                      config_subdir=".codex", hook_state_root=self.native)
                targets = {HOOK_STATE_NAME: self.hook_configuration(False, b"")}
                storage.apply(targets, storage.capture(targets))
                preview = storage.preview_restore("initial")
                storage.restore("initial", preview["baseline"])
                actual = self.user_config.read_bytes() if self.user_config.exists() else None
                self.assertEqual(actual, original)

    # //// 受损的恢复点正文保留存档并显示明确的不可恢复原因 [@x380kkm 2026-09-10] ////
    def test_invalid_hook_snapshot_is_unavailable(self):
        self.user_config.write_bytes(b"[broken")
        self.storage.initialize()
        current = b'model = "current"\n'
        self.user_config.write_bytes(current)
        backup = next(item for item in self.storage.status()["backups"] if item["id"] == "initial")
        self.assertFalse(backup["restorable"])
        self.assertIn("恢复点", backup["restoreError"])
        self.assertIn("UTF-8 TOML", backup["restoreError"])
        with self.assertRaisesRegex(StorageValidationError, "恢复点"):
            self.storage.preview_restore("initial")
        self.assertEqual(self.user_config.read_bytes(), current)

    # //// 用户配置在恢复预览后变化时保持所有当前文件 [@x380kkm 2026-09-10] ////
    def test_restore_rejects_user_config_changed_after_preview(self):
        self.user_config.write_bytes(self.hook_configuration(True))
        targets = {"AGENTS.override.md": b"project rule", HOOK_STATE_NAME: self.hook_configuration(False)}
        self.storage.apply(targets, self.storage.capture(targets))
        preview = self.storage.preview_restore("initial")
        current = self.hook_configuration(False, b'model = "external"\n')
        self.user_config.write_bytes(current)
        with self.assertRaises(StorageConflictError):
            self.storage.restore("initial", preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), current)
        self.assertEqual((self.project / "AGENTS.override.md").read_bytes(), b"project rule")

    # //// 恢复提交失败时按事务前完整字节回滚用户配置 [@x380kkm 2026-09-10] ////
    def test_restore_commit_failure_rolls_back_exact_user_bytes(self):
        self.user_config.write_bytes(self.hook_configuration(True))
        targets = {HOOK_STATE_NAME: self.hook_configuration(False)}
        self.storage.apply(targets, self.storage.capture(targets))
        current = self.hook_configuration(False, b'# current note\nmodel = "external"\n')
        self.user_config.write_bytes(current)
        preview = self.storage.preview_restore("initial")
        commit = self.storage._commit

        def fail_completion(documents):
            if any(item.get("reason") == "restore" and item.get("status") == "applied" for item in documents):
                raise PermissionError("metadata locked")
            return commit(documents)

        with patch.object(self.storage, "_commit", side_effect=fail_completion):
            with self.assertRaises(HostTransactionError):
                self.storage.restore("initial", preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), current)
        self.assertFalse(self.storage.status()["recoveryRequired"])

    # //// 用户配置并发变化阻止项目事务提交 [@x380kkm 2026-09-10] ////
    def test_stale_user_file_rejects_project_commit(self):
        targets = {"hooks.json": b"changed", HOOK_STATE_NAME: b"user = false\n"}
        baseline = self.storage.capture(targets)
        self.user_config.write_bytes(b"external = true\n")
        with self.assertRaises(StorageConflictError):
            self.storage.apply(targets, baseline)
        self.assertEqual(self.hooks.read_bytes(), b'{"hooks":{}}\n')
        self.assertEqual(self.user_config.read_bytes(), b"external = true\n")

    # //// 项目事务持有用户宿主锁以排斥共享配置写入 [@x380kkm 2026-09-10] ////
    def test_project_and_user_transactions_share_the_user_lock(self):
        project_targets = {HOOK_STATE_NAME: b"user = false\n"}
        user_targets = {"config.toml": b"another = true\n"}
        user_baseline = self.user_storage.capture(user_targets)
        write = self.storage._write_target

        def check_user_lock(name, content, expected):
            with self.assertRaises(StorageBusyError):
                self.user_storage.apply(user_targets, user_baseline)
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=check_user_lock):
            self.storage.apply(project_targets, self.storage.capture(project_targets))
        self.assertEqual(self.user_config.read_bytes(), project_targets[HOOK_STATE_NAME])
        with self.user_storage.store._locked_catalog():
            with self.assertRaises(StorageBusyError):
                self.storage.apply(project_targets, self.storage.capture(project_targets))

    # //// 项目备份保持目标用户根目录且预览同样绑定该目录 [@x380kkm 2026-09-10] ////
    def test_archive_and_baseline_reject_retargeted_user_root(self):
        targets = {HOOK_STATE_NAME: b"user = false\n"}
        baseline = self.storage.capture(targets)
        foreign = self.root / "foreign-codex"
        foreign.mkdir()
        other = HostStorage(self.user, self.project, "codex-project", config_subdir=".codex",
                            hook_state_root=foreign)
        with self.assertRaises(StorageBoundaryError):
            other.apply(targets, baseline)
        self.storage.apply(targets, baseline)
        with self.assertRaises(StorageBoundaryError):
            other.preview_restore("initial")
        self.assertFalse((foreign / "config.toml").exists())

    # //// 仅含项目文件的备份按记录的目标恢复并保留用户配置 [@x380kkm 2026-09-10] ////
    def test_definition_only_backup_preserves_unrelated_user_configuration(self):
        legacy = HostStorage(self.user, self.project, "codex-project", config_subdir=".codex")
        targets = {"hooks.json": b"managed"}
        legacy.apply(targets, legacy.capture(targets))
        original = next(item for item in legacy.store.snapshot() if item["id"] == "initial")
        configured = self.open_project()
        preview = configured.preview_restore("initial")
        self.assertEqual(preview["targets"][HOOK_STATE_NAME], b"user = true\n")
        configured.restore("initial", preview["baseline"])
        self.assertEqual(self.user_config.read_bytes(), b"user = true\n")
        self.assertEqual(self.hooks.read_bytes(), b'{"hooks":{}}\n')
        self.assertEqual(next(item for item in configured.store.snapshot() if item["id"] == "initial"), original)

    # //// 缺少开关快照的项目恢复按完整处理器保持当前选择 [@x380kkm 2026-09-10] ////
    def test_definition_only_backup_remaps_current_states_and_checks_user_baseline(self):
        groups = [{"hooks": [{"type": "command", "command": name}]} for name in ("first", "second")]
        original = json.dumps({"hooks": {"Stop": groups}}).encode()
        self.hooks.write_bytes(original)
        legacy = HostStorage(self.user, self.project, "codex-project", config_subdir=".codex")
        legacy.initialize()
        saved = next(item for item in legacy.store.snapshot() if item["id"] == "initial")
        rows = {hook_state_key(self.hooks, "Stop", 0, 0): {"enabled": True, "trusted_hash": "second"},
                hook_state_key(self.hooks, "Stop", 1, 0): {"enabled": False, "trusted_hash": "first"},
                "plugin:other": {"enabled": False, "trusted_hash": "external"}}
        current = tomlkit.dumps({"model": "keep", "hooks": {"state": rows}}).encode()
        targets = {"hooks.json": json.dumps({"hooks": {"Stop": groups[::-1]}}).encode(), HOOK_STATE_NAME: current}
        self.storage.apply(targets, self.storage.capture(targets))
        preview = self.storage.preview_restore("initial")
        self.user_config.write_bytes(current + b"# concurrent setting\n")
        with self.assertRaises(StorageConflictError):
            self.storage.restore("initial", preview["baseline"])
        self.assertEqual(self.hooks.read_bytes(), targets["hooks.json"])
        preview = self.storage.preview_restore("initial")
        self.storage.restore("initial", preview["baseline"])
        restored = parse_hook_states(self.user_config.read_bytes())
        self.assertEqual(restored[hook_state_key(self.hooks, "Stop", 0, 0)], {"enabled": False, "trusted_hash": "first"})
        self.assertEqual(restored[hook_state_key(self.hooks, "Stop", 1, 0)], {"enabled": True, "trusted_hash": "second"})
        self.assertEqual(restored["plugin:other"], rows["plugin:other"])
        self.assertIn(b'model = "keep"', self.user_config.read_bytes())
        self.assertIn(b"# concurrent setting", self.user_config.read_bytes())
        self.assertEqual(self.hooks.read_bytes(), original)
        self.assertEqual(next(item for item in self.storage.store.snapshot() if item["id"] == "initial"), saved)

    # //// 重复处理器的恢复歧义保持原文件并显示阻断原因 [@x380kkm 2026-09-10] ////
    def test_definition_only_backup_blocks_ambiguous_native_states(self):
        group = {"hooks": [{"type": "command", "command": "same"}]}
        self.hooks.write_text(json.dumps({"hooks": {"Stop": [group]}}), encoding="utf-8")
        legacy = HostStorage(self.user, self.project, "codex-project", config_subdir=".codex")
        legacy.initialize()
        self.hooks.write_text(json.dumps({"hooks": {"Stop": [group, group]}}), encoding="utf-8")
        current = self.hook_configuration(False)
        self.user_config.write_bytes(current)
        backup = next(item for item in self.storage.status()["backups"] if item["id"] == "initial")
        self.assertFalse(backup["restorable"])
        with self.assertRaisesRegex(StorageValidationError, "唯一对应"):
            self.storage.preview_restore("initial")
        self.assertEqual(self.user_config.read_bytes(), current)

    # //// 固定别名仅在已配置的独立用户目标上可用 [@x380kkm 2026-09-10] ////
    def test_alias_requires_a_distinct_configured_root(self):
        with self.assertRaises(StorageValidationError):
            self.user_storage.capture({HOOK_STATE_NAME: b"changed"})
        with self.assertRaises(StorageBoundaryError):
            HostStorage(self.user, self.project, "codex-project", config_subdir=".codex",
                        hook_state_root=self.project / ".codex")
        with self.assertRaises(StorageBoundaryError):
            HostStorage(self.user, self.project, "codex-user", hook_state_root=self.native)

    # //// 用户配置目录中的文件链接保持外部原文 [@x380kkm 2026-09-10] ////
    def test_alias_rejects_symlink_target(self):
        outside = self.root / "outside.toml"
        outside.write_bytes(b"outside = true\n")
        self.user_config.unlink()
        try:
            self.user_config.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"当前宿主无法创建符号链接: {error}")
        with self.assertRaises(StorageBoundaryError):
            self.storage.capture({HOOK_STATE_NAME: b"changed"})
        self.assertEqual(outside.read_bytes(), b"outside = true\n")

    # //// 待应用请求独立合并并保留关闭状态和原始配置 [@x380kkm 2026-09-10] ////
    def test_hook_requests_merge_without_touching_host_files(self):
        self.storage.enable(False)
        original = self.user_config.read_bytes()
        first = {"enabled": True, "groups": [{"enabled": False}]}
        self.storage.save_hook_requests({"one#hook": first})
        self.storage.save_hook_requests({"two#hook": {"enabled": False}})
        first["enabled"] = False
        requests = self.storage.read_ownership()["hookRequests"]
        self.assertTrue(requests["one#hook"]["enabled"])
        self.assertFalse(self.storage.status()["enabled"])
        self.assertFalse(self.storage.status()["initialized"])
        self.assertEqual(self.user_config.read_bytes(), original)
        self.storage.save_hook_requests({"one#hook": None})
        self.assertEqual(self.storage.read_ownership()["hookRequests"], {"two#hook": {"enabled": False}})
        self.storage.save_hook_requests({"two#hook": None})
        self.assertNotIn("hookRequests", self.storage.read_ownership())

    # //// 请求来源基线在协作锁内核对并阻止失效请求保存 [@x380kkm 2026-09-10] ////
    def test_hook_request_input_conflict_preserves_ownership(self):
        self.storage.save_hook_requests({"one#hook": {"enabled": False}})
        before = self.storage.read_ownership()
        verify = unittest.mock.Mock(side_effect=StorageConflictError("binding"))
        with self.assertRaises(StorageConflictError):
            self.storage.save_hook_requests({"one#hook": {"enabled": True}}, verify_inputs=verify)
        verify.assert_called_once_with()
        self.assertEqual(self.storage.read_ownership(), before)


# //// 执行跨目录 Hook 存储测试 [@x380kkm 2026-09-10] ////
if __name__ == "__main__":
    unittest.main()
