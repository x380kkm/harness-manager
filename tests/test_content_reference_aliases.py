# audience: internal
# # content-reference-alias-tests
"""完整读取在已选模块中解析原内容依赖, 版本与使用选项仍属于各自的绑定入口."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.service import Manager
from test_content_dependencies import requirement
from test_content_snapshots import binding, member, package


# //// 核对引用别名及其配套内容的完整读取 [@x380kkm 2026-09-10] ////
class ContentReferenceAliasTests(unittest.TestCase):
    # //// 建立关闭原包而启用模块的本地内容来源 [@x380kkm 2026-09-10] ////
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        user = self.root / "user"
        user.mkdir()
        self.manager = Manager(None, [self.source], user_root=user)
        for name in ("a.md", "b.md", "notes.md"):
            (self.source / name).write_text(name + " 完整正文", encoding="utf-8")
        self.original = package("plugin:methods", self.source, [
            member("a", "skill.x380kkm/deployment", "a.md", name="A"),
            member("b", "skill.x380kkm/deployment", "b.md", name="B"),
            member("notes", "context.x380kkm/task", "notes.md", skill="plugin:methods#b"),
        ])
        self.original["contributions"][0]["requirements"] = [requirement("plugin:methods#b")]
        self.usage = binding("binding:methods", self.original, {"user": "current"})
        self.usage["enabled"] = False

    # //// 保存被内容读取选择的完整声明 [@x380kkm 2026-09-10] ////
    def save(self, *documents):
        for document in documents:
            self.manager.apply_document(self.manager.preview_document(document)["plan"])

    # //// 生成分别固定原始发布的模块别名 [@x380kkm 2026-09-10] ////
    def module(self, identity, references, version="1.0.0"):
        return {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": identity,
                "release": {"version": "1.0.0"}, "contributions": [
                    {"id": name, "ref": reference, "constraint": version} for name, reference in references.items()]}

    # //// 读取当前模块并取得实际选择的配置引用 [@x380kkm 2026-09-10] ////
    def read(self):
        result = self.manager.open_content("plugin:bundle#a", "1.0.0")
        configuration = next(unit["content"] for unit in result["units"] if unit["ref"].endswith("/configuration"))
        return result, {entry["ref"]: entry for entry in configuration["selections"]}

    # //// 关闭原包时模块内的依赖别名携带原说明与成员职责 [@x380kkm 2026-09-10] ////
    def test_disabled_package_dependency_uses_selected_module_alias(self):
        bundle = self.module("plugin:bundle", {name: "plugin:methods#" + name for name in ("a", "b", "notes")})
        bundle["extensions"] = [{"contract": {"id": "manager.module/presentation", "range": "^1.0.0"},
                                 "payload": {"members": {"b": {"kind": "skill", "role": "B 的模块职责."}}}}]
        self.save(self.original, self.usage, bundle, binding("binding:bundle", bundle, {"user": "current"}))

        result, selections = self.read()

        self.assertEqual(result["readiness"], "ready")
        self.assertIn("plugin:bundle#b", selections)
        self.assertIn("plugin:bundle#notes", selections)
        self.assertEqual({unit["content"] for unit in result["units"] if isinstance(unit["content"], str)},
                         {"a.md 完整正文", "b.md 完整正文", "notes.md 完整正文"})
        self.assertTrue(any("B 的模块职责." in unit["content"].get("text", "")
                            for unit in result["units"] if isinstance(unit["content"], dict)))

    # //// 原引用有直接使用入口时保留其选择优先级 [@x380kkm 2026-09-10] ////
    def test_direct_selected_reference_precedes_module_alias(self):
        self.usage["enabled"] = True
        bundle = self.module("plugin:bundle", {name: "plugin:methods#" + name for name in ("a", "b")})
        self.save(self.original, self.usage, bundle, binding("binding:bundle", bundle, {"user": "current"}))

        result, selections = self.read()

        self.assertEqual(result["readiness"], "ready")
        self.assertIn("plugin:methods#b", selections)
        self.assertNotIn("plugin:bundle#b", selections)

    # //// 多层别名在中间引用处继续解析完整依赖 [@x380kkm 2026-09-10] ////
    def test_dependency_matches_intermediate_alias_reference(self):
        self.original["contributions"][0]["requirements"] = [requirement("plugin:middle#b")]
        middle = self.module("plugin:middle", {name: "plugin:methods#" + name for name in ("a", "b", "notes")})
        bundle = self.module("plugin:bundle", {name: "plugin:middle#" + name for name in ("a", "b", "notes")})
        self.save(self.original, self.usage, middle, bundle, binding("binding:bundle", bundle, {"user": "current"}))

        result, selections = self.read()

        self.assertEqual(result["readiness"], "ready")
        self.assertIn("plugin:bundle#b", selections)
        self.assertIn("plugin:bundle#notes", selections)

    # //// 同一模块的唯一入口保持该模块的使用选项 [@x380kkm 2026-09-10] ////
    def test_same_module_alias_precedes_other_selected_usage(self):
        bundle = self.module("plugin:bundle", {name: "plugin:methods#" + name for name in ("a", "b")})
        other = self.module("plugin:other", {"b": "plugin:methods#b"})
        selected = binding("binding:bundle", bundle, {"user": "current"})
        selected["options"] = {"mode": "selected"}
        alternative = binding("binding:other", other, {"user": "current"})
        alternative["options"] = {"mode": "other"}
        self.save(self.original, self.usage, bundle, selected, other, alternative)

        result, selections = self.read()

        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(selections["plugin:bundle#b"]["options"], {"mode": "selected"})
        self.assertNotIn("plugin:other#b", selections)

    # //// 多个模块分别选择的版本保持明确的依赖歧义 [@x380kkm 2026-09-10] ////
    def test_aliases_of_different_versions_leave_dependency_unavailable(self):
        second = deepcopy(self.original)
        second["release"]["version"] = "2.0.0"
        bundle = self.module("plugin:bundle", {"a": "plugin:methods#a"})
        first_alias = self.module("plugin:first", {"b": "plugin:methods#b"})
        second_alias = self.module("plugin:second", {"b": "plugin:methods#b"}, "2.0.0")
        self.save(self.original, second, self.usage, bundle, binding("binding:bundle", bundle, {"user": "current"}),
                  first_alias, binding("binding:first", first_alias, {"user": "current"}),
                  second_alias, binding("binding:second", second_alias, {"user": "current"}))

        result, selections = self.read()

        self.assertEqual(result["readiness"], "needs-content")
        self.assertEqual(result["missing"], ["plugin:methods#b"])
        self.assertEqual(set(selections), {"plugin:bundle#a"})
        self.assertIn("content.required-reference-unavailable", {note["code"] for note in result["diagnostics"]})

    # //// 同版本的多个独立用法保持显式入口选择 [@x380kkm 2026-09-10] ////
    def test_aliases_with_different_options_remain_ambiguous(self):
        bundle = self.module("plugin:bundle", {"a": "plugin:methods#a"})
        self.save(self.original, self.usage, bundle, binding("binding:bundle", bundle, {"user": "current"}))
        for name in ("first", "second"):
            module = self.module("plugin:" + name, {"b": "plugin:methods#b"})
            usage = binding("binding:" + name, module, {"user": "current"})
            usage["options"] = {"mode": name}
            self.save(module, usage)

        result, selections = self.read()

        self.assertEqual(result["readiness"], "needs-content")
        self.assertEqual(result["missing"], ["plugin:methods#b"])
        self.assertEqual(set(selections), {"plugin:bundle#a"})


if __name__ == "__main__":
    unittest.main()
