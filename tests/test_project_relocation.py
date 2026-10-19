# audience: internal
# # project-relocation-tests
"""隔离项目搬移验证私人覆盖, 来源位置, 宿主归属和跨目录失败恢复."""
from copy import deepcopy
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.card_subjects import CardError, source_document, source_record
from harness_manager.project_relocation import ProjectRelocation, RelocationError, project_host
from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from harness_manager.storage import Store
from harness_manager.storage_errors import StorageConflictError, StorageIOError


# //// 在两个项目位置验证明确搬移与独立用户来源 [@x380kkm 2026-09-08] ////
class ProjectRelocationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.old, self.current = [self.root / name for name in ("user", "old", "current")]
        self.user.mkdir()
        self.old.mkdir()
        self.skill_path = self.old / "skills/shell/SKILL.md"
        self.skill_path.parent.mkdir(parents=True)
        self.skill_path.write_text("---\nname: shell\ndescription: Project shell method.\n---\nUse pwsh.\n", encoding="utf-8")
        (self.old / "AGENTS.md").write_text("# Workspace\nPreserve local conventions.\n", encoding="utf-8")
        self.author = Manager(self.old, [self.old], user_root=self.user)
        self.skill = self.author.import_file(str(self.skill_path))["document"]
        self.save(self.author, self.skill)
        self.skill_id = next(item["id"] for item in self.author.snapshot_cards()["items"] if item["kind"] == "skill")
        self.rule = source_document({"id": "rule:local", "name": "Workspace rule", "kind": "rule", "summary": "Project policy.",
                                     "content": "# Workspace\nPreserve local conventions.\n", "scope": "directory", "path": str(self.old / "AGENTS.md")})
        self.save(self.author, self.rule)
        for identity in (self.skill_id, "rule:local"):
            self.configure(self.author, identity, "enabled")
            self.author.cards.set_shared(identity, True, self.author.cards.describe(identity, "project-local")["sharingBaseline"])
        self.author.cards.set_relation("rule:local", self.skill_id, "Read the selected method.", "project-local")
        self.configure(self.author, "rule:local", "disabled")
        self.author.host.initialize("project-local")
        self.author.host.synchronize("project-local")
        self.old_private = self.author.catalogs.project_local
        self.old_host = self.author.host.storage("project-local")
        self.before_private = self.old_private.snapshot()
        self.before_host = self.old_host.store.snapshot()
        self.old.rename(self.current)
        self.manager = Manager(self.current, [self.current], user_root=self.user)
        self.relocation = ProjectRelocation(self.manager.catalogs)

    # //// 保存隔离目录中的完整声明 [@x380kkm 2026-09-08] ////
    def save(self, manager, document, scope="user", baseline=None):
        manager.apply_document(manager.preview_document(document, baseline, scope)["plan"])

    # //// 使用读取基线修改个人项目开关 [@x380kkm 2026-09-08] ////
    def configure(self, manager, identity, state):
        baseline = manager.cards.describe(identity, "project-local")["configBaseline"]
        return manager.cards.configure(identity, state, "project-local", baseline)

    # //// 搬移重连私人覆盖和宿主归属并保留旧记录 [@x380kkm 2026-09-08] ////
    def test_relocation_restores_private_overrides_sources_and_host_records(self):
        native = {path.relative_to(self.current): path.read_bytes() for path in self.current.rglob("*") if path.is_file() and ".harness" not in path.parts}
        prepared = self.relocation.preview(str(self.old))
        self.assertTrue(any(value["documentId"] == document_identity(self.skill) for value in prepared["sourceCandidates"]))
        result = self.relocation.apply(prepared["plan"])
        self.assertTrue(result["changed"])
        self.assertFalse(result["hostFilesChanged"])
        self.assertEqual(result["hostApplyRequired"], ["project-local", "user"])
        description = self.manager.cards.describe("rule:local", "project-local")
        self.assertFalse(description["management"]["effectiveEnabled"])
        subject = self.manager.cards.describe(self.skill_id, "project-local")["subject"]
        content = self.manager.read_content(subject["ref"], subject["version"], scope="project-local")
        self.assertIn("Use pwsh", content["units"][0]["content"])
        self.assertEqual(self.old_private.snapshot(), self.before_private)
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)
        current_host = self.manager.host.storage("project-local")
        self.assertEqual(current_host.store.snapshot(), [{**record, **current_host.context} for record in self.before_host])
        self.assertEqual({path: (self.current / path).read_bytes() for path in native}, native)
        self.assertFalse(self.manager.catalogs.for_scope("project-local").diagnostics)

    # //// Windows 旧目录拼写差异保持私人覆盖与宿主存档的共同归属 [@x380kkm 2026-09-08] ////
    @unittest.skipUnless(os.name == "nt", "Windows 路径使用大小写等价语义.")
    def test_public_relocation_matches_old_workspace_casing(self):
        old_workspace = str(self.old.with_name(self.old.name.upper()))
        prepared = self.manager.invoke("project.relocate_preview", {"old_workspace": old_workspace})
        self.assertEqual(prepared["plan"]["oldWorkspace"], str(self.old))
        self.assertEqual(len(prepared["plan"]["oldHost"]["records"]), len(self.before_host))
        self.manager.invoke("project.relocate_apply", {"plan": prepared["plan"]})
        description = self.manager.cards.describe("rule:local", "project-local")
        self.assertFalse(description["management"]["effectiveEnabled"])
        current_host = self.manager.host.storage("project-local")
        self.assertEqual(current_host.store.snapshot(), [{**record, **current_host.context} for record in self.before_host])
        self.assertEqual(self.old_private.snapshot(), self.before_private)
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)

    # //// 旧目录的多个宿主存档阻止重连并保留双方内容 [@x380kkm 2026-09-08] ////
    @unittest.skipUnless(os.name == "nt", "Windows 路径使用大小写等价语义.")
    def test_public_relocation_rejects_ambiguous_host_casing(self):
        alternative = project_host(self.user, self.old.with_name(self.old.name.title()))
        documents = [{**record, **alternative.context} for record in self.before_host]
        for document in documents:
            alternative.store.apply(alternative.store.preview_put(document))
        old_workspace = str(self.old.with_name(self.old.name.upper()))
        with self.assertRaises(RelocationError) as caught:
            self.manager.invoke("project.relocate_preview", {"old_workspace": old_workspace})
        self.assertEqual(caught.exception.code, "relocation_host_conflict")
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])
        self.assertEqual(alternative.store.snapshot(), documents)
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)

    # //// 共享目录范围和来源记录随项目位置解析 [@x380kkm 2026-09-08] ////
    def test_shared_relative_receipts_and_scope_work_for_another_user(self):
        shared_rule = next(value for value in self.manager.catalogs.project.snapshot() if value["id"] == self.rule["id"])
        self.assertEqual(source_record(shared_rule)["path"], "AGENTS.md")
        self.assertEqual(shared_rule["contributions"][0]["scope"]["selector"]["path"], ".")
        other_root = self.root / "other"
        other_root.mkdir()
        other = Manager(self.current, [self.current], user_root=other_root)
        self.assertTrue(other.cards.describe("rule:local", "project-local")["management"]["effectiveEnabled"])
        candidates = other.discover_content(scope="project-local")["candidates"]
        self.assertIn("Workspace rule", {candidate["name"] for candidate in candidates})
        self.assertFalse(other.catalogs.for_scope("project-local").diagnostics)
        self.assertEqual(other.cards.relations("rule:local", "project-local")["outgoing"][0]["to"], self.skill_id)

    # //// 新位置的个人覆盖冲突保持双方原文 [@x380kkm 2026-09-08] ////
    def test_existing_private_conflict_blocks_copy(self):
        original = next(value for value in self.before_private if value["kind"] == "PluginBinding" and value["plugin"]["id"] == self.rule["id"])
        conflicting = deepcopy(original)
        conflicting["enabled"] = True
        store = self.manager.catalogs.project_local
        store.apply(store.preview_put(conflicting))
        with self.assertRaises(RelocationError) as caught:
            self.relocation.preview(str(self.old))
        self.assertEqual(caught.exception.code, "relocation_conflict")
        self.assertEqual(store.snapshot(), [conflicting])
        self.assertEqual(self.old_private.snapshot(), self.before_private)

    # //// 晚段写入失败回滚来源与个人目录 [@x380kkm 2026-09-08] ////
    def test_host_record_write_failure_rolls_back_prior_stages(self):
        prepared = self.relocation.preview(str(self.old))
        before = {scope: store.snapshot() for scope, store in self.manager.catalogs.layers().items()}
        current_host = self.manager.host.storage("project-local").store
        original_apply = Store.apply_many

        # //// 在宿主存档提交入口注入文件错误 [@x380kkm 2026-09-08] ////
        def fail_host(store, plans, **kwargs):
            if store.catalog == current_host.catalog:
                raise StorageIOError("Host archive unavailable.")
            return original_apply(store, plans, **kwargs)

        with patch.object(Store, "apply_many", fail_host), self.assertRaises(StorageIOError):
            self.relocation.apply(prepared["plan"])
        self.assertEqual({scope: store.snapshot() for scope, store in self.manager.catalogs.layers().items()}, before)
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)
        self.assertEqual(current_host.snapshot(), [])

    # //// 来源更新使预览失效且不写入新目录 [@x380kkm 2026-09-08] ////
    def test_source_baseline_changes_reject_preview(self):
        prepared = self.relocation.preview(str(self.old))
        changed = deepcopy(self.skill)
        changed["metadata"]["description"] = "Another edit."
        self.save(self.manager, changed, baseline=self.skill)
        with self.assertRaises((StorageConflictError, RelocationError)):
            self.relocation.apply(prepared["plan"])
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])
        self.assertEqual(self.old_private.snapshot(), self.before_private)


    # //// 跨进程计划隔离私密宿主正文并保持恢复内容 [@x380kkm 2026-09-08] ////
    def test_public_plan_preserves_private_host_records_without_exposing_them(self):
        secret = "private-host-credential"
        encoded = base64.b64encode(secret.encode()).decode()
        initial = next(value for value in self.before_host if value["id"] == "initial")
        updated = deepcopy(initial)
        updated["files"]["config.toml"] = encoded
        self.old_host.store.apply(self.old_host.store.preview_put(updated, initial))
        prepared = self.relocation.preview(str(self.old))
        serialized = json.dumps(prepared)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(encoded, serialized)
        self.assertNotIn("documents", prepared["plan"]["oldHost"])
        saved = self.old_host.store.snapshot()
        ProjectRelocation(self.manager.catalogs).apply(json.loads(serialized)["plan"])
        current_host = self.manager.host.storage("project-local")
        self.assertEqual(current_host.store.snapshot(), [{**value, **current_host.context} for value in saved])
        self.assertEqual(self.old_host.store.snapshot(), saved)

    # //// 私密备份变化使已确认的重连计划失效 [@x380kkm 2026-09-08] ////
    def test_private_host_change_invalidates_public_plan(self):
        prepared = self.relocation.preview(str(self.old))
        initial = next(value for value in self.before_host if value["id"] == "initial")
        updated = deepcopy(initial)
        updated["files"]["config.toml"] = base64.b64encode(b"external-backup-edit").decode()
        self.old_host.store.apply(self.old_host.store.preview_put(updated, initial))
        with self.assertRaises(StorageConflictError):
            self.relocation.apply(prepared["plan"])
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])
        self.assertIn(updated, self.old_host.store.snapshot())

    # //// 恢复记录无法落盘时仍向调用者隔离宿主私密正文 [@x380kkm 2026-09-08] ////
    def test_recovery_error_exposes_locations_without_host_record_contents(self):
        prepared = self.relocation.preview(str(self.old))
        failure = CardError("sharing_recovery", "Concurrent edit.")
        failure.details = {"recoveryError": "Recovery file unavailable.",
                           "stages": [{"scope": "host", "before": {"initial": None}, "after": {"initial": self.before_host[0]}}]}
        with patch("harness_manager.project_relocation.apply_sharing", side_effect=failure), self.assertRaises(RelocationError) as caught:
            self.relocation.apply(prepared["plan"])
        details = caught.exception.details
        self.assertEqual(caught.exception.code, "relocation_recovery")
        self.assertEqual(details["stages"][0]["recordIds"], ["initial"])
        self.assertNotIn("before", details["stages"][0])
        self.assertNotIn("after", details["stages"][0])
        self.assertIn(str(self.old_host.store.catalog), details["preserved"])
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)

    # //// 已有目标恢复点保持独立并阻止覆盖 [@x380kkm 2026-09-08] ////
    def test_existing_current_host_archive_blocks_copy(self):
        current_host = self.manager.host.storage("project-local")
        current_host.initialize()
        preserved = current_host.store.snapshot()
        with self.assertRaises(RelocationError) as caught:
            self.relocation.preview(str(self.old))
        self.assertEqual(caught.exception.code, "relocation_host_conflict")
        self.assertEqual(current_host.store.snapshot(), preserved)
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])

    # //// 新旧位置的恢复中事务阻止迁移提交 [@x380kkm 2026-09-08] ////
    def test_pending_host_recovery_blocks_both_locations(self):
        transaction = {**self.old_host.context, "id": "transaction", "kind": "transaction", "status": "pending",
                       "createdAt": "2026-09-08T00:00:00Z", "reason": "apply",
                       "before": {"AGENTS.override.md": None}, "after": {"AGENTS.override.md": None}}
        current_host = self.manager.host.storage("project-local")
        for host in (self.old_host, current_host):
            with self.subTest(location=host.target_root):
                before = next((value for value in host.store.snapshot() if value["id"] == "transaction"), None)
                pending = {**deepcopy(transaction), **host.context, "status": "pending"}
                host.store.apply(host.store.preview_put(pending, before))
                with self.assertRaises(RelocationError) as caught:
                    self.relocation.preview(str(self.old))
                self.assertEqual(caught.exception.code, "relocation_recovery")
                self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])
                plan = host.store.preview_put(before, pending) if before else host.store.preview_remove("transaction", pending)
                host.store.apply(plan)

    # //// 预览后的新增用户声明保留原文并阻止过期计划 [@x380kkm 2026-09-08] ////
    def test_new_user_document_invalidates_preview(self):
        prepared = self.relocation.preview(str(self.old))
        external = source_document({"id": "rule:other", "name": "Other", "kind": "rule", "summary": "Other rule.",
                                    "content": "Other content.", "scope": "user", "path": str(self.user / "AGENTS.md")})
        self.save(self.manager, external)
        with self.assertRaises(StorageConflictError):
            self.relocation.apply(prepared["plan"])
        self.assertIn(external, self.manager.catalogs.user.snapshot())
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), [])

    # //// 最后一次保存期间的外部修改触发补偿并保留外部内容 [@x380kkm 2026-09-08] ////
    def test_final_verification_rolls_back_writes_and_preserves_external_edit(self):
        prepared = self.relocation.preview(str(self.old))
        before = {scope: store.snapshot() for scope, store in self.manager.catalogs.layers().items()}
        external = source_document({"id": "rule:other", "name": "Other", "kind": "rule", "summary": "Other rule.",
                                    "content": "Other content.", "scope": "user", "path": str(self.user / "AGENTS.md")})
        current_host = self.manager.host.storage("project-local").store
        original_apply = Store.apply_many
        injected = False

        # //// 在宿主存档提交后保存独立用户对象 [@x380kkm 2026-09-08] ////
        def write_external(store, plans, **kwargs):
            nonlocal injected
            result = original_apply(store, plans, **kwargs)
            if store.catalog == current_host.catalog and not injected:
                injected = True
                user = self.manager.catalogs.user
                original_apply(user, [user.preview_put(external)])
            return result

        with patch.object(Store, "apply_many", write_external), self.assertRaises(StorageConflictError):
            self.relocation.apply(prepared["plan"])
        self.assertEqual(self.manager.catalogs.user.snapshot(), [*before["user"], external])
        self.assertEqual(self.manager.catalogs.project.snapshot(), before["project"])
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), before["project-local"])
        self.assertEqual(current_host.snapshot(), [])
        self.assertEqual(self.old_host.store.snapshot(), self.before_host)


if __name__ == "__main__":
    unittest.main()
