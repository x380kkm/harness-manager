# audience: internal
# # host-ownership-tests
"""内存文件快照验证归属撤销, 重复路径, 外部编辑和注释保留."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

from harness_manager.content_plan import SKILL_POINT
from harness_manager.host_ownership import HOOK_POINT, reconcile
from harness_manager.storage_errors import StorageConflictError, StorageValidationError


# //// 将内存文件内容包装为宿主捕获基线 [@x380kkm 2026-09-07] ////
def baseline(files: dict) -> dict:
    return {name: base64.b64encode(value).decode("ascii") if value is not None else None for name, value in files.items()}


# //// 从独立路径构造 Skill 编译结果 [@x380kkm 2026-09-07] ////
def skill(path: str, enabled: bool = True) -> dict:
    return {"ref": "plugin:test#skill", "point": SKILL_POINT, "target": "config.toml", "path": path, "enabled": enabled}


# //// 构造带来源身份的完整 Hook 事件组 [@x380kkm 2026-09-07] ////
def hook(reference: str, command: str, enabled: bool = True) -> dict:
    return {"ref": reference, "point": HOOK_POINT, "enabled": enabled, "target": "hooks.json",
            "hook": {"event": "PostToolUse", "group": {"matcher": "Edit", "hooks": [{"type": "command", "command": command}]}}}


# //// 在固定的内存配置根下核对字段归属 [@x380kkm 2026-09-07] ////
class HostOwnershipTests(unittest.TestCase):
    root = Path.cwd() / "ownership-fixture"
    folder = (root.parent / "skills/method").as_posix()

    # //// 在纯内存中调用宿主调和 [@x380kkm 2026-09-07] ////
    def reconcile(self, targets: dict, contributions: list, files: dict, ownership: dict):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "resolve", side_effect=AssertionError("filesystem resolution")):
            return reconcile(targets, contributions, baseline(files), ownership, self.root)

    # //// 指令失去最后绑定时恢复原文件或原不存在状态 [@x380kkm 2026-09-07] ////
    def test_rules_restore_original_bytes_and_check_external_edits(self) -> None:
        for before in (None, b"# Original\r\n"):
            with self.subTest(before=before):
                managed = {"AGENTS.override.md": b"# Managed\n"}
                targets, ownership = self.reconcile(managed, [], {"AGENTS.override.md": before}, {})
                restored, final = self.reconcile({}, [], targets, json.loads(json.dumps(ownership)))
                self.assertEqual(restored, {"AGENTS.override.md": before})
                self.assertEqual(final, {})
                with self.assertRaises(StorageConflictError):
                    self.reconcile({}, [], {"AGENTS.override.md": b"# User edit\n"}, ownership)
                with self.assertRaises(StorageConflictError):
                    self.reconcile(managed, [], {"AGENTS.override.md": b"# User edit\n"}, ownership)

    # //// 清理 Skill 字段时保留其他配置和用户注释 [@x380kkm 2026-09-07] ////
    def test_existing_skill_restores_only_owned_fields(self) -> None:
        before = ('# original\nmodel="first"\n[[skills.config]]\n# source comment\npath="' + self.folder + '/SKILL.md"\n'
                  'enabled=false # enabled comment\nnote="independent"\n').encode()
        compiled = ('model="first"\n[[skills.config]]\npath="' + self.folder + '"\nenabled=true\n').encode()
        targets, ownership = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": before}, {})
        current = targets["config.toml"].replace(b'model="first"', b'model="edited"').replace(b'note="independent"', b'note="user-edit"')
        restored, final = self.reconcile({}, [], {"config.toml": current}, json.loads(json.dumps(ownership)))
        content = restored["config.toml"].decode()
        parsed = tomllib.loads(content)
        self.assertEqual(parsed["model"], "edited")
        self.assertEqual(parsed["skills"]["config"], [{"path": self.folder + "/SKILL.md", "enabled": False, "note": "user-edit"}])
        self.assertIn("# source comment", content)
        self.assertIn("# enabled comment", content)
        self.assertEqual(final, {})

    # //// 编译期间发生的无关编辑由当前基线保留 [@x380kkm 2026-09-07] ////
    def test_current_baseline_preserves_unrelated_edits_and_missing_enabled(self) -> None:
        current = ('model="edited"\n[skills]\nconfig=[{path="' + self.folder + '", note="keep"}]\n').encode()
        compiled = ('model="stale"\n[skills]\nconfig=[{path="' + self.folder + '",enabled=true}]\n').encode()
        targets, ownership = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": current}, {})
        restored, final = self.reconcile({}, [], targets, ownership)
        parsed = tomllib.loads(restored["config.toml"].decode())
        self.assertEqual(parsed["model"], "edited")
        self.assertEqual(parsed["skills"]["config"], [{"path": self.folder, "note": "keep"}])
        self.assertEqual(final, {})

    # //// 新建开关在撤销时清理并保留用户追加的独立 Skill [@x380kkm 2026-09-07] ////
    def test_new_skill_cleanup_preserves_other_rows_and_comments(self) -> None:
        compiled = ('[[skills.config]]\npath="' + self.folder + '"\nenabled=true\n').encode()
        targets, ownership = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": b'model="keep"\n'}, {})
        current = targets["config.toml"] + b'\n# keep this note\n[[skills.config]]\npath="other"\nenabled=false\n'
        restored, final = self.reconcile({}, [], {"config.toml": current}, ownership)
        parsed = tomllib.loads(restored["config.toml"].decode())
        self.assertEqual(parsed, {"model": "keep", "skills": {"config": [{"path": "other", "enabled": False}]}})
        self.assertIn(b"# keep this note", restored["config.toml"])
        self.assertEqual(final, {})
        empty, initial = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": None}, {})
        clean, cleared = self.reconcile({}, [], empty, initial)
        self.assertEqual(tomllib.loads(clean["config.toml"].decode()), {})
        self.assertEqual(cleared, {})

    # //// 受管字段变化与新行附加字段使清理明确冲突 [@x380kkm 2026-09-07] ////
    def test_external_owned_field_and_new_row_metadata_are_conflicts(self) -> None:
        compiled = ('[[skills.config]]\npath="' + self.folder + '"\nenabled=true\n').encode()
        targets, ownership = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": None}, {})
        edited = targets["config.toml"].replace(b"true", b"false")
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], {"config.toml": edited}, ownership)
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], {"config.toml": targets["config.toml"] + b'note="mine"\n'}, ownership)

    # //// 重复路径按原数组顺序恢复并拒绝行数变化 [@x380kkm 2026-09-07] ////
    def test_duplicate_paths_preserve_order_and_count(self) -> None:
        before = ('[[skills.config]]\npath="' + self.folder + '"\nenabled=false\nnote="one"\n'
                  '[[skills.config]]\npath="' + self.folder + '/SKILL.md"\nnote="two"\n').encode()
        compiled = ('[[skills.config]]\npath="' + self.folder + '"\nenabled=true\n'
                    '[[skills.config]]\npath="' + self.folder + '"\nenabled=true\n').encode()
        targets, ownership = self.reconcile({"config.toml": compiled}, [skill(self.folder)], {"config.toml": before}, {})
        restored, _ = self.reconcile({}, [], targets, ownership)
        self.assertEqual(tomllib.loads(restored["config.toml"].decode()), tomllib.loads(before.decode()))
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], {"config.toml": targets["config.toml"] + compiled}, ownership)

    # //// 输入与归属在调和完成后仍保持调用者原值 [@x380kkm 2026-09-07] ////
    def test_reconcile_keeps_inputs_and_requires_explicit_skill_path(self) -> None:
        targets = {"AGENTS.override.md": b"# Rule\n"}
        ownership = {}
        before = deepcopy(targets)
        self.reconcile(targets, [], {"AGENTS.override.md": None}, ownership)
        self.assertEqual(targets, before)
        self.assertEqual(ownership, {})
        with self.assertRaises(StorageValidationError):
            self.reconcile({"config.toml": b""}, [{"point": SKILL_POINT}], {"config.toml": None}, {})

    # //// Hook 只添加与清理自己归属的事件组 [@x380kkm 2026-09-07] ////
    def test_hook_cleanup_preserves_unmanaged_groups_and_metadata(self) -> None:
        managed = hook("plugin:one#hook", "managed-command")
        external = hook("external", "external-command")["hook"]["group"]
        original = {"description": "Original metadata", "extra": {"preserve": True}, "hooks": {"PostToolUse": [external]}}
        before = json.dumps(original).encode()
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [managed], {"hooks.json": before}, {})
        combined = json.loads(targets["hooks.json"])
        self.assertEqual(combined["hooks"]["PostToolUse"], [external, managed["hook"]["group"]])
        combined["description"] = "User edit"
        later = hook("external-two", "later-command")["hook"]["group"]
        combined["hooks"]["PostToolUse"].append(later)
        restored, final = self.reconcile({}, [], {"hooks.json": json.dumps(combined).encode()}, json.loads(json.dumps(ownership)))
        result = json.loads(restored["hooks.json"])
        self.assertEqual(result["hooks"]["PostToolUse"], [external, later])
        self.assertEqual(result["description"], "User edit")
        self.assertEqual(result["extra"], {"preserve": True})
        self.assertEqual(final, {})

    # //// Hook 关闭或移除绑定时恢复新文件的原不存在状态 [@x380kkm 2026-09-07] ////
    def test_hook_disable_removes_only_owned_new_file(self) -> None:
        entry = hook("plugin:one#hook", "managed-command")
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [entry], {"hooks.json": None}, {})
        disabled = {**entry, "enabled": False}
        cleared, final = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [disabled], targets, ownership)
        self.assertEqual(cleared, {"hooks.json": None})
        self.assertFalse(final["hooks"]["groups"][0]["enabled"])
        removed, final = self.reconcile({}, [], targets, ownership)
        self.assertEqual(removed, {"hooks.json": None})
        self.assertEqual(final, {})

    # //// 原生 Hook 关闭和来源交接后恢复原组与位置 [@x380kkm 2026-09-08] ////
    def test_existing_hook_handoff_preserves_disabled_group_and_external_fields(self) -> None:
        entry = hook("plugin:one#hook", "same-command")
        external = hook("external", "external-command")["hook"]["group"]
        after = hook("after", "after-command")["hook"]["group"]
        original = json.dumps({"metadata": "keep", "hooks": {"PostToolUse": [external, entry["hook"]["group"], after]}}).encode()
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [entry], {"hooks.json": original}, {})
        self.assertEqual(targets["hooks.json"], original)
        self.assertEqual(ownership["hooks"]["groups"][0]["beforeIndex"], 1)
        disabled, final = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [{**entry, "enabled": False}], targets, ownership)
        self.assertEqual(json.loads(disabled["hooks.json"])["hooks"]["PostToolUse"], [external, after])
        replacement = {**entry, "ref": "plugin:replacement#hook", "enabled": False}
        disabled, final = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [replacement], disabled, final)
        edited = json.loads(disabled["hooks.json"])
        edited["metadata"] = "later edit"
        released, remaining = self.reconcile({}, [], {"hooks.json": json.dumps(edited).encode()}, final)
        self.assertEqual(json.loads(released["hooks.json"])["hooks"], json.loads(original)["hooks"])
        self.assertEqual(json.loads(released["hooks.json"])["metadata"], "later edit")
        self.assertEqual(remaining, {})

    # //// 编辑和交接原生 Hook 保持启用状态与原序并在解除后恢复原组 [@x380kkm 2026-09-08] ////
    def test_edited_native_hook_handoff_retains_state_and_original_position(self) -> None:
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                entry = hook("plugin:one#hook", "original-command", enabled)
                left = hook("left", "left-command")["hook"]["group"]
                right = hook("right", "right-command")["hook"]["group"]
                original = {"hooks": {"PostToolUse": [left, entry["hook"]["group"], right]}}
                targets, ownership = self.reconcile({"hooks.json": b"{}"}, [entry], {"hooks.json": json.dumps(original).encode()}, {})
                edited = hook(entry["ref"], "edited-command", enabled)
                targets, ownership = self.reconcile({"hooks.json": b"{}"}, [edited], targets, json.loads(json.dumps(ownership)))
                expected = [left, edited["hook"]["group"], right] if enabled else [left, right]
                self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], expected)
                toggled = {**edited, "ref": "plugin:replacement#hook", "enabled": not enabled}
                targets, ownership = self.reconcile({"hooks.json": b"{}"}, [toggled], targets, ownership)
                expected = [left, right] if enabled else [left, edited["hook"]["group"], right]
                self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], expected)
                returned = {**entry, "ref": "plugin:returned#hook", "enabled": not enabled}
                targets, ownership = self.reconcile({"hooks.json": b"{}"}, [returned], targets, ownership)
                expected = [left, right] if enabled else original["hooks"]["PostToolUse"]
                self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], expected)
                restored, released = self.reconcile({}, [], targets, ownership)
                self.assertEqual(json.loads(restored["hooks.json"]), original)
                self.assertEqual(released, {})

    # //// 共享来源交接已编辑组后按直接引用恢复原生组 [@x380kkm 2026-09-08] ////
    def test_edited_hook_handoff_releases_original_when_its_new_reference_leaves(self) -> None:
        native = hook("plugin:native#hook", "native")
        first = hook("plugin:first#hook", "shared")
        second = hook("plugin:second#hook", "shared")
        original = {"hooks": {"PostToolUse": [native["hook"]["group"]]}}
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [native, first, second],
                                            {"hooks.json": json.dumps(original).encode()}, {})
        edited = hook(native["ref"], "edited")
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [edited, first, second], targets, ownership)
        replacement = {**edited, "ref": first["ref"]}
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [replacement, second], targets, ownership)
        self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"],
                         [edited["hook"]["group"], second["hook"]["group"]])
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [second], targets, ownership)
        self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"],
                         [native["hook"]["group"], second["hook"]["group"]])
        restored, released = self.reconcile({}, [], targets, ownership)
        self.assertEqual(json.loads(restored["hooks.json"]), original)
        self.assertEqual(released, {})

    # //// 原生 Hook 的共享引用分开编辑后保留同一恢复来源 [@x380kkm 2026-09-08] ////
    def test_edited_shared_native_hook_keeps_original_until_last_release(self) -> None:
        first = hook("plugin:one#hook", "original", False)
        second = hook("plugin:two#hook", "original", False)
        original = {"hooks": {"PostToolUse": [first["hook"]["group"]]}}
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [first, second], {"hooks.json": json.dumps(original).encode()}, {})
        edited = hook(first["ref"], "edited", False)
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [edited, second], targets, ownership)
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [second], targets, ownership)
        self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], [])
        restored, released = self.reconcile({}, [], targets, ownership)
        self.assertEqual(json.loads(restored["hooks.json"]), original)
        self.assertEqual(released, {})

    # //// 连续编辑接管多个原生组后按各自原序恢复 [@x380kkm 2026-09-08] ////
    def test_editing_between_native_groups_restores_each_original(self) -> None:
        first = hook("plugin:one#hook", "first", False)
        second = hook(first["ref"], "second", False)
        original = {"hooks": {"PostToolUse": [first["hook"]["group"], second["hook"]["group"]]}}
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [first], {"hooks.json": json.dumps(original).encode()}, {})
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [second], targets, ownership)
        self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], [])
        restored, _ = self.reconcile({}, [], targets, ownership)
        self.assertEqual(json.loads(restored["hooks.json"]), original)

    # //// 编辑事件名称后解除管理恢复原事件并清理生成事件 [@x380kkm 2026-09-08] ////
    def test_changed_hook_event_restores_original_event_on_release(self) -> None:
        entry = hook("plugin:one#hook", "original")
        original = {"hooks": {"PostToolUse": [entry["hook"]["group"]]}}
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [entry], {"hooks.json": json.dumps(original).encode()}, {})
        edited = hook(entry["ref"], "edited")
        edited["hook"]["event"] = "PreToolUse"
        targets, ownership = self.reconcile({"hooks.json": b"{}"}, [edited], targets, ownership)
        current = json.loads(targets["hooks.json"])["hooks"]
        self.assertEqual(current["PostToolUse"], [])
        self.assertEqual(current["PreToolUse"], [edited["hook"]["group"]])
        restored, _ = self.reconcile({}, [], targets, ownership)
        self.assertEqual(json.loads(restored["hooks.json"]), original)

    # //// 首次关闭也可接管已有组且多个原始组按原位置恢复 [@x380kkm 2026-09-08] ////
    def test_initial_disabled_groups_preserve_original_order(self) -> None:
        first = hook("plugin:first#hook", "first", False)
        second = hook("plugin:second#hook", "second", False)
        external = hook("external", "external")["hook"]["group"]
        original = {"hooks": {"PostToolUse": [first["hook"]["group"], external, second["hook"]["group"]]}}
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [second, first],
                                            {"hooks.json": json.dumps(original).encode()}, {})
        self.assertEqual(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"], [external])
        restored, remaining = self.reconcile({}, [], targets, ownership)
        self.assertEqual(json.loads(restored["hooks.json"]), original)
        self.assertEqual(remaining, {})

    # //// 新增组的精简归属记录沿用其原不存在状态 [@x380kkm 2026-09-08] ////
    def test_legacy_hook_ownership_releases_added_group(self) -> None:
        entry = hook("plugin:one#hook", "managed")
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [entry], {"hooks.json": None}, {})
        record = ownership["hooks"]["groups"][0]
        del record["beforeIndex"]
        del record["enabled"]
        restored, final = self.reconcile({}, [], targets, ownership)
        self.assertEqual(restored, {"hooks.json": None})
        self.assertEqual(final, {})

    # //// 同一 Hook 组的多个来源只产生一次宿主执行 [@x380kkm 2026-09-07] ////
    def test_identical_groups_share_one_owned_group(self) -> None:
        first = hook("plugin:one#hook", "same-command")
        second = hook("plugin:two#hook", "same-command")
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [first, second], {"hooks.json": None}, {})
        self.assertEqual(len(json.loads(targets["hooks.json"])["hooks"]["PostToolUse"]), 1)
        self.assertEqual(ownership["hooks"]["groups"][0]["refs"], [first["ref"], second["ref"]])
        remaining, updated = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [second], targets, ownership)
        self.assertEqual(remaining, targets)
        self.assertEqual(updated["hooks"]["groups"][0]["refs"], [second["ref"]])
        mixed, _ = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [{**first, "enabled": False}, second], targets, ownership)
        self.assertEqual(mixed, targets)

    # //// 外部修改与重复 Hook 组使归属清理明确冲突 [@x380kkm 2026-09-07] ////
    def test_hook_external_changes_and_duplicate_matches_are_conflicts(self) -> None:
        entry = hook("plugin:one#hook", "managed-command")
        targets, ownership = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [entry], {"hooks.json": None}, {})
        altered = json.loads(targets["hooks.json"])
        altered["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "user-command"
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], {"hooks.json": json.dumps(altered).encode()}, ownership)
        duplicated = json.loads(targets["hooks.json"])
        duplicated["hooks"]["PostToolUse"].append(entry["hook"]["group"])
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], {"hooks.json": json.dumps(duplicated).encode()}, ownership)
        disabled, inactive = self.reconcile({"hooks.json": b'{"hooks":{}}'}, [{**entry, "enabled": False}], targets, ownership)
        with self.assertRaises(StorageConflictError):
            self.reconcile({}, [], targets, inactive)
