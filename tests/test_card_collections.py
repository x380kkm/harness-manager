# audience: internal
# # card-collection-tests
"""隔离目录验证集合层级, 独立启用, 编辑基线和项目共享引用边界."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness_manager.card_collections import CARD_IDENTITY_CONTRACT, CollectionError, shared_card_ids
from harness_manager.card_subjects import source_document
from harness_manager.protocol import document_identity, validate_document
from harness_manager.service import Manager
from harness_manager.storage_errors import StorageConflictError, StorageValidationError


# //// 构造有序的通用卡片集合 [@x380kkm 2026-09-07] ////
def collection(item: str = "rule:format") -> dict:
    return {"apiVersion": "manager.x380kkm/v1", "kind": "CardCollection", "id": "collection:writing",
            "metadata": {"name": "Writing Standards"}, "category": "rule", "nodes": [
                {"id": "ladder", "title": "The Ladder", "context": "Placement table.", "children": [
                    {"id": "movement", "title": "Movement", "children": [{"id": "format", "itemId": item}]}]}]}


# //// 在隔离目录中管理声明及展示结构 [@x380kkm 2026-09-07] ////
class CardCollectionTests(unittest.TestCase):
    # //// 准备有独立来源身份的用户规则 [@x380kkm 2026-09-07] ////
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.user, self.project = root / "user", root / "project"
        self.user.mkdir()
        self.project.mkdir()
        self.manager = Manager(self.project, user_root=self.user)
        self.rule = source_document({"id": "rule:format", "kind": "rule", "name": "Unit placement",
                                     "path": str(self.user / ".codex/AGENTS.md"), "scope": "user",
                                     "summary": "Place markers around units.", "content": "//// belongs to code units only."})
        self.save(self.rule)

    # //// 沿用当前目录原文提交声明 [@x380kkm 2026-09-07] ////
    def save(self, document: dict, scope: str = "user") -> dict:
        baseline = next((entry for entry in self.manager.catalogs.select(scope).snapshot()
                         if document_identity(entry) == document_identity(document)), None)
        preview = self.manager.preview_document(document, baseline, scope)
        return self.manager.apply_document(preview["plan"])

    # //// 从卡片共享入口建立项目可见来源 [@x380kkm 2026-09-07] ////
    def share_rule(self) -> None:
        baseline = self.manager.cards.describe("rule:format", "project-local")["sharingBaseline"]
        self.manager.cards.set_shared("rule:format", True, baseline)

    # //// 自定义共享绑定继续支持集合保存和本层启停 [@x380kkm 2026-09-10] ////
    def test_shared_collection_accepts_custom_binding_identity(self):
        self.share_rule()
        previous = next(value for value in self.manager.catalogs.select("project").snapshot()
                        if value["kind"] == "PluginBinding" and value["plugin"]["id"] == self.rule["id"])
        custom = deepcopy(previous)
        custom["id"] = "binding:custom/shared"
        self.save(custom, "project")
        removal = self.manager.preview_remove(previous["id"], previous, "project")
        self.manager.apply_document(removal["plan"])
        self.save(collection(), "project")
        baseline = self.manager.cards.describe("rule:format", "project")["configBaseline"]
        self.manager.cards.configure("rule:format", "disabled", "project", baseline)
        inventory = self.manager.snapshot_cards("project-local")
        self.assertEqual(len(inventory["collections"]), 1)
        self.assertFalse(any(note["code"] == "collection_missing_card" for note in inventory["diagnostics"]))

    # //// 同一发布的多个共享绑定保留各自声明的卡片身份 [@x380kkm 2026-09-10] ////
    def test_shared_card_identities_include_all_bindings(self):
        reference = self.rule["id"] + "#" + self.rule["contributions"][0]["id"]
        bindings = []
        for name in ("codex", "manager"):
            bindings.append({"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": "binding:custom/" + name,
                             "plugin": {"id": self.rule["id"], "constraint": "local"},
                             "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": name}},
                             "extensions": [{"contract": {"id": CARD_IDENTITY_CONTRACT, "range": "^1.0.0"},
                                             "payload": {"itemId": "rule:" + name, "ref": reference}}]})
        self.assertTrue({"rule:codex", "rule:manager"} <= shared_card_ids([self.rule, *bindings]))

    # //// 层级覆盖保留当前层基线与来源发布冲突 [@x380kkm 2026-09-07] ////
    def test_layer_overrides_are_limited_to_collections(self) -> None:
        user = collection()
        self.save(user)
        inherited = self.manager.snapshot_cards("project-local")["collections"][0]
        self.assertEqual(inherited, {"document": user, "scope": "user", "baseline": None})
        self.share_rule()
        shared = deepcopy(user)
        shared["metadata"]["name"] = "Shared writing"
        self.save(shared, "project")
        private = deepcopy(user)
        private["nodes"][0]["title"] = "Personal organization"
        self.save(private, "project-local")
        self.assertEqual(self.manager.snapshot_cards("user")["collections"][0]["document"], user)
        self.assertEqual(self.manager.snapshot_cards("project")["collections"][0]["document"], shared)
        self.assertEqual(self.manager.snapshot_cards("project-local")["collections"][0],
                         {"document": private, "scope": "project-local", "baseline": private})
        changed_rule = deepcopy(self.rule)
        changed_rule["metadata"]["name"] = "Conflicting source"
        self.save(changed_rule, "project-local")
        view = self.manager.catalogs.for_scope("project-local")
        self.assertIn("catalog_identity_conflict", [diagnostic["code"] for diagnostic in view.diagnostics])
        self.assertEqual(next(value for value in view.documents if value["kind"] == "CardCollection"), private)

    # //// 集合排序与说明保持原文并独立于启用状态 [@x380kkm 2026-09-07] ////
    def test_structure_preserves_order_without_enabling_cards(self) -> None:
        document = collection()
        document["nodes"].append({"id": "earlier-title", "title": "A title", "children": []})
        self.save(document)
        snapshot = self.manager.snapshot_cards()
        self.assertEqual(snapshot["collections"][0]["document"], document)
        self.assertIsNone(snapshot["cardManagement"]["rule:format"]["effectiveEnabled"])
        self.assertFalse(any(value["kind"] == "PluginBinding" for value in self.manager.store.snapshot()))
        self.assertEqual(self.manager.discover_content()["candidates"], [])

    # //// 无效节点, 重复身份与循环输入在存储前报错 [@x380kkm 2026-09-07] ////
    def test_invalid_trees_are_rejected(self) -> None:
        missing_children = collection()
        del missing_children["nodes"][0]["children"]
        duplicate_node = collection()
        duplicate_node["nodes"].append({"id": "ladder", "title": "Other", "children": []})
        duplicate_card = collection()
        duplicate_card["nodes"].append({"id": "again", "itemId": "rule:format"})
        for document in (missing_children, duplicate_node, duplicate_card):
            with self.subTest(document=document):
                with self.assertRaises(StorageValidationError):
                    self.manager.preview_document(document)
        circular = collection()
        circular["nodes"][0]["children"].append(circular["nodes"][0])
        with self.assertRaises(ValueError):
            validate_document(circular)

    # //// 集合原文变化使旧编辑计划失效 [@x380kkm 2026-09-07] ////
    def test_stale_collection_save_keeps_current_tree(self) -> None:
        original = collection()
        self.save(original)
        proposed = deepcopy(original)
        proposed["nodes"] = []
        stale = self.manager.preview_document(proposed, original)["plan"]
        current = deepcopy(original)
        current["metadata"]["description"] = "Current structure"
        self.save(current)
        with self.assertRaises(StorageConflictError):
            self.manager.apply_document(stale)
        self.assertEqual(self.manager.read_document(original["id"])["document"], current)

    # //// 私人引用在共享预览与提交时均由当前归属约束 [@x380kkm 2026-09-07] ////
    def test_private_reference_cannot_enter_shared_collection(self) -> None:
        document = collection()
        with self.assertRaises(CollectionError):
            self.manager.preview_document(document, scope="project")
        self.share_rule()
        plan = self.manager.preview_document(document, scope="project")["plan"]
        baseline = self.manager.cards.describe("rule:format", "project-local")["sharingBaseline"]
        self.manager.cards.set_shared("rule:format", False, baseline)
        with self.assertRaises(CollectionError):
            self.manager.apply_document(plan)
        self.assertFalse(any(value["kind"] == "CardCollection" for value in self.manager.catalogs.project.snapshot()))

    # //// 共享集合仍引用卡片时保持共享状态与私人原文 [@x380kkm 2026-09-07] ////
    def test_shared_collection_blocks_unsharing_referenced_card(self) -> None:
        self.share_rule()
        self.save(collection(), "project")
        shared_before = self.manager.catalogs.project.snapshot()
        private_before = self.manager.catalogs.project_local.snapshot()
        baseline = self.manager.cards.describe("rule:format", "project-local")["sharingBaseline"]
        with self.assertRaises(CollectionError) as caught:
            self.manager.cards.set_shared("rule:format", False, baseline)
        self.assertEqual(caught.exception.code, "collection_private_reference")
        self.assertEqual(self.manager.catalogs.project.snapshot(), shared_before)
        self.assertEqual(self.manager.catalogs.project_local.snapshot(), private_before)
        self.assertTrue(self.manager.cards.describe("rule:format", "project-local")["management"]["shared"])
        self.assertNotIn(self.rule["contributions"][0]["payload"]["text"], str(caught.exception))
        self.assertNotIn(str(self.user), str(caught.exception))

    # //// 独立导入的 Skill 通过共享绑定保存稳定卡片身份 [@x380kkm 2026-09-07] ////
    def test_imported_skill_shared_collection_preserves_plugin_definition(self) -> None:
        path = self.user / ".agents/skills/imported/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: imported\ndescription: Inspect source.\n---\nSource procedure.\n", encoding="utf-8")
        manager = Manager(self.project, [self.user], user_root=self.user)
        imported = manager.import_file(str(path))["document"]
        self.save(imported)
        item = next(item for item in manager.snapshot_cards()["items"] if item["kind"] == "skill")
        subject = manager.cards.describe(item["id"], "project-local")
        manager.cards.set_shared(item["id"], True, subject["sharingBaseline"])
        baseline = manager.cards.describe(item["id"], "project-local")["sharingBaseline"]
        manager.cards.set_shared(item["id"], True, baseline)
        document = collection(item["id"])
        document["category"] = "skill"
        self.save(document, "project")
        shared = manager.catalogs.project.snapshot()
        self.assertEqual(next(value for value in shared if value.get("id") == imported["id"]), imported)
        self.assertEqual(manager.catalogs.for_scope("project-local").diagnostics, [])
        binding = next(value for value in shared if value.get("plugin", {}).get("id") == imported["id"])
        identities = [value for value in binding["extensions"] if value["contract"]["id"] == CARD_IDENTITY_CONTRACT]
        self.assertEqual(len(identities), 1)
        identity = identities[0]
        self.assertEqual(identity["payload"], {"itemId": item["id"], "ref": subject["subject"]["ref"]})
        self.assertNotIn(str(self.user), str(identity))
        self.assertNotIn("Source procedure.", str(identity))
        baseline = manager.cards.describe(item["id"], "project-local")["sharingBaseline"]
        with self.assertRaises(CollectionError):
            manager.cards.set_shared(item["id"], False, baseline)
        self.assertEqual(manager.catalogs.project.snapshot(), shared)
        invalid = deepcopy(shared)
        invalid_binding = next(value for value in invalid if value.get("plugin", {}).get("id") == imported["id"])
        for reference in ("plugin:other#missing", imported["id"] + "#missing"):
            invalid_binding["extensions"][0]["payload"]["ref"] = reference
            self.assertNotIn(item["id"], shared_card_ids(invalid))
        collaborator = self.user.parent / "collaborator"
        collaborator.mkdir()
        snapshot = Manager(self.project, user_root=collaborator).snapshot_cards("project-local")
        self.assertIn(item["id"], {entry["id"] for entry in snapshot["items"]})
        self.assertNotIn("collection_missing_card", {entry["code"] for entry in snapshot["diagnostics"]})

    # //// 共享元数据可在来源文件缺失的机器上组织 [@x380kkm 2026-09-07] ////
    def test_shared_metadata_does_not_require_local_source_access(self) -> None:
        self.share_rule()
        self.save(collection(), "project")
        other_user = self.user.parent / "other-user"
        other_user.mkdir()
        other = Manager(self.project, user_root=other_user)
        snapshot = other.snapshot_cards("project-local")
        self.assertEqual(snapshot["collections"][0]["scope"], "project")
        self.assertIn("rule:format", {item["id"] for item in snapshot["items"]})

    # //// 缺失卡片留在集合内并通过诊断呈现 [@x380kkm 2026-09-07] ////
    def test_missing_card_diagnostic_preserves_structure(self) -> None:
        document = collection("rule:missing")
        self.save(document)
        snapshot = self.manager.snapshot_cards()
        self.assertEqual(snapshot["collections"][0]["document"], document)
        self.assertIn("collection_missing_card", [entry["code"] for entry in snapshot["diagnostics"]])
