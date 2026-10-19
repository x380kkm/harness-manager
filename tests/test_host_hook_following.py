# audience: internal
# # host-hook-following-tests
"""隔离用户和项目配置核对原生开关跟随, 结构编辑和并发边界."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import tomlkit

from harness_manager.codex_hook_state import hook_state_key, parse_hook_states
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError
from harness_manager.card_subjects import CardError
from harness_manager.usage import usage_document
from harness_manager.host_storage import HostStorage
from harness_manager.project_hook_relocation import relocate_host_hooks
from harness_manager.host_storage_records import encode_file, decode_file


# //// 在隔离配置中交替操作 Manager 与原生开关 [@x380kkm 2026-09-10] ////
class HookFollowingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.codex = self.user / ".codex"
        self.codex.mkdir(parents=True)
        self.config = self.codex / "config.toml"
        self.config.write_text('# User comment\nmodel = "chosen"\n', encoding="utf-8")
        self.hooks = self.codex / "hooks.json"
        self.groups = [{"hooks": [{"type": "command", "command": name}]} for name in ("first", "second", "third")]
        self.hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        self.manager = Manager(user_root=self.user)

    # //// 模拟 Codex 保存个人开关和原有信任记录 [@x380kkm 2026-09-10] ////
    def native(self, index, enabled, handler=0, path=None, trust=None):
        document = tomlkit.parse(self.config.read_text(encoding="utf-8"))
        hooks = document.setdefault("hooks", tomlkit.table())
        states = hooks.setdefault("state", tomlkit.table())
        key = hook_state_key(path or self.hooks, "Stop", index, handler)
        row = states.setdefault(key, tomlkit.table())
        row["enabled"] = enabled
        if trust is not None:
            row["trusted_hash"] = trust
        self.config.write_text(tomlkit.dumps(document), encoding="utf-8")

    # //// 登记独立 Hook 并通过声明采用现有原生选择 [@x380kkm 2026-09-10] ////
    def adopt(self, index=0, *, scope="user", manager=None):
        manager = manager or self.manager
        point = "hook.x380kkm/lifecycle"
        document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": f"plugin:hook/{index}",
                    "release": {"version": "local"}, "metadata": {"name": f"Hook {index}"},
                    "contributions": [{"id": "hook", "point": point, "contract": {"id": point, "range": "^1.0.0"},
                                       "payload": {"event": "Stop", "handlers": deepcopy(self.groups[index]["hooks"])}}]}
        preview = manager.preview_document(document, scope=scope)
        manager.apply_document(preview["plan"])
        inventory = manager.snapshot_cards(scope)
        identity = next(item["id"] for item in inventory["items"] if item.get("details", {}).get("sourceDeclaration") == document["id"] + "@local")
        binding = usage_document(document, {"state": "enabled"}, None, scope)
        manager.apply_document(manager.preview_document(binding, scope=scope)["plan"])
        result = manager.host.synchronize(scope)
        self.assertIn(result["status"], {"applied", "unchanged"}, result)
        return identity, document

    # //// 从同一窗口基线执行显式启停 [@x380kkm 2026-09-10] ////
    def configure(self, identity, state, *, scope="user", manager=None, baseline=None):
        manager = manager or self.manager
        baseline = baseline or manager.cards.describe(identity, scope)["configBaseline"]
        return manager.invoke("card.configure", {"id": identity, "state": state, "scope": scope, "baseline": baseline})

    # //// 原生关闭与重复开启保持其他命令的身份 [@x380kkm 2026-09-10] ////
    def test_native_and_manager_follow_without_shifting_other_hooks(self):
        for index, enabled in enumerate((False, True, False)):
            self.native(index, enabled, trust=f"trusted-{index}")
        original = self.hooks.read_bytes()
        identity, _ = self.adopt()
        self.assertFalse(self.manager.cards.describe(identity)["management"]["effectiveEnabled"])
        for enabled in (True, False, True):
            result = self.configure(identity, "enabled" if enabled else "disabled")
            self.assertEqual(result["hostSync"]["status"], "applied", result["hostSync"])
            self.assertIs(result["management"]["effectiveEnabled"], enabled)
            self.assertEqual(self.hooks.read_bytes(), original)
            states = parse_hook_states(self.config.read_bytes())
            self.assertTrue(states[hook_state_key(self.hooks, "Stop", 1, 0)]["enabled"])
            self.assertFalse(states[hook_state_key(self.hooks, "Stop", 2, 0)]["enabled"])
            self.assertEqual(states[hook_state_key(self.hooks, "Stop", 1, 0)]["trusted_hash"], "trusted-1")
        self.native(0, False)
        self.assertEqual(self.manager.cards.describe(identity)["management"]["nativeState"], "disabled")
        self.manager.host.synchronize()
        self.assertFalse(self.manager.cards.describe(identity)["management"]["effectiveEnabled"])
        self.assertTrue(self.configure(identity, "enabled")["management"]["effectiveEnabled"])

    # //// 关闭接管时保留原生状态并保存重复启用选择 [@x380kkm 2026-09-10] ////
    def test_repeat_enable_stays_pending_when_takeover_is_off(self):
        identity, _ = self.adopt()
        self.native(0, False)
        self.manager.host.set_enabled(False)
        before = self.config.read_bytes()
        result = self.configure(identity, "enabled")
        self.assertEqual(result["hostSync"]["status"], "disabled")
        self.assertTrue(result["management"]["pendingNativeState"])
        self.assertEqual(self.config.read_bytes(), before)
        result = self.manager.host.set_enabled(True)
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        self.assertTrue(self.manager.cards.describe(identity)["management"]["effectiveEnabled"])
        self.assertNotIn("hookRequests", self.manager.host.storage("user").read_ownership())

    # //// 选项类型改变后重新开启使用当前声明保存排队选择 [@x380kkm 2026-09-10] ////
    def test_repeat_enable_after_option_type_change(self):
        identity, _ = self.adopt()
        self.native(0, False)
        self.manager.host.set_enabled(False)
        binding = self.manager.cards.describe(identity)["configBaseline"]["binding"]
        for flag in (False, 0):
            current = deepcopy(binding)
            current["options"] = {"flags": [flag]}
            self.manager.apply_document(self.manager.preview_document(current, binding)["plan"])
            binding = current

            result = self.configure(identity, "enabled")

            self.assertTrue(result["management"]["pendingNativeState"])
        applied = self.manager.host.set_enabled(True)
        self.assertEqual(applied["hostSync"]["status"], "applied")
        self.assertTrue(self.manager.cards.describe(identity)["management"]["effectiveEnabled"])

    # //// 原生在窗口打开后变化时拒绝旧开关基线 [@x380kkm 2026-09-10] ////
    def test_native_change_rejects_stale_card_baseline(self):
        identity, _ = self.adopt()
        baseline = self.manager.cards.describe(identity)["configBaseline"]
        self.native(0, False)
        before = self.manager.store.snapshot()
        with self.assertRaises(StorageConflictError):
            self.configure(identity, "enabled", baseline=baseline)
        self.assertEqual(self.manager.store.snapshot(), before)

    # //// 原生在预览后变化时拒绝提交并保留外部选择 [@x380kkm 2026-09-10] ////
    def test_native_change_after_preview_rejects_host_apply(self):
        identity, _ = self.adopt()
        baseline = self.manager.cards.describe(identity)["configBaseline"]
        self.manager.cards.configure(identity, "disabled", baseline=baseline)
        plan = self.manager.host.preview()
        self.native(1, False)
        with self.assertRaises(StorageConflictError):
            self.manager.host.apply(plan["planId"])

    # //// 混合组编辑保留被替换处理器的关闭选择 [@x380kkm 2026-09-10] ////
    def test_mixed_handler_edit_preserves_disabled_replacement(self):
        self.groups[0]["hooks"].append({"type": "command", "command": "extra"})
        self.hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        self.native(0, False, trust="old-first")
        self.native(0, True, handler=1, trust="old-extra")
        identity, document = self.adopt()
        self.assertEqual(self.manager.cards.describe(identity)["management"]["nativeState"], "mixed")
        modified = deepcopy(document)
        modified["contributions"][0]["payload"]["handlers"][0]["command"] = "edited"
        preview = self.manager.preview_document(modified, document)
        result = self.manager.invoke("document.apply", {"plan": preview["plan"]})
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        states = parse_hook_states(self.config.read_bytes())
        first = states[hook_state_key(self.hooks, "Stop", 0, 0)]
        self.assertFalse(first["enabled"])
        self.assertNotIn("trusted_hash", first)
        self.assertEqual(states[hook_state_key(self.hooks, "Stop", 0, 1)]["trusted_hash"], "old-extra")

    # //// 编辑采用另一原生组时保持两个原始定义的独立选择 [@x380kkm 2026-09-10] ////
    def test_edit_handoff_keeps_original_native_records_separate(self):
        self.native(0, True, trust="first-trust")
        self.native(1, True, trust="second-trust")
        identity, original = self.adopt()
        self.native(0, False)
        previous = original
        for command in ("second", "edited-again"):
            modified = deepcopy(previous)
            modified["contributions"][0]["payload"]["handlers"][0]["command"] = command
            plan = self.manager.preview_document(modified, previous)["plan"]
            result = self.manager.invoke("document.apply", {"plan": plan})
            self.assertEqual(result["hostSync"]["status"], "applied", result)
            previous = modified
        self.configure(identity, "inherit")
        states = parse_hook_states(self.config.read_bytes())
        self.assertFalse(states[hook_state_key(self.hooks, "Stop", 0, 0)]["enabled"])
        self.assertEqual(states[hook_state_key(self.hooks, "Stop", 0, 0)]["trusted_hash"], "first-trust")
        self.assertTrue(states[hook_state_key(self.hooks, "Stop", 1, 0)]["enabled"])
        self.assertEqual(states[hook_state_key(self.hooks, "Stop", 1, 0)]["trusted_hash"], "second-trust")

    # //// 原生后来确认的信任在编辑和解除管理后保留 [@x380kkm 2026-09-10] ////
    def test_release_restores_later_native_trust(self):
        identity, original = self.adopt()
        self.native(0, True, trust="later-trust")
        self.manager.host.synchronize()
        modified = deepcopy(original)
        modified["contributions"][0]["payload"]["handlers"][0]["command"] = "edited"
        plan = self.manager.preview_document(modified, original)["plan"]
        self.manager.invoke("document.apply", {"plan": plan})
        self.configure(identity, "inherit")
        self.assertEqual(parse_hook_states(self.config.read_bytes())[hook_state_key(self.hooks, "Stop", 0, 0)]["trusted_hash"], "later-trust")

    # //// 解除原始组管理时保留未受管尾部项的原生记录 [@x380kkm 2026-09-10] ////
    def test_release_preserves_unmanaged_native_state(self):
        identity, _ = self.adopt()
        self.native(1, False, trust="second-trust")
        self.native(2, True, trust="third-trust")
        result = self.configure(identity, "inherit")
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        self.assertEqual(json.loads(self.hooks.read_bytes())["hooks"]["Stop"], self.groups)
        self.assertFalse(parse_hook_states(self.config.read_bytes())[hook_state_key(self.hooks, "Stop", 1, 0)]["enabled"])

    # //// 解除管理保留用户后来在原生界面关闭的选择 [@x380kkm 2026-09-10] ////
    def test_release_preserves_later_native_choice(self):
        identity, _ = self.adopt()
        self.native(0, False, trust="later-trust")
        result = self.configure(identity, "inherit")
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        row = parse_hook_states(self.config.read_bytes())[hook_state_key(self.hooks, "Stop", 0, 0)]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["trusted_hash"], "later-trust")

    # //// 原生选择经过普通同步后仍在解除管理时保留 [@x380kkm 2026-09-10] ////
    def test_release_preserves_observed_native_choice(self):
        identity, _ = self.adopt()
        self.native(0, False, trust="later-trust")
        self.manager.host.synchronize()
        self.configure(identity, "inherit")
        row = parse_hook_states(self.config.read_bytes())[hook_state_key(self.hooks, "Stop", 0, 0)]
        self.assertFalse(row["enabled"])

    # //// 较早请求延迟保存时保持较新声明和原生选择 [@x380kkm 2026-09-10] ////
    def test_delayed_request_cannot_replace_newer_choice(self):
        identity, _ = self.adopt()
        self.manager.host.set_enabled(False)
        save = HostStorage.save_hook_requests
        newer = []
        def interleave(storage, updates, **kwargs):
            if not newer:
                newer.append(True)
                self.configure(identity, "disabled")
            return save(storage, updates, **kwargs)
        with patch.object(HostStorage, "save_hook_requests", interleave):
            delayed = self.configure(identity, "enabled")
        self.assertEqual(delayed["hostSync"]["status"], "blocked")
        self.manager.host.set_enabled(True)
        result = self.manager.cards.describe(identity)["management"]
        self.assertEqual(result["userState"], "disabled")
        self.assertFalse(result["nativeEnabled"])

    # //// 继承用户 Hook 的项目页面读取同一原生状态 [@x380kkm 2026-09-10] ////
    def test_project_inherited_hook_stays_at_user_scope(self):
        identity, _ = self.adopt()
        project = self.root / "project"
        project.mkdir()
        manager = Manager(workspace=project, user_root=self.user)
        self.native(0, False)
        detail = manager.cards.describe(identity, "project-local")
        self.assertFalse(detail["management"]["effectiveEnabled"])
        with self.assertRaises(CardError) as failure:
            self.configure(identity, "enabled", scope="project-local", manager=manager)
        self.assertEqual(failure.exception.code, "host_inherited_hook_scope")
        self.assertFalse(manager.cards.describe(identity, "project-local")["management"]["effectiveEnabled"])
        self.configure(identity, "disabled")
        with self.assertRaises(CardError):
            self.configure(identity, "enabled", scope="project-local", manager=manager)
        self.assertEqual(manager.cards.describe(identity, "project-local")["management"]["nativeScope"], "user")

    # //// 项目开关读写用户配置并保留项目配置原文 [@x380kkm 2026-09-10] ////
    def test_project_switch_uses_user_native_state(self):
        project = self.root / "project"
        (project / ".codex").mkdir(parents=True)
        hooks = project / ".codex/hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        config = project / ".codex/config.toml"
        config.write_text('# Project comment\n', encoding="utf-8")
        manager = Manager(workspace=project, user_root=self.user)
        self.native(0, False, path=hooks, trust="project-trust")
        identity, _ = self.adopt(scope="project-local", manager=manager)
        self.assertFalse(manager.cards.describe(identity, "project-local")["management"]["effectiveEnabled"])
        result = self.configure(identity, "enabled", scope="project-local", manager=manager)
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        self.assertTrue(parse_hook_states(self.config.read_bytes())[hook_state_key(hooks, "Stop", 0, 0)]["enabled"])
        self.assertEqual(config.read_text(encoding="utf-8"), '# Project comment\n')

    # //// 共享前保留尚未应用的原生开关选择 [@x380kkm 2026-09-10] ////
    def test_sharing_pending_hook_preserves_request_and_bindings(self):
        project = self.root / "project"
        (project / ".codex").mkdir(parents=True)
        hooks = project / ".codex/hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        manager = Manager(workspace=project, user_root=self.user)
        self.native(0, False, path=hooks)
        identity, _ = self.adopt(scope="project-local", manager=manager)
        manager.host.set_enabled(False, "project-local")
        self.configure(identity, "enabled", scope="project-local", manager=manager)
        detail = manager.cards.describe(identity, "project-local")
        documents = {key: store.snapshot() for key, store in manager.catalogs.layers().items()}
        with self.assertRaisesRegex(CardError, "待处理"):
            manager.cards.set_shared(identity, True, detail["sharingBaseline"])
        self.assertEqual({key: store.snapshot() for key, store in manager.catalogs.layers().items()}, documents)
        self.assertTrue(manager.cards.describe(identity, "project-local")["management"]["pendingNativeState"])
        result = manager.host.set_enabled(True, "project-local")
        self.assertEqual(result["hostSync"]["status"], "applied", result)
        self.assertTrue(manager.cards.describe(identity, "project-local")["management"]["nativeEnabled"])

    # //// 共享检查后到达的开关请求仍在提交边界保留 [@x380kkm 2026-09-10] ////
    def test_pending_request_arriving_during_share_is_preserved(self):
        from harness_manager.card_sharing import apply_sharing

        project = self.root / "project"
        (project / ".codex").mkdir(parents=True)
        hooks = project / ".codex/hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        manager = Manager(workspace=project, user_root=self.user)
        self.native(0, False, path=hooks)
        identity, _ = self.adopt(scope="project-local", manager=manager)
        manager.host.set_enabled(False, "project-local")
        detail = manager.cards.describe(identity, "project-local")
        before = {key: store.snapshot() for key, store in manager.catalogs.layers().items()}

        # //// 在共享事务之前提交明确的原生开启请求 [@x380kkm 2026-09-10] ////
        def request_before_sharing(frame, stages, guard):
            self.configure(identity, "enabled", scope="project-local", manager=manager)
            return apply_sharing(frame, stages, guard)

        with patch("harness_manager.card_sharing.apply_sharing", request_before_sharing):
            with self.assertRaisesRegex(CardError, "待处理"):
                manager.cards.set_shared(identity, True, detail["sharingBaseline"])
        self.assertEqual({key: store.snapshot() for key, store in manager.catalogs.layers().items()}, before)
        self.assertTrue(manager.cards.describe(identity, "project-local")["management"]["pendingNativeState"])

    # //// 项目搬移后的普通应用与恢复沿用原生关闭和信任 [@x380kkm 2026-09-10] ////
    def test_project_relocation_preserves_native_state_and_restore(self):
        old = self.root / "project"
        (old / ".codex").mkdir(parents=True)
        old_hooks = old / ".codex/hooks.json"
        old_hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        manager = Manager(workspace=old, user_root=self.user)
        self.native(0, False, path=old_hooks, trust="project-trust")
        identity, _ = self.adopt(scope="project-local", manager=manager)
        current = self.root / "moved-project"
        old.rename(current)
        relocated = Manager(workspace=current, user_root=self.user)
        plan = relocated.relocation.preview(str(old))
        relocated.relocation.apply(plan["plan"])
        self.assertFalse(relocated.cards.describe(identity, "project-local")["management"]["nativeEnabled"])
        result = relocated.host.synchronize("project-local")
        self.assertEqual(result["status"], "applied", result)
        new_key = hook_state_key(current / ".codex/hooks.json", "Stop", 0, 0)
        row = parse_hook_states(self.config.read_bytes())[new_key]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["trusted_hash"], "project-trust")
        self.configure(identity, "enabled", scope="project-local", manager=relocated)
        restore = relocated.host.preview_restore("initial", "project-local")
        relocated.host.apply(restore["planId"])
        self.assertFalse(parse_hook_states(self.config.read_bytes())[new_key]["enabled"])

    # //// 搬移后恢复受管备份仍读取当前位置的原生选择 [@x380kkm 2026-09-10] ////
    def test_relocated_managed_backup_restores_matching_native_location(self):
        old = self.root / "project"
        (old / ".codex").mkdir(parents=True)
        hooks = old / ".codex/hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": self.groups}}), encoding="utf-8")
        manager = Manager(workspace=old, user_root=self.user)
        self.native(0, False, path=hooks, trust="project-trust")
        identity, _ = self.adopt(scope="project-local", manager=manager)
        restore = manager.host.preview_restore("initial", "project-local")
        manager.host.apply(restore["planId"])
        manager.host.set_enabled(True, "project-local")
        current = self.root / "moved-project"
        old.rename(current)
        relocated = Manager(workspace=current, user_root=self.user)
        plan = relocated.relocation.preview(str(old))
        relocated.relocation.apply(plan["plan"])
        self.assertEqual(relocated.host.synchronize("project-local")["status"], "applied")
        restore = relocated.host.preview_restore("before-restore", "project-local")
        relocated.host.apply(restore["planId"])
        detail = relocated.cards.describe(identity, "project-local")["management"]
        self.assertFalse(detail["nativeEnabled"])
        self.assertTrue(detail["nativeHandlers"][0]["trustRecorded"])
        self.assertEqual(relocated.host.set_enabled(True, "project-local")["hostSync"]["status"], "unchanged")

    # //// 恢复事务两端与请求基线随其快照位置搬移 [@x380kkm 2026-09-10] ////
    def test_relocation_aligns_snapshot_ownership_and_preserves_active_source(self):
        old, current = self.root / "project", self.root / "moved"
        source, target = old / ".codex/hooks.json", current / ".codex/hooks.json"
        key = hook_state_key(source, "Stop", 0, 0)
        row = {"key": key, "enabled": False, "saved": {"enabled": False, "trusted_hash": "kept"}}
        ownership = {"hooks": {}, "hookState": {"path": str(source), "groups": [], "originals": {"group": [row]}},
                     "hookRequests": {"plugin:one#hook": {"native": {"path": str(source), "states": [deepcopy(row)]}}}}
        content = tomlkit.dumps({"hooks": {"state": {key: row["saved"]}}}).encode()
        record = {"kind": "transaction", "before": {"hook-state.toml": encode_file(content)},
                  "after": {"hook-state.toml": encode_file(content)},
                  "beforeOwnership": deepcopy(ownership), "afterOwnership": deepcopy(ownership)}
        moved = relocate_host_hooks(record, old, current)
        new_key = hook_state_key(target, "Stop", 0, 0)
        for field, owner in (("before", "beforeOwnership"), ("after", "afterOwnership")):
            self.assertEqual(parse_hook_states(decode_file(moved[field]["hook-state.toml"]))[new_key], row["saved"])
            self.assertEqual(moved[owner]["hookState"]["path"], str(target))
            self.assertEqual(moved[owner]["hookState"]["originals"]["group"][0]["key"], new_key)
            self.assertEqual(moved[owner]["hookRequests"]["plugin:one#hook"]["native"]["states"][0]["key"], new_key)
        active = relocate_host_hooks({"kind": "state", "ownership": ownership}, old, current)
        self.assertEqual(active["ownership"]["hookState"]["path"], str(source))
        self.assertEqual(record["beforeOwnership"]["hookState"]["path"], str(source))


if __name__ == "__main__":
    unittest.main()
