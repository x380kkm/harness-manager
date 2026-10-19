# audience: internal
# # project-hook-request-tests
"""隔离项目搬移核对排队的原生选择与其声明基线, 用户配置仅来自临时夹具."""
from copy import deepcopy
import json
import unittest

from harness_manager.codex_hook_state import hook_state_key, parse_hook_states
from harness_manager.service import Manager
from tests import test_host_hook_following as following


# //// 在临时项目中保存可搬移的待应用开关 [@x380kkm 2026-09-10] ////
class ProjectHookRequestTests(unittest.TestCase):
    # //// 创建原生关闭且具有明确项目绑定的独立 Hook [@x380kkm 2026-09-10] ////
    def prepare(self, scope="project-local"):
        fixture = following.HookFollowingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        old = fixture.root / "project"
        (old / ".codex").mkdir(parents=True)
        hooks = old / ".codex/hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": fixture.groups}}), encoding="utf-8")
        fixture.native(0, False, path=hooks, trust="project-trust")
        manager = Manager(workspace=old, user_root=fixture.user)
        identity, _ = fixture.adopt(scope=scope, manager=manager)
        store = manager.catalogs.select(scope)
        binding = next(value for value in store.snapshot() if value["kind"] == "PluginBinding")
        selected = deepcopy(binding)
        selected["target"]["selector"]["project"] = old.as_uri()
        store.apply(store.preview_put(selected, binding))
        manager.host.set_enabled(False, "project-local")
        fixture.configure(identity, "enabled", scope=scope, manager=manager)
        self.assertTrue(manager.cards.describe(identity, scope)["management"]["pendingNativeState"])
        return fixture, manager, identity, old

    # //// 移动项目文件并通过公开搬移入口重新关联配置 [@x380kkm 2026-09-10] ////
    def move(self, fixture, old, name):
        current = fixture.root / name
        old.rename(current)
        relocated = Manager(workspace=current, user_root=fixture.user)
        relocated.relocation.apply(relocated.relocation.preview(str(old))["plan"])
        return relocated, current

    # //// 项目与私人绑定的排队选择跨多次搬移后保持执行意图 [@x380kkm 2026-09-10] ////
    def test_pending_choice_follows_repeated_moves(self):
        for scope in ("project", "project-local"):
            with self.subTest(scope=scope):
                fixture, manager, identity, old = self.prepare(scope)
                native = manager.cards.describe(identity, scope)["configBaseline"]["native"]
                _, current = self.move(fixture, old, "first")
                relocated, current = self.move(fixture, current, "second")
                description = relocated.cards.describe(identity, scope)
                self.assertTrue(description["management"]["pendingNativeState"])
                self.assertEqual(description["configBaseline"]["native"], native)

                result = relocated.host.set_enabled(True, "project-local")

                self.assertEqual(result["hostSync"]["status"], "applied")
                states = parse_hook_states(fixture.config.read_bytes())
                row = states[hook_state_key(current / ".codex/hooks.json", "Stop", 0, 0)]
                self.assertTrue(row["enabled"])
                self.assertEqual(row["trusted_hash"], "project-trust")

    # //// 恢复点中的有效请求随对应声明和原生快照共同搬移 [@x380kkm 2026-09-10] ////
    def test_restored_pending_choice_uses_relocated_binding(self):
        fixture, manager, identity, old = self.prepare()
        plan = manager.host.preview_restore("initial", "project-local")
        manager.host.apply(plan["planId"])
        relocated, current = self.move(fixture, old, "restored")
        plan = relocated.host.preview_restore("before-restore", "project-local")
        relocated.host.apply(plan["planId"])
        self.assertTrue(relocated.cards.describe(identity, "project-local")["management"]["pendingNativeState"])

        result = relocated.host.set_enabled(True, "project-local")

        self.assertEqual(result["hostSync"]["status"], "applied")
        states = parse_hook_states(fixture.config.read_bytes())
        self.assertTrue(states[hook_state_key(current / ".codex/hooks.json", "Stop", 0, 0)]["enabled"])

    # //// 过时请求与搬移后的绑定相同时仍保持失效 [@x380kkm 2026-09-10] ////
    def test_stale_choice_is_not_revived_by_path_normalization(self):
        fixture, manager, identity, old = self.prepare()
        store = manager.catalogs.project_local
        binding = next(value for value in store.snapshot() if value["kind"] == "PluginBinding")
        current = deepcopy(binding)
        current["target"]["selector"]["project"] = "current"
        store.apply(store.preview_put(current, binding))
        fixture.configure(identity, "enabled", scope="project-local", manager=manager)
        store.apply(store.preview_put(binding, current))
        self.assertIsNone(manager.cards.describe(identity, "project-local")["management"]["pendingNativeState"])
        relocated, current = self.move(fixture, old, "stale")
        self.assertIsNone(relocated.cards.describe(identity, "project-local")["management"]["pendingNativeState"])

        result = relocated.host.set_enabled(True, "project-local")

        self.assertEqual(result["hostSync"]["status"], "applied")
        states = parse_hook_states(fixture.config.read_bytes())
        self.assertFalse(states[hook_state_key(current / ".codex/hooks.json", "Stop", 0, 0)]["enabled"])

    # //// 选项类型改变使原请求在搬移前后保持失效 [@x380kkm 2026-09-10] ////
    def test_stale_choice_is_not_revived_by_option_type_equality(self):
        fixture, manager, identity, old = self.prepare()
        store = manager.catalogs.project_local
        previous = next(value for value in store.snapshot() if value["kind"] == "PluginBinding")
        binding = deepcopy(previous)
        binding["options"] = {"flags": [False]}
        store.apply(store.preview_put(binding, previous))
        fixture.configure(identity, "enabled", scope="project-local", manager=manager)
        current = deepcopy(binding)
        current["options"] = {"flags": [0]}
        store.apply(store.preview_put(current, binding))
        self.assertIsNone(manager.cards.describe(identity, "project-local")["management"]["pendingNativeState"])

        relocated, current = self.move(fixture, old, "changed-options")

        self.assertIsNone(relocated.cards.describe(identity, "project-local")["management"]["pendingNativeState"])
        result = relocated.host.set_enabled(True, "project-local")
        self.assertEqual(result["hostSync"]["status"], "applied")
        states = parse_hook_states(fixture.config.read_bytes())
        self.assertFalse(states[hook_state_key(current / ".codex/hooks.json", "Stop", 0, 0)]["enabled"])

    # //// 用户排队选择保持在其独立宿主存档中 [@x380kkm 2026-09-10] ////
    def test_project_move_preserves_user_pending_choice(self):
        fixture, _, _, old = self.prepare()
        fixture.native(1, False)
        identity, _ = fixture.adopt(1)
        fixture.manager.host.set_enabled(False)
        fixture.configure(identity, "enabled")
        before = fixture.manager.host.storage("user").read_ownership()

        relocated, _ = self.move(fixture, old, "with-user")

        self.assertEqual(relocated.host.storage("user").read_ownership(), before)
        self.assertTrue(relocated.cards.describe(identity)["management"]["pendingNativeState"])

    # //// 首次投放前的排队请求跨项目搬移后创建原生定义 [@x380kkm 2026-09-10] ////
    def test_new_hook_pending_choice_follows_move(self):
        fixture = following.HookFollowingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        old = fixture.root / "project"
        old.mkdir()
        manager = Manager(workspace=old, user_root=fixture.user)
        manager.host.set_enabled(False, "project-local")
        point = "hook.x380kkm/lifecycle"
        document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:new-hook",
                    "release": {"version": "local"}, "contributions": [
                        {"id": "hook", "point": point, "contract": {"id": point, "range": "^1.0.0"},
                         "payload": {"event": "Stop", "handlers": fixture.groups[0]["hooks"]}}]}
        manager.apply_document(manager.preview_document(document, scope="project-local")["plan"])
        identity = next(item["id"] for item in manager.snapshot_cards("project-local")["items"]
                        if item.get("details", {}).get("sourceDeclaration") == "plugin:new-hook@local")
        fixture.configure(identity, "enabled", scope="project-local", manager=manager)
        relocated, current = self.move(fixture, old, "new-hook")
        self.assertTrue(relocated.cards.describe(identity, "project-local")["management"]["pendingNativeState"])

        result = relocated.host.set_enabled(True, "project-local")

        self.assertEqual(result["hostSync"]["status"], "applied")
        self.assertEqual(json.loads((current / ".codex/hooks.json").read_text(encoding="utf-8"))["hooks"]["Stop"],
                         [fixture.groups[0]])


if __name__ == "__main__":
    unittest.main()
