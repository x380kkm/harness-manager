# audience: internal
# # content-sources
"""来源读取范围由进程入口提供. Git 元数据和对象链接的实际位置使用同一读取授权."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .protocol import matches_version
from .source_files import file_observation, opened_file_path

MAX_CONTENT_BYTES = 1024 * 1024
SOURCE_RESOLVER_VERSION = "1.0.0"


# //// 来源读取错误保留可行动的原因 [@x380kkm 2026-09-06] ////
class SourceError(ValueError):
    pass


# //// 标识来源入口与本次返回正文的组合 [@x380kkm 2026-09-06] ////
def text_revision(origin: str, content: str) -> str:
    return hashlib.sha256((origin + "\0" + content).encode("utf-8")).hexdigest()


# //// 保存已经核对的 Git 工作区与存储位置 [@x380kkm 2026-09-06] ////
@dataclass(frozen=True)
class GitLocation:
    root: Path
    directory: Path
    bare: bool
    access_paths: tuple[Path, ...] = ()


# //// 读取授权与来源接口绑定到当前工作目录 [@x380kkm 2026-09-06] ////
class SourceReader:
    def __init__(self, workspace: Path, read_roots: list[Path]) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.roots = tuple(dict.fromkeys([self.workspace, *(root.resolve(strict=True) for root in read_roots)]))

    # //// 解析进程批准范围中的本地路径 [@x380kkm 2026-09-06] ////
    def approved_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise SourceError(f"来源不存在或无法访问: {path}") from error
        self.check_access(resolved)
        return resolved

    # //// 按当前进程授权核对已解析位置 [@x380kkm 2026-09-06] ////
    def check_access(self, resolved: Path) -> None:
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.roots):
            raise SourceError(f"来源超出进程批准的读取范围: {resolved}. 请通过目录选择或 --read-root 授权.")

    # //// 按当前路径映射核对快照的来源访问范围 [@x380kkm 2026-09-06] ////
    def authorize_snapshot(self, paths: list[str]) -> None:
        for value in paths:
            path = Path(value)
            if not path.is_absolute():
                raise SourceError("快照来源需要已解析的绝对位置.")
            self.check_access(path.resolve())

    # //// 按当前元数据和对象链接核对 Git 来源授权 [@x380kkm 2026-09-08] ////
    def authorize_git_source(self, root: str | Path) -> None:
        self._git_location(self.approved_path(root))

    # //// 完整读取一个有大小边界的文本文件 [@x380kkm 2026-09-06] ////
    def read_file(self, value: str | Path, *, source_root: Path | None = None) -> tuple[Path, str]:
        path = self.approved_path(value)
        if not stat.S_ISREG(path.stat().st_mode):
            raise SourceError(f"来源需要普通文件: {path}")
        with path.open("rb") as stream:
            opened = self.check_open_file(path, stream.fileno(), source_root)
            data = stream.read(MAX_CONTENT_BYTES + 1)
            if file_observation(self.check_open_file(path, stream.fileno(), source_root)) != file_observation(opened):
                raise SourceError(f"来源在读取期间发生变化: {path}")
        if len(data) > MAX_CONTENT_BYTES:
            raise SourceError(f"文件超过 {MAX_CONTENT_BYTES} 字节的单次读取上限, 需要通过来源工具读取完整语义单元.")
        try:
            return path, data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise SourceError(f"来源需要 UTF-8 文本: {path}") from error

    # //// 核对已打开文件的实际位置与当前路径身份 [@x380kkm 2026-09-10] ////
    def check_open_file(self, path: Path, descriptor: int, source_root: Path | None) -> os.stat_result:
        actual = opened_file_path(descriptor)
        self.check_access(actual)
        if source_root is not None and not actual.is_relative_to(source_root):
            raise SourceError("内容入口超出声明的来源目录.")
        current = self.approved_path(path)
        info = os.fstat(descriptor)
        if actual != path or current != path or not os.path.samestat(info, path.stat()):
            raise SourceError(f"来源路径在打开期间发生变化: {path}")
        if not stat.S_ISREG(info.st_mode):
            raise SourceError(f"来源需要普通文件: {path}")
        return info

    # //// 读取声明指定的本地或固定 Git 内容 [@x380kkm 2026-09-06] ////
    def read(self, source: dict[str, Any], entry: str) -> dict[str, Any]:
        relative = Path(source.get("subpath", "")) / Path(entry)
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise SourceError("内容入口需要来源目录内的相对路径.")
        resolver = source.get("resolver", {}).get("id")
        if matches_version(SOURCE_RESOLVER_VERSION, source.get("resolver", {}).get("range")) is not True:
            raise SourceError(f"来源解析器版本约束需要匹配 {SOURCE_RESOLVER_VERSION}.")
        locator = source.get("locator", "")
        if not isinstance(locator, str) or "://" in locator:
            raise SourceError("此来源需要显式登记本地路径或本地 Git 镜像.")
        root = self.approved_path(locator)
        location = (root / relative).resolve()
        if not root.is_dir() or not location.is_relative_to(root):
            raise SourceError("内容入口超出来源目录.")
        readers = {"manager.source/path": self._read_path, "manager.source/git": self._read_git}
        reader = readers.get(resolver)
        if reader is None:
            raise SourceError(f"尚无此来源解析器: {resolver}")
        return reader(root, relative, source)

    # //// 从当前本地内容取得完整读取单元 [@x380kkm 2026-09-06] ////
    def _read_path(self, root: Path, entry: Path, source: dict[str, Any]) -> dict[str, Any]:
        path, content = self.read_file(root / entry, source_root=root)
        return {"content": content, "path": str(path), "mediaType": media_type(path),
                "revision": "local:" + text_revision(str(path), content), "accessPaths": [str(root), str(path)]}

    # //// 从批准的本地 Git 来源读取固定提交中的文本 [@x380kkm 2026-09-06] ////
    def _read_git(self, root: Path, entry: Path, source: dict[str, Any]) -> dict[str, Any]:
        constraint = source.get("constraint")
        if not isinstance(constraint, str) or not constraint:
            raise SourceError("Git 来源读取需要明确的提交或引用约束.")
        location = self._git_location(root)
        revision = git_read(location, ["rev-parse", "--verify", "--end-of-options", constraint + "^{commit}"]).strip()
        path = (root / entry).relative_to(location.root).as_posix()
        object_name = f"{revision}:{path}"
        size = int(git_read(location, ["cat-file", "-s", object_name]).strip())
        if size > MAX_CONTENT_BYTES:
            raise SourceError("固定 Git 内容超过单次完整读取上限.")
        content = git_read(location, ["cat-file", "blob", object_name])
        observation = text_revision(str(root / entry), content)
        return {"content": content, "path": str(root / entry), "mediaType": media_type(entry),
                "commit": revision, "revision": f"git:{revision}:{observation}",
                "accessPaths": [str(root), str(root / entry), *(str(path) for path in location.access_paths)]}

    # //// 在批准范围内定位 Git 元数据 [@x380kkm 2026-09-06] ////
    def _git_location(self, source_root: Path) -> GitLocation:
        current = source_root
        access: set[Path] = set()
        while True:
            self.approved_path(current)
            marker = current / ".git"
            if marker.exists():
                approved = self.approved_path(marker)
                access.add(approved)
                if approved.is_dir():
                    directory = approved
                else:
                    _, text = self.read_file(approved)
                    if not text.strip().startswith("gitdir:"):
                        raise SourceError("Git 元数据入口需要 gitdir 路径.")
                    directory = self.approved_path(current / text.strip()[7:].strip())
                location = GitLocation(current, directory, False)
                break
            if (current / "HEAD").is_file() and (current / "objects").is_dir():
                location = GitLocation(current, current, True)
                break
            if current == current.parent:
                raise SourceError("来源目录没有可访问的 Git 元数据.")
            current = current.parent
        common = location.directory
        access.add(common)
        common_file = common / "commondir"
        if common_file.exists():
            _, text = self.read_file(common_file)
            access.add(common_file)
            common = self.approved_path(common / text.strip())
            access.add(common)
        for directory in {location.directory, common}:
            access.update(self._check_git_links(directory))
        access.update(self._check_git_configurations([location.directory / "config", common / "config",
                                                     location.directory / "config.worktree", common / "config.worktree"]))
        access.update(self._check_object_directories(common / "objects"))
        return GitLocation(location.root, location.directory, location.bare, tuple(sorted(access)))

    # //// 按声明路径核对 Git 附加配置及其相对引用 [@x380kkm 2026-09-08] ////
    def _check_git_configurations(self, paths: list[Path]) -> set[Path]:
        pending, visited, accessed = list(paths), set(), set()
        while pending:
            path = pending.pop()
            resolved = path.resolve()
            self.check_access(resolved)
            accessed.update((path, resolved))
            if not resolved.exists():
                continue
            if not resolved.is_file():
                raise SourceError("Git 配置需要普通文件.")
            directory = path.parent.resolve()
            identity = (resolved, directory)
            if identity in visited:
                continue
            visited.add(identity)
            for value in git_configuration_includes(path):
                target = Path(value)
                pending.append(target if target.is_absolute() else directory / target)
        return accessed

    # //// 核对递归对象库引用仍位于批准范围 [@x380kkm 2026-09-06] ////
    def _check_object_directories(self, directory: Path) -> set[Path]:
        pending = [directory]
        visited: set[Path] = set()
        accessed: set[Path] = set()
        while pending:
            current = self.approved_path(pending.pop())
            if current in visited:
                continue
            visited.add(current)
            accessed.add(current)
            if not current.is_dir():
                raise SourceError("Git 对象库需要目录.")
            accessed.update(self._check_git_links(current))
            alternates = current / "info" / "alternates"
            if alternates.exists():
                _, text = self.read_file(alternates)
                accessed.add(alternates)
                for line in text.splitlines():
                    value = line.strip()
                    if value:
                        if value.startswith('"'):
                            raise SourceError("Git 对象库的引用需要直接的本地路径.")
                        pending.append(current / value)
        return accessed

    # //// 核对 Git 存储中的链接目标并保留其授权路径 [@x380kkm 2026-09-08] ////
    def _check_git_links(self, directory: Path) -> set[Path]:
        pending, visited, accessed = [directory], set(), set()
        while pending:
            path = pending.pop()
            current = self.approved_path(path)
            if current != path:
                accessed.update((path, current))
            if current in visited:
                continue
            visited.add(current)
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if entry.is_symlink() or entry.is_junction():
                        target = self.approved_path(path)
                        accessed.update((path, target))
                        if target.is_dir():
                            pending.append(target)
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        return accessed


# //// 在隔离配置环境中执行只读 Git 命令 [@x380kkm 2026-09-08] ////
def git_output(arguments: list[str], directory: Path) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                       GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CEILING_DIRECTORIES=str(directory.parent))
    result = subprocess.run(["git", *arguments], cwd=directory, capture_output=True, timeout=15, env=environment)
    if result.returncode:
        raise SourceError(result.stderr.decode("utf-8", errors="replace").strip() or "Git 来源读取失败.")
    return result.stdout


# //// 在独立目录解析单个 Git 配置的附加文件声明 [@x380kkm 2026-09-08] ////
def git_configuration_includes(path: Path) -> list[str]:
    result = []
    with TemporaryDirectory(prefix="harness-git-config-") as temporary:
        directory = Path(temporary)
        command = ["config", "--no-includes", "--file", str(path), "--null"]
        data = git_output([*command, "--list"], directory)
        keys = set()
        for record in data.split(b"\0"):
            key, separator, value = record.partition(b"\n")
            normalized = key.lower()
            if separator and value and (normalized == b"include.path" or normalized.startswith(b"includeif.") and normalized.endswith(b".path")):
                keys.add(key)
        for key in sorted(keys):
            try:
                paths = git_output([*command, "--type=path", "--get-all", key.decode("utf-8")], directory)
                result.extend(value.decode("utf-8") for value in paths.split(b"\0") if value)
            except UnicodeDecodeError as error:
                raise SourceError("Git 附加配置路径需要 UTF-8 文本.") from error
    return result


# //// 执行指定仓库的只读 Git 子命令并解码正文 [@x380kkm 2026-09-06] ////
def git_read(location: GitLocation, arguments: list[str]) -> str:
    command = ["--git-dir", str(location.directory)]
    if not location.bare:
        command.extend(["--work-tree", str(location.root)])
    output = git_output([*command, *arguments], location.root)
    try:
        return output.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SourceError("Git 内容需要 UTF-8 文本.") from error


# //// 返回文本载体的媒体类型 [@x380kkm 2026-09-06] ////
def media_type(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return "text/markdown"
    return mimetypes.guess_type(path.name)[0] or "text/plain"
