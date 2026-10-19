# audience: internal
# # codex-inventory-input
"""盘点输入限于明确的宿主载体和 Skill 目录. 解析错误只返回类型, 配置原文留在文件中."""
from __future__ import annotations

import json
import os
import stat
from collections import deque
from pathlib import Path
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid5

from .sources import MAX_CONTENT_BYTES, SourceError, SourceReader

MAX_SCAN_ENTRIES = 6000
MAX_SCAN_BYTES = 24 * MAX_CONTENT_BYTES


# //// 按来源位置与局部定位生成稳定身份 [@x380kkm 2026-09-06] ////
def item_id(path: Path, locator: str = "") -> str:
    return "codex:" + uuid5(NAMESPACE_URL, path.absolute().as_uri() + "#" + locator).hex


# //// 从白名单字段取得有限文本 [@x380kkm 2026-09-06] ////
def text_field(value: Any, fallback: str = "", limit: int = 500) -> str:
    return value.strip()[:limit] if isinstance(value, str) else fallback


# //// 读取对象中的声明集合 [@x380kkm 2026-09-06] ////
def object_field(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# //// 读取路径本身与链接目标的可比较元数据 [@x380kkm 2026-09-07] ////
def path_metadata(path: Path) -> tuple | None:
    try:
        info = path.lstat()
        linked = stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) != 0
        target = os.readlink(path) if linked else None
        target_info = None
        if linked:
            try:
                target_stat = path.stat()
                target_info = (target_stat.st_dev, target_stat.st_ino, target_stat.st_size,
                               target_stat.st_mtime_ns, target_stat.st_ctime_ns, target_stat.st_mode)
            except OSError:
                target_info = (None,)
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0),
                getattr(info, "st_reparse_tag", 0), target, target_info)
    except OSError:
        return None


# //// 保存只读观察与可见的覆盖缺口 [@x380kkm 2026-09-06] ////
class InventoryInput:
    def __init__(self, root_targets: dict[Path, Path] | None = None) -> None:
        self.items: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.diagnostics: list[dict] = []
        self.entries = 0
        self.bytes_read = 0
        self.read_paths: set[Path] = set()
        self.import_roots: dict[str, Path] = {}
        self.observed: dict[Path, tuple | None] = {}
        self._resolved_roots: dict[Path, Path] = {}
        self._readers: dict[Path, SourceReader] = {}
        self._root_targets = root_targets if root_targets is not None else {}
        self._changed = False

    # //// 记录固定路径及其目标元数据 [@x380kkm 2026-09-07] ////
    def observe(self, path: Path) -> tuple | None:
        path = Path(path)
        signature = path_metadata(path)
        if path in self.observed and self.observed[path] != signature:
            self._changed = True
        self.observed.setdefault(path, signature)
        return signature

    # //// 判断本次盘点记录的路径元数据仍然有效 [@x380kkm 2026-09-07] ////
    def is_current(self) -> bool:
        return not self._changed and all(path_metadata(path) == signature for path, signature in self.observed.items())

    # //// 复用同一来源根的 UTF-8 读取器 [@x380kkm 2026-09-07] ////
    def reader(self, root: Path) -> SourceReader:
        root = Path(root)
        if root not in self._readers:
            allowed = self.resolved_root(root)
            if allowed is None:
                raise SourceError("来源根需要与已确认的盘点位置一致.")
            reader = SourceReader(allowed, [])
            if reader.workspace != allowed:
                raise SourceError("来源根在盘点期间发生重定向.")
            self._readers[root] = reader
        return self._readers[root]

    # //// 固定明确来源根的解析位置与缺失入口 [@x380kkm 2026-09-07] ////
    def pin_root(self, root: Path) -> None:
        self.observe(root)
        if root not in self._root_targets:
            self._root_targets[root] = root.resolve()

    # //// 缓存一次来源根的解析位置并保持链接边界 [@x380kkm 2026-09-07] ////
    def resolved_root(self, root: Path) -> Path | None:
        root = Path(root)
        if root not in self._resolved_roots:
            try:
                allowed = root.resolve(strict=True)
            except OSError:
                return None
            if allowed != self._root_targets.setdefault(root, allowed):
                self.diagnose(root, "redirected-scan-root", "来源根已重定向, 请重新确认来源位置.")
                return None
            self.observe(root)
            self._resolved_roots[root] = allowed
        return self._resolved_roots[root]

    # //// 把缺口定位到原始载体 [@x380kkm 2026-09-06] ////
    def diagnose(self, path: Path, code: str, message: str) -> None:
        note = {"path": str(path), "code": code, "message": message}
        if note not in self.diagnostics:
            self.diagnostics.append(note)

    # //// 限定解析后的真实路径仍在指定目录中 [@x380kkm 2026-09-06] ////
    def contains(self, path: Path, root: Path) -> bool:
        try:
            self.observe(path)
            resolved = path.resolve(strict=True)
            allowed = self.resolved_root(root)
            if allowed is None:
                self.diagnose(path, "unreadable-path", "路径无法读取.")
                return False
            if resolved == allowed or resolved.is_relative_to(allowed):
                return True
            self.diagnose(path, "outside-scan-root", "链接目标位于盘点目录范围之外.")
        except OSError:
            self.diagnose(path, "unreadable-path", "路径无法读取.")
        return False

    # //// 通过来源读取器读取有界文本 [@x380kkm 2026-09-06] ////
    def read_text(self, path: Path, root: Path) -> str | None:
        self.observe(path)
        if not path.exists() or not self.contains(path, root):
            return None
        if self.bytes_read >= MAX_SCAN_BYTES:
            self.diagnose(path, "scan-budget", "盘点正文达到读取预算, 其余来源保留读取缺口.")
            return None
        try:
            _, content = self.reader(root).read_file(path)
        except (OSError, ValueError):
            self.diagnose(path, "unreadable-file", "文件需要可访问的普通 UTF-8 文本, 大小上限为 1 MiB.")
            return None
        self.bytes_read += len(content.encode("utf-8"))
        if self.bytes_read > MAX_SCAN_BYTES:
            self.diagnose(path, "scan-budget", "盘点正文达到读取预算, 此文件保留完整读取缺口.")
            return None
        self.read_paths.add(path)
        return content

    # //// 解析 JSON 载体并保留安全错误摘要 [@x380kkm 2026-09-06] ////
    def read_json(self, path: Path, root: Path) -> dict:
        content = self.read_text(path, root)
        if content is None:
            return {}
        try:
            value = json.loads(content)
            if isinstance(value, dict):
                return value
        except (ValueError, RecursionError):
            pass
        self.diagnose(path, "invalid-json", "配置需要有效的 JSON 对象.")
        return {}

    # //// 有界遍历一个目录中的明确文件名 [@x380kkm 2026-09-06] ////
    def find(self, root: Path, filename: str, depth: int = 5) -> Iterator[Path]:
        self.observe(root)
        if not root.is_dir():
            return
        pending = deque([(root, 0)])
        visited: set[Path] = set()
        while pending and self.entries < MAX_SCAN_ENTRIES:
            directory, level = pending.popleft()
            self.observe(directory)
            if not self.contains(directory, root):
                continue
            resolved = directory.resolve()
            if resolved in visited:
                continue
            visited.add(resolved)
            candidate = directory / filename
            self.observe(candidate)
            if candidate.is_file():
                yield candidate
                continue
            try:
                entries = sorted(directory.iterdir(), key=lambda entry: entry.name.casefold())
            except OSError:
                self.diagnose(directory, "unreadable-directory", "目录无法列出.")
                continue
            for path in entries:
                self.observe(path)
                self.entries += 1
                if self.entries > MAX_SCAN_ENTRIES:
                    self.diagnose(root, "scan-budget", "目录条目达到盘点预算.")
                    return
                if path.is_dir() and level < depth and path.name not in {".git", "node_modules", "__pycache__"}:
                    pending.append((path, level + 1))

    # //// 登记来源清晰的观察节点 [@x380kkm 2026-09-06] ////
    def add(self, path: Path, name: str, kind: str, summary: str, *, locator: str = "",
            scope: str = "user", status: str = "声明存在; 运行状态未知", details: dict | None = None,
            content: str | None = None, line: int | None = None, import_root: Path | None = None) -> str:
        identifier = item_id(path, locator)
        item = {"id": identifier, "name": name, "kind": kind, "summary": summary,
                "path": str(path), "scope": scope, "status": status, "details": details or {},
                "importable": import_root is not None, "managedIds": []}
        if content is not None:
            item["content"] = content
        if line is not None:
            item["line"] = line
        self.items[identifier] = item
        if import_root is not None:
            self.import_roots[identifier] = import_root
        return identifier

    # //// 连接已经登记的节点 [@x380kkm 2026-09-06] ////
    def connect(self, source: str, target: str, label: str) -> None:
        edge = {"from": source, "to": target, "label": label}
        if source in self.items and target in self.items and edge not in self.edges:
            self.edges.append(edge)
