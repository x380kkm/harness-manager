# audience: internal
# # card-operation-tests
"""隔离目录中的来源、绑定和适配正文验证作用域继承、双向查询与联合写入冲突."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_operations import CardOperations
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.sources import SourceError
from harness_manager.storage import Store
from harness_manager.card_subjects import CardError, source_document
from harness_manager.storage_errors import StorageConflictError, StorageIOError


# //// 在隔离的 Codex 来源中维护卡片与关系 [@x380kkm 2026-09-07] ////
class CardOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project = root / "user", root / "project"
        self.project.mkdir()
        skills = self.user / ".agents/skills"
        for name in ("writing", "analysis"):
            path = skills / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {name} method.\n---\n# {name}\nSource content.\n", encoding="utf-8")
        agents = self.user / ".codex/AGENTS.md"
        agents.parent.mkdir()
        agents.write_text("# Rules\n\n- Preserve public names.\n- Write clear comments.\n", encoding="utf-8")
        self.manager = Manager(self.project, [self.user], user_root=self.user)
        self.declare_rule("public-names", "Preserve public names.", agents)
        self.declare_rule("clear-comments", "Write clear comments.", agents)
        self.cards = CardOperations(self.manager.catalogs, self.manager.codex)
        self.items = self.cards.inventory()["items"]
        self.skills = {item["name"]: item["id"] for item in self.items if item["kind"] == "skill"}
        self.rules = [item["id"] for item in self.items if item["kind"] == "rule"]

    # //// 保存独立规则声明及其原文来源 [@x380kkm 2026-09-07] ////
    def declare_rule(self, name: str, text: str, path: Path, scope: str = "user") -> dict:
        item = {"id": "rule:" + name, "name": name, "kind": "rule", "summary": text,
                "content": text, "path": str(path), "scope": scope}
        document = source_document(item)
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        return item

    # //// 沿用当前卡片基线修改指定范围 [@x380kkm 2026-09-07] ////
    def configure(self, identity: str, state: str, scope: str = "user") -> dict:
        baseline = self.cards.describe(identity, scope)["configBaseline"]
        return self.cards.configure(identity, state, scope, baseline)

    # //// 读取方法实际得到的配套单元 [@x380kkm 2026-09-07] ////
    def contexts(self, identity: str, scope: str = "user") -> list[str]:
        subject = self.cards.describe(identity, scope)["subject"]
        result = self.manager.open_content(subject["ref"], subject["version"], scope=scope)
        return [unit["content"]["text"] for unit in result["units"]
                if isinstance(unit["content"], dict) and "text" in unit["content"]]

    # //// 从当前共享基线发布或移回项目卡片 [@x380kkm 2026-09-07] ////
    def share(self, identity: str, shared: bool = True) -> dict:
        baseline = self.cards.describe(identity, "project-local")["sharingBaseline"]
        return self.cards.set_shared(identity, shared, baseline)

    # //// 取得项目共享目录中的有向适配正文 [@x380kkm 2026-09-07] ////
    def shared_adapters(self) -> list[dict]:
        return [value for value in self.manager.catalogs.project.snapshot()
                if value["kind"] == "Plugin" and value["id"].startswith("plugin:adapter/")]

    # //// 首次启用同时保存来源与绑定且保留宿主文件 [@x380kkm 2026-09-07] ////
    def test_first_enable_creates_source_and_binding_without_changing_host(self) -> None:
        path = self.user / ".agents/skills/writing/SKILL.md"
        before = path.read_bytes()
        documents_before = self.manager.store.snapshot()
        result = self.cards.configure(self.skills["writing"], "enabled")
        self.assertTrue(result["management"]["effectiveEnabled"])
        self.assertEqual(result["management"]["userState"], "enabled")
        added = [document for document in self.manager.store.snapshot() if document not in documents_before]
        self.assertEqual({document["kind"] for document in added}, {"Plugin", "PluginBinding"})
        self.assertEqual(len(added), 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.contexts(self.skills["writing"]), [])

    # //// 独立规则按用户和项目范围选择并恢复继承 [@x380kkm 2026-09-07] ////
    def test_rules_are_independently_enabled_across_scopes(self) -> None:
        first, second = self.rules
        self.configure(self.skills["writing"], "enabled")
        self.configure(first, "enabled")
        self.configure(second, "disabled")
        source_text = next(item["content"] for item in self.items if item["id"] == first)
        self.assertEqual(self.contexts(self.skills["writing"]), [source_text])
        self.configure(first, "disabled", "project")
        self.assertEqual(self.contexts(self.skills["writing"], "project"), [])
        self.configure(first, "inherit", "project")
        self.assertEqual(self.contexts(self.skills["writing"], "project"), [source_text])
        self.assertEqual(self.cards.describe(second)["management"]["userState"], "disabled")

    # //// 一个主题规则可携带独立 Skill 引用并保留完整正文 [@x380kkm 2026-09-07] ////
    def test_rule_can_compose_upstream_skill_without_splitting_its_text(self) -> None:
        imported = self.manager.import_file(str(self.user / ".agents/skills/analysis/SKILL.md"))["document"]
        self.manager.apply_document(self.manager.preview_document(imported)["plan"])
        identity = self.rules[0]
        record = self.manager.read_document(self.cards.describe(identity)["management"]["documentId"])
        document = deepcopy(record["document"])
        text = "## Quoting\nUse shell quoting.\n\n## Encoding\nUse UTF-8.\n"
        document["contributions"][0]["payload"]["text"] = text
        document["contributions"].append({"id": "method", "ref": imported["id"] + "#" + imported["contributions"][0]["id"],
                                           "constraint": imported["release"]["version"]})
        self.manager.apply_document(self.manager.preview_document(document, record["baseline"])["plan"])
        result = self.configure(identity, "enabled")
        self.assertEqual(len(result["management"]["contents"]), 2)
        selected = self.manager.open_content(document["id"] + "#method", "local", scope="user")
        self.assertTrue(any(isinstance(unit["content"], dict) and unit["content"].get("text") == text for unit in selected["units"]))
        self.assertEqual(self.manager.read_document(document_identity(imported))["document"], imported)

    # //// 项目先启用后用户接续时保持来源与引用身份 [@x380kkm 2026-09-07] ////
    def test_project_first_adoption_preserves_subject_identity(self) -> None:
        identity = self.skills["writing"]
        project = self.configure(identity, "enabled", "project")
        user = self.configure(identity, "enabled")
        self.assertEqual(project["subject"]["ref"], user["subject"]["ref"])
        self.configure(identity, "inherit", "project")
        current = self.cards.describe(identity, "project")
        self.assertTrue(current["management"]["effectiveEnabled"])
        self.assertEqual(current["management"]["projectState"], "inherit")
        self.assertEqual(self.manager.catalogs.for_scope("project").diagnostics, [])

    # //// 复用已经登记的 Skill 发布而保持既有引用 [@x380kkm 2026-09-07] ////
    def test_existing_imported_source_is_reused(self) -> None:
        path = self.user / ".agents/skills/writing/SKILL.md"
        imported = self.manager.import_file(str(path))["document"]
        self.manager.apply_document(self.manager.preview_document(imported)["plan"])
        sources_before = [document for document in self.manager.store.snapshot() if document["kind"] == "Plugin"]
        result = self.cards.configure(self.skills["writing"], "enabled")
        self.assertEqual(result["management"]["pluginId"], imported["id"])
        self.assertEqual([document for document in self.manager.store.snapshot() if document["kind"] == "Plugin"], sources_before)

    # //// 启用卡片保留内容读取所需的来源授权 [@x380kkm 2026-09-07] ////
    def test_enable_does_not_grant_source_read_access(self) -> None:
        result = self.cards.configure(self.skills["writing"], "enabled")
        restricted = Manager(self.project, user_root=self.user)
        with self.assertRaises(SourceError):
            restricted.open_content(result["subject"]["ref"], "local", scope="user")

    # //// 目录级规则的正文只进入对应路径范围 [@x380kkm 2026-09-07] ////
    def test_directory_rule_preserves_original_scope(self) -> None:
        path = self.user / "AGENTS.md"
        path.write_text("# Folder\n\n- Use local fixtures.\n", encoding="utf-8")
        rule = self.declare_rule("local-fixtures", "Use local fixtures.", path, "directory")
        self.configure(rule["id"], "enabled")
        method = self.configure(self.skills["writing"], "enabled")["subject"]
        external = self.manager.open_content(method["ref"], "local", context={"path": self.project.as_posix()}, scope="user")
        local = self.manager.open_content(method["ref"], "local", context={"path": self.user.as_posix()}, scope="user")
        self.assertEqual(len(local["units"]), len(external["units"]) + 1)
        self.assertTrue(any(isinstance(unit["content"], dict) and unit["content"].get("text") == rule["content"] for unit in local["units"]))

    # //// 同名但不同文件的能力保持各自身份 [@x380kkm 2026-09-07] ////
    def test_same_named_skills_remain_distinct(self) -> None:
        path = self.user / ".codex/skills/writing/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: writing\n---\nAnother source.\n", encoding="utf-8")
        matches = [item for item in self.cards.inventory()["items"] if item["kind"] == "skill" and item["name"] == "writing"]
        results = [self.cards.configure(item["id"], "enabled") for item in matches]
        self.assertEqual(len({result["subject"]["ref"] for result in results}), 2)

    # //// 两侧查询同一有向关系且只为来源方法提供适配文本 [@x380kkm 2026-09-07] ////
    def test_relation_roundtrip_uses_one_edge_and_one_adapter(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.configure(source, "enabled")
        created = self.cards.set_relation(source, target)["relation"]
        outgoing = self.cards.relations(source)["outgoing"]
        incoming = self.cards.relations(target)["incoming"]
        self.assertEqual(outgoing, incoming)
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(self.contexts(source), ["使用本 skill 时可以参考使用 **analysis** skill"])
        self.assertIsNone(self.cards.describe(target)["management"]["effectiveEnabled"])
        self.configure(target, "enabled")
        self.assertEqual(self.contexts(target), [])
        self.cards.set_relation(source, target, "参考分析步骤.", baseline=created["baseline"])
        self.assertEqual(self.contexts(source), ["参考分析步骤."])
        documents = self.manager.store.snapshot()
        self.assertEqual(sum(value["kind"] == "Plugin" and value["id"].startswith("plugin:adapter/") for value in documents), 1)

    # //// 规则适配文本只随实际选中的规则进入方法读取 [@x380kkm 2026-09-07] ////
    def test_rule_relation_follows_rule_enablement(self) -> None:
        source, target = self.rules[0], self.skills["analysis"]
        self.configure(self.skills["writing"], "enabled")
        self.configure(source, "enabled")
        self.cards.set_relation(source, target, "需要时使用分析方法.")
        self.assertIn("需要时使用分析方法.", self.contexts(self.skills["writing"]))
        self.configure(source, "disabled")
        self.assertEqual(self.contexts(self.skills["writing"]), [])

    # //// 项目适配正文覆盖用户正文并可恢复继承 [@x380kkm 2026-09-07] ////
    def test_relation_project_override_and_disable_preserve_user(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.configure(source, "enabled")
        self.cards.set_relation(source, target, "用户说明.")
        inherited = self.cards.relations(source, "project-local")["outgoing"][0]
        self.assertTrue(inherited["inherited"])
        self.assertIsNone(inherited["baseline"])
        project = self.cards.set_relation(source, target, "项目说明.", "project-local")["relation"]
        self.assertEqual(self.contexts(source, "project-local"), ["项目说明."])
        self.assertEqual(self.contexts(source), ["用户说明."])
        disabled = self.cards.set_relation(source, target, "项目说明.", "project-local", project["baseline"], False)["relation"]
        self.assertEqual(self.contexts(source, "project-local"), [])
        self.assertFalse(self.cards.relations(source, "project-local")["outgoing"][0]["enabled"])
        self.cards.remove_relation(source, target, "project-local", disabled["baseline"])
        self.assertEqual(self.contexts(source, "project-local"), ["用户说明."])

    # //// 并发绑定修改阻止整组正文提交 [@x380kkm 2026-09-07] ////
    def test_relation_binding_race_preserves_adapter_text(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        created = self.cards.set_relation(source, target, "原文.")["relation"]
        binding = created["baseline"]["binding"]
        original_apply = Store.apply_many
        injected = False

        def apply_with_race(store, plans, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                changed = {**binding, "enabled": False}
                original_apply(store, [store.preview_put(changed, binding)])
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", apply_with_race):
            with self.assertRaises(StorageConflictError):
                self.cards.set_relation(source, target, "新正文.", baseline=created["baseline"])
        actual = self.cards.relations(source)["outgoing"][0]
        self.assertEqual(actual["text"], "原文.")
        self.assertFalse(actual["enabled"])

    # //// 来源定义并发变化阻止启用绑定单独落盘 [@x380kkm 2026-09-07] ////
    def test_source_race_prevents_partial_configuration(self) -> None:
        identity = self.skills["writing"]
        created = self.cards.configure(identity, "disabled")
        document = created["configBaseline"]["document"]
        original_apply = Store.apply_many
        injected = False

        def apply_with_race(store, plans, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                changed = deepcopy(document)
                changed["metadata"]["description"] = "External edit."
                original_apply(store, [store.preview_put(changed, document)])
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", apply_with_race):
            with self.assertRaises(StorageConflictError):
                self.cards.configure(identity, "enabled", baseline=created["configBaseline"])
        actual = self.cards.describe(identity)
        self.assertEqual(actual["management"]["userState"], "disabled")
        self.assertEqual(actual["configBaseline"]["document"]["metadata"]["description"], "External edit.")

    # //// 共享在各次提交与结束时保留用户来源的协作修改 [@x380kkm 2026-09-08] ////
    def test_sharing_checks_user_source_at_commit_boundaries(self) -> None:
        identity = self.rules[0]
        self.configure(identity, "enabled")
        user = self.manager.catalogs.user
        original_apply = Store.apply_many
        for boundary in ("private", "project", "complete"):
            with self.subTest(boundary=boundary):
                baseline = self.cards.describe(identity, "project-local")["sharingBaseline"]
                document = baseline["source"]
                changed = deepcopy(document)
                changed["contributions"][0]["payload"]["text"] = "Collaborator content: " + boundary
                before = {scope: store.snapshot() for scope, store in self.manager.catalogs.layers().items() if scope != "user"}
                commits = 0

                def apply_with_source_edit(store, plans, **kwargs):
                    nonlocal commits
                    commits += 1
                    if (boundary, commits) in {("private", 1), ("project", 2)}:
                        original_apply(user, [user.preview_put(changed, document)])
                    result = original_apply(store, plans, **kwargs)
                    if boundary == "complete" and commits == 3:
                        original_apply(user, [user.preview_put(changed, document)])
                    return result

                with patch.object(Store, "apply_many", apply_with_source_edit), self.assertRaises(StorageConflictError):
                    self.cards.set_shared(identity, True, baseline)
                self.assertEqual(next(value for value in user.snapshot() if document_identity(value) == document_identity(document)), changed)
                for scope, documents in before.items():
                    self.assertEqual(self.manager.catalogs.select(scope).snapshot(), documents)
                self.assertFalse(self.manager.catalogs.for_scope("project-local").diagnostics)
                self.assertEqual(self.cards.describe(identity, "project-local")["subject"]["id"], identity)

    # //// 共享期间保留其他卡片的独立用户修改 [@x380kkm 2026-09-08] ////
    def test_sharing_accepts_unrelated_user_change(self) -> None:
        identity, other = self.rules
        document = self.cards.describe(other)["configBaseline"]["source"]
        changed = deepcopy(document)
        changed["contributions"][0]["payload"]["text"] = "Independent collaborator content."
        user = self.manager.catalogs.user
        original_apply = Store.apply_many
        injected = False

        def apply_with_unrelated_edit(store, plans, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                original_apply(user, [user.preview_put(changed, document)])
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", apply_with_unrelated_edit):
            result = self.share(identity)
        self.assertTrue(result["management"]["shared"])
        self.assertEqual(next(value for value in user.snapshot() if document_identity(value) == document_identity(document)), changed)

    # //// 两种勾选顺序都在双端共享时迁移同一关系 [@x380kkm 2026-09-07] ////
    def test_shared_relationship_requires_both_endpoints_in_either_order(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.configure(source, "enabled", "project-local")
        self.configure(target, "disabled", "project-local")
        self.cards.set_relation(source, target, "共同说明.", "project-local")
        for first, second in ((source, target), (target, source)):
            with self.subTest(first=first):
                self.share(first)
                self.assertEqual(self.shared_adapters(), [])
                first_id = self.cards.describe(first)["management"]["pluginId"]
                shared_sources = [value for value in self.manager.catalogs.project.snapshot() if value["kind"] == "Plugin"]
                self.assertEqual([value["id"] for value in shared_sources], [first_id])
                result = self.share(second)
                self.assertEqual(len(self.shared_adapters()), 1)
                relation = self.cards.relations(source, "project-local")["outgoing"][0]
                self.assertEqual(relation["scope"], "project")
                self.assertEqual(relation["text"], "共同说明.")
                self.assertEqual(result["portability"], "local-source-required")
                self.share(first, False)
                self.share(second, False)

    # //// 取消任一端共享保留私人关系并移出共享规则正文 [@x380kkm 2026-09-07] ////
    def test_unsharing_rule_keeps_private_text_out_of_shared_catalog(self) -> None:
        rule, method = self.rules[0], self.skills["analysis"]
        self.configure(rule, "enabled", "project-local")
        self.cards.set_relation(rule, method, "规则专属提示.", "project-local")
        self.share(method)
        rule_text = next(item["content"] for item in self.items if item["id"] == rule)
        self.assertNotIn(rule_text.strip(), self.manager.catalogs.project.catalog.read_text(encoding="utf-8"))
        self.share(rule)
        self.assertEqual(len(self.shared_adapters()), 1)
        self.share(rule, False)
        self.assertEqual(self.shared_adapters(), [])
        rule_id = self.cards.describe(rule)["management"]["pluginId"]
        self.assertFalse(any(value.get("id") == rule_id for value in self.manager.catalogs.project.snapshot()))
        relation = self.cards.relations(rule, "project-local")["outgoing"][0]
        self.assertEqual(relation["scope"], "project-local")
        self.assertEqual(relation["text"], "规则专属提示.")
        self.assertFalse(self.manager.catalogs.project_local.catalog.is_relative_to(self.project))

    # //// 私人启停保持共享文件并可显式发布当前配置 [@x380kkm 2026-09-07] ////
    def test_private_override_keeps_git_content_until_published(self) -> None:
        identity = self.skills["writing"]
        self.configure(identity, "enabled", "project-local")
        self.share(identity)
        before = self.manager.catalogs.project.catalog.read_bytes()
        changed = self.configure(identity, "disabled", "project-local")
        self.assertTrue(changed["management"]["shared"])
        self.assertFalse(changed["management"]["effectiveEnabled"])
        self.assertEqual(self.manager.catalogs.project.catalog.read_bytes(), before)
        result = self.share(identity)
        self.assertEqual(result["management"]["sharedState"], "disabled")
        self.assertEqual(result["management"]["privateState"], "inherit")
        self.assertFalse(result["management"]["effectiveEnabled"])

    # //// 用户关系进入共享时保留用户原文与项目独立版本 [@x380kkm 2026-09-07] ////
    def test_user_relation_materializes_without_changing_user_configuration(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.configure(source, "enabled")
        self.cards.set_relation(source, target, "用户参考说明.")
        before = self.manager.store.catalog.read_bytes()
        self.share(source)
        self.share(target)
        shared = self.shared_adapters()[0]
        self.assertEqual(shared["release"]["version"], "local:project")
        self.assertEqual(shared["contributions"][0]["payload"]["text"], "用户参考说明.")
        self.assertEqual(self.manager.store.catalog.read_bytes(), before)
        self.assertEqual(self.contexts(source, "project-local"), ["用户参考说明."])

    # //// 采用用户级关系保留双端共享归属与用户原文 [@x380kkm 2026-09-07] ////
    def test_adopting_user_relation_keeps_shared_adapter(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.configure(source, "enabled")
        self.cards.set_relation(source, target, "用户参考说明.", enabled=False)
        user_before = self.manager.store.catalog.read_bytes()
        self.share(source)
        self.share(target)
        shared = self.cards.relations(source, "project-local")["outgoing"][0]
        edited = self.cards.set_relation(source, target, "项目独立说明.", "project-local", shared["baseline"])["relation"]
        result = self.cards.remove_relation(source, target, "project-local", edited["baseline"])
        self.assertTrue(result["derivedFromUser"])
        self.assertFalse(result["removed"])
        self.assertEqual(result["scope"], "project")
        restored = self.cards.relations(source, "project-local")["outgoing"][0]
        self.assertEqual(restored["text"], "用户参考说明.")
        self.assertFalse(restored["enabled"])
        self.assertEqual(self.shared_adapters()[0]["contributions"][0]["payload"]["text"], "用户参考说明.")
        self.assertEqual(self.manager.store.catalog.read_bytes(), user_before)

    # //// 从被参考一侧编辑共享说明仍修改同一条关系 [@x380kkm 2026-09-07] ////
    def test_shared_adapter_edit_is_visible_from_both_sides(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.share(source)
        self.share(target)
        self.cards.set_relation(source, target, "共享说明.", "project-local")
        incoming = self.cards.relations(target, "project-local")["incoming"][0]
        self.cards.set_relation(source, target, "修改后的共享说明.", "project-local", incoming["baseline"])
        outgoing = self.cards.relations(source, "project-local")["outgoing"][0]
        self.assertEqual(outgoing, self.cards.relations(target, "project-local")["incoming"][0])
        self.assertEqual(outgoing["text"], "修改后的共享说明.")
        self.assertEqual(self.shared_adapters()[0]["contributions"][0]["payload"]["text"], outgoing["text"])
        self.assertFalse(any(value["kind"] == "Plugin" and value["id"].startswith("plugin:adapter/")
                             for value in self.manager.catalogs.project_local.snapshot()))

    # //// 共享读取基线覆盖相邻卡片的外部修改 [@x380kkm 2026-09-07] ////
    def test_external_shared_edit_invalidates_sharing_baseline(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.cards.set_relation(source, target, "参考说明.", "project-local")
        self.share(source)
        self.share(target)
        baseline = self.cards.describe(source, "project-local")["sharingBaseline"]
        target_plugin = self.cards.describe(target)["management"]["pluginId"]
        store = self.manager.catalogs.project
        binding = next(value for value in store.snapshot() if value["kind"] == "PluginBinding" and value["plugin"]["id"] == target_plugin)
        external = {**binding, "enabled": not binding["enabled"]}
        store.apply(store.preview_put(external, binding))
        before = store.snapshot()
        with self.assertRaises(StorageConflictError):
            self.cards.set_shared(source, False, baseline)
        self.assertEqual(store.snapshot(), before)

    # //// 共享文件写入失败时恢复本操作改动的私人对象 [@x380kkm 2026-09-07] ////
    def test_shared_write_failure_rolls_back_private_staging(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.cards.set_relation(source, target, "保留正文.", "project-local")
        self.share(source)
        private_before = self.manager.catalogs.project_local.snapshot()
        shared_before = self.manager.catalogs.project.snapshot()
        original_apply = Store.apply_many

        def fail_shared(store, plans, **kwargs):
            if store is self.manager.catalogs.project:
                raise StorageIOError("Shared file is unavailable.")
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", fail_shared):
            with self.assertRaises(StorageIOError):
                self.share(target)
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), private_before)
        self.assertEqual(self.manager.catalogs.project.snapshot(), shared_before)
        self.assertEqual(self.cards.relations(source, "project-local")["outgoing"][0]["text"], "保留正文.")

    # //// 回滚遇到私人协作修改时保留正文与恢复记录 [@x380kkm 2026-09-07] ////
    def test_rollback_conflict_preserves_external_private_text(self) -> None:
        source, target = self.skills["writing"], self.skills["analysis"]
        self.cards.set_relation(source, target, "原有正文.", "project-local")
        self.share(source)
        original_apply = Store.apply_many
        private = self.manager.catalogs.project_local

        def fail_after_external_edit(store, plans, **kwargs):
            if store is self.manager.catalogs.project:
                document = next(value for value in private.snapshot() if value["kind"] == "Plugin" and value["id"].startswith("plugin:adapter/"))
                changed = deepcopy(document)
                changed["contributions"][0]["payload"]["text"] = "协作者的私人正文."
                target_plugin = self.cards.describe(target, "project-local")["management"]["pluginId"]
                binding = next(value for value in private.snapshot() if value["kind"] == "PluginBinding" and value["plugin"]["id"] == target_plugin)
                edited_binding = {**binding, "enabled": True}
                original_apply(private, [private.preview_put(changed, document), private.preview_put(edited_binding, binding)])
                raise StorageIOError("Shared file is unavailable.")
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", fail_after_external_edit):
            with self.assertRaises(CardError) as caught:
                self.share(target)
        self.assertEqual(caught.exception.code, "sharing_recovery")
        self.assertTrue(Path(caught.exception.details["recovery"]).is_file())
        self.assertFalse(Path(caught.exception.details["recovery"]).is_relative_to(self.project))
        self.assertEqual(self.cards.relations(source, "project-local")["outgoing"][0]["text"], "协作者的私人正文.")
        self.assertEqual(self.shared_adapters(), [])

    # //// 另一位协作者读取共享卡片而保留原用户私人设置 [@x380kkm 2026-09-07] ////
    def test_another_user_sees_shared_cards_without_private_content(self) -> None:
        shared_rule, private_rule = self.rules
        method = self.skills["analysis"]
        self.configure(shared_rule, "enabled", "project-local")
        self.configure(private_rule, "enabled", "project-local")
        self.configure(method, "enabled", "project-local")
        self.cards.set_relation(shared_rule, method, "共享适配说明.", "project-local")
        self.share(shared_rule)
        self.share(method)
        other_root = self.user.parent / "other-user"
        other_root.mkdir()
        other_manager = Manager(self.project, user_root=other_root)
        other_cards = CardOperations(other_manager.catalogs, other_manager.codex)
        inventory = other_cards.inventory("project-local")
        ids = {item["id"] for item in inventory["items"]}
        self.assertIn(shared_rule, ids)
        self.assertIn(method, ids)
        self.assertNotIn(private_rule, ids)
        self.assertTrue(any(shared_rule in group["itemIds"] for group in inventory["groups"]))
        self.assertEqual(other_cards.describe(shared_rule, "project-local")["subject"]["ref"],
                         self.cards.describe(shared_rule, "project-local")["subject"]["ref"])
        declared_skill = next(item for item in inventory["items"] if item["id"] == method)
        self.assertEqual(declared_skill["availability"], "source-unresolved")
        self.assertNotIn("content", declared_skill)
        skill = other_cards.describe(method, "project-local")["subject"]
        with self.assertRaises(SourceError):
            other_manager.open_content(skill["ref"], skill["version"], scope="project-local")


if __name__ == "__main__":
    unittest.main()
