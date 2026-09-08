# audience: internal
# # read-statistics
"""统计调用方提交的完整方法读取. UTC 日历窗口按原始 Skill 引用聚合, 同一快照只计一次."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import stat

from .observations import ObservationStore
from .protocol import document_identity
from .storage_errors import StorageFormatError


# //// 从原始贡献中提取显示名称和来源版本 [@x380kkm 2026-09-07] ////
def selection_metadata(snapshot: dict) -> tuple[str, str | None]:
    ref = snapshot["selection"]["ref"]
    owner, _, member_id = ref.partition("#")
    source = snapshot["selection"].get("artifact", {}).get("source", "").partition("#")[0]
    for item in snapshot.get("inputs", []):
        document = item.get("document", {})
        if document.get("kind") != "Plugin" or document.get("id") != owner:
            continue
        if source and document_identity(document) != source:
            continue
        name = document.get("metadata", {}).get("name", member_id or owner)
        for member in document.get("contributions", []):
            if member.get("id") == member_id and isinstance(member.get("payload"), dict):
                name = member["payload"].get("name", name)
                break
        version = document.get("release", {}).get("version")
        return name if isinstance(name, str) and name else ref, version
    return member_id or owner, None


# //// 建立 UTC 日历窗口和每日计数位置 [@x380kkm 2026-09-07] ////
def calendar_window(days: int, now: datetime) -> tuple[dict, dict[str, int]]:
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("统计天数需要介于 1 和 365 之间的整数.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("统计时间需要明确的时区.")
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    window = {"days": days, "start": start.isoformat(timespec="microseconds"),
              "end": now.isoformat(timespec="microseconds"), "timeZone": "UTC"}
    counts = {(start + timedelta(days=offset)).date().isoformat(): 0 for offset in range(days)}
    return window, counts


# //// 保存完整读取并按日期汇集原始 Skill 的计数 [@x380kkm 2026-09-07] ////
class ReadStatistics:
    def __init__(self, user_root: Path) -> None:
        self.store = ObservationStore(user_root)
        self.path = self.store.directory / "read-statistics.sqlite3"

    # //// 核对统计数据库与 SQLite 辅助文件的直接归属 [@x380kkm 2026-09-07] ////
    def _check_paths(self) -> None:
        self.store.check_location()
        for suffix in ("", "-journal", "-wal", "-shm"):
            self.store.check_path(self.path.with_name(self.path.name + suffix), stat.S_ISREG)

    # //// 在已验证的用户观察位置建立统计目录 [@x380kkm 2026-09-07] ////
    def _prepare_directory(self) -> None:
        self._check_paths()
        self.store.directory.parent.mkdir(exist_ok=True)
        self._check_paths()
        self.store.directory.mkdir(exist_ok=True)
        self._check_paths()

    # //// 为匹配的完整读取记录一次 UTC 完成时间 [@x380kkm 2026-09-07] ////
    def record(self, result: dict, snapshot: dict) -> None:
        if result.get("readiness") != "ready":
            return
        if (snapshot.get("id") != result.get("snapshot") or not snapshot.get("id")
                or result.get("selection") != snapshot.get("selection")):
            raise StorageFormatError("完整读取与统计快照需要对应同一项内容.")
        selection = snapshot.get("selection", {})
        ref = selection.get("ref")
        if not isinstance(ref, str) or not ref:
            raise StorageFormatError("完整读取需要原始内容引用.")
        name, version = selection_metadata(snapshot)
        completed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._prepare_directory()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS completed_reads (
                snapshot_id TEXT PRIMARY KEY, completed_at TEXT NOT NULL,
                ref TEXT NOT NULL, version TEXT, name TEXT NOT NULL
            )""")
            connection.execute("CREATE INDEX IF NOT EXISTS completed_reads_date ON completed_reads(completed_at)")
            connection.execute("""INSERT OR IGNORE INTO completed_reads
                (snapshot_id, completed_at, ref, version, name) VALUES (?, ?, ?, ?, ?)""",
                (snapshot["id"], completed_at, ref, version, name))

    # //// 只读汇总窗口内的完成次数并标明统计覆盖范围 [@x380kkm 2026-09-07] ////
    def summary(self, days: int = 7, now: datetime | None = None) -> dict:
        window, daily = calendar_window(days, now or datetime.now(timezone.utc))
        result = {"metric": "manager-complete-read", "window": window,
                  "coverage": {"manager": "observed", "nativeCodex": "unavailable"},
                  "total": 0, "skills": [], "byDay": []}
        self._check_paths()
        skills = {}
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as connection:
                rows = connection.execute("""SELECT ref, version, name, substr(completed_at, 1, 10),
                    COUNT(*), MAX(completed_at) FROM completed_reads
                    WHERE completed_at >= ? AND completed_at <= ?
                    GROUP BY ref, version, name, substr(completed_at, 1, 10)""",
                    (window["start"], window["end"]))
                for ref, version, name, day, count, last_read in rows:
                    entry = skills.setdefault(ref, {"ref": ref, "name": name, "version": version,
                                                   "count": 0, "lastRead": last_read, "byDay": {}})
                    if last_read >= entry["lastRead"]:
                        entry.update(name=name, version=version, lastRead=last_read)
                    entry["count"] += count
                    entry["byDay"][day] = entry["byDay"].get(day, 0) + count
                    daily[day] += count
                    result["total"] += count
        for entry in skills.values():
            entry["byDay"] = [{"date": day, "count": entry["byDay"].get(day, 0)} for day in daily]
            if entry["version"] is None:
                del entry["version"]
        result["skills"] = sorted(skills.values(), key=lambda item: (-item["count"], item["name"], item["ref"]))
        result["byDay"] = [{"date": day, "count": count} for day, count in daily.items()]
        return result
