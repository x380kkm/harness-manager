# audience: internal
# # project-relocation-relation-tests
"""来源文件与个人配置位于各用例创建的独立目录中."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.project_relocation import ProjectRelocation, RelocationError
from harness_manager.projection import project_content
from harness_manager.protocol import document_identity
from harness_manager.service import Manager


# //// 在项目个人绑定中保留用户层参考链 [@x380kkm 2026-09-08] ////
class ProjectRelocationRelationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.old, self.current = [root / name for name in ("user", "old", "current")]
        self.user.mkdir()
        self.old.mkdir()
        author = Manager(self.old, [self.old], user_root=self.user)
        self.definitions = {}
        for name in ("source", "target", "detail", "unrelated", "disabled"):
            path = self.old / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {name} method.\n---\n{name} content.\n", encoding="utf-8")
            document = author.import_file(str(path))["document"]
            author.apply_document(author.preview_document(document)["plan"])
            self.definitions[name] = document
        identities = {item["name"]: item["id"] for item in author.snapshot_cards()["items"] if item["kind"] == "skill"}
        self.target_edge = author.cards.set_relation(identities["source"], identities["target"], "Read target.")["relation"]
        self.detail_edge = author.cards.set_relation(identities["target"], identities["detail"], "Read detail.")["relation"]
        self.unrelated_edge = author.cards.set_relation(identities["unrelated"], identities["source"], "Read source.")["relation"]
        author.cards.set_relation(identities["source"], identities["disabled"], "Read disabled.", enabled=False)
        baseline = author.cards.describe(identities["source"], "project-local")["configBaseline"]
        author.cards.configure(identities["source"], "enabled", "project-local", baseline)
        self.old.rename(self.current)
        self.manager = Manager(self.current, [self.current], user_root=self.user)
        self.relocation = ProjectRelocation(self.manager.catalogs)

    # //// 保存关系的使用设置并返回对象基线 [@x380kkm 2026-09-08] ////
    def save_relation_usage(self, settings: dict, baseline: dict | None = None, scope: str = "user") -> dict:
        preview = self.manager.preview_usage(self.target_edge["id"], settings, baseline, scope)
        self.manager.apply_document(preview["plan"])
        return preview["plan"]["after"]

    # //// 取得项目中实际选择的发布身份与诊断 [@x380kkm 2026-09-08] ////
    def projected_plugins(self) -> tuple[set[str], list[dict]]:
        view = self.manager.catalogs.for_scope("project-local")
        entries, diagnostics = project_content(view.documents, self.manager.content_context(None, "project-local"), layers=view.layers)
        return {entry.summary["plugin"] for entry in entries}, diagnostics

    # //// 读取参考链中的完整来源正文 [@x380kkm 2026-09-08] ////
    def assert_relation_content(self) -> None:
        for name, edge in (("target", self.target_edge), ("detail", self.detail_edge)):
            content = self.manager.read_content(edge["targetRef"], edge["targetVersion"], scope="project-local")
            self.assertIn(name + " content.", content["units"][0]["content"])

    # //// 为关系保存由外部契约解释的启用条件 [@x380kkm 2026-09-08] ////
    def save_conditional_relation(self, edge: dict) -> dict:
        document = edge["baseline"]["document"]
        updated = deepcopy(document)
        updated["contributions"][0]["activation"] = {
            "contract": {"id": "external.activation", "range": "*"}, "mode": "conditional",
            "when": {"contract": {"id": "external.condition", "range": "*"}, "expression": True}}
        self.manager.apply_document(self.manager.preview_document(updated, document)["plan"])
        return updated

    # //// 参考链上的来源随项目重连且保持关系方向和开关 [@x380kkm 2026-09-08] ////
    def test_relocation_follows_inherited_relation_chain(self) -> None:
        preview = self.relocation.preview(str(self.old))
        expected = {document_identity(self.definitions[name]) for name in ("source", "target", "detail")}
        self.assertEqual({value["documentId"] for value in preview["sourceCandidates"]}, expected)
        self.relocation.apply(preview["plan"])
        self.assert_relation_content()
        self.assertFalse(preview["externalLocations"]["source"])

    # //// 未重定位的参考目标保留原路径并进入外部来源提示 [@x380kkm 2026-09-08] ////
    def test_unselected_inherited_source_is_reported(self) -> None:
        selected = [document_identity(self.definitions[name]) for name in ("source", "detail")]
        preview = self.relocation.preview(str(self.old), selected)
        self.assertIn(str(self.old / "skills/target"), preview["externalLocations"]["source"])
        self.relocation.apply(preview["plan"])
        target = next(value for value in self.manager.catalogs.user.snapshot()
                      if document_identity(value) == document_identity(self.definitions["target"]))
        self.assertEqual(target, self.definitions["target"])

    # //// 自定义启用绑定保留参考目标的来源迁移与正文读取 [@x380kkm 2026-09-08] ////
    def test_custom_binding_relocates_inherited_sources(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        self.save_relation_usage({"id": binding["id"] + "/custom", "state": "enabled"})
        self.manager.apply_document(self.manager.preview_remove(binding["id"], binding)["plan"])
        preview = self.relocation.preview(str(self.old))
        self.assertEqual({value["name"] for value in preview["sourceCandidates"]}, {"source", "target", "detail"})
        unselected = self.relocation.preview(str(self.old), [document_identity(self.definitions["source"])])
        self.assertIn(str(self.old / "skills/target"), unselected["externalLocations"]["source"])
        self.relocation.apply(preview["plan"])
        self.assert_relation_content()

    # //// 原生与内容宿主的明确关系分别参与项目搬移 [@x380kkm 2026-09-10] ////
    def test_host_specific_relations_relocate_sources(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        for host in ("codex", "harness-manager", "another-host"):
            with self.subTest(host=host):
                binding = self.save_relation_usage({"selector": {"host": host}}, binding)
                preview = self.relocation.preview(str(self.old))
                self.assertEqual({value["name"] for value in preview["sourceCandidates"]}, {"source", "target", "detail"})

    # //// 不同宿主的关系保持独立的可达来源集合 [@x380kkm 2026-09-10] ////
    def test_host_specific_chains_stay_independent(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        self.save_relation_usage({"selector": {"host": "harness-manager"}}, binding)
        binding = self.detail_edge["baseline"]["binding"]
        updated = deepcopy(binding)
        updated["target"]["selector"]["host"] = "codex"
        self.manager.apply_document(self.manager.preview_document(updated, binding)["plan"])

        preview = self.relocation.preview(str(self.old))

        self.assertEqual({value["name"] for value in preview["sourceCandidates"]}, {"source", "target"})

    # //// 其他宿主的不可达条件保持在其独立内容范围 [@x380kkm 2026-09-10] ////
    def test_other_host_unreachable_relation_preserves_relocation(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        self.save_relation_usage({"selector": {"host": "harness-manager", "task": "review"}}, binding)
        private = self.manager.relocation._context(str(self.old))[2]
        binding = next(value for value in private.snapshot() if value["kind"] == "PluginBinding")
        updated = deepcopy(binding)
        updated["target"]["selector"]["host"] = "codex"
        private.apply(private.preview_put(updated, binding))
        private.apply(private.preview_put(self.definitions["source"]))

        preview = self.relocation.preview(str(self.old))

        self.assertEqual([value["name"] for value in preview["sourceCandidates"]], ["source"])

    # //// 其他项目的关系保留用户来源的原始位置 [@x380kkm 2026-09-08] ////
    def test_other_project_relation_preserves_sources(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        self.save_relation_usage({"selector": {"project": (self.current.parent / "other").as_uri()}}, binding)
        preview = self.relocation.preview(str(self.old))
        self.assertEqual([value["name"] for value in preview["sourceCandidates"]], ["source"])
        self.relocation.apply(preview["plan"])
        entries, _ = self.projected_plugins()
        self.assertNotIn(binding["plugin"]["id"], entries)
        for name in ("target", "detail"):
            self.assertIn(self.definitions[name], self.manager.catalogs.user.snapshot())

    # //// 原项目范围的绑定随来源一起重连到当前位置 [@x380kkm 2026-09-08] ////
    def test_old_project_relation_relocates_its_binding(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        binding = self.save_relation_usage({"selector": {"project": self.old.as_uri()}}, binding)
        preview = self.relocation.preview(str(self.old))
        expected = {document_identity(self.definitions[name]) for name in ("source", "target", "detail")}
        self.assertEqual({value["documentId"] for value in preview["sourceCandidates"]}, expected | {binding["id"]})
        self.relocation.apply(preview["plan"])
        self.assert_relation_content()
        actual = next(value for value in self.manager.catalogs.user.snapshot() if value.get("id") == binding["id"])
        self.assertEqual(actual["target"]["selector"]["project"], self.current.as_uri())

    # //// 项目默认与个人覆盖共同决定继承关系的来源集合 [@x380kkm 2026-09-08] ////
    def test_project_binding_overrides_control_inherited_sources(self) -> None:
        plugin = self.target_edge["baseline"]["binding"]["plugin"]["id"]
        self.save_relation_usage({"id": "binding:project/relation-choice", "state": "disabled"}, scope="project")
        preview = self.relocation.preview(str(self.old))
        self.assertEqual([value["name"] for value in preview["sourceCandidates"]], ["source"])
        entries, _ = self.projected_plugins()
        self.assertNotIn(plugin, entries)
        self.save_relation_usage({"id": "binding:project-local/relation-choice", "state": "enabled"}, scope="project-local")
        preview = self.relocation.preview(str(self.old))
        self.assertEqual({value["name"] for value in preview["sourceCandidates"]}, {"source", "target", "detail"})
        self.relocation.apply(preview["plan"])
        self.assert_relation_content()
        entries, _ = self.projected_plugins()
        self.assertIn(plugin, entries)

    # //// 项目成员选择和接纳范围约束继承目标的来源迁移 [@x380kkm 2026-09-08] ////
    def test_relation_member_selection_controls_inherited_sources(self) -> None:
        original = self.save_relation_usage({"id": "binding:project-local/relation-members", "state": "enabled"}, scope="project-local")
        previous = original
        for selection in ({"selection": {"exclude": ["adapter"]}},
                          {"selectionBaseline": {"release": {"version": "local:user"}, "included": []}}):
            with self.subTest(selection=selection):
                updated = {**deepcopy(original), **selection}
                self.manager.apply_document(self.manager.preview_document(updated, previous, "project-local")["plan"])
                previous = updated
                preview = self.relocation.preview(str(self.old))
                self.assertEqual([value["name"] for value in preview["sourceCandidates"]], ["source"])
                entries, _ = self.projected_plugins()
                self.assertNotIn(original["plugin"]["id"], entries)

    # //// 条件未知的可达关系通过搬移冲突公开原始诊断 [@x380kkm 2026-09-08] ////
    def test_unknown_relation_condition_requires_source_resolution(self) -> None:
        document = self.save_conditional_relation(self.target_edge)
        with self.assertRaises(RelocationError) as caught:
            self.relocation.preview(str(self.old))
        self.assertEqual(caught.exception.code, "relocation_conflict")
        diagnostics = caught.exception.details["diagnostics"]
        self.assertIn(("unknown_condition", document["id"] + "#adapter"),
                      {(value["code"], value["subject"]) for value in diagnostics})

    # //// 关系缺少任务上下文时要求明确来源选择的适用范围 [@x380kkm 2026-09-08] ////
    def test_missing_relation_context_requires_source_resolution(self) -> None:
        binding = self.target_edge["baseline"]["binding"]
        self.save_relation_usage({"selector": {"task": "review"}}, binding)
        with self.assertRaises(RelocationError) as caught:
            self.relocation.preview(str(self.old))
        self.assertEqual(caught.exception.code, "relocation_conflict")
        diagnostics = caught.exception.details["diagnostics"]
        self.assertIn(("missing_context", binding["id"]), {(value["code"], value["subject"]) for value in diagnostics})

    # //// 独立用户关系的未知条件保持在其内容诊断中 [@x380kkm 2026-09-08] ////
    def test_unrelated_relation_condition_preserves_project_relocation(self) -> None:
        document = self.save_conditional_relation(self.unrelated_edge)
        preview = self.relocation.preview(str(self.old))
        self.assertEqual({value["name"] for value in preview["sourceCandidates"]}, {"source", "target", "detail"})
        self.relocation.apply(preview["plan"])
        self.assert_relation_content()
        _, diagnostics = self.projected_plugins()
        self.assertIn(("unknown_condition", document["id"] + "#adapter"),
                      {(value["code"], value["subject"]) for value in diagnostics})
