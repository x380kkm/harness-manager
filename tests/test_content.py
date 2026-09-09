# audience: internal
# # content-unit-tests
# 实际来源读取核对开放载体, 包内来源作用域, 选择身份和正文修订变化.

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from harness_manager.content import ContentError
from harness_manager.service import Manager
from test_declarations import make_declaration
from test_projection import make_binding
from test_service import create_skill


# //// 通过实际登记与读取验证内容边界 [@x380kkm 2026-09-06] ////
class ContentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.manager = Manager(workspace, [self.root], user_root=self.root)

    # //// 保存真实 Skill 的独立来源 [@x380kkm 2026-09-06] ////
    def register_skill(self) -> tuple[Path, dict]:
        path = create_skill(self.root)
        plugin = self.manager.import_file(str(path))["document"]
        plugin["release"]["version"] = "1.0.0"
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        return path, plugin

    # //// 开放载体保持对应的文本或 JSON 正文 [@x380kkm 2026-09-06] ////
    def test_open_payloads_read_as_complete_units(self) -> None:
        baseline = None
        for payload in (None, False, 12, ["资料", {"key": 1}], "普通说明", {"content": "", "options": {"strict": True}}):
            with self.subTest(payload=payload):
                plugin = make_declaration(payload)
                self.manager.apply_document(self.manager.preview_document(plugin, baseline)["plan"])
                baseline = deepcopy(plugin)
                result = self.manager.read_content("plugin:content#content", "1.0.0")
                unit = result["units"][0]
                if isinstance(payload, str):
                    self.assertEqual(unit["content"], payload)
                else:
                    self.assertEqual(json.loads(unit["content"]), payload)
                self.assertEqual(result["unit_status"], "ready")
                self.assertNotIn("readiness", result)

    # //// 直接定位器与包内来源引用读取同一实际入口 [@x380kkm 2026-09-06] ////
    def test_direct_source_locator_resolves_without_binding_lookup(self) -> None:
        path, plugin = self.register_skill()
        direct = deepcopy(plugin)
        direct["contributions"][0]["source"] = direct.pop("sources")[0]["source"]
        self.manager.apply_document(self.manager.preview_document(direct, plugin)["plan"])
        result = self.manager.read_content(plugin["id"] + "#example-skill", "1.0.0")
        self.assertEqual(result["units"][0]["content"], path.read_bytes().decode("utf-8"))

    # //// 待解析来源保持原始引用与明确诊断 [@x380kkm 2026-09-06] ////
    def test_inherited_and_catalog_sources_preserve_unresolved_reference(self) -> None:
        _, plugin = self.register_skill()
        baseline = plugin
        for reference in ("inherit", "catalog:shared/source"):
            changed = deepcopy(baseline)
            changed["contributions"][0]["source"] = reference
            self.manager.apply_document(self.manager.preview_document(changed, baseline)["plan"])
            baseline = changed
            with self.assertRaises(ContentError) as caught:
                self.manager.read_content(plugin["id"] + "#example-skill", "1.0.0")
            self.assertEqual(caught.exception.code, "unresolved_source")
            self.assertEqual(caught.exception.details["source"], reference)

    # //// 组合选择的版本可继续用于同一引用的资源读取 [@x380kkm 2026-09-06] ////
    def test_alias_version_remains_paired_with_selected_reference(self) -> None:
        path, plugin = self.register_skill()
        (path.parent / "reference.md").write_text("来源配套资料", encoding="utf-8")
        bundle = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:bundle",
                  "release": {"version": "2.0.0"},
                  "sources": [{"id": plugin["sources"][0]["id"], "source": {
                      "resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": "missing"}}],
                  "contributions": [{"id": "method", "ref": plugin["id"] + "#example-skill", "constraint": "1.0.0"}]}
        binding = make_binding("binding:bundle", bundle, {"project": self.manager.workspace.as_uri()})
        for document in (bundle, binding):
            self.manager.apply_document(self.manager.preview_document(document)["plan"])
        candidate = self.manager.discover_content(detail="full")["candidates"][0]
        result = self.manager.read_content(candidate["ref"], candidate["version"])
        pending = self.manager.read_content(candidate["ref"], candidate["version"], budget=1)
        for response in (result, pending):
            self.assertEqual((response["ref"], response["version"]), (candidate["ref"], "2.0.0"))
            self.assertEqual((response["content_ref"], response["content_version"]),
                             (candidate["content"], candidate["content_version"]))
        resource = self.manager.read_content(result["ref"], result["version"], "reference.md")
        self.assertEqual(resource["units"][0]["content"], "来源配套资料")

    # //// 本地正文变化使同一入口的陈旧修订比较失败 [@x380kkm 2026-09-06] ////
    def test_local_revision_observes_source_changes(self) -> None:
        path, plugin = self.register_skill()
        ref = plugin["id"] + "#example-skill"
        original = self.manager.read_content(ref, "1.0.0")["units"][0]
        repeated = self.manager.read_content(ref, "1.0.0", expected_revision=original["revision"])
        self.assertEqual(repeated["units"][0]["content"], original["content"])
        path.write_text("变化后的方法正文", encoding="utf-8")
        with self.assertRaises(ContentError) as caught:
            self.manager.read_content(ref, "1.0.0", expected_revision=original["revision"])
        self.assertEqual(caught.exception.code, "content_changed")
        current = self.manager.read_content(ref, "1.0.0")["units"][0]
        self.assertNotEqual(current["revision"], original["revision"])
        self.assertEqual(current["content"], "变化后的方法正文")


# //// 运行内容读取边界验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
