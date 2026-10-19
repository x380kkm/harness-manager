# audience: internal
# # source-file-races-tests
"""临时目录链接和打开句柄核对正文读取的授权边界."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.sources import SourceError, SourceReader
from test_source_boundaries import directory_link


# //// 核对来源在打开和读取期间发生变化的结果 [@x380kkm 2026-09-10] ////
class SourceFileRaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.approved = self.root / "approved"
        self.source = self.approved / "source"
        self.outside = self.root / "outside"
        self.source.mkdir(parents=True)
        self.outside.mkdir()
        (self.source / "text.md").write_text("approved", encoding="utf-8")
        (self.outside / "text.md").write_text("outside", encoding="utf-8")
        self.reader = SourceReader(self.approved, [])

    # //// 授权后替换父目录链接时拒绝目录外的打开句柄 [@x380kkm 2026-09-10] ////
    def test_directory_exchange_after_authorization_is_rejected(self):
        approve = self.reader.approved_path
        def exchange(value):
            path = approve(value)
            self.source.rename(self.approved / "original")
            self.addCleanup(directory_link(self.source, self.outside))
            return path
        with patch.object(self.reader, "approved_path", side_effect=exchange):
            with self.assertRaises(SourceError):
                self.reader.read_file(self.source / "text.md")

    # //// 打开后把目录改回原址仍拒绝已经打开的外部句柄 [@x380kkm 2026-09-10] ////
    def test_restored_path_does_not_authorize_external_handle(self):
        approve = self.reader.approved_path
        original = self.approved / "original"
        cleanup = []
        def exchange(value):
            path = approve(value)
            self.source.rename(original)
            cleanup.append(directory_link(self.source, self.outside))
            return path
        opener = Path.open
        def restore_after_open(path, *args, **kwargs):
            stream = opener(path, *args, **kwargs)
            cleanup.pop()()
            original.rename(self.source)
            return stream
        with patch.object(self.reader, "approved_path", side_effect=exchange), patch.object(Path, "open", restore_after_open):
            with self.assertRaises(SourceError):
                self.reader.read_file(self.source / "text.md")

    # //// 授权范围内的稳定普通文件继续返回完整正文 [@x380kkm 2026-09-10] ////
    def test_stable_authorized_file_is_read(self):
        path, content = self.reader.read_file(self.source / "text.md")
        self.assertEqual(content, "approved")
        self.assertEqual(path, (self.source / "text.md").resolve())

    # //// 进程授权内的其他目录仍受声明来源边界限制 [@x380kkm 2026-09-10] ////
    def test_declared_source_stays_inside_its_root_after_exchange(self):
        inside = self.source / "inside"
        sibling = self.approved / "sibling"
        inside.mkdir()
        sibling.mkdir()
        (inside / "entry.md").write_text("declared", encoding="utf-8")
        (sibling / "entry.md").write_text("other-source", encoding="utf-8")
        read = self.reader._read_path
        def exchange(root, entry, source):
            inside.rename(self.source / "original")
            self.addCleanup(directory_link(inside, sibling))
            return read(root, entry, source)
        source = {"resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": str(self.source)}
        with patch.object(self.reader, "_read_path", side_effect=exchange):
            with self.assertRaisesRegex(SourceError, "来源目录"):
                self.reader.read(source, "inside/entry.md")


if __name__ == "__main__":
    unittest.main()
