# audience: internal
# # host-migration-recovery-tests
# 隔离目录核对逐项迁移顺序, 来源提交边界和中断保护点的文件归属.

import base64
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.host_ownership import HOOK_POINT, reconcile
from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.storage_errors import StorageConflictError, StorageValidationError
from harness_manager.codex_hook_state import hook_state_key, parse_hook_states


# //// 为归属调和编码完整文件快照 [@x380kkm 2026-09-08] ////
def encoded(files):
    if "hooks.json" in files:
        files = {"config.toml": None, **files}
    return {name: base64.b64encode(content).decode("ascii") if content is not None else None for name, content in files.items()}


# //// 逐项迁移已有 Hook 后按原序恢复 [@x380kkm 2026-09-08] ////
class HookMigrationOrderTests(unittest.TestCase):
    # //// 关闭与解除分别发生时保持各组的原序关系 [@x380kkm 2026-09-08] ////
    def test_sequential_adoption_and_release_keep_original_order(self):
        entries = [{"ref": name, "point": HOOK_POINT, "enabled": False,
                    "hook": {"event": "PreToolUse", "group": {"hooks": [{"type": "command", "command": name}]}}}
                   for name in ("A", "B", "C", "external")]
        original = {"metadata": "private", "hooks": {"PreToolUse": [entry["hook"]["group"] for entry in entries]}}
        for release_order in (("A", "B", "C"), ("C", "B", "A")):
            with self.subTest(release_order=release_order):
                files, ownership, managed = {"hooks.json": json.dumps(original).encode()}, {}, []
                for entry in entries[:3]:
                    managed.append(entry)
                    files, ownership = reconcile({"hooks.json": b"{}"}, managed, encoded(files), ownership, Path.cwd())
                self.assertEqual([group["beforeIndex"] for group in ownership["hooks"]["groups"]], [0, 1, 2])
                for name in release_order:
                    managed = [entry for entry in managed if entry["ref"] != name]
                    files, ownership = reconcile({"hooks.json": b"{}"} if managed else {}, managed, encoded(files), ownership, Path.cwd())
                    output = json.loads(files["hooks.json"])
                    hidden = {entry["ref"] for entry in managed}
                    self.assertEqual([group["hooks"][0]["command"] for group in output["hooks"]["PreToolUse"]],
                                     [entry["ref"] for entry in entries])
                    states = parse_hook_states(files["config.toml"])
                    self.assertEqual([states.get(hook_state_key(Path.cwd() / "hooks.json", "PreToolUse", index, 0), {}).get("enabled", True)
                                      for index in range(len(entries))], [entry["ref"] not in hidden for entry in entries])
                    self.assertEqual(output["metadata"], "private")
                self.assertEqual(ownership, {})

    # //// 恢复组以重复外部邻居的出现次序保持原位置 [@x380kkm 2026-09-08] ////
    def test_duplicate_external_neighbors_keep_the_restored_position(self):
        external = {"hooks": [{"type": "command", "command": "external"}]}
        group = {"hooks": [{"type": "command", "command": "managed"}]}
        entry = {"ref": "managed", "point": HOOK_POINT, "enabled": False,
                 "hook": {"event": "PreToolUse", "group": group}}
        original = {"hooks": {"PreToolUse": [external, group, external]}}
        files, ownership = reconcile({"hooks.json": b"{}"}, [entry],
                                     encoded({"hooks.json": json.dumps(original).encode()}), {}, Path.cwd())
        restored, released = reconcile({}, [], encoded(files), ownership, Path.cwd())
        self.assertEqual(json.loads(restored["hooks.json"]), original)
        self.assertEqual(released, {})
        changed = {"hooks.json": json.dumps({"hooks": {"PreToolUse": [external]}}).encode()}
        with self.assertRaises(StorageConflictError):
            reconcile({}, [], encoded(changed), ownership, Path.cwd())

    # //// 恢复已有组时保留外部新增组的重复次数与位置 [@x380kkm 2026-09-08] ////
    def test_new_duplicate_external_groups_keep_the_restored_position(self):
        group = {"hooks": [{"type": "command", "command": "managed"}]}
        external = {"hooks": [{"type": "command", "command": "external"}]}
        added = {"hooks": [{"type": "command", "command": "added"}]}
        entry = {"ref": "managed", "point": HOOK_POINT, "enabled": False,
                 "hook": {"event": "PreToolUse", "group": group}}
        original = {"hooks": {"PreToolUse": [group, external]}}
        _, ownership = reconcile({"hooks.json": b"{}"}, [entry],
                                 encoded({"hooks.json": json.dumps(original).encode()}), {}, Path.cwd())
        current = {"hooks": {"PreToolUse": [group, external, added, added]}}
        restored, released = reconcile({}, [], encoded({"hooks.json": json.dumps(current).encode()}), ownership, Path.cwd())
        self.assertEqual(json.loads(restored["hooks.json"])["hooks"]["PreToolUse"], [group, external, added, added])
        self.assertEqual(released, {})

    # //// 分次接管外部新增组时按重复邻居的出现次序保存位置 [@x380kkm 2026-09-08] ////
    def test_later_adoption_between_duplicate_neighbors_keeps_its_position(self):
        entries = [{"ref": name, "point": HOOK_POINT, "enabled": False,
                    "hook": {"event": "PreToolUse", "group": {"hooks": [{"type": "command", "command": name}]}}}
                   for name in ("managed", "added", "external")]
        managed, added, external = [entry["hook"]["group"] for entry in entries]
        original = {"hooks": {"PreToolUse": [external, managed, external]}}
        _, ownership = reconcile({"hooks.json": b"{}"}, entries[:1],
                                 encoded({"hooks.json": json.dumps(original).encode()}), {}, Path.cwd())
        current = {"hooks": {"PreToolUse": [external, managed, added, external]}}
        files, ownership = reconcile({"hooks.json": b"{}"}, entries[:2],
                                     encoded({"hooks.json": json.dumps(current).encode()}), ownership, Path.cwd())
        files, ownership = reconcile({"hooks.json": b"{}"}, entries[:1], encoded(files), ownership, Path.cwd())
        self.assertEqual(json.loads(files["hooks.json"]), current)
        files, ownership = reconcile({}, [], encoded(files), ownership, Path.cwd())
        self.assertEqual(json.loads(files["hooks.json"])["hooks"]["PreToolUse"], [external, managed, added, external])
        self.assertEqual(ownership, {})


# //// 核对来源变化与中断恢复发生在持久化边界时的结果 [@x380kkm 2026-09-08] ////
class HostMigrationRecoveryTests(unittest.TestCase):
    # //// 创建原文, 覆盖文件和独立存档目录 [@x380kkm 2026-09-08] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.host = self.root / "user", self.root / "host"
        self.user.mkdir()
        self.host.mkdir()
        self.agents = self.host / "AGENTS.md"
        self.override = self.host / "AGENTS.override.md"
        self.agents.write_bytes(b"Original source.\n")
        self.override.write_bytes(b"Original override.\n")
        self.storage = HostStorage(self.user, self.host, "codex-user")

    # //// 准备包含原文片段归属的完整规则输出 [@x380kkm 2026-09-08] ////
    def rule_plan(self, text):
        baseline = self.storage.capture({"AGENTS.override.md": text})
        targets, ownership = reconcile({"AGENTS.override.md": text} if text is not None else {}, [], baseline["files"],
                                       self.storage.read_ownership(), self.host)
        if text is not None:
            ownership["AGENTS.override.md"]["source"] = {"file": "AGENTS.override.md", "entries": {"rule": {"fragments": ["Original override."]}}}
        return targets, baseline, ownership

    # //// 应用规则并保存其对应的原文归属 [@x380kkm 2026-09-08] ////
    def apply_rule(self, text):
        targets, baseline, ownership = self.rule_plan(text)
        return self.storage.apply(targets, baseline, ownership=ownership)

    # //// 恢复明确选中的备份并返回实际保护点 [@x380kkm 2026-09-08] ////
    def restore(self, identity):
        preview = self.storage.preview_restore(identity)
        return self.storage.restore(identity, preview["baseline"])

    # //// 来源在 pending 持久化后变化时保持原宿主文件 [@x380kkm 2026-09-08] ////
    def test_source_change_after_pending_is_rejected(self):
        targets, baseline, ownership = self.rule_plan(b"Managed override.\n")
        baseline["instructionReads"] = self.storage.capture_instructions(["AGENTS.md", "AGENTS.override.md"])
        commit = self.storage._commit

        def changed_source(documents):
            commit(documents)
            if any(item.get("status") == "pending" for item in documents):
                self.agents.write_bytes(b"Private source addition.\n")

        with patch.object(self.storage, "_commit", side_effect=changed_source):
            with self.assertRaises(HostTransactionError):
                self.storage.apply(targets, baseline, ownership=ownership)
        self.assertEqual(self.override.read_bytes(), b"Original override.\n")
        self.assertEqual(self.agents.read_bytes(), b"Private source addition.\n")
        self.assertEqual(self.storage.read_ownership(), {})
        self.assertFalse(self.storage.status()["recoveryRequired"])

    # //// 来源在目标写入后变化时回滚本次目标并保留私人原文 [@x380kkm 2026-09-08] ////
    def test_source_change_after_target_write_rolls_back(self):
        targets, baseline, ownership = self.rule_plan(b"Managed override.\n")
        baseline["instructionReads"] = self.storage.capture_instructions(["AGENTS.md", "AGENTS.override.md"])
        write = self.storage._write_target

        def changed_source(name, content, expected):
            write(name, content, expected)
            if content == b"Managed override.\n":
                self.agents.write_bytes(b"Private source addition.\n")

        with patch.object(self.storage, "_write_target", side_effect=changed_source):
            with self.assertRaises(HostTransactionError):
                self.storage.apply(targets, baseline, ownership=ownership)
        self.assertEqual(self.override.read_bytes(), b"Original override.\n")
        self.assertEqual(self.agents.read_bytes(), b"Private source addition.\n")
        self.assertEqual(self.storage.read_ownership(), {})

    # //// 本次同时写入的配置与说明使用目标值核对来源 [@x380kkm 2026-09-08] ////
    def test_written_inputs_are_checked_against_expected_targets(self):
        (self.host / "config.toml").write_bytes(b"model = 'original'\n")
        targets = {"AGENTS.override.md": b"Managed override.\n", "config.toml": b"model = 'managed'\n"}
        baseline = self.storage.capture(targets)
        baseline["instructionReads"] = self.storage.capture_instructions(["AGENTS.md", "AGENTS.override.md"])
        configuration = self.storage.capture_configuration()
        calls = []

        def verify_inputs(written):
            calls.append(dict(written))
            expected = encoded({"config.toml": written["config.toml"]}) if "config.toml" in written else configuration
            if self.storage.capture_configuration() != expected:
                raise StorageConflictError("config.toml")

        self.storage.apply(targets, baseline, verify_inputs=verify_inputs)
        self.assertEqual(calls, [{}, {}, targets])
        self.assertEqual(self.override.read_bytes(), targets["AGENTS.override.md"])

    # //// 中断时目标已完整写出则保护副本使用目标归属 [@x380kkm 2026-09-08] ////
    def test_complete_interrupted_output_restores_matching_ownership(self):
        self.apply_rule(b"Managed A.\n")
        self.restore("initial")
        self.apply_rule(b"Managed A.\n")
        protected = deepcopy(next(item for item in self.storage.store.snapshot() if item["id"] == "before-restore"))
        targets, baseline, ownership = self.rule_plan(b"Managed B.\n")
        write = self.storage._write_target

        def interrupted(name, content, expected):
            write(name, content, expected)
            raise SystemExit()

        with patch.object(self.storage, "_write_target", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.storage.apply(targets, baseline, ownership=ownership)
        self.storage = HostStorage(self.user, self.host, "codex-user")
        result = self.restore("initial")
        self.assertEqual(result["backupId"], "interrupted")
        self.assertEqual(next(item for item in self.storage.store.snapshot() if item["id"] == "before-restore"), protected)
        self.restore("interrupted")
        self.assertEqual(self.storage.read_ownership(), ownership)
        self.assertEqual(self.override.read_bytes(), b"Managed B.\n")
        self.storage.enable(True)
        self.apply_rule(None)
        self.assertEqual(self.override.read_bytes(), b"Original override.\n")

    # //// 混合中断配置保留字节和现有保护点并明确阻止自动恢复 [@x380kkm 2026-09-08] ////
    def test_mixed_interrupted_output_is_archived_without_guessing_ownership(self):
        self.apply_rule(b"Managed A.\n")
        self.restore("initial")
        self.apply_rule(b"Managed A.\n")
        protected = deepcopy(next(item for item in self.storage.store.snapshot() if item["id"] == "before-restore"))
        targets, _, ownership = self.rule_plan(b"Managed B.\n")
        targets["config.toml"] = b"model = 'managed'\n"
        baseline = self.storage.capture(targets)
        write = self.storage._write_target

        def interrupted(name, content, expected):
            if name == "config.toml":
                (self.host / name).write_bytes(b"private = 'keep this edit'\n")
                raise SystemExit()
            write(name, content, expected)

        with patch.object(self.storage, "_write_target", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.storage.apply(targets, baseline, ownership=ownership)
        self.storage = HostStorage(self.user, self.host, "codex-user")
        result = self.restore("initial")
        self.assertEqual(result["backupId"], "interrupted")
        documents = self.storage.store.snapshot()
        saved = next(item for item in documents if item["id"] == "interrupted")
        self.assertEqual(base64.b64decode(saved["files"]["config.toml"]), b"private = 'keep this edit'\n")
        self.assertIsNone(saved["ownership"])
        self.assertEqual(next(item for item in documents if item["id"] == "before-restore"), protected)
        summary = next(item for item in self.storage.status()["backups"] if item["id"] == "interrupted")
        self.assertFalse(summary["restorable"])
        self.assertTrue(summary["restoreError"])
        with self.assertRaises(StorageValidationError):
            self.storage.preview_restore("interrupted")
        self.assertEqual(self.storage.read_ownership(), {})
        self.assertFalse(self.storage.status()["recoveryRequired"])


# //// 执行迁移与恢复边界测试 [@x380kkm 2026-09-08] ////
if __name__ == "__main__":
    unittest.main()
