# audience: internal
# # codex-hook-state-tests
# 内存配置覆盖原生身份移动, 逐处理器开关与信任归属, 外部配置保留.

from copy import deepcopy
from pathlib import Path
import unittest

import tomlkit

from harness_manager.codex_hook_state import group_states, hook_state_key, parse_hook_states, relocate_hook_state, remap_hook_states
from harness_manager.storage_errors import StorageConflictError, StorageValidationError


# //// 构造原生文件处理器的内存配置 [@x380kkm 2026-09-10] ////
def command_group(*commands, matcher=None):
    group = {"hooks": [{"type": "command", "command": command} for command in commands]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


# //// 序列化只含调用者提供状态行的配置 [@x380kkm 2026-09-10] ////
def configuration(rows):
    return tomlkit.dumps({"hooks": {"state": rows}}).encode("utf-8")


# //// 核对静态文件之间的原生开关与信任对应 [@x380kkm 2026-09-10] ////
class CodexHookStateTests(unittest.TestCase):
    path = Path("C:/Users/example/.codex/hooks.json")

    # //// 取得测试事件组内的原生身份 [@x380kkm 2026-09-10] ////
    def key(self, group, handler=0, event="Stop"):
        return hook_state_key(self.path, event, group, handler)

    # //// 原生键使用原路径文本和事件内序号 [@x380kkm 2026-09-10] ////
    def test_key_uses_native_event_spelling_and_path(self):
        self.assertEqual(self.key(2, 1, "PreToolUse"), f"{self.path}:pre_tool_use:2:1")
        self.assertEqual(self.key(0, event="SubagentStart"), f"{self.path}:subagent_start:0:0")

    # //// 空配置和无状态变化保持文件字节 [@x380kkm 2026-09-10] ////
    def test_noop_preserves_absence_bom_comments_and_line_endings(self):
        document = {"hooks": {"Stop": [command_group("unchanged")]}}
        original = b'\xef\xbb\xbf# model comment\r\nmodel = "chosen"\r\n'
        self.assertIsNone(remap_hook_states(None, {}, document, self.path))
        self.assertEqual(remap_hook_states(original, document, document, self.path), original)
        self.assertIsNone(remap_hook_states(None, {}, document, self.path, {self.key(0): True}))

    # //// 非布尔开关和损坏状态阻止读取与调和 [@x380kkm 2026-09-10] ////
    def test_malformed_native_state_is_rejected(self):
        values = [b'[hooks.state."x"]\nenabled = "false"\n',
                  b'[hooks.state."x"]\ntrusted_hash = 12\n',
                  b'[hooks]\nstate = 12\n', b'[hooks.state]\nx = false\n', b'hooks = false\n']
        for value in values:
            with self.subTest(config=value), self.assertRaises(StorageValidationError):
                parse_hook_states(value)
            with self.subTest(config=value), self.assertRaises(StorageValidationError):
                remap_hook_states(value, {}, {}, self.path)

    # //// 同组处理器保持各自开关和完整保存基线 [@x380kkm 2026-09-10] ////
    def test_group_states_reads_mixed_handlers_without_assuming_trust(self):
        group = command_group("first", "second", "third")
        saved = {self.key(0): {"enabled": False, "trusted_hash": "first-hash", "note": "retained"},
                 self.key(0, 1): {"trusted_hash": "second-hash", "extension": {"mode": "local"}}}
        rows = group_states(configuration(saved), {"hooks": {"Stop": [group]}}, self.path,
                            {"event": "Stop", "group": group})
        self.assertEqual([row["enabled"] for row in rows], [False, True, True])
        self.assertEqual(rows[0]["saved"], saved[self.key(0)])
        self.assertEqual(rows[1]["saved"], saved[self.key(0, 1)])
        self.assertIsNone(rows[2]["saved"])

    # //// 消失组和重复组分别返回缺失和冲突 [@x380kkm 2026-09-10] ////
    def test_group_states_distinguishes_missing_and_ambiguous_group(self):
        group = command_group("same")
        hook = {"event": "Stop", "group": group}
        self.assertIsNone(group_states(None, {}, self.path, hook))
        with self.assertRaises(StorageConflictError):
            group_states(None, {"hooks": {"Stop": [group, deepcopy(group)]}}, self.path, hook)

    # //// 删除组后外部处理器的开关与信任随定义移动 [@x380kkm 2026-09-10] ////
    def test_removed_group_preserves_following_states_and_unrelated_config(self):
        first, second, third = [command_group(name) for name in ("removed", "external-disabled", "external-enabled")]
        before = {"hooks": {"Stop": [first, second, third]}}
        after = {"hooks": {"Stop": [second, third]}}
        external_key = "plugin:other:stop:0:0"
        rows = {self.key(0): {"enabled": False, "trusted_hash": "removed-hash"},
                self.key(1): {"enabled": False, "trusted_hash": "second-hash", "note": "second"},
                self.key(2): {"enabled": True, "trusted_hash": "third-hash"},
                external_key: {"enabled": False, "trusted_hash": "external-hash"}}
        prefix = b'# chosen model\nmodel = "chosen" # keep this\n\n'
        result = remap_hook_states(prefix + configuration(rows), before, after, self.path)
        actual = parse_hook_states(result)
        self.assertEqual(actual, {self.key(0): rows[self.key(1)], self.key(1): rows[self.key(2)], external_key: rows[external_key]})
        self.assertTrue(result.startswith(prefix))
        self.assertEqual(before["hooks"]["Stop"], [first, second, third])

    # //// 重排组与组内处理器保留完整状态行 [@x380kkm 2026-09-10] ////
    def test_reordered_handlers_keep_disabled_and_trusted_rows(self):
        before = {"hooks": {"Stop": [command_group("a", "b"), command_group("c")]}}
        after = {"hooks": {"Stop": [command_group("c"), command_group("b", "a")]}}
        rows = {self.key(0): {"enabled": False, "trusted_hash": "a"},
                self.key(0, 1): {"enabled": True, "trusted_hash": "b"}, self.key(1): {"trusted_hash": "c"}}
        result = parse_hook_states(remap_hook_states(configuration(rows), before, after, self.path))
        self.assertEqual(result, {self.key(0): rows[self.key(1)], self.key(1): rows[self.key(0, 1)],
                                  self.key(1, 1): rows[self.key(0)]})

    # //// 新正文只继承调用者明确保留的关闭选择 [@x380kkm 2026-09-10] ////
    def test_edited_definition_drops_trust_and_accepts_explicit_disabled_choice(self):
        before = {"hooks": {"Stop": [command_group("old")]}}
        after = {"hooks": {"Stop": [command_group("new")]}}
        original = configuration({self.key(0): {"enabled": False, "trusted_hash": "old-hash"}})
        result = remap_hook_states(original, before, after, self.path, {self.key(0): False})
        self.assertEqual(parse_hook_states(result), {self.key(0): {"enabled": False}})

    # //// 匹配器变化使旧信任与新定义分离 [@x380kkm 2026-09-10] ////
    def test_changed_matcher_drops_old_state(self):
        before = {"hooks": {"PreToolUse": [command_group("same", matcher="Bash")]}}
        after = {"hooks": {"PreToolUse": [command_group("same", matcher="Write")]}}
        key = self.key(0, event="PreToolUse")
        result = remap_hook_states(configuration({key: {"trusted_hash": "old"}}), before, after, self.path)
        self.assertEqual(parse_hook_states(result), {})

    # //// 重复定义按出现顺序保持独立开关 [@x380kkm 2026-09-10] ////
    def test_repeated_definitions_preserve_occurrence_states(self):
        repeated, other = command_group("same"), command_group("other")
        before = {"hooks": {"Stop": [repeated, other, deepcopy(repeated)]}}
        after = {"hooks": {"Stop": [other, repeated, deepcopy(repeated)]}}
        rows = {self.key(0): {"enabled": False, "trusted_hash": "same"}, self.key(2): {"enabled": True, "trusted_hash": "same"}}
        result = parse_hook_states(remap_hook_states(configuration(rows), before, after, self.path))
        self.assertEqual(result, {self.key(1): rows[self.key(0)], self.key(2): rows[self.key(2)]})

    # //// 有状态重复项数量改变时拒绝猜测保留对象 [@x380kkm 2026-09-10] ////
    def test_ambiguous_duplicate_removal_is_rejected(self):
        group = command_group("same")
        before = {"hooks": {"Stop": [group, deepcopy(group)]}}
        after = {"hooks": {"Stop": [group]}}
        original = configuration({self.key(0): {"enabled": False}, self.key(1): {"enabled": True}})
        with self.assertRaises(StorageConflictError):
            remap_hook_states(original, before, after, self.path)

    # //// 关闭新处理器只创建该开关字段 [@x380kkm 2026-09-10] ////
    def test_override_creates_only_requested_state(self):
        after = {"hooks": {"Stop": [command_group("new")]}}
        result = remap_hook_states(None, {}, after, self.path, {self.key(0): False})
        self.assertEqual(parse_hook_states(result), {self.key(0): {"enabled": False}})
        with self.assertRaises(StorageValidationError):
            remap_hook_states(None, {}, after, self.path, {"plugin:unrelated": False})

    # //// 原生状态表的注释与扩展字段经过开关修改保留 [@x380kkm 2026-09-10] ////
    def test_enabled_override_preserves_row_comments_and_unknown_fields(self):
        key = self.key(0)
        document = {"hooks": {"Stop": [command_group("same")]}}
        original = configuration({key: {"enabled": True, "trusted_hash": "same", "extension": "kept"}})
        original = original.replace(b"enabled = true", b"enabled = true # user choice")
        result = remap_hook_states(original, document, document, self.path, {key: False})
        self.assertIn(b"enabled = false # user choice", result)
        self.assertEqual(parse_hook_states(result), {key: {"enabled": False, "trusted_hash": "same", "extension": "kept"}})

    # //// 恢复完整原定义时使用原信任并接受显式开关 [@x380kkm 2026-09-10] ////
    def test_restored_original_recovers_saved_state_before_enabled_override(self):
        before = {"hooks": {"Stop": [command_group("managed")]}}
        after = {"hooks": {"Stop": [command_group("original")]}}
        current = configuration({self.key(0): {"enabled": True, "trusted_hash": "managed"}})
        original = {"enabled": True, "trusted_hash": "original", "extension": "retained"}
        result = remap_hook_states(current, before, after, self.path, {self.key(0): False},
                                   restored={self.key(0): original})
        self.assertEqual(parse_hook_states(result), {self.key(0): {**original, "enabled": False}})
        self.assertTrue(original["enabled"])

    # //// 空原生基线恢复缺省状态并保留其他文件的记录 [@x380kkm 2026-09-10] ////
    def test_restored_absent_row_removes_managed_state(self):
        document = {"hooks": {"Stop": [command_group("same")]}}
        external = {"enabled": False, "trusted_hash": "external"}
        current = configuration({self.key(0): {"enabled": False, "trusted_hash": "managed"}, "plugin:other": external})
        result = remap_hook_states(current, document, document, self.path, restored={self.key(0): None})
        self.assertEqual(parse_hook_states(result), {"plugin:other": external})

    # //// 恢复状态沿用原生字段校验和当前文件身份边界 [@x380kkm 2026-09-10] ////
    def test_restored_row_requires_valid_state_and_current_key(self):
        document = {"hooks": {"Stop": [command_group("original")]}}
        for row in ({"enabled": "false"}, {"trusted_hash": False}, []):
            with self.subTest(row=row), self.assertRaises(StorageValidationError):
                remap_hook_states(None, {}, document, self.path, restored={self.key(0): row})
        with self.assertRaises(StorageValidationError):
            remap_hook_states(None, {}, document, self.path, restored={"plugin:other": None})

    # //// 项目路径与事件组同时移动时保留原生开关和信任 [@x380kkm 2026-09-10] ////
    def test_path_move_and_group_reorder_follow_complete_definitions(self):
        destination = Path("D:/relocated/.codex/hooks.json")
        first, second = command_group("first"), command_group("second")
        before = {"hooks": {"Stop": [first, second]}}
        after = {"hooks": {"Stop": [second, first]}}
        rows = {self.key(0): {"enabled": False, "trusted_hash": "first"},
                self.key(1): {"enabled": True, "trusted_hash": "second"}, "plugin:other": {"enabled": False}}
        result = remap_hook_states(configuration(rows), before, after, destination, source_path=self.path)
        self.assertEqual(parse_hook_states(result), {
            hook_state_key(destination, "Stop", 0, 0): rows[self.key(1)],
            hook_state_key(destination, "Stop", 1, 0): rows[self.key(0)], "plugin:other": rows["plugin:other"]})

    # //// 恢复快照仅迁移精确来源文件前缀并保留注释 [@x380kkm 2026-09-10] ////
    def test_relocate_snapshot_preserves_unrelated_rows_and_comments(self):
        destination = Path("D:/relocated/.codex/hooks.json")
        similar = f"{self.path}.backup:stop:0:0"
        rows = {self.key(0): {"enabled": False, "trusted_hash": "first", "extension": "kept"},
                similar: {"trusted_hash": "other-file"}, "plugin:other": {"enabled": True}}
        prefix = b'\xef\xbb\xbf# user model\nmodel = "chosen" # retained\n\n'
        original = prefix + configuration(rows).replace(b"enabled = false", b"enabled = false # user choice")
        result = relocate_hook_state(original, self.path, destination)
        self.assertTrue(result.startswith(prefix))
        self.assertIn(b"enabled = false # user choice", result)
        self.assertEqual(parse_hook_states(result), {
            hook_state_key(destination, "Stop", 0, 0): rows[self.key(0)],
            similar: rows[similar], "plugin:other": rows["plugin:other"]})

    # //// 已占用的新文件身份与旧状态冲突时停止路径迁移 [@x380kkm 2026-09-10] ////
    def test_path_move_rejects_conflicting_destination_state(self):
        destination = Path("D:/relocated/.codex/hooks.json")
        target = hook_state_key(destination, "Stop", 0, 0)
        original = configuration({self.key(0): {"enabled": False, "trusted_hash": "original"},
                                  target: {"enabled": True, "trusted_hash": "other"}})
        document = {"hooks": {"Stop": [command_group("same")]}}
        with self.assertRaises(StorageConflictError):
            relocate_hook_state(original, self.path, destination)
        with self.assertRaises(StorageConflictError):
            remap_hook_states(original, document, document, destination, source_path=self.path)

    # //// 相同迁移目标合并一份状态并保持无变化快照 [@x380kkm 2026-09-10] ////
    def test_relocate_identical_destination_and_missing_source(self):
        destination = Path("D:/relocated/.codex/hooks.json")
        target = hook_state_key(destination, "Stop", 0, 0)
        saved = {"enabled": False, "trusted_hash": "same"}
        original = configuration({self.key(0): saved, target: saved})
        result = relocate_hook_state(original, self.path, destination)
        self.assertEqual(parse_hook_states(result), {target: saved})
        self.assertEqual(relocate_hook_state(original, self.path, self.path), original)
        self.assertEqual(relocate_hook_state(result, self.path, destination), result)
        self.assertIsNone(relocate_hook_state(None, self.path, destination))
