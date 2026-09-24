# audience: internal
# # content-dependency-tests
"""隔离来源核对必需引用, 配套上下文和模块说明共同形成的完整读取范围."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from harness_manager.content_plan import plan_content
from harness_manager.service import Manager
from test_content_snapshots import binding, member, package


# //// 创建带有明确缺失行为的内容依赖 [@x380kkm 2026-09-10] ////
def requirement(target: str) -> dict:
    return {"id": "dependency", "target": target, "strength": "required",
            "missing": {"visibility": "reported", "acquisition": "none"}}


# //// 核对直接读取与经依赖读取的齐备范围 [@x380kkm 2026-09-10] ////
class ContentDependencyTests(unittest.TestCase):
    # //// 建立具有独立正文与配套说明的来源 [@x380kkm 2026-09-10] ////
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        user = self.root / "user"
        user.mkdir()
        self.manager = Manager(None, [self.source], user_root=user)
        self.a = "plugin:methods#a"
        self.b = "plugin:methods#b"
        self.c = "plugin:methods#c"
        self.notes = "plugin:methods#notes"
        self.plugin = package("plugin:methods", self.source, [
            member("a", "skill.x380kkm/deployment", "a.md", name="A"),
            member("b", "skill.x380kkm/deployment", "b.md", name="B"),
            member("notes", "context.x380kkm/task", "notes.md", skill=self.b),
            member("c", "skill.x380kkm/deployment", "c.md", name="C"),
            member("detail", "context.x380kkm/task", "detail.md", subject=self.c),
            member("unrelated", "context.x380kkm/task", "unrelated.md", skill="plugin:methods#unrelated"),
        ])
        self.plugin["contributions"][0]["requirements"] = [requirement(self.b)]
        self.plugin["contributions"][2]["criticality"] = {"default": "required"}
        self.usage = binding("binding:methods", self.plugin, {"user": "current"})
        for name in ("a.md", "b.md", "notes.md", "c.md", "detail.md", "unrelated.md", "required.md", "requested.md"):
            (self.source / name).write_text(name + " 完整正文", encoding="utf-8")

    # //// 保存用于读取的声明与当前绑定 [@x380kkm 2026-09-10] ////
    def save(self, document: dict, baseline: dict | None = None) -> None:
        self.manager.apply_document(self.manager.preview_document(document, baseline)["plan"])

    # //// 取得同一来源下完整读取的正文单元 [@x380kkm 2026-09-10] ////
    def read(self, reference: str, **options) -> dict:
        return self.manager.open_content(reference, "1.0.0", **options)

    # //// 依赖说明继续展开依赖与说明, 循环引用合并必需资源 [@x380kkm 2026-09-10] ////
    def test_transitive_context_dependencies_keep_resources_and_deduplicate_cycles(self) -> None:
        contributions = self.plugin["contributions"]
        contributions[2]["requirements"] = [requirement(self.c)]
        contributions[3]["requirements"] = [requirement(self.b)]
        contributions[4]["payload"]["subject"] = self.notes
        contributions[4]["requirements"] = [requirement(self.notes)]
        contributions[1]["extensions"] = [{"contract": {"id": "carrier.x380kkm/required-units", "range": "^1.0.0"},
                                             "payload": {"entries": ["required.md"]}}]
        self.save(self.plugin)
        self.save(self.usage)
        direct = self.read(self.b)
        through_dependency = self.read(self.a, resources=["requested.md"])
        self.assertEqual(direct["readiness"], "ready")
        self.assertEqual(through_dependency["readiness"], "ready")
        direct_texts = [unit["content"] for unit in direct["units"] if isinstance(unit["content"], str)]
        dependency_texts = [unit["content"] for unit in through_dependency["units"] if isinstance(unit["content"], str)]
        self.assertEqual(set(direct_texts), {name + " 完整正文" for name in ("b.md", "notes.md", "c.md", "detail.md", "required.md")})
        self.assertEqual(set(dependency_texts), set(direct_texts) | {"a.md 完整正文", "requested.md 完整正文"})
        self.assertEqual(len(dependency_texts), len(set(dependency_texts)))

    # //// 被排除的必需配套说明在依赖读取中保持缺口 [@x380kkm 2026-09-10] ////
    def test_excluded_required_dependency_context_prevents_readiness(self) -> None:
        self.usage["selection"] = {"exclude": ["notes"]}
        self.save(self.plugin)
        self.save(self.usage)
        for reference in (self.b, self.a):
            with self.subTest(reference=reference):
                result = self.read(reference)
                self.assertEqual(result["readiness"], "needs-content")
                self.assertEqual(result["missing"], [self.notes])

    # //// 配套正文无法读取时依赖入口保留所缺单元 [@x380kkm 2026-09-10] ////
    def test_unreadable_dependency_context_prevents_readiness(self) -> None:
        self.plugin["contributions"][2]["payload"]["entry"] = "missing.md"
        self.save(self.plugin)
        self.save(self.usage)
        direct = self.read(self.b)
        through_dependency = self.read(self.a)
        self.assertEqual(direct["readiness"], "needs-content")
        self.assertEqual(through_dependency["readiness"], "needs-content")
        self.assertEqual(through_dependency["missing"], direct["missing"])

    # //// 必需别名的缺失发布和不匹配版本保留读取缺口 [@x380kkm 2026-09-10] ////
    def test_required_unresolved_aliases_prevent_readiness(self) -> None:
        original = deepcopy(self.plugin)
        for cause, required_by in (("missing", "member"), ("version", "binding"), ("nested", "member"), ("cycle", "member")):
            with self.subTest(cause=cause, required_by=required_by):
                plugin = deepcopy(original)
                alias = {"id": "required-rule", "ref": "plugin:missing#rule"}
                usage = deepcopy(self.usage)
                if required_by == "binding":
                    usage["selection"] = {"require": [alias["id"]]}
                else:
                    alias["criticality"] = {"default": "required"}
                if cause == "version":
                    alias.update(ref=self.notes, constraint="2.0.0")
                if cause == "nested":
                    plugin["contributions"].append(alias)
                    alias = {"id": "outer", "ref": "plugin:methods#required-rule", "constraint": "1.0.0"}
                if cause == "cycle":
                    alias["ref"] = "plugin:methods#required-rule"
                plugin["contributions"].append(alias)
                store = self.manager.catalogs.user
                old = {item["kind"]: item for item in store.snapshot()}
                self.save(plugin, old.get("Plugin"))
                self.save(usage, old.get("PluginBinding"))
                result = self.read(self.a)
                self.assertEqual(result["readiness"], "needs-content")
                self.assertIn("plugin:methods#required-rule", result["missing"])
                self.assertIn("required_unavailable", {note["code"] for note in result["diagnostics"]})

    # //// 未参与读取的包与可选别名保持独立的内容缺口 [@x380kkm 2026-09-10] ////
    def test_unrelated_required_and_optional_aliases_do_not_block_selected_content(self) -> None:
        self.plugin["contributions"].append({"id": "optional", "ref": "plugin:missing#rule"})
        other = package("plugin:other", self.source, [{"id": "required", "ref": "plugin:missing#rule",
                                                       "criticality": {"default": "required"}}])
        for item in (self.plugin, self.usage, other, binding("binding:other", other, {"user": "current"})):
            self.save(item)
        result = self.read(self.a)
        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(result["missing"], [])

    # //// 声明的必需性随成员宿主范围限定, 绑定的显式要求独立保留 [@x380kkm 2026-09-10] ////
    def test_required_context_host_scope_matches_direct_and_dependency_reads(self) -> None:
        notes = self.plugin["contributions"][2]
        notes["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": "codex"}}
        self.save(self.plugin)
        self.save(self.usage)
        for reference in (self.b, self.a):
            with self.subTest(reference=reference):
                manager = self.read(reference, context={"host": "harness-manager"})
                self.assertEqual(manager["readiness"], "ready")
                self.assertFalse(any("notes.md" in unit["ref"] for unit in manager["units"]))
                codex = self.read(reference, context={"host": "codex"})
                self.assertTrue(any("notes.md" in unit["ref"] for unit in codex["units"]))
        required = deepcopy(self.usage)
        required["selection"] = {"require": ["notes"]}
        self.save(required, self.usage)
        result = self.read(self.a, context={"host": "harness-manager"})
        self.assertEqual(result["readiness"], "needs-content")
        self.assertIn(self.notes, result["missing"])

    # //// 缺少任务上下文仍保留已适用宿主的必需说明缺口 [@x380kkm 2026-09-10] ////
    def test_required_context_unknown_task_scope_remains_unavailable(self) -> None:
        self.plugin["contributions"][2]["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"},
                                                     "selector": {"host": "harness-manager", "task": "review"}}
        self.save(self.plugin)
        self.save(self.usage)
        result = self.read(self.a, context={"host": "harness-manager"})
        self.assertEqual(result["readiness"], "needs-content")
        self.assertIn(self.notes, result["missing"])
        self.assertIn("missing_context", {note["code"] for note in result["diagnostics"]})

    # //// 模块别名依赖携带原始说明, 成员职责和模块偏好 [@x380kkm 2026-09-10] ////
    def test_module_dependency_contexts_follow_selected_modules(self) -> None:
        self.plugin["contributions"][0]["requirements"] = [requirement("plugin:bundle#b")]
        hook = {"id": "event", "point": "hook.x380kkm/lifecycle", "contract": {"id": "hook.x380kkm/lifecycle", "range": "^1.0.0"},
                "payload": {"event": "Stop", "handlers": [{"type": "command", "command": "inspect"}]}}
        self.plugin["contributions"].append(hook)
        module = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:bundle", "release": {"version": "1.0.0"},
                  "contributions": [{"id": "b", "ref": self.b, "constraint": "1.0.0"},
                                    {"id": "event", "ref": "plugin:methods#event", "constraint": "1.0.0"}],
                  "extensions": [{"contract": {"id": "manager.module/presentation", "range": "^1.0.0"}, "payload": {
                      "members": {"b": {"kind": "skill", "role": "模块 B 的使用说明."},
                                  "event": {"kind": "hook", "role": "模块 Hook 的使用说明."}}}}]}
        unrelated = deepcopy(module)
        unrelated["id"] = "plugin:unrelated"
        unrelated["contributions"] = unrelated["contributions"][1:]
        for document in (self.plugin, self.usage, module, binding("binding:bundle", module, {"user": "current"}),
                         unrelated, binding("binding:unrelated", unrelated, {"user": "current"})):
            self.save(document)
        view = self.manager.catalogs.for_scope("user")
        plan = plan_content(view.documents, self.manager.content_context(None, "user"), self.a, "1.0.0", [], layers=view.layers)
        references = [entry.content.summary["ref"] for entry in plan.entries]
        payloads = [entry.content.member["payload"] for entry in plan.entries]
        self.assertIn(self.notes, references)
        self.assertTrue(any(payload.get("skill") == "plugin:bundle#b" for payload in payloads))
        self.assertTrue(any("模块 Hook 的使用说明." in payload.get("guidance", "") for payload in payloads))
        self.assertFalse(any(reference.startswith("plugin:unrelated#") for reference in references))
        self.assertEqual(plan.missing, [])


if __name__ == "__main__":
    unittest.main()
