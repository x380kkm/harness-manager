# audience: internal
# # host-profile-projection-tests
# 同一份声明按宿主档案编译到各自的说明文件, 这里核对两个宿主的输出互不串位.
# 尚未实现的宿主能力以阻断诊断表达, 不产生半对的宿主文件.

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.catalogs import Catalogs
from harness_manager.codex_inventory import CodexInventory
from harness_manager.content_plan import INSTRUCTION_POINT, SKILL_POINT
from harness_manager.host_profiles import CLAUDE, CODEX
from harness_manager.host_projection import HOOK_POINT, compile_host
from harness_manager.sources import SourceReader
from harness_manager.usage import usage_document


# //// 在隔离目录中把同一声明编译到两个宿主 [@x380kkm 2026-09-24] ////
class HostProfileProjectionTests(unittest.TestCase):
    # //// 构造只含规则成员的组合声明 [@x380kkm 2026-09-24] ////
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project, self.library = root / "user", root / "project", root / "library"
        for directory in (self.user, self.project, self.library):
            directory.mkdir()
        self.codex = CodexInventory(self.user)
        self.codex.root.mkdir()
        self.catalogs = Catalogs(self.user, self.project)
        self.reader = SourceReader(self.project, [self.library])
        self.skill = self.library / "method/SKILL.md"
        self.skill.parent.mkdir()
        self.skill.write_text("---\nname: method\ndescription: Read source.\n---\n# Method\nComplete procedure.\n",
                              encoding="utf-8")
        self.module = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:rules",
                       "release": {"version": "local"}, "metadata": {"name": "Rules"},
                       "sources": [{"id": "files", "source": {"resolver": {"id": "manager.source/path", "range": "^1.0.0"},
                                                              "locator": str(self.library)}}],
                       "contributions": [
                           {"id": "rule", "point": INSTRUCTION_POINT,
                            "contract": {"id": INSTRUCTION_POINT, "range": "^1.0.0"},
                            "payload": {"name": "Naming", "text": "## Naming\n\nKeep existing names.\n"}}]}

    # //// 保存声明并沿用同一对象基线 [@x380kkm 2026-09-24] ////
    def save(self, document: dict, scope: str = "user") -> None:
        store = self.catalogs.select(scope)
        baseline = next((value for value in store.snapshot() if value["id"] == document["id"]), None)
        store.apply(store.preview_put(document, baseline))

    # //// 保存启用绑定并编译指定宿主 [@x380kkm 2026-09-24] ////
    def compile(self, profile=CODEX, scope: str = "user") -> dict:
        self.save(self.module)
        self.save(usage_document(self.module, {"state": "enabled"}, None, scope), scope)
        return compile_host(self.catalogs, self.codex, self.reader, scope, profile=profile)

    # //// 规则成员按档案编译到各自宿主的说明文件 [@x380kkm 2026-09-24] ////
    def test_rules_follow_the_profile_rule_file(self) -> None:
        codex_result = self.compile(CODEX)
        self.assertEqual(codex_result["diagnostics"], [])
        self.assertIn(CODEX.rule_file, codex_result["targets"])
        self.assertNotIn(CLAUDE.rule_file, codex_result["targets"])

        claude_result = self.compile(CLAUDE)
        self.assertEqual(claude_result["diagnostics"], [])
        self.assertIn(CLAUDE.rule_file, claude_result["targets"])
        self.assertNotIn(CODEX.rule_file, claude_result["targets"])
        self.assertEqual(claude_result["targets"][CLAUDE.rule_file],
                         codex_result["targets"][CODEX.rule_file])

    # //// 贡献记录标注实际写入的宿主文件 [@x380kkm 2026-09-24] ////
    def test_contribution_target_names_the_profile_file(self) -> None:
        claude_result = self.compile(CLAUDE)
        targets = {entry["target"] for entry in claude_result["contributions"]}
        self.assertEqual(targets, {CLAUDE.rule_file})

    # //// 绑定限定宿主时只有该宿主取得输出 [@x380kkm 2026-09-24] ////
    def test_selector_host_follows_the_profile(self) -> None:
        self.save(self.module)
        binding = usage_document(self.module, {"state": "enabled"}, None, "user")
        binding["target"]["selector"]["host"] = "claude"
        self.save(binding, "user")
        codex_result = compile_host(self.catalogs, self.codex, self.reader, "user", profile=CODEX)
        claude_result = compile_host(self.catalogs, self.codex, self.reader, "user", profile=CLAUDE)
        self.assertEqual(codex_result["targets"], {})
        self.assertIn(CLAUDE.rule_file, claude_result["targets"])

    # //// 宿主缺少 Skill 载体时跳过该成员并照常写出其余内容 [@x380kkm 2026-09-24] ////
    def test_skill_member_is_skipped_when_host_places_directories(self) -> None:
        self.module["contributions"].append(
            {"id": "skill", "point": SKILL_POINT, "contract": {"id": SKILL_POINT, "range": "^1.0.0"},
             "source": "source:files", "payload": {"name": "method", "description": "Read source.",
                                                   "entry": "method/SKILL.md"}})
        codex_result = self.compile(CODEX)
        self.assertEqual(codex_result["diagnostics"], [])
        self.assertIn(CODEX.config_file, codex_result["targets"])

        claude_result = self.compile(CLAUDE)
        self.assertEqual({item["code"] for item in claude_result["diagnostics"]},
                         {"host_capability_unsupported"})
        self.assertTrue(all(item["severity"] == "warning" for item in claude_result["diagnostics"]))
        self.assertIn(CLAUDE.rule_file, claude_result["targets"])
        self.assertNotIn(CLAUDE.config_file, claude_result["targets"])

    # //// Hook 成员在两个宿主分别编译到各自的定义文件 [@x380kkm 2026-09-24] ////
    def test_hook_member_compiles_on_both_hosts(self) -> None:
        self.module["contributions"].append(
            {"id": "hook", "point": HOOK_POINT, "contract": {"id": HOOK_POINT, "range": "^1.0.0"},
             "payload": {"event": "Stop", "handlers": [{"type": "command", "command": "inspect"}]}})
        codex_result = self.compile(CODEX)
        self.assertEqual(codex_result["diagnostics"], [])
        self.assertIn(CODEX.hook_file, codex_result["targets"])
        self.assertIn(CODEX.rule_file, codex_result["targets"])

        claude_result = self.compile(CLAUDE)
        self.assertEqual(claude_result["diagnostics"], [])
        self.assertIn(CLAUDE.hook_file, claude_result["targets"])
        self.assertIn(CLAUDE.rule_file, claude_result["targets"])

    # //// 宿主缺少全局规则读取器时项目范围阻断 [@x380kkm 2026-09-24] ////
    def test_project_scope_blocks_without_native_rule_reader(self) -> None:
        result = self.compile(CLAUDE, "project")
        self.assertIn("host_scope_unsupported", {item["code"] for item in result["diagnostics"]})
        self.assertEqual(result["targets"], {})
