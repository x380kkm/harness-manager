# audience: internal
# # snapshot-source-boundaries
# 本地 Git 与观察文件的实际变化核对固定提交, 来源授权和读取链归属.
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.content import ContentError
from harness_manager.service import Manager
from harness_manager.sources import SourceError
from test_source_boundaries import directory_link
from test_content_snapshots import binding, member, package


# //// 在临时来源中执行固定配置的 Git 操作 [@x380kkm 2026-09-06] ////
def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=disabled-hooks", "-c", "user.name=Harness test",
                             "-c", "user.email=fixture@example.invalid", *arguments],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout.strip()


# //// 通过实际来源核对快照的固定边界 [@x380kkm 2026-09-06] ////
class SnapshotBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.source = self.root / "source"
        self.user.mkdir()
        self.source.mkdir()
        self.entry = self.source / "SKILL.md"
        self.extra = self.source / "extra.md"
        self.entry.write_text("原始方法", encoding="utf-8")
        self.extra.write_text("原始引用", encoding="utf-8")
        self.manager = Manager(read_roots=[self.root], user_root=self.user)

    # //// 登记当前来源与用户级使用关系 [@x380kkm 2026-09-06] ////
    def register(self, source: Path, resolver: str = "path") -> None:
        plugin = package("plugin:source", source, [member("method", "skill.x380kkm/deployment", "SKILL.md", name="source", description="读取方法来源.")])
        if resolver == "git":
            plugin["sources"][0]["source"].update(resolver={"id": "manager.source/git", "range": "^1.0.0"}, constraint="HEAD")
        for document in (plugin, binding("binding:source", plugin, {"user": "current"})):
            self.manager.apply_document(self.manager.preview_document(document)["plan"])

    # //// 读取期间更新 Git 引用仍使用开始时的提交 [@x380kkm 2026-09-06] ////
    def test_git_commit_is_pinned_across_required_units(self) -> None:
        git(self.source, "init")
        git(self.source, "add", "SKILL.md", "extra.md")
        git(self.source, "commit", "-m", "保存来源")
        original = git(self.source, "rev-parse", "HEAD")
        self.register(self.source, "git")
        read = self.manager.reader.read
        calls = []

        # //// 在首次读取后更新同一来源的引用 [@x380kkm 2026-09-06] ////
        def read_and_update(source, entry):
            result = read(source, entry)
            calls.append(source["constraint"])
            if len(calls) == 1:
                self.extra.write_text("更新后的引用", encoding="utf-8")
                git(self.source, "add", "extra.md")
                git(self.source, "commit", "-m", "更新引用")
            return result

        with patch.object(self.manager.reader, "read", side_effect=read_and_update):
            result = self.manager.open_content("plugin:source#method", "1.0.0", resources=["extra.md"])
        self.assertEqual(calls, ["HEAD", original])
        self.assertIn("原始引用", [unit["content"] for unit in result["units"]])
        self.assertNotIn("更新后的引用", [unit["content"] for unit in result["units"]])

    # //// 共享 Git 元数据的访问范围也约束缓存读取 [@x380kkm 2026-09-06] ////
    def test_cached_git_content_rechecks_shared_metadata_authority(self) -> None:
        git(self.source, "init")
        git(self.source, "add", "SKILL.md", "extra.md")
        git(self.source, "commit", "-m", "保存来源")
        checkout = self.root / "checkout"
        git(self.source, "worktree", "add", "--detach", str(checkout), "HEAD")
        self.register(checkout, "git")
        pending = self.manager.open_content("plugin:source#method", "1.0.0", budget=1)
        restricted = Manager(read_roots=[checkout], user_root=self.user)
        with self.assertRaises(SourceError):
            restricted.continue_content(pending["continuation"])

    # //// 缓存续读按当前对象链接重新核对来源授权 [@x380kkm 2026-09-08] ////
    def test_cached_git_content_rechecks_object_links(self) -> None:
        git(self.source, "init")
        git(self.source, "add", "SKILL.md", "extra.md")
        git(self.source, "commit", "-m", "保存来源")
        self.register(self.source, "git")
        pending = self.manager.open_content("plugin:source#method", "1.0.0", budget=1)
        external = self.root / "external-objects"
        external.mkdir()
        link = self.source / ".git/objects/pack/nested"
        self.addCleanup(directory_link(link, external))
        restricted = Manager(read_roots=[self.source], user_root=self.user)
        with self.assertRaisesRegex(SourceError, "超出进程批准"):
            restricted.continue_content(pending["continuation"])

    # //// 读取链连接到另一快照时拒绝累计齐备状态 [@x380kkm 2026-09-06] ////
    def test_foreign_snapshot_in_previous_chain_is_rejected(self) -> None:
        self.register(self.source)
        first = self.manager.open_content("plugin:source#method", "1.0.0", budget=1)
        second = self.manager.open_content("plugin:source#method", "1.0.0", budget=1)
        first["previous"] = second["id"]
        path = self.manager.observations.path_for(first["id"])
        path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ContentError) as caught:
            self.manager.continue_content(first["continuation"])
        self.assertEqual(caught.exception.code, "content_read_chain")

    # //// 读取回执正文变化时拒绝把它计为已提供内容 [@x380kkm 2026-09-06] ////
    def test_changed_receipt_unit_cannot_satisfy_required_content(self) -> None:
        self.register(self.source)
        first = self.manager.open_content("plugin:source#method", "1.0.0", budget=20)
        self.assertTrue(first["units"])
        first["units"][0]["content"] = "另一份正文"
        path = self.manager.observations.path_for(first["id"])
        path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ContentError) as caught:
            self.manager.continue_content(first["continuation"])
        self.assertEqual(caught.exception.code, "content_read_chain")


# //// 运行来源与读取链边界验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
