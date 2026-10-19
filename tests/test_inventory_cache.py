# audience: internal
# # inventory-cache-tests
"""隔离目录核对盘点缓存对文件, 目录和固定入口变化的即时感知."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from harness_manager.codex_inventory import CodexInventory
from harness_manager.codex_inventory_io import InventoryInput
from harness_manager.sources import SourceReader


# //// 在独立用户目录构造可扫描的 Skill 来源 [@x380kkm 2026-09-07] ////
class InventoryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "profile"
        self.codex = self.user / ".codex"
        self.codex.mkdir(parents=True)
        self.skills = self.codex / "skills"
        self.inventory = CodexInventory(self.user)

    # //// 写入测试专用来源并保证父目录存在 [@x380kkm 2026-09-07] ////
    def write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # //// 创建固定大小可观察修订时间的 Skill [@x380kkm 2026-09-07] ////
    def skill(self, name: str, body: str = "Original.") -> Path:
        path = self.skills / name / "SKILL.md"
        self.write(path, f"---\nname: {name}\ndescription: Read source.\n---\n{body}\n")
        return path

    # //// 为隔离测试创建当前系统允许的目录链接 [@x380kkm 2026-09-07] ////
    def link_directory(self, path: Path, target: Path) -> None:
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError as error:
            if os.name != "nt":
                self.skipTest(f"系统提供的链接权限不足: {error.__class__.__name__}")
            command = ("$ErrorActionPreference = 'Stop'\nNew-Item -ItemType Junction -Path '"
                       + str(path).replace("'", "''") + "' -Target '" + str(target).replace("'", "''") + "' | Out-Null")
            subprocess.run(["pwsh", "-NoProfile", "-Command", command], check=True, capture_output=True, text=True, encoding="utf-8")

    # //// 未变化请求复用扫描结果而不复用声明匹配结果 [@x380kkm 2026-09-07] ////
    def test_unchanged_scan_is_cached_and_document_match_is_recomputed(self) -> None:
        path = self.skill("sample")
        with patch.object(self.inventory, "_scan", wraps=self.inventory._scan) as scan:
            first = self.inventory.snapshot([])
            second = self.inventory.snapshot([{"id": "document"}])
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(first["items"], second["items"])
        self.assertIsNot(first, second)
        self.assertTrue(any(item["path"] == str(path) for item in second["items"]))
        second["items"][0]["name"] = "Changed by caller"
        self.assertNotEqual(self.inventory.snapshot([{"id": "document"}])["items"][0]["name"], "Changed by caller")

    # //// 热盘点仅检查元数据而不重复读取正文或解析真实路径 [@x380kkm 2026-09-07] ////
    def test_warm_snapshot_avoids_source_reads_and_resolve(self) -> None:
        self.skill("sample")
        self.inventory.snapshot([])
        with patch("harness_manager.sources.SourceReader.read_file", side_effect=AssertionError("repeated source read")), \
                patch.object(Path, "resolve", side_effect=AssertionError("repeated path resolution")):
            self.assertTrue(self.inventory.snapshot([])["items"])

    # //// 来源声明增减仅重新匹配受管身份 [@x380kkm 2026-09-07] ////
    def test_managed_document_changes_do_not_rescan_disk(self) -> None:
        self.skill("sample")
        item = next(item for item in self.inventory.snapshot([])["items"] if item["kind"] == "skill")
        draft = self.inventory.prepare_import(item["id"])
        with patch.object(self.inventory, "_scan", wraps=self.inventory._scan) as scan:
            managed = self.inventory.snapshot([draft])
            unbound = self.inventory.snapshot([])
        self.assertEqual(scan.call_count, 0)
        self.assertTrue(next(value for value in managed["items"] if value["id"] == item["id"])["managedIds"])
        self.assertFalse(next(value for value in unbound["items"] if value["id"] == item["id"])["managedIds"])

    # //// 重复观察与有效性检查保持首个签名 [@x380kkm 2026-09-07] ////
    def test_observation_and_validation_do_not_advance_baseline(self) -> None:
        path = self.skill("sample")
        state = InventoryInput()
        first = state.observe(path)
        self.write(path, "Changed file.")
        self.assertFalse(state.is_current())
        self.assertFalse(state.is_current())
        self.assertEqual(state.observed[path], first)
        state.observe(path)
        self.assertEqual(state.observed[path], first)
        self.assertFalse(state.is_current())

    # //// 清单读取后变动在缓存完成前触发有界重读 [@x380kkm 2026-09-07] ////
    def test_changed_manifest_during_scan_is_not_paired_with_new_metadata(self) -> None:
        version = self.codex / "plugins/cache/local/sample/1.0"
        manifest = version / ".codex-plugin/plugin.json"
        self.write(manifest, json.dumps({"name": "old-name", "version": "1.0"}))
        original_read = SourceReader.read_file
        mutated = False

        # //// 在已返回旧正文后模拟外部更新 [@x380kkm 2026-09-07] ////
        def read_and_change(reader, path):
            nonlocal mutated
            result = original_read(reader, path)
            if Path(path) == manifest and not mutated:
                mutated = True
                self.write(manifest, json.dumps({"name": "new-name", "version": "1.0"}))
            return result

        with patch.object(SourceReader, "read_file", read_and_change), \
                patch.object(self.inventory, "_scan", wraps=self.inventory._scan) as scan:
            result = self.inventory.snapshot([])
        self.assertEqual(scan.call_count, 2)
        self.assertTrue(any(item["name"].startswith("new-name") for item in result["items"]))
        with patch.object(self.inventory, "_scan", wraps=self.inventory._scan) as scan:
            cached = self.inventory.snapshot([])
        self.assertEqual(scan.call_count, 0)
        self.assertEqual(cached["items"], result["items"])

    # //// 文件修改立即使缓存失效并更新正文摘要 [@x380kkm 2026-09-07] ////
    def test_file_change_is_detected_without_ttl(self) -> None:
        path = self.skill("sample", "Original.")
        initial = self.inventory.snapshot([])
        self.write(path, "---\nname: sample\ndescription: Read source.\n---\nChanged content.\n")
        changed = self.inventory.snapshot([])
        self.assertNotEqual(initial["scannedAt"], changed["scannedAt"])
        item = next(item for item in changed["items"] if item["path"] == str(path))
        self.assertIn("Changed content.", item["content"])

    # //// 新增目录与 Skill 立即进入盘点结果 [@x380kkm 2026-09-07] ////
    def test_directory_change_is_detected(self) -> None:
        self.skill("first")
        self.inventory.snapshot([])
        self.skill("second")
        names = {item["name"] for item in self.inventory.snapshot([])["items"] if item["kind"] == "skill"}
        self.assertEqual(names, {"first", "second"})

    # //// 固定入口从缺失变为存在或被删除时立即更新 [@x380kkm 2026-09-07] ////
    def test_missing_fixed_entries_are_watched(self) -> None:
        self.inventory.snapshot([])
        config = self.codex / "config.toml"
        self.write(config, "model = \"selected\"\n")
        self.assertTrue(any(item["kind"] == "config" for item in self.inventory.snapshot([])["items"]))
        config.unlink()
        self.assertFalse(any(item["kind"] == "config" for item in self.inventory.snapshot([])["items"]))
        self.write(self.codex / "AGENTS.md", "# Rules\nKeep context.\n")
        self.assertTrue(any(item["kind"] == "instruction" for item in self.inventory.snapshot([])["items"]))
        self.write(self.codex / "hooks.json", '{"hooks":{"Stop":[]}}')
        self.assertTrue(any(item["kind"] == "hook" for item in self.inventory.snapshot([])["items"]))

    # //// 同尺寸同修改时间的文件替换仍按目标身份失效 [@x380kkm 2026-09-07] ////
    def test_replaced_file_identity_invalidates_cache(self) -> None:
        path = self.skill("sample", "Original.")
        self.inventory.snapshot([])
        initial = path.stat()
        replacement = path.with_suffix(".replacement")
        self.write(replacement, path.read_text(encoding="utf-8").replace("Original.", "Replaced."))
        os.utime(replacement, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        os.replace(replacement, path)
        item = next(item for item in self.inventory.snapshot([])["items"] if item["kind"] == "skill")
        self.assertIn("Replaced.", item["content"])

    # //// 已存在版本中的缺失插件清单创建后立即被发现 [@x380kkm 2026-09-07] ////
    def test_plugin_manifest_creation_under_existing_version_is_detected(self) -> None:
        version = self.codex / "plugins/cache/local/sample/1.0"
        version.mkdir(parents=True)
        self.inventory.snapshot([])
        self.write(version / ".codex-plugin/plugin.json", json.dumps({"name": "sample", "version": "1.0"}))
        self.write(version / "skills/sample/SKILL.md", "---\nname: plugin-method\ndescription: Read source.\n---\nMethod.\n")
        self.assertTrue(any(item["name"] == "plugin-method" for item in self.inventory.snapshot([])["items"]))

    # //// 手动失效接口跳过元数据比较并重新发现内容 [@x380kkm 2026-09-07] ////
    def test_invalidate_forces_rescan(self) -> None:
        self.skill("sample")
        self.inventory.snapshot([])
        self.inventory.invalidate()
        with patch.object(self.inventory, "_scan", wraps=self.inventory._scan) as scan:
            self.inventory.snapshot([])
        self.assertEqual(scan.call_count, 1)

    # //// 根链接目标变化后按原授权位置重新盘点 [@x380kkm 2026-09-07] ////
    def test_root_redirect_is_detected(self) -> None:
        external = self.root / "external"
        external.mkdir()
        self.write(external / "config.toml", 'model="redirected-value"\n')
        redirected = self.user / "linked-codex"
        self.link_directory(redirected, self.codex)
        inventory = CodexInventory(self.user, redirected)
        inventory.snapshot([])
        redirected.rmdir() if redirected.is_junction() else redirected.unlink()
        self.link_directory(redirected, external)
        snapshot = inventory.snapshot([])
        self.assertTrue(any(note["code"] == "redirected-scan-root" for note in snapshot["diagnostics"]))
        self.assertNotIn("redirected-value", json.dumps(snapshot))
