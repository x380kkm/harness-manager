# audience: internal
# # card-binding-tests
"""隔离目录验证自定义绑定的启停、继承和共享往返, 宿主独立选择保留原始范围与选项."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_operations import CardOperations
from harness_manager.card_subjects import CardError
from harness_manager.service import Manager
from harness_manager.storage import Store
from harness_manager.storage_errors import StorageConflictError


# //// 在隔离 Skill 上操作独立配置层 [@x380kkm 2026-09-10] ////
class CardBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        user, project = root / "user", root / "project"
        project.mkdir()
        path = user / ".agents/skills/writing/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: writing\ndescription: Writing method.\n---\nUse complete sentences.\n", encoding="utf-8")
        self.manager = Manager(project, [user], user_root=user)
        self.cards = CardOperations(self.manager.catalogs, self.manager.codex)
        self.identity = next(item["id"] for item in self.cards.inventory()["items"] if item["kind"] == "skill")

    # //// 沿用返回的最新读取基线修改开关 [@x380kkm 2026-09-10] ////
    def configure(self, state: str, scope: str, current: dict | None = None) -> dict:
        current = current or self.cards.describe(self.identity, scope)
        return self.cards.configure(self.identity, state, scope, current["configBaseline"])

    # //// 将唯一绑定改为保留选项和范围的自定义身份 [@x380kkm 2026-09-10] ////
    def customize(self, scope: str) -> dict:
        store = self.manager.catalogs.select(scope)
        binding = self.cards.describe(self.identity, scope)["configBaseline"]["binding"]
        custom = deepcopy(binding)
        custom.update(id="binding:custom/" + scope, options={"mode": "personal"})
        custom["target"]["selector"]["host"] = "codex"
        store.apply_many([store.preview_remove(binding["id"], binding), store.preview_put(custom)])
        return custom

    # //// 多次启停沿用唯一自定义身份并在继承时移除该绑定 [@x380kkm 2026-09-10] ////
    def test_custom_bindings_toggle_and_inherit_in_each_layer(self) -> None:
        for scope in ("user", "project", "project-local"):
            with self.subTest(scope=scope):
                self.configure("enabled", scope)
                custom = self.customize(scope)
                current = self.cards.describe(self.identity, scope)
                self.assertEqual(current["configBaseline"]["binding"], custom)
                for state in ("disabled", "enabled", "disabled"):
                    previous = current
                    current = self.configure(state, scope, previous)
                    binding = current["configBaseline"]["binding"]
                    self.assertEqual(binding, {**custom, "enabled": state == "enabled"})
                    self.assertEqual(current["management"]["effectiveEnabled"], state == "enabled")
                    with self.assertRaises(StorageConflictError):
                        self.configure("enabled", scope, previous)
                current = self.configure("inherit", scope, current)
                self.assertIsNone(current["configBaseline"]["binding"])
                self.assertFalse(any(item["kind"] == "PluginBinding" for item in self.manager.catalogs.select(scope).snapshot()))

    # //// 自定义共享绑定关闭后保留选项并能返回私人层 [@x380kkm 2026-09-10] ////
    def test_shared_custom_binding_can_disable_and_unshare(self) -> None:
        self.configure("enabled", "user")
        current = self.cards.describe(self.identity, "project-local")
        self.cards.set_shared(self.identity, True, current["sharingBaseline"])
        custom = self.customize("project")
        current = self.configure("disabled", "project")
        self.assertEqual(current["configBaseline"]["binding"], {**custom, "enabled": False})
        current = self.cards.describe(self.identity, "project-local")
        private = self.cards.set_shared(self.identity, False, current["sharingBaseline"])
        self.assertFalse(private["management"]["effectiveEnabled"])
        self.assertEqual(private["configBaseline"]["binding"]["options"], custom["options"])
        self.assertEqual(private["configBaseline"]["binding"]["target"], custom["target"])
        self.assertFalse(any(item["kind"] == "PluginBinding" for item in self.manager.catalogs.project.snapshot()))

    # //// 自定义共享绑定继承用户状态时保留用户原值 [@x380kkm 2026-09-10] ////
    def test_shared_custom_binding_can_inherit_user_state(self) -> None:
        self.configure("enabled", "user")
        user_before = self.manager.catalogs.user.snapshot()
        self.configure("enabled", "project")
        self.customize("project")
        current = self.configure("disabled", "project")
        current = self.configure("inherit", "project", current)
        self.assertIsNone(current["configBaseline"]["binding"])
        self.assertTrue(current["management"]["effectiveEnabled"])
        self.assertEqual(self.manager.catalogs.user.snapshot(), user_before)
        self.assertEqual(self.manager.catalogs.for_scope("project").diagnostics, [])

    # //// 原生与内容读取的宿主绑定分别保持自己的开关 [@x380kkm 2026-09-10] ////
    def test_codex_shortcut_preserves_manager_binding(self) -> None:
        for scope in ("user", "project"):
            with self.subTest(scope=scope):
                initial = self.configure("disabled", scope)["configBaseline"]["binding"]
                native = deepcopy(initial)
                native["target"]["selector"]["host"] = "codex"
                manager = deepcopy(initial)
                manager.update(id="binding:manager/" + scope, enabled=True)
                manager["target"]["selector"]["host"] = "harness-manager"
                store = self.manager.catalogs.select(scope)
                store.apply_many([store.preview_put(native, initial), store.preview_put(manager)])
                current = self.cards.describe(self.identity, scope)
                self.assertEqual(current["configBaseline"]["binding"], native)
                self.assertFalse(current["management"]["effectiveEnabled"])
                for state in ("enabled", "disabled", "inherit"):
                    current = self.configure(state, scope, current)
                    self.assertIn(manager, store.snapshot())
                self.assertIsNone(current["configBaseline"]["binding"])
                self.assertTrue(any(item["kind"] == "Plugin" and item["id"] == native["plugin"]["id"] for item in store.snapshot()))

    # //// 自定义项目范围匹配当前目录并独立于其他宿主的默认身份 [@x380kkm 2026-09-10] ////
    def test_custom_project_scope_excludes_other_host_default(self) -> None:
        initial = self.configure("enabled", "project")["configBaseline"]["binding"]
        custom = self.customize("project")
        native = deepcopy(custom)
        native["target"]["selector"]["path"] = "."
        manager = deepcopy(initial)
        manager["target"]["selector"]["host"] = "harness-manager"
        store = self.manager.catalogs.project
        store.apply_many([store.preview_put(native, custom), store.preview_put(manager)])
        current = self.configure("disabled", "project")
        self.assertEqual(current["configBaseline"]["binding"], {**native, "enabled": False})
        self.assertIn(manager, store.snapshot())
        current = self.configure("inherit", "project", current)
        self.assertTrue(current["management"]["shared"])
        self.assertIn(manager, store.snapshot())
        for state in ("enabled", "disabled", "inherit", "disabled"):
            current = self.configure(state, "project", current)
            self.assertIn(manager, store.snapshot())
            if state != "inherit":
                binding = current["configBaseline"]["binding"]
                self.assertNotEqual(binding["id"], manager["id"])
                self.assertEqual(binding["target"]["selector"]["host"], "codex")
                self.assertEqual(current["management"]["effectiveEnabled"], state == "enabled")

    # //// 用户和私人层的宿主入口占用身份后仍可独立重新开启 [@x380kkm 2026-09-10] ////
    def test_reenable_preserves_other_host_bindings_and_identity_collisions(self) -> None:
        for scope in ("user", "project-local"):
            with self.subTest(scope=scope):
                initial = self.configure("enabled", scope)["configBaseline"]["binding"]
                custom = self.customize(scope)
                manager = deepcopy(initial)
                manager["target"]["selector"]["host"] = "harness-manager"
                occupied = deepcopy(manager)
                occupied["id"] = f"binding:{scope}/codex/{manager['plugin']['id']}"
                occupied["target"]["selector"]["host"] = "other-host"
                store = self.manager.catalogs.select(scope)
                store.apply_many([store.preview_put(manager), store.preview_put(occupied)])
                current = self.configure("inherit", scope)
                self.assertNotIn(custom, store.snapshot())
                for state in ("enabled", "disabled"):
                    current = self.configure(state, scope, current)
                    self.assertIn(manager, store.snapshot())
                    self.assertIn(occupied, store.snapshot())
                    binding = current["configBaseline"]["binding"]
                    self.assertEqual(binding["target"]["selector"]["host"], "codex")
                    self.assertNotIn(binding["id"], {manager["id"], occupied["id"]})

    # //// Hook 连续恢复继承时保留其他宿主的默认绑定 [@x380kkm 2026-09-10] ////
    def test_hook_repeated_inherit_preserves_other_host_default(self) -> None:
        point = "hook.x380kkm/lifecycle"
        document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:hook/repeated",
                    "release": {"version": "local"}, "metadata": {"name": "Repeated hook"},
                    "contributions": [{"id": "hook", "point": point, "contract": {"id": point, "range": "^1.0.0"},
                                       "payload": {"event": "Stop", "handlers": [{"type": "command", "command": "inspect"}]}}]}
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.identity = next(item["id"] for item in self.cards.inventory()["items"] if item["kind"] == "hook")
        initial = self.configure("enabled", "user")["configBaseline"]["binding"]
        native = self.customize("user")
        manager = deepcopy(initial)
        manager["target"]["selector"]["host"] = "harness-manager"
        store = self.manager.catalogs.user
        store.apply(store.preview_put(manager))
        current = self.cards.describe(self.identity)
        for state in ("inherit", "inherit"):
            current = self.configure(state, "user", current)
            self.assertNotIn("hostSync", current)
            self.assertIsNone(current["configBaseline"]["binding"])
            self.assertIn(manager, store.snapshot())
            self.assertNotIn(native, store.snapshot())

    # //// 同一上下文的多个绑定保留原值并提供选择提示 [@x380kkm 2026-09-10] ////
    def test_ambiguous_applicable_bindings_preserve_all_layers(self) -> None:
        current = self.configure("enabled", "project")
        binding = current["configBaseline"]["binding"]
        duplicate = {**binding, "id": "binding:custom/duplicate"}
        store = self.manager.catalogs.project
        store.apply(store.preview_put(duplicate))
        before = {scope: layer.snapshot() for scope, layer in self.manager.catalogs.layers().items()}
        for state in ("enabled", "disabled", "inherit"):
            with self.assertRaises(CardError) as raised:
                self.configure(state, "project", current)
            self.assertEqual(raised.exception.code, "card_binding_ambiguous")
        self.assertEqual({scope: layer.snapshot() for scope, layer in self.manager.catalogs.layers().items()}, before)

    # //// 其他层的混合绑定保留诊断并允许编辑当前用户层 [@x380kkm 2026-09-10] ////
    def test_other_layer_ambiguity_keeps_current_layer_editable(self) -> None:
        current = self.configure("enabled", "project")
        binding = current["configBaseline"]["binding"]
        duplicate = {**binding, "id": "binding:custom/duplicate", "enabled": False}
        store = self.manager.catalogs.project
        store.apply(store.preview_put(duplicate))
        project_before = store.snapshot()
        current = self.cards.describe(self.identity)
        self.assertIsNone(current["management"]["projectState"])
        current = self.configure("disabled", "user", current)
        self.assertEqual(current["management"]["userState"], "disabled")
        self.assertEqual(store.snapshot(), project_before)

    # //// 提交期间新增的同范围绑定使快捷修改保留并发原值 [@x380kkm 2026-09-10] ////
    def test_concurrent_binding_insertion_blocks_shortcut(self) -> None:
        current = self.configure("enabled", "user")
        binding = current["configBaseline"]["binding"]
        duplicate = {**binding, "id": "binding:custom/concurrent"}
        store = self.manager.catalogs.user
        apply_many = Store.apply_many
        inserted = False

        def insert_binding(selected, plans, **kwargs):
            nonlocal inserted
            if selected is store and not inserted:
                inserted = True
                apply_many(store, [store.preview_put(duplicate)])
            return apply_many(selected, plans, **kwargs)

        with patch.object(Store, "apply_many", insert_binding):
            with self.assertRaises(CardError) as raised:
                self.configure("disabled", "user", current)
        self.assertEqual(raised.exception.code, "card_binding_ambiguous")
        self.assertIn(binding, store.snapshot())
        self.assertIn(duplicate, store.snapshot())
