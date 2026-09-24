# audience: internal
# # host-profiles-tests
# 宿主档案决定文件集与目录布局, 这里核对 Codex 档案与既有常量逐项一致, 并让第二宿主走完整的存档与恢复往返.
# 两个宿主的存档目录按作用域身份隔离, 既有记录因此不需要迁移.

from pathlib import Path
import tempfile
import unittest

from harness_manager.host_ownership import (
    CONFIG_FILE, RULE_FILE, TARGET_FILES, encode_file, reconcile_rules,
)
from harness_manager.host_paths import project_host_storage
from harness_manager.host_profiles import CLAUDE, CODEX, host_profile
from harness_manager.host_storage import HostStorage
from harness_manager.host_storage_records import HOOK_STATE_NAME, SOURCE_NAMES, TARGET_NAMES
from harness_manager.storage_errors import StorageConflictError


# //// 核对 Codex 档案与既有常量逐项一致 [@x380kkm 2026-09-24] ////
class CodexProfileTests(unittest.TestCase):
    # //// 档案取值与模块常量保持同一份事实 [@x380kkm 2026-09-24] ////
    def test_profile_matches_existing_constants(self):
        self.assertEqual(CODEX.source_names, SOURCE_NAMES)
        self.assertEqual(CODEX.target_names, TARGET_NAMES)
        self.assertEqual(CODEX.hook_state_name, HOOK_STATE_NAME)
        self.assertEqual(CODEX.rule_file, RULE_FILE)
        self.assertEqual(CODEX.config_file, CONFIG_FILE)
        self.assertEqual(CODEX.compiled_names(), TARGET_FILES)

    # //// 编译目标集合排除仅由存储层写入的 Hook 状态 [@x380kkm 2026-09-24] ////
    def test_compiled_names_exclude_hook_state(self):
        self.assertIn(CODEX.hook_state_name, CODEX.target_names)
        self.assertNotIn(CODEX.hook_state_name, CODEX.compiled_names())
        self.assertEqual(CLAUDE.compiled_names(), CLAUDE.target_names)

    # //// 作用域身份与用户范围判定按档案生成 [@x380kkm 2026-09-24] ////
    def test_scope_identity_follows_profile(self):
        self.assertEqual(CODEX.scope_id("user"), "codex-user")
        self.assertEqual(CLAUDE.scope_id("user"), "claude-user")
        self.assertTrue(CODEX.is_user_scope("codex-user"))
        self.assertFalse(CODEX.is_user_scope("claude-user"))
        self.assertFalse(CLAUDE.is_user_scope("codex-user"))

    # //// Hook 定义位置区分独立文件与内嵌主配置 [@x380kkm 2026-09-24] ////
    def test_hook_location_differs_between_hosts(self):
        self.assertFalse(CODEX.hooks_in_config())
        self.assertTrue(CLAUDE.hooks_in_config())

    # //// 两个宿主的 Hook 定义位置与开关载体不同 [@x380kkm 2026-09-24] ////
    def test_hook_layout_differs_between_hosts(self):
        self.assertFalse(CODEX.hooks_in_config())
        self.assertTrue(CLAUDE.hooks_in_config())
        self.assertTrue(CODEX.hook_state_name)
        self.assertFalse(CLAUDE.hook_state_name)

    # //// 受管说明与原文同名的宿主整份生成正文 [@x380kkm 2026-09-24] ////
    def test_instruction_mode_follows_the_file_layout(self):
        self.assertFalse(CODEX.manages_instruction_file())
        self.assertNotEqual(CODEX.rule_file, CODEX.instruction_file)
        self.assertTrue(CLAUDE.manages_instruction_file())
        self.assertEqual(CLAUDE.rule_file, CLAUDE.instruction_file)

    # //// 未登记的宿主身份被拒绝 [@x380kkm 2026-09-24] ////
    def test_unknown_host_is_rejected(self):
        self.assertIs(host_profile(), CODEX)
        self.assertIs(host_profile("claude"), CLAUDE)
        with self.assertRaises(KeyError):
            host_profile("unknown")


# //// 让第二宿主走完整的存档, 应用与恢复往返 [@x380kkm 2026-09-24] ////
class ClaudeProfileStorageTests(unittest.TestCase):
    # //// 创建相互独立的个人存档与宿主配置目录 [@x380kkm 2026-09-24] ////
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user = self.root / "user"
        self.user.mkdir()
        self.host = self.root / ".claude"
        self.host.mkdir()
        self.storage = HostStorage(self.user, self.host, CLAUDE.scope_id("user"), profile=CLAUDE)

    # //// 应用宿主字节并使用当前预览基线 [@x380kkm 2026-09-24] ////
    def apply(self, targets):
        return self.storage.apply(targets, self.storage.capture(targets))

    # //// 用户范围的全部文件落在宿主根目录 [@x380kkm 2026-09-24] ////
    def test_user_scope_writes_into_host_root(self):
        targets = {"CLAUDE.md": "规则正文".encode("utf-8"),
                   "settings.json": b'{"model":"opus"}\n'}
        self.apply(targets)
        self.assertEqual((self.host / "CLAUDE.md").read_bytes(), targets["CLAUDE.md"])
        self.assertEqual((self.host / "settings.json").read_bytes(), targets["settings.json"])

    # //// 恢复点记录接管前的完整文件集与不存在状态 [@x380kkm 2026-09-24] ////
    def test_initial_backup_covers_absent_files(self):
        result = self.apply({"CLAUDE.md": b"managed\n"})
        preview = self.storage.preview_restore(result["backupId"])
        self.assertEqual(preview["targets"], {"CLAUDE.md": None, "settings.json": None})
        self.storage.restore(result["backupId"], preview["baseline"])
        self.assertFalse((self.host / "CLAUDE.md").exists())

    # //// 接管前的原文进入恢复点并在撤销后写回 [@x380kkm 2026-09-24] ////
    def test_takeover_preserves_and_restores_original_text(self):
        original = "我的手写规则".encode("utf-8")
        (self.host / "CLAUDE.md").write_bytes(original)
        result = self.apply({"CLAUDE.md": "受管正文".encode("utf-8")})
        self.assertEqual((self.host / "CLAUDE.md").read_bytes(), "受管正文".encode("utf-8"))
        preview = self.storage.preview_restore(result["backupId"])
        self.assertEqual(preview["targets"]["CLAUDE.md"], original)
        self.storage.restore(result["backupId"], preview["baseline"])
        self.assertEqual((self.host / "CLAUDE.md").read_bytes(), original)

    # //// 项目范围把说明留在项目根, 配置写入子目录 [@x380kkm 2026-09-24] ////
    def test_project_scope_splits_root_and_config_directory(self):
        project = self.root / "project"
        project.mkdir()
        storage = project_host_storage(self.user, project, self.host, CLAUDE)
        targets = {"settings.json": b'{"hooks":{}}\n'}
        storage.apply(targets, storage.capture(targets))
        self.assertEqual((project / ".claude/settings.json").read_bytes(), targets["settings.json"])

    # //// 两个宿主的存档目录按作用域身份隔离 [@x380kkm 2026-09-24] ////
    def test_host_archives_stay_separated(self):
        codex_root = self.root / ".codex"
        codex_root.mkdir()
        codex = HostStorage(self.user, codex_root, CODEX.scope_id("user"), profile=CODEX)
        self.assertNotEqual(codex.store.catalog, self.storage.store.catalog)
        self.apply({"CLAUDE.md": b"claude\n"})
        codex.apply({"AGENTS.override.md": b"codex\n"}, codex.capture({"AGENTS.override.md": b"codex\n"}))
        self.assertEqual((self.host / "CLAUDE.md").read_bytes(), b"claude\n")
        self.assertEqual((codex_root / "AGENTS.override.md").read_bytes(), b"codex\n")


# //// 规则归属调和服务任意宿主的说明文件 [@x380kkm 2026-09-24] ////
class RuleOwnershipTests(unittest.TestCase):
    # //// 接管前的原文在目标撤销后写回 [@x380kkm 2026-09-24] ////
    def test_rules_restore_original_text_for_second_host(self):
        baseline = {CLAUDE.rule_file: encode_file("原文".encode("utf-8"))}
        targets, ownership = {CLAUDE.rule_file: "受管正文".encode("utf-8")}, {}
        reconcile_rules(targets, baseline, ownership, CLAUDE.rule_file)
        self.assertIn(CLAUDE.rule_file, ownership)
        applied = {CLAUDE.rule_file: encode_file("受管正文".encode("utf-8"))}
        released, kept = {}, dict(ownership)
        reconcile_rules(released, applied, kept, CLAUDE.rule_file)
        self.assertEqual(released[CLAUDE.rule_file], "原文".encode("utf-8"))
        self.assertNotIn(CLAUDE.rule_file, kept)

    # //// 说明文件被外部修改时拒绝继续接管 [@x380kkm 2026-09-24] ////
    def test_external_edit_blocks_second_host_rules(self):
        baseline = {CLAUDE.rule_file: encode_file("原文".encode("utf-8"))}
        targets, ownership = {CLAUDE.rule_file: "受管正文".encode("utf-8")}, {}
        reconcile_rules(targets, baseline, ownership, CLAUDE.rule_file)
        edited = {CLAUDE.rule_file: encode_file("他人改写".encode("utf-8"))}
        with self.assertRaises(StorageConflictError):
            reconcile_rules({}, edited, dict(ownership), CLAUDE.rule_file)
