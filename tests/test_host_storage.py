# audience: internal
# # host-storage-tests
# 独立临时目录承载宿主原文, 覆盖存档与恢复的字节语义及协作写入边界.

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.storage_errors import StorageBoundaryError, StorageConflictError, StorageValidationError


# //// 检查宿主配置的存档, 接管开关和恢复事务 [@x380kkm 2026-09-07] ////
class HostStorageTests(unittest.TestCase):
    # //// 创建相互独立的个人存档和宿主配置目录 [@x380kkm 2026-09-07] ////
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user = self.root / "user"
        self.user.mkdir()
        self.host = self.root / "project"
        self.host.mkdir()
        self.storage = HostStorage(self.user, self.host, "project-example", config_subdir=".codex")

    # //// 应用宿主字节并使用当前预览基线 [@x380kkm 2026-09-07] ////
    def apply(self, targets):
        return self.storage.apply(targets, self.storage.capture(targets))

    # //// 空文件与不存在状态分别存档且恢复前保留当前文件 [@x380kkm 2026-09-07] ////
    def test_restore_preserves_empty_and_absent_files_and_archives_current(self):
        empty = self.host / "AGENTS.override.md"
        empty.write_bytes(b"")
        targets = {"AGENTS.override.md": "规则正文".encode("utf-8"), "config.toml": b"enabled=true\n"}
        result = self.apply(targets)
        self.assertEqual((self.host / ".codex/config.toml").read_bytes(), targets["config.toml"])
        preview = self.storage.preview_restore(result["backupId"])
        self.assertEqual(preview["targets"], {"AGENTS.md": None, "AGENTS.override.md": b"", "config.toml": None,
                                              "hooks.json": None})
        restored = self.storage.restore(result["backupId"], preview["baseline"])
        self.assertFalse(restored["enabled"])
        self.assertEqual(empty.read_bytes(), b"")
        self.assertFalse((self.host / ".codex/config.toml").exists())
        protection = self.storage.preview_restore(restored["backupId"])["targets"]
        self.assertEqual(protection, {"AGENTS.md": None, "hooks.json": None, **targets})
        self.assertFalse(self.storage.status()["enabled"])

    # //// 初始恢复点捕获全部宿主配置且重复使用保持原样 [@x380kkm 2026-09-07] ////
    def test_initial_snapshot_is_complete_and_immutable(self):
        original = {"AGENTS.md": b"user instructions", "AGENTS.override.md": None,
                    "config.toml": b"custom = true\n", "hooks.json": b""}
        (self.host / "AGENTS.md").write_bytes(original["AGENTS.md"])
        (self.host / ".codex").mkdir()
        (self.host / ".codex/config.toml").write_bytes(original["config.toml"])
        (self.host / ".codex/hooks.json").write_bytes(original["hooks.json"])
        status = self.storage.initialize()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["initialized"])
        self.assertEqual(status["initialBackupId"], "initial")
        self.assertFalse((self.host / "AGENTS.override.md").exists())
        self.apply({"AGENTS.override.md": b"managed"})
        (self.host / "AGENTS.md").write_bytes(b"later instructions")
        (self.host / ".codex/config.toml").write_bytes(b"later = true\n")
        self.storage.initialize()
        self.apply({"AGENTS.override.md": b"revised"})
        self.storage.initialize()
        preview = self.storage.preview_restore("initial")
        self.assertEqual(preview["targets"], original)
        self.assertEqual([item["id"] for item in self.storage.status()["backups"]], ["initial"])
        documents = self.storage.store.snapshot()
        self.assertEqual({item["id"] for item in documents}, {"initial", "transaction", "state"})
        self.storage.restore("initial", preview["baseline"])
        self.assertEqual((self.host / "AGENTS.md").read_bytes(), original["AGENTS.md"])
        self.assertEqual((self.host / ".codex/config.toml").read_bytes(), original["config.toml"])

    # //// 单一覆盖文件的首次应用也保存其余原始宿主配置 [@x380kkm 2026-09-07] ////
    def test_first_apply_captures_all_files_without_invalidating_its_baseline(self):
        (self.host / "AGENTS.md").write_bytes(b"instructions")
        (self.host / ".codex").mkdir()
        (self.host / ".codex/config.toml").write_bytes(b"config = true")
        targets = {"AGENTS.override.md": b"managed"}
        baseline = self.storage.capture(targets)
        self.storage.apply(targets, baseline)
        self.assertEqual(self.storage.preview_restore("initial")["targets"],
                         {"AGENTS.md": b"instructions", "AGENTS.override.md": None,
                          "config.toml": b"config = true", "hooks.json": None})

    # //// 恢复保护点时分别保留所选目标和最近保护副本 [@x380kkm 2026-09-08] ////
    def test_restoring_protection_keeps_initial_and_retains_selected_target(self):
        self.apply({"AGENTS.override.md": b"managed"})
        initial = self.storage.preview_restore("initial")
        self.storage.restore("initial", initial["baseline"])
        protection = self.storage.preview_restore("before-restore")
        self.storage.restore("before-restore", protection["baseline"])
        self.assertEqual((self.host / "AGENTS.override.md").read_bytes(), b"managed")
        self.assertIsNone(self.storage.preview_restore("before-restore")["targets"]["AGENTS.override.md"])
        self.assertIsNone(self.storage.preview_restore("initial")["targets"]["AGENTS.override.md"])
        self.assertEqual(self.storage.preview_restore("restore-target")["targets"]["AGENTS.override.md"], b"managed")
        self.assertEqual({item["id"] for item in self.storage.store.snapshot()},
                         {"initial", "before-restore", "restore-target", "transaction", "state"})

    # //// 默认启用的只读状态保持目录原样且关闭保留宿主内容 [@x380kkm 2026-09-07] ////
    def test_status_is_read_only_and_shutdown_preserves_configuration(self):
        self.assertTrue(self.storage.status()["enabled"])
        self.assertFalse((self.user / ".harness").exists())
        target = {"AGENTS.override.md": b"private-content"}
        self.apply(target)
        status = self.storage.status()
        self.assertNotIn("private-content", json.dumps(status))
        self.storage.enable(False, status["baseline"])
        self.assertEqual((self.host / "AGENTS.override.md").read_bytes(), b"private-content")
        self.assertFalse(self.storage.status()["enabled"])

    # //// 字段归属随宿主事务同步且状态摘要仅暴露并发令牌 [@x380kkm 2026-09-07] ////
    def test_ownership_commits_atomically_and_stays_private(self):
        targets = {"AGENTS.override.md": b"managed"}
        ownership = {"config.toml": {"before": "sensitive-original", "written": "managed"}}
        self.storage.apply(targets, self.storage.capture(targets), ownership=ownership)
        read = self.storage.read_ownership()
        read["config.toml"]["before"] = "changed-copy"
        self.assertEqual(self.storage.read_ownership(), ownership)
        self.assertNotIn("sensitive-original", json.dumps(self.storage.status()))
        self.storage.enable(False, self.storage.status()["baseline"])
        self.assertEqual(self.storage.read_ownership(), ownership)
        self.storage.enable(True)
        newer = {"config.toml": {"before": "another-original", "written": "revised"}}
        changed = {"AGENTS.override.md": b"revised"}
        with patch.object(self.storage, "_write_target", side_effect=PermissionError("locked")):
            with self.assertRaises(HostTransactionError):
                self.storage.apply(changed, self.storage.capture(changed), ownership=newer)
        self.assertEqual(self.storage.read_ownership(), ownership)
        stale = self.storage.capture(targets)
        self.storage.apply(targets, stale, ownership=newer)
        self.assertEqual(self.storage.read_ownership(), newer)
        with self.assertRaises(StorageConflictError):
            self.storage.apply(targets, stale, ownership=ownership)
        initial = self.storage.preview_restore("initial")
        self.storage.restore("initial", initial["baseline"])
        self.assertEqual(self.storage.read_ownership(), {})

    # //// 后续目标写入失败时恢复已写入目标并保留备份 [@x380kkm 2026-09-07] ////
    def test_write_failure_rolls_back_only_completed_writes(self):
        target = self.host / "AGENTS.override.md"
        target.write_bytes(b"original")
        targets = {"AGENTS.override.md": b"managed", "config.toml": b"second"}
        write = self.storage._write_target

        def failing_write(name, content, expected):
            if name == "config.toml":
                raise PermissionError("locked")
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=failing_write):
            with self.assertRaises(HostTransactionError) as caught:
                self.apply(targets)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(caught.exception.details["recoveryRequired"])
        self.assertEqual([item["id"] for item in self.storage.status()["backups"]], ["initial"])
        transaction = next(item for item in self.storage.store.snapshot() if item["id"] == "transaction")
        self.assertEqual(transaction["status"], "rolled-back")

    # //// 完成记录落盘失败时回滚宿主文件并保留可选备份 [@x380kkm 2026-09-07] ////
    def test_completion_record_failure_rolls_back_host(self):
        target = self.host / "AGENTS.override.md"
        target.write_bytes(b"original")
        commit = self.storage._commit

        def fail_completion(documents):
            if any(item.get("status") == "applied" for item in documents):
                raise PermissionError("metadata locked")
            return commit(documents)

        with patch.object(self.storage, "_commit", side_effect=fail_completion):
            with self.assertRaises(HostTransactionError):
                self.apply({"AGENTS.override.md": b"managed"})
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(self.storage.status()["recoveryRequired"])
        self.assertIsNone(self.storage.status()["lastApplied"])

    # //// 回滚期间遇到外部改动时保留新内容并要求显式恢复 [@x380kkm 2026-09-07] ////
    def test_failure_keeps_collaborator_changes_and_recovery_record(self):
        target = self.host / "AGENTS.override.md"
        target.write_bytes(b"original")
        targets = {"AGENTS.override.md": b"managed", "config.toml": b"second"}
        write = self.storage._write_target

        def concurrent_write(name, content, expected):
            if name == "config.toml":
                target.write_bytes(b"collaborator")
                raise PermissionError("locked")
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=concurrent_write):
            with self.assertRaises(HostTransactionError):
                self.apply(targets)
        self.assertEqual(target.read_bytes(), b"collaborator")
        status = self.storage.status()
        self.assertTrue(status["recoveryRequired"])
        self.storage.enable(False)
        preview = self.storage.preview_restore(status["pending"][0])
        with patch.object(self.storage, "_write_target", side_effect=PermissionError("locked")):
            with self.assertRaises(HostTransactionError):
                self.storage.restore(preview["id"], preview["baseline"])
        self.assertTrue(self.storage.status()["recoveryRequired"])
        preview = self.storage.preview_restore("initial")
        result = self.storage.restore(preview["id"], preview["baseline"])
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(self.storage.preview_restore(result["backupId"])["targets"]["AGENTS.override.md"], b"collaborator")
        self.assertFalse(self.storage.status()["recoveryRequired"])

    # //// 陈旧文件或控制状态阻止应用并保持当前宿主内容 [@x380kkm 2026-09-07] ////
    def test_stale_preview_rejects_file_and_control_changes(self):
        targets = {"AGENTS.override.md": b"managed"}
        baseline = self.storage.capture(targets)
        target = self.host / "AGENTS.override.md"
        target.write_bytes(b"collaborator")
        with self.assertRaises(StorageConflictError):
            self.storage.apply(targets, baseline)
        self.assertEqual(target.read_bytes(), b"collaborator")
        baseline = self.storage.capture(targets)
        self.storage.enable(False)
        with self.assertRaises(StorageConflictError):
            self.storage.apply(targets, baseline)
        self.assertEqual(self.storage.status()["backups"], [])

    # //// 进程中断留下恢复记录且再次读取保持宿主原样 [@x380kkm 2026-09-07] ////
    def test_interrupted_apply_requires_explicit_restore(self):
        target = self.host / "AGENTS.override.md"
        target.write_bytes(b"original")
        targets = {"AGENTS.override.md": b"managed", "hooks.json": b"{}"}
        write = self.storage._write_target

        def interrupted_write(name, content, expected):
            if name == "hooks.json":
                raise SystemExit()
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=interrupted_write):
            with self.assertRaises(SystemExit):
                self.apply(targets)
        restarted = HostStorage(self.user, self.host, "project-example", config_subdir=".codex")
        status = restarted.status()
        self.assertTrue(status["recoveryRequired"])
        self.assertEqual(target.read_bytes(), b"managed")
        with self.assertRaises(StorageValidationError):
            restarted.apply(targets, restarted.capture(targets))
        preview = restarted.preview_restore(status["pending"][0])
        restarted.restore(preview["id"], preview["baseline"])
        self.assertEqual(target.read_bytes(), b"original")
        self.assertFalse(restarted.status()["enabled"])

    # //// 备份绑定宿主根且文件名称只能来自固定集合 [@x380kkm 2026-09-07] ////
    def test_restore_rejects_unknown_backup_and_foreign_root(self):
        self.apply({"config.toml": b"value=true"})
        with self.assertRaises(StorageValidationError):
            self.storage.preview_restore("../unknown")
        with self.assertRaises(StorageValidationError):
            self.storage.capture({"../secrets": b""})
        foreign = self.root / "foreign"
        foreign.mkdir()
        other = HostStorage(self.user, foreign, "project-example", config_subdir=".codex")
        with self.assertRaises(StorageBoundaryError):
            other.status()

    # //// 恢复预览绑定备份原文并拒绝随后修改的存档 [@x380kkm 2026-09-07] ////
    def test_restore_rejects_backup_changed_after_preview(self):
        result = self.apply({"AGENTS.override.md": b"managed"})
        preview = self.storage.preview_restore(result["backupId"])
        documents = self.storage.store.snapshot()
        backup = next(item for item in documents if item["id"] == result["backupId"])
        backup["files"]["AGENTS.override.md"] = ""
        with self.storage.store._locked_catalog():
            self.storage._commit(documents)
        with self.assertRaises(StorageConflictError):
            self.storage.restore(result["backupId"], preview["baseline"])
        self.assertEqual((self.host / "AGENTS.override.md").read_bytes(), b"managed")

    # //// 中间普通文件阻止宿主和备份路径穿越 [@x380kkm 2026-09-07] ////
    def test_non_directory_ancestors_reject_target_and_metadata(self):
        (self.host / ".codex").write_text("existing", encoding="utf-8")
        with self.assertRaises(StorageBoundaryError):
            self.storage.capture({"config.toml": b""})
        (self.host / ".codex").unlink()
        (self.user / ".harness").write_text("existing", encoding="utf-8")
        with self.assertRaises(StorageBoundaryError):
            self.storage.status()

    # //// 文件链接阻止宿主内容读取并保留外部原文 [@x380kkm 2026-09-07] ////
    def test_file_symlink_is_rejected(self):
        external = self.root / "external.md"
        external.write_bytes(b"external")
        link = self.host / "AGENTS.override.md"
        try:
            link.symlink_to(external)
        except OSError as error:
            self.skipTest(f"当前宿主无法创建符号链接: {error}")
        with self.assertRaises(StorageBoundaryError):
            self.storage.capture({"AGENTS.override.md": b"managed"})
        self.assertEqual(external.read_bytes(), b"external")

    # //// 宿主与存档目录连接保持外部目录原样 [@x380kkm 2026-09-07] ////
    @unittest.skipUnless(os.name == "nt", "Windows 目录连接使用原生重解析点.")
    def test_junction_targets_and_metadata_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        for junction in (self.host / ".codex", self.user / ".harness"):
            with self.subTest(path=junction):
                source = str(junction).replace("'", "''")
                destination = str(outside).replace("'", "''")
                command = ("$ErrorActionPreference = 'Stop'\n"
                           f"New-Item -ItemType Junction -Path '{source}' -Target '{destination}' | Out-Null")
                subprocess.run(["pwsh", "-Command", command], check=True, capture_output=True,
                               text=True, encoding="utf-8")
                try:
                    with self.assertRaises(StorageBoundaryError):
                        self.storage.status()
                    with self.assertRaises(StorageBoundaryError):
                        self.storage.capture({"config.toml": b""})
                    self.assertEqual(list(outside.iterdir()), [])
                finally:
                    junction.rmdir()


# //// 执行宿主存储测试 [@x380kkm 2026-09-07] ////
if __name__ == "__main__":
    unittest.main()
