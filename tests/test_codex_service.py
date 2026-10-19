# audience: internal
# # codex-service-boundary
"""隔离用户目录验证本机观察、登记预览和实际存储之间的边界."""
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.protocol import document_identity
from harness_manager.service import Manager
from test_projection import make_plugin


# //// 从本机观察预览并登记来源且保持项目与宿主原文 [@x380kkm 2026-09-06] ////
class CodexServiceTests(unittest.TestCase):
    # //// 一次卡片盘点共用声明读取且保留各层可见范围 [@x380kkm 2026-09-07] ////
    def test_card_inventory_shares_catalog_reads_across_projections(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            user, project = root / "user", root / "project"
            user.mkdir()
            project.mkdir()
            manager = Manager(project, user_root=user)
            layers = manager.catalogs.layers()
            for scope, store in layers.items():
                document = make_plugin(f"plugin:{scope}/methods", names=("analysis", "writing"))
                store.apply(store.preview_put(document))
            expected = set()
            for scope in layers:
                expected.add(f"plugin:{scope}/methods")
                with self.subTest(scope=scope), ExitStack() as stack:
                    reads = [stack.enter_context(patch.object(store, "snapshot", wraps=store.snapshot))
                             for store in layers.values()]
                    result = manager.invoke("card.inventory", {"scope": scope})
                    for read in reads:
                        read.assert_called_once()
                    self.assertEqual({item["details"]["pluginId"] for item in result["items"]
                                      if item["kind"] == "module"}, expected)

    # //// 外部声明编辑进入热盘点且手动刷新重新读取来源 [@x380kkm 2026-09-07] ////
    def test_card_inventory_observes_catalog_edits_and_explicit_refresh(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = Manager(user_root=Path(temporary))
            document = make_plugin(names=("analysis", "writing"))
            manager.store.apply(manager.store.preview_put(document))
            manager.invoke("card.inventory")
            catalog = manager.store.catalog
            data = json.loads(catalog.read_text(encoding="utf-8"))
            data["documents"][0]["metadata"] = {"name": "External change"}
            catalog.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(manager.codex, "_scan", wraps=manager.codex._scan) as scan:
                result = manager.invoke("card.inventory")
                module = next(item for item in result["items"] if item["kind"] == "module")
                self.assertEqual(module["name"], "External change")
                scan.assert_not_called()
                manager.invoke("card.inventory", {"refresh": True})
                scan.assert_called_once()

    # //// 默认聚合查询保留个人同名内容且按来源查询官方组 [@x380kkm 2026-09-07] ////
    def test_group_discovery_keeps_official_origins_explicit(self) -> None:
        with TemporaryDirectory() as temporary:
            user = Path(temporary)
            personal = user / ".agents/skills/shared/SKILL.md"
            builtin = user / ".codex/skills/.system/shared/SKILL.md"
            for file in (personal, builtin):
                file.parent.mkdir(parents=True)
                file.write_text("---\nname: shared\ndescription: A reusable method.\n---\n# Method\n", encoding="utf-8")
            manager = Manager(user_root=user)
            groups = manager.invoke("codex.groups", {"query": "shared"})["groups"]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["origin"], "user")
            self.assertEqual(manager.read_codex(groups[0]["itemIds"][0])["item"]["path"], str(personal))
            official = manager.invoke("codex.groups", {"query": "shared", "origin": "official"})["groups"]
            self.assertEqual(len(official), 1)
            self.assertEqual(official[0]["origin"], "official")
            self.assertFalse((user / ".harness").exists())

    # //// 从原始来源生成用户级登记并保留项目原文 [@x380kkm 2026-09-07] ////
    def test_registration_uses_user_catalog_and_preserves_source(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            user, project = root / "user", root / "project"
            source = user / ".agents/skills/example/SKILL.md"
            source.parent.mkdir(parents=True)
            project.mkdir()
            original = "---\nname: example\ndescription: Scoped task instructions.\n---\n# Method\nRead the selected inputs.\n"
            source.write_text(original, encoding="utf-8", newline="\n")
            (project / "AGENTS.md").write_text("# Project only\nLocal rules.\n", encoding="utf-8")
            manager = Manager(project, user_root=user)
            listing = manager.invoke("codex.list", {"kind": "skill"})
            self.assertEqual(len(listing["items"]), 1)
            item = listing["items"][0]
            self.assertNotIn("content", item)
            self.assertEqual(manager.invoke("codex.read", {"id": item["id"]})["item"]["content"], original)
            draft = manager.invoke("codex.import", {"id": item["id"]})
            plan = manager.invoke("document.preview", {"document": draft["document"], "scope": draft["scope"]})["plan"]
            self.assertEqual(plan["scope"], "user")
            self.assertFalse((user / ".harness").exists())
            manager.invoke("document.apply", {"plan": plan})
            observed = manager.invoke("codex.read", {"id": item["id"]})["item"]
            self.assertEqual(observed["managedIds"], [document_identity(draft["document"])])
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertFalse((project / ".harness").exists())
            self.assertFalse(any(node["path"] == str(project / "AGENTS.md") for node in manager.snapshot_codex()["items"]))

    # //// 用户库损坏时继续展示本机原始内容 [@x380kkm 2026-09-06] ////
    def test_broken_catalog_keeps_inventory_accessible(self) -> None:
        with TemporaryDirectory() as temporary:
            user = Path(temporary)
            (user / ".codex").mkdir()
            (user / ".codex/AGENTS.md").write_text("# Guidance\nKeep source ownership.\n", encoding="utf-8")
            (user / ".harness").mkdir()
            (user / ".harness/catalog.json").write_text("{", encoding="utf-8")
            snapshot = Manager(user_root=user).snapshot_codex()
            self.assertTrue(any(item["kind"] == "instruction" for item in snapshot["items"]))
            self.assertTrue(any(note["code"] == "catalog-unavailable" for note in snapshot["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
