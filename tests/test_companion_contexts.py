# audience: internal
# # companion-context-tests
"""实际目录中的独立正文、使用绑定和读取快照验证 Skill 隔离、条件匹配与联合写入冲突."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.companion_contexts import ContextError
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError


# //// 在隔离用户库中维护两种方法与独立配套正文 [@x380kkm 2026-09-06] ////
class CompanionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project, self.source = root / "user", root / "project", root / "sources"
        for path in (self.user, self.project, self.source):
            path.mkdir()
        self.manager = Manager(read_roots=[self.source], user_root=self.user)
        self.plugins = {}
        for name in ("writing", "coding"):
            path = self.source / name / "SKILL.md"
            path.parent.mkdir()
            path.write_text(f"---\nname: {name}\ndescription: {name} method.\n---\n# {name}\nOriginal method.\n", encoding="utf-8")
            plugin = self.manager.import_file(str(path))["document"]
            self.save(plugin)
            identity = document_identity(plugin)
            self.manager.apply_document(self.manager.preview_usage(identity, {"state": "enabled"})["plan"])
            self.plugins[name] = plugin
        self.target = document_identity(self.plugins["writing"])
        self.ref = self.plugins["writing"]["id"] + "#writing"

    # //// 保存声明并沿用真实对象基线 [@x380kkm 2026-09-06] ////
    def save(self, document: dict, baseline: dict | None = None) -> None:
        self.manager.apply_document(self.manager.preview_document(document, baseline)["plan"])

    # //// 构造方法专属的配套设置 [@x380kkm 2026-09-06] ////
    def settings(self, **changes) -> dict:
        return {"name": "专用术语", "skill": self.ref, "text": "Use the approved terminology.",
                "enabled": True, "selector": {"user": "current"}, **changes}

    # //// 通过外部接口创建独立正文和绑定 [@x380kkm 2026-09-06] ////
    def create(self, scope: str = "user", **settings) -> dict:
        plan = self.manager.invoke("context.preview", {"plugin": self.target, "scope": scope, "settings": self.settings(**settings)})["plan"]
        result = self.manager.invoke("context.apply", {"plan": plan})
        described = self.manager.invoke("context.describe", {"plugin": self.target, "scope": scope})
        return next(item for item in described["contexts"] if item["id"] == result["id"])

    # //// 取得完整方法快照中实际纳入的配套文本 [@x380kkm 2026-09-06] ////
    def companion_texts(self, name: str = "writing", context: dict | None = None, scope: str = "user") -> list[str]:
        plugin = self.plugins[name]
        result = self.manager.open_content(plugin["id"] + "#" + name, "local", context=context, scope=scope)
        self.assertEqual(result["readiness"], "ready")
        return [unit["content"]["text"] for unit in result["units"]
                if isinstance(unit["content"], dict) and "text" in unit["content"]]

    # //// 配套正文只随指定方法读取且保持上游独立 [@x380kkm 2026-09-06] ////
    def test_context_is_independent_and_selected_by_skill(self) -> None:
        original = deepcopy(self.plugins["writing"])
        path = self.source / "writing/SKILL.md"
        before = path.read_bytes()
        self.create()
        self.assertEqual(self.companion_texts(), ["Use the approved terminology."])
        self.assertEqual(self.companion_texts("coding"), [])
        self.assertEqual(self.manager.read_document(self.target)["document"], original)
        self.assertEqual(path.read_bytes(), before)
        graph = self.manager.snapshot_catalog()["graph"]
        self.assertTrue(any(edge["label"] == "supplements" and edge["to"] == self.target + "#writing" for edge in graph["edges"]))

    # //// 任务条件与启停控制配套正文的纳入 [@x380kkm 2026-09-06] ////
    def test_task_scope_and_disable_preserve_text(self) -> None:
        item = self.create(selector={"task": "release-notes"})
        self.assertEqual(self.companion_texts(context={"task": "release-notes"}), ["Use the approved terminology."])
        self.assertEqual(self.companion_texts(context={"task": "technical-review"}), [])
        plan = self.manager.contexts.preview(self.target, {**item["settings"], "enabled": False}, item["baseline"])["plan"]
        self.manager.contexts.apply(plan)
        self.assertEqual(self.companion_texts(context={"task": "release-notes"}), [])
        saved = self.manager.contexts.describe(self.target)["contexts"][0]
        self.assertEqual(saved["settings"]["text"], item["settings"]["text"])

    # //// 任一声明发生协作冲突时整组保存保持原状 [@x380kkm 2026-09-06] ////
    def test_binding_conflict_prevents_partial_text_save(self) -> None:
        item = self.create()
        plan = self.manager.contexts.preview(self.target, self.settings(text="Locally edited text."), item["baseline"])["plan"]
        binding = item["baseline"]["binding"]
        changed = {**binding, "enabled": False}
        self.save(changed, binding)
        before = self.manager.store.snapshot()
        with self.assertRaises(StorageConflictError):
            self.manager.contexts.apply(plan)
        self.assertEqual(self.manager.store.snapshot(), before)
        refreshed = self.manager.contexts.describe(self.target)["contexts"][0]
        approved = self.manager.contexts.preview(self.target, self.settings(text="Locally edited text."), refreshed["baseline"])["plan"]
        self.manager.contexts.apply(approved)
        self.assertEqual(self.companion_texts(), ["Locally edited text."])

    # //// 项目配套说明保持本层独立并继承用户说明 [@x380kkm 2026-09-06] ////
    def test_project_context_does_not_enter_user_reads(self) -> None:
        self.create()
        self.manager = Manager(self.project, [self.source], user_root=self.user)
        self.create(scope="project", text="Project-specific terms.", selector={})
        inherited = next(item for item in self.manager.contexts.describe(self.target, "project")["contexts"] if item["scope"] == "user")
        self.assertFalse(inherited["editable"])
        self.assertTrue(inherited["settings"]["enabled"])
        self.assertEqual(inherited["settings"]["selector"], {"user": "current"})
        self.assertEqual(self.companion_texts(), ["Use the approved terminology."])
        self.assertCountEqual(self.companion_texts(scope="project"), ["Use the approved terminology.", "Project-specific terms."])

    # //// 移出配套说明同时移出本层绑定并保留方法 [@x380kkm 2026-09-06] ////
    def test_removal_conflict_preserves_both_entries(self) -> None:
        item = self.create()
        plan = self.manager.contexts.preview_remove(self.target, item["baseline"])["plan"]
        original = item["baseline"]["document"]
        changed = deepcopy(original)
        changed["contributions"][0]["payload"]["text"] = "External change."
        self.save(changed, original)
        with self.assertRaises(StorageConflictError):
            self.manager.contexts.apply(plan)
        self.assertIsNotNone(self.manager.read_document(item["baseline"]["binding"]["id"]))
        refreshed = self.manager.contexts.describe(self.target)["contexts"][0]
        self.manager.contexts.apply(self.manager.contexts.preview_remove(self.target, refreshed["baseline"])["plan"])
        self.assertEqual(self.manager.contexts.describe(self.target)["contexts"], [])
        self.assertEqual(self.companion_texts(), [])
        self.assertIsNotNone(self.manager.read_document(self.target))

    # //// 无效范围或目标无法生成可保存的配套计划 [@x380kkm 2026-09-06] ////
    def test_invalid_selection_keeps_catalog_unchanged(self) -> None:
        before = self.manager.store.snapshot()
        for settings in (self.settings(skill="plugin:unknown#entry"), self.settings(selector={"path": "*/src"}), self.settings(text="  ")):
            with self.assertRaises(ContextError):
                self.manager.contexts.preview(self.target, settings)
        self.assertEqual(self.manager.store.snapshot(), before)

    # //// 缺失绑定在提交前被恢复时保留正文与新绑定 [@x380kkm 2026-09-06] ////
    def test_new_binding_invalidates_unbound_removal(self) -> None:
        item = self.create()
        binding = item["baseline"]["binding"]
        self.manager.apply_document(self.manager.preview_remove(binding["id"], binding)["plan"])
        unbound = self.manager.contexts.describe(self.target)["contexts"][0]
        self.assertFalse(unbound["settings"]["enabled"])
        plan = self.manager.contexts.preview_remove(self.target, unbound["baseline"])["plan"]
        self.save(binding)
        before = self.manager.store.snapshot()
        with self.assertRaises(StorageConflictError):
            self.manager.contexts.apply(plan)
        self.assertEqual(self.manager.store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
