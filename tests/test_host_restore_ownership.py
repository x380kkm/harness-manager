# audience: internal
# # host-restore-ownership-tests
# 隔离宿主目录核对文件恢复与字段归属同步, 包括原始恢复点和中断事务.

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.host_ownership import reconcile
from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.storage_errors import StorageValidationError


# //// 核对恢复后继续接管与解除管理的文件结果 [@x380kkm 2026-09-08] ////
class HostRestoreOwnershipTests(unittest.TestCase):
    # //// 创建具有原始覆盖内容的独立宿主 [@x380kkm 2026-09-08] ////
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.user = self.root / "user"
        self.host = self.root / "host"
        self.user.mkdir()
        self.host.mkdir()
        self.storage = HostStorage(self.user, self.host, "codex-user")
        self.original = b"Original local guidance.\n"
        self.override = self.host / "AGENTS.override.md"
        self.override.write_bytes(self.original)

    # //// 通过公开归属调和入口应用或解除完整规则 [@x380kkm 2026-09-08] ////
    def apply_rule(self, text):
        compiled = {} if text is None else {"AGENTS.override.md": text}
        ownership = self.storage.read_ownership()
        baseline = self.storage.capture({"AGENTS.override.md": text})
        targets, updated = reconcile(compiled, [], baseline["files"], ownership, self.host)
        baseline["files"] = {name: baseline["files"][name] for name in targets}
        return self.storage.apply(targets, baseline, ownership=updated)

    # //// 恢复选择的文件与对应归属并保持接管关闭 [@x380kkm 2026-09-08] ////
    def restore(self, identity):
        preview = self.storage.preview_restore(identity)
        result = self.storage.restore(identity, preview["baseline"])
        self.assertFalse(result["enabled"])

    # //// 恢复管理器输出后解除规则仍回到接管前原文 [@x380kkm 2026-09-08] ////
    def test_protection_restores_ownership_for_later_release(self):
        self.apply_rule(b"Managed guidance.\n")
        owned = self.storage.read_ownership()
        self.restore("initial")
        self.assertEqual(self.override.read_bytes(), self.original)
        self.assertEqual(self.storage.read_ownership(), {})
        self.restore("before-restore")
        self.assertEqual(self.override.read_bytes(), b"Managed guidance.\n")
        self.assertEqual(self.storage.read_ownership(), owned)
        self.storage.enable(True)
        self.apply_rule(b"Managed guidance.\n")
        self.apply_rule(None)
        self.assertEqual(self.override.read_bytes(), self.original)
        self.assertEqual(self.storage.read_ownership(), {})

    # //// 中断恢复保留事务前归属以继续撤销受管内容 [@x380kkm 2026-09-08] ////
    def test_transaction_recovery_restores_before_ownership(self):
        self.apply_rule(b"Managed guidance.\n")
        owned = self.storage.read_ownership()
        targets = {"AGENTS.override.md": b"Changed guidance.\n", "config.toml": b"value = true\n"}
        write = self.storage._write_target

        def interrupted(name, content, expected):
            if name == "config.toml":
                raise SystemExit()
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.storage.apply(targets, self.storage.capture(targets), ownership={})
        self.restore("transaction")
        self.assertEqual(self.override.read_bytes(), b"Managed guidance.\n")
        self.assertEqual(self.storage.read_ownership(), owned)
        self.storage.enable(True)
        self.apply_rule(None)
        self.assertEqual(self.override.read_bytes(), self.original)

    # //// 保护副本在目标写入失败和中断后保持原文与字段归属 [@x380kkm 2026-09-08] ////
    def test_selected_protection_survives_failed_and_interrupted_restores(self):
        self.apply_rule(b"Managed guidance.\n")
        ownership = self.storage.read_ownership()
        config = self.host / "config.toml"
        custom = b'model = "saved-choice"\n'
        config.write_bytes(custom)
        self.restore("initial")
        selected = self.storage.preview_restore("before-restore")["targets"]
        for failure in (PermissionError("locked"), SystemExit()):
            with self.subTest(failure=type(failure).__name__):
                write = self.storage._write_target

                # //// 在恢复配置文件时中止写入 [@x380kkm 2026-09-08] ////
                def failing_write(name, content, expected):
                    if name == "config.toml":
                        raise failure
                    return write(name, content, expected)

                with patch.object(self.storage, "_write_target", side_effect=failing_write):
                    with self.assertRaises(HostTransactionError if isinstance(failure, Exception) else SystemExit):
                        self.restore("before-restore")
                self.storage = HostStorage(self.user, self.host, "codex-user")
                self.assertEqual(self.storage.preview_restore("before-restore")["targets"], selected)
                self.assertTrue(next(item for item in self.storage.status()["backups"]
                                     if item["id"] == "before-restore")["restorable"])
        self.restore("before-restore")
        self.assertEqual(config.read_bytes(), custom)
        self.assertEqual(self.storage.read_ownership(), ownership)
        self.storage.enable(True)
        self.apply_rule(None)
        self.assertEqual(self.override.read_bytes(), self.original)
        self.assertEqual(config.read_bytes(), custom)

    # //// 事务恢复再次中断时通过可选目标继续恢复原文和归属 [@x380kkm 2026-09-08] ////
    def test_transaction_restore_keeps_selected_target_across_restarts(self):
        self.apply_rule(b"Managed guidance.\n")
        ownership = self.storage.read_ownership()
        config = self.host / "config.toml"
        config.write_bytes(b'model = "saved-choice"\n')
        targets = {"AGENTS.override.md": b"Changed guidance.\n", "config.toml": b'model = "another-choice"\n'}
        write = self.storage._write_target

        # //// 在应用配置文件前中断宿主事务 [@x380kkm 2026-09-08] ////
        def interrupted_apply(name, content, expected):
            if name == "config.toml":
                raise SystemExit()
            return write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=interrupted_apply):
            with self.assertRaises(SystemExit):
                self.storage.apply(targets, self.storage.capture(targets), ownership={})
        selected = {"AGENTS.md": None, "hooks.json": None, **self.storage.preview_restore("transaction")["targets"]}
        with patch.object(self.storage, "_write_target", side_effect=PermissionError("locked")):
            with self.assertRaises(HostTransactionError) as caught:
                self.restore("transaction")
        self.assertEqual(caught.exception.details["restoreTargetId"], "restore-target")
        self.storage = HostStorage(self.user, self.host, "codex-user")
        self.assertEqual(self.storage.preview_restore("restore-target")["targets"], selected)
        write = self.storage._write_target

        # //// 在恢复目标字节后中断完成记录 [@x380kkm 2026-09-08] ////
        def interrupted_restore(name, content, expected):
            write(name, content, expected)
            raise SystemExit()

        with patch.object(self.storage, "_write_target", side_effect=interrupted_restore):
            with self.assertRaises(SystemExit):
                self.restore("restore-target")
        self.storage = HostStorage(self.user, self.host, "codex-user")
        self.assertEqual(self.storage.preview_restore("restore-target")["targets"], selected)
        self.assertTrue(next(item for item in self.storage.status()["backups"]
                             if item["id"] == "restore-target")["restorable"])
        self.restore("restore-target")
        self.assertEqual(self.override.read_bytes(), b"Managed guidance.\n")
        self.assertEqual(config.read_bytes(), b'model = "saved-choice"\n')
        self.assertEqual(self.storage.read_ownership(), ownership)
        self.storage.enable(True)
        self.apply_rule(None)
        self.assertEqual(self.override.read_bytes(), self.original)

    # //// 归属缺失的保护点在预览时要求选择原始恢复点 [@x380kkm 2026-09-08] ////
    def test_legacy_protection_requires_matching_ownership(self):
        self.apply_rule(b"Managed guidance.\n")
        self.restore("initial")
        documents = self.storage.store.snapshot()
        protection = next(item for item in documents if item["id"] == "before-restore")
        del protection["ownership"]
        with self.storage.store._locked_catalog():
            self.storage._commit(documents)
        with self.assertRaises(StorageValidationError):
            self.storage.preview_restore("before-restore")
        self.assertEqual(self.override.read_bytes(), self.original)
        self.restore("initial")


# //// 执行恢复归属测试 [@x380kkm 2026-09-08] ////
if __name__ == "__main__":
    unittest.main()
