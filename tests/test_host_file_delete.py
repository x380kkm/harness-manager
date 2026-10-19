# audience: internal
# # host-file-delete-tests
"""临时宿主目录核对删除对象和既有事务回滚."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.host_file_delete import remove_host_file
from harness_manager.host_storage import HostStorage, HostTransactionError
from harness_manager.storage_errors import StorageConflictError
from test_source_boundaries import directory_link


# //// 核对预览选中的删除对象 [@x380kkm 2026-09-10] ////
class HostFileDeleteTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.host, self.outside = (self.root / name for name in ("user", "host", "outside"))
        for path in (self.user, self.host, self.outside):
            path.mkdir()
        self.name = "AGENTS.override.md"
        (self.host / self.name).write_bytes(b"managed")
        (self.outside / self.name).write_bytes(b"outside")
        self.storage = HostStorage(self.user, self.host, "codex-user")

    # //// 正常删除和原始恢复点保持完整文件内容 [@x380kkm 2026-09-10] ////
    def test_delete_and_restore_original(self):
        self.storage.apply({self.name: None}, self.storage.capture({self.name: None}))
        self.assertFalse((self.host / self.name).exists())
        restore = self.storage.preview_restore("initial")
        self.storage.restore("initial", restore["baseline"])
        self.assertEqual((self.host / self.name).read_bytes(), b"managed")

    # //// 父目录在最终删除前变化时保留目录外文件 [@x380kkm 2026-09-10] ////
    def test_parent_exchange_does_not_delete_outside_file(self):
        baseline = self.storage.capture({self.name: None})
        parked = self.root / "parked"

        # //// 在删除入口前把原父目录替换为目录链接 [@x380kkm 2026-09-10] ////
        def exchange(path, expected):
            self.host.rename(parked)
            self.addCleanup(directory_link(self.host, self.outside))
            return remove_host_file(path, expected)

        with patch("harness_manager.host_storage.remove_host_file", exchange):
            with self.assertRaises(HostTransactionError):
                self.storage.apply({self.name: None}, baseline)
        self.assertEqual((self.outside / self.name).read_bytes(), b"outside")
        self.assertEqual((parked / self.name).read_bytes(), b"managed")

    # //// 预览之后变化的正文保持原样 [@x380kkm 2026-09-10] ////
    def test_changed_file_is_not_deleted(self):
        with self.assertRaises(StorageConflictError):
            remove_host_file(self.host / self.name, b"older")
        self.assertEqual((self.host / self.name).read_bytes(), b"managed")


if __name__ == "__main__":
    unittest.main()
