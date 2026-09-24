# audience: internal
# # relation-binding-tests
"""隔离目录验证关系正文和自定义绑定的启停、继承及共享归属, 独立宿主选择保持原值."""
from copy import deepcopy
import unittest

from harness_manager.card_subjects import CardError
from tests import test_card_operations as fixtures


# //// 在独立卡片来源中维护真实关系绑定 [@x380kkm 2026-09-10] ////
class RelationBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CardOperationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cards, self.manager = self.fixture.cards, self.fixture.manager
        self.source, self.target = self.fixture.skills["writing"], self.fixture.skills["analysis"]
        self.fixture.configure(self.source, "enabled")

    # //// 读取当前宿主实际选择的有向关系 [@x380kkm 2026-09-10] ////
    def edge(self, scope="user") -> dict:
        return self.cards.relations(self.source, scope)["outgoing"][0]

    # //// 将关系绑定改为完整保留原值的自定义身份 [@x380kkm 2026-09-10] ////
    def customize(self, scope) -> dict:
        binding = self.edge(scope)["baseline"]["binding"]
        custom = deepcopy(binding)
        custom.update(id="binding:custom/relation/" + scope, options={"mode": "personal"})
        store = self.manager.catalogs.select(scope)
        store.apply_many([store.preview_remove(binding["id"], binding), store.preview_put(custom)])
        return custom

    # //// 同层自定义关系按返回基线反复开闭并精确移除 [@x380kkm 2026-09-10] ////
    def test_custom_relation_toggle_and_remove_in_user_and_private_layers(self) -> None:
        for scope in ("user", "project-local"):
            with self.subTest(scope=scope):
                self.cards.set_relation(self.source, self.target, "REFERENCE TEXT", scope)
                custom = self.customize(scope)
                edge = self.edge(scope)
                self.assertTrue(edge["enabled"])
                self.assertIn("REFERENCE TEXT", self.fixture.contexts(self.source, scope))
                for enabled in (False, True, False):
                    result = self.cards.set_relation(self.source, self.target, "REFERENCE TEXT", scope, edge["baseline"], enabled)
                    edge = result["relation"]
                    self.assertEqual(edge["baseline"]["binding"], {**custom, "enabled": enabled})
                    self.assertEqual("REFERENCE TEXT" in self.fixture.contexts(self.source, scope), enabled)
                self.cards.remove_relation(self.source, self.target, scope, edge["baseline"])
                self.assertEqual(self.cards.relations(self.source, scope)["outgoing"], [])
                self.assertFalse(any(item.get("plugin", {}).get("id") == custom["plugin"]["id"]
                                     for item in self.manager.catalogs.select(scope).snapshot()))

    # //// 自定义共享绑定保持原身份并能关闭后移回私人层 [@x380kkm 2026-09-10] ////
    def test_shared_relation_edit_preserves_custom_binding(self) -> None:
        self.fixture.share(self.source)
        self.fixture.share(self.target)
        self.cards.set_relation(self.source, self.target, "Shared text.", "project-local")
        custom = self.customize("project")
        edge = self.edge("project-local")
        self.cards.set_relation(self.source, self.target, "Edited text.", "project-local", edge["baseline"], False)
        self.assertEqual(self.edge("project-local")["baseline"]["binding"], {**custom, "enabled": False})
        self.fixture.share(self.source, False)
        private = self.edge("project-local")
        self.assertEqual(private["scope"], "project-local")
        self.assertFalse(private["enabled"])
        self.assertEqual(private["baseline"]["binding"]["options"], custom["options"])
        self.assertFalse(any(item.get("id") == custom["id"] for item in self.manager.catalogs.project.snapshot()))
        self.assertEqual(self.manager.catalogs.for_scope("project-local").diagnostics, [])

    # //// 私人自定义关系随两端共享往返并保留范围和选项 [@x380kkm 2026-09-10] ////
    def test_private_custom_relation_sharing_roundtrip_preserves_controls(self) -> None:
        self.cards.set_relation(self.source, self.target, "Private guidance.", "project-local")
        custom = self.customize("project-local")
        store = self.manager.catalogs.project_local
        scoped = deepcopy(custom)
        scoped["target"]["selector"].update(host="codex", path=".")
        store.apply(store.preview_put(scoped, custom))
        for _ in range(2):
            self.fixture.share(self.target)
            self.fixture.share(self.source)
            edge = self.edge("project-local")
            self.assertEqual(edge["scope"], "project")
            self.assertEqual(edge["baseline"]["binding"]["options"], scoped["options"])
            self.assertEqual(edge["baseline"]["binding"]["target"]["selector"]["host"], "codex")
            self.assertFalse(any(item.get("plugin", {}).get("id") == scoped["plugin"]["id"] for item in store.snapshot()))
            self.fixture.share(self.source, False)
            edge = self.edge("project-local")
            self.assertEqual(edge["scope"], "project-local")
            self.assertEqual(edge["baseline"]["binding"]["options"], scoped["options"])
            self.assertEqual(edge["baseline"]["binding"]["target"]["selector"]["path"], ".")
            self.assertEqual(self.manager.catalogs.for_scope("project-local").diagnostics, [])
        self.cards.remove_relation(self.source, self.target, "project-local", edge["baseline"])
        self.assertEqual(self.cards.relations(self.source, "project-local")["outgoing"], [])

    # //// 当前任务外的唯一关系范围随共享移动并保留继承字段 [@x380kkm 2026-09-10] ////
    def test_task_scoped_relation_moves_with_original_control_fields(self) -> None:
        created = self.cards.set_relation(self.source, self.target, "Review only.", "project-local")["relation"]
        original = created["baseline"]["binding"]
        scoped = deepcopy(original)
        scoped.update(id="binding:custom/review-only", options={"mode": "review"})
        scoped.pop("enabled")
        scoped["target"]["selector"]["task"] = "review"
        private = self.manager.catalogs.project_local
        private.apply_many([private.preview_remove(original["id"], original), private.preview_put(scoped)])
        self.fixture.share(self.target)
        self.fixture.share(self.source)
        plugin = scoped["plugin"]["id"]
        bindings = [value for value in self.manager.catalogs.project.snapshot() if value.get("plugin", {}).get("id") == plugin]
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["options"], scoped["options"])
        self.assertEqual(bindings[0]["target"]["selector"]["task"], "review")
        self.assertNotIn("enabled", bindings[0])
        self.assertFalse(any(value.get("plugin", {}).get("id") == plugin for value in private.snapshot()))
        self.fixture.share(self.source, False)
        bindings = [value for value in private.snapshot() if value.get("plugin", {}).get("id") == plugin]
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["options"], scoped["options"])
        self.assertEqual(bindings[0]["target"]["selector"]["task"], "review")
        self.assertNotIn("enabled", bindings[0])

    # //// 移除原生关系绑定后保留内容宿主的正文与开关 [@x380kkm 2026-09-10] ////
    def test_native_relation_reenable_preserves_manager_binding(self) -> None:
        created = self.cards.set_relation(self.source, self.target, "Shared definition.")["relation"]
        original = created["baseline"]["binding"]
        custom = self.customize("user")
        native, manager = deepcopy(custom), deepcopy(original)
        native["target"]["selector"]["host"] = "codex"
        manager["target"]["selector"]["host"] = "harness-manager"
        store = self.manager.catalogs.user
        store.apply_many([store.preview_put(native, custom), store.preview_put(manager)])
        for _ in range(2):
            edge = self.edge()
            self.cards.remove_relation(self.source, self.target, baseline=edge["baseline"])
            self.assertIn(manager, store.snapshot())
            self.assertIn(created["baseline"]["document"], store.snapshot())
            edge = self.edge()
            result = self.cards.set_relation(self.source, self.target, "Shared definition.", baseline=edge["baseline"], enabled=True)
            self.assertEqual(result["relation"]["baseline"]["binding"]["target"]["selector"]["host"], "codex")
            self.assertIn(manager, store.snapshot())

    # //// 多个同上下文绑定在快捷修改前保持全部原值 [@x380kkm 2026-09-10] ////
    def test_ambiguous_relation_bindings_are_rejected_before_writes(self) -> None:
        created = self.cards.set_relation(self.source, self.target, "Original text.")["relation"]
        duplicate = {**created["baseline"]["binding"], "id": "binding:duplicate/relation"}
        store = self.manager.catalogs.user
        store.apply(store.preview_put(duplicate))
        before = store.snapshot()
        with self.assertRaises(CardError) as raised:
            self.cards.set_relation(self.source, self.target, "Edited text.", baseline=created["baseline"], enabled=False)
        self.assertEqual(raised.exception.code, "card_binding_ambiguous")
        self.assertEqual(store.snapshot(), before)

    # //// 跨层搬移多宿主关系前保留独立绑定和所有配置层 [@x380kkm 2026-09-10] ////
    def test_multi_host_relation_move_requires_binding_choice_before_writes(self) -> None:
        self.fixture.share(self.source)
        self.fixture.share(self.target)
        created = self.cards.set_relation(self.source, self.target, "Shared text.", "project-local")["relation"]
        original = created["baseline"]["binding"]
        native = deepcopy(original)
        native["target"]["selector"]["host"] = "codex"
        manager = deepcopy(native)
        manager["id"] = "binding:manager/relation"
        manager["target"]["selector"]["host"] = "harness-manager"
        store = self.manager.catalogs.project
        store.apply_many([store.preview_put(native, original), store.preview_put(manager)])
        self.fixture.configure(self.source, "disabled", "project")
        before = {scope: value.snapshot() for scope, value in self.manager.catalogs.layers().items()}
        with self.assertRaises(CardError) as raised:
            self.fixture.share(self.source, False)
        self.assertEqual(raised.exception.code, "sharing_relation_binding_ambiguous")
        self.assertEqual({scope: value.snapshot() for scope, value in self.manager.catalogs.layers().items()}, before)

    # //// 当前宿主继承用户关系时保留项目中另一宿主的独立记录 [@x380kkm 2026-09-10] ////
    def test_sharing_move_preserves_other_host_project_relation(self) -> None:
        self.cards.set_relation(self.source, self.target, "User guidance.")
        self.fixture.share(self.source)
        self.fixture.share(self.target)
        edge = self.edge("project-local")
        binding = edge["baseline"]["binding"]
        manager = deepcopy(binding)
        manager["target"]["selector"]["host"] = "harness-manager"
        store = self.manager.catalogs.project
        store.apply(store.preview_put(manager, binding))
        self.assertTrue(self.edge("project-local")["inherited"])
        before = {scope: value.snapshot() for scope, value in self.manager.catalogs.layers().items()}
        with self.assertRaises(CardError) as raised:
            self.fixture.share(self.source, False)
        self.assertEqual(raised.exception.code, "sharing_relation_binding_ambiguous")
        self.assertEqual({scope: value.snapshot() for scope, value in self.manager.catalogs.layers().items()}, before)
