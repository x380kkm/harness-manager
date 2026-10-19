# audience: internal
# # read-statistics-tests
# 统计跨续读和重启按快照去重, 日期边界和存储归属使用真实数据库验证.
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness_manager.read_statistics import ReadStatistics
from harness_manager.storage_errors import StorageBoundaryError, StorageFormatError


# //// 构造带来源包与别名请求的读取快照 [@x380kkm 2026-09-07] ////
def read_pair(identity: str, ref: str = "plugin:methods#analysis") -> tuple[dict, dict]:
    selection = {"ref": ref, "artifact": {"source": "plugin:methods@1.0.0#analysis", "revision": "local"},
                 "entry": "SKILL.md"}
    snapshot = {"id": identity, "selection": selection,
                "request": {"ref": "plugin:wrapper#alias", "version": "4.0.0"},
                "createdAt": "1999-01-01T00:00:00+00:00", "inputs": [
                    {"document": {"kind": "Plugin", "id": "plugin:wrapper", "release": {"version": "4.0.0"},
                                  "metadata": {"name": "wrapper"}, "contributions": []}},
                    {"document": {"kind": "Plugin", "id": "plugin:methods", "release": {"version": "0.9.0"},
                                  "metadata": {"name": "其他来源版本"}, "contributions": []}},
                    {"document": {"kind": "Plugin", "id": "plugin:methods", "release": {"version": "1.0.0"},
                                  "metadata": {"name": "方法合集"}, "contributions": [
                                      {"id": "analysis", "payload": {"name": "分析方法", "body": "private body"}}
                                  ]}},
                ]}
    result = {"snapshot": identity, "selection": selection, "readiness": "ready", "units": []}
    return result, snapshot


# //// 验证统计持久化, 覆盖范围和 UTC 查询边界 [@x380kkm 2026-09-07] ////
class ReadStatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.user.mkdir()
        self.statistics = ReadStatistics(self.user)
        self.now = datetime(2026, 9, 7, 10, 30, tzinfo=timezone.utc)

    # //// 在固定完成时间提交统计记录 [@x380kkm 2026-09-07] ////
    def record_at(self, identity: str, moment: datetime, *, alias: str | None = None) -> None:
        result, snapshot = read_pair(identity)
        if alias:
            snapshot["request"]["ref"] = alias
        with patch("harness_manager.read_statistics.datetime", wraps=datetime) as clock:
            clock.now.return_value = moment
            self.statistics.record(result, snapshot)

    # //// 空查询保持用户目录原样并明确宿主原生统计不可用 [@x380kkm 2026-09-07] ////
    def test_empty_summary_is_read_only(self) -> None:
        summary = self.statistics.summary(now=self.now)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["skills"], [])
        self.assertEqual(summary["coverage"]["nativeCodex"], "unavailable")
        self.assertEqual(list(self.user.iterdir()), [])

    # //// 续读完成与重试跨重启合并为同一快照记录 [@x380kkm 2026-09-07] ////
    def test_partial_then_ready_counts_once_after_restart(self) -> None:
        result, snapshot = read_pair("snapshot:first")
        partial = {**result, "readiness": "needs-content", "continuation": "read:partial"}
        self.statistics.record(partial, snapshot)
        self.assertFalse(self.statistics.path.exists())
        self.record_at("snapshot:first", self.now - timedelta(minutes=2))
        self.statistics = ReadStatistics(self.user)
        self.record_at("snapshot:first", self.now - timedelta(minutes=1))
        summary = self.statistics.summary(now=self.now)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["skills"][0]["lastRead"], "2026-09-07T10:28:00.000000+00:00")
        self.assertEqual(summary["skills"][0]["name"], "分析方法")
        self.assertEqual(summary["skills"][0]["version"], "1.0.0")
        with closing(sqlite3.connect(self.statistics.path)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(completed_reads)")]
            self.assertEqual(columns, ["snapshot_id", "completed_at", "ref", "version", "name"])

    # //// 别名按原始引用聚合并保留 UTC 起止边界 [@x380kkm 2026-09-07] ////
    def test_calendar_window_and_original_reference_aggregation(self) -> None:
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.record_at("snapshot:outside", start - timedelta(microseconds=1))
        self.record_at("snapshot:start", start, alias="plugin:wrapper#first")
        self.record_at("snapshot:today", self.now, alias="plugin:other#second")
        self.record_at("snapshot:future", self.now + timedelta(microseconds=1))
        summary = self.statistics.summary(days=7, now=self.now.astimezone(timezone(timedelta(hours=8))))
        self.assertEqual(summary["window"]["start"], "2026-09-01T00:00:00.000000+00:00")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(len(summary["skills"]), 1)
        self.assertEqual(summary["skills"][0]["ref"], "plugin:methods#analysis")
        self.assertEqual(summary["byDay"][0], {"date": "2026-09-01", "count": 1})
        self.assertEqual(summary["byDay"][-1], {"date": "2026-09-07", "count": 1})
        self.assertEqual(self.statistics.summary(days=1, now=self.now)["total"], 1)

    # //// 读取和快照身份不一致时保持统计原样 [@x380kkm 2026-09-07] ////
    def test_mismatched_snapshot_rejected(self) -> None:
        result, snapshot = read_pair("snapshot:first")
        with self.assertRaises(StorageFormatError):
            self.statistics.record({**result, "snapshot": "snapshot:other"}, snapshot)
        changed = deepcopy(result)
        changed["selection"]["ref"] = "plugin:other#selection"
        with self.assertRaises(StorageFormatError):
            self.statistics.record(changed, snapshot)
        self.assertFalse(self.statistics.path.exists())

    # //// 统计文件链接阻止读取和写入越界 [@x380kkm 2026-09-07] ////
    def test_database_symlink_is_rejected(self) -> None:
        self.statistics.store.directory.mkdir(parents=True)
        external = self.root / "external.sqlite3"
        external.write_text("outside", encoding="utf-8")
        try:
            self.statistics.path.symlink_to(external)
        except OSError as error:
            self.skipTest(f"当前宿主无法创建符号链接: {error}")
        with self.assertRaises(StorageBoundaryError):
            self.statistics.summary(now=self.now)
        with self.assertRaises(StorageBoundaryError):
            self.record_at("snapshot:first", self.now)
        self.assertEqual(external.read_text(encoding="utf-8"), "outside")

    # //// Windows 目录连接保持外部统计目录原样 [@x380kkm 2026-09-07] ////
    @unittest.skipUnless(os.name == "nt", "Windows 目录连接使用原生重解析点.")
    def test_observation_junction_is_rejected(self) -> None:
        self.statistics.store.directory.parent.mkdir()
        external = self.root / "external"
        external.mkdir()
        source_path = str(self.statistics.store.directory).replace("'", "''")
        target_path = str(external).replace("'", "''")
        command = ("$ErrorActionPreference = 'Stop'\n"
                   f"New-Item -ItemType Junction -Path '{source_path}' -Target '{target_path}' | Out-Null")
        subprocess.run(["pwsh", "-Command", command], check=True, capture_output=True, text=True, encoding="utf-8")
        try:
            with self.assertRaises(StorageBoundaryError):
                self.statistics.summary(now=self.now)
            with self.assertRaises(StorageBoundaryError):
                self.record_at("snapshot:first", self.now)
            self.assertEqual(list(external.iterdir()), [])
        finally:
            self.statistics.store.directory.rmdir()


# //// 执行完整读取统计测试 [@x380kkm 2026-09-07] ////
if __name__ == "__main__":
    unittest.main()
