# audience: internal
# # agent-manager-installer
"""把 manifest 声明的 harness overlay 安装到用户级 Agent 与 Codex 目录.

安装目标只允许使用相对路径. Manager 记录自己拥有的目标, 先完整准备候选副本,
再替换目标并写入状态. 用户配置文件与其他组件目录保持原样.
"""
from __future__ import annotations

import filecmp
from hashlib import sha256
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .manifest import Component, ManifestError

IGNORED_INSTALL_NAMES = {"__pycache__", ".DS_Store", ".git", "cleanread", ".codegraph", "node_modules"}
IGNORED_INSTALL_SUFFIXES = {".pyc", ".pyo"}


# //// 表示一个经过路径校验的安装目标 [@x380kkm 2026-09-03] ////
@dataclass(frozen=True)
class InstallTarget:
    component_id: str
    source: Path
    target: Path
    owner: str
    action: str
    ownership: str


# //// 安装错误携带目标级修正信息 [@x380kkm 2026-09-03] ////
class InstallError(ValueError):
    pass


# //// 解析用户级 Codex 与 Agent 根目录 [@x380kkm 2026-09-03] ////
def resolve_install_roots(host_root: Path | None, agents_root: Path | None) -> dict[str, Path]:
    codex_env = os.environ.get("CODEX_HOME")
    agents_env = os.environ.get("AGENTS_HOME")
    resolved_host = host_root if host_root is not None else Path(codex_env) if codex_env else Path.home() / ".codex"
    resolved_agents = agents_root if agents_root is not None else Path(agents_env) if agents_env else Path.home() / ".agents"
    resolved_host = resolved_host.expanduser().resolve()
    return {
        "host": resolved_host,
        "codex": resolved_host,
        "agents": resolved_agents.expanduser().resolve(),
    }


# //// 将清单内相对路径限制在声明根目录内 [@x380kkm 2026-09-03] ////
def resolve_relative(root: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ManifestError(f"{field} 必须是非空相对路径")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ManifestError(f"{field} 必须位于声明根目录内")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"{field} 超出声明根目录") from exc
    return target


# //// 读取 manager 已登记的目标所有权 [@x380kkm 2026-09-03] ////
def read_install_state(codex_root: Path) -> dict[str, Any]:
    state_path = install_state_path(codex_root)
    if not state_path.is_file():
        return {"schema": 1, "targets": {}}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"安装状态 JSON 无效: {state_path}") from exc
    if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("targets"), dict):
        raise InstallError(f"安装状态结构无效: {state_path}")
    return state


# //// 返回 manager 私有的安装状态路径 [@x380kkm 2026-09-03] ////
def install_state_path(codex_root: Path) -> Path:
    return codex_root / "managed" / "clean-vibing.json"


# //// 比较文件或目录是否已经等同于发行源 [@x380kkm 2026-09-03] ////
def content_matches(source: Path, target: Path) -> bool:
    if source.is_file():
        return target.is_file() and filecmp.cmp(source, target, shallow=False)
    if not source.is_dir() or not target.is_dir():
        return False
    source_files = install_files(source)
    target_files = install_files(target)
    if source_files != target_files:
        return False
    return all(filecmp.cmp(source / path, target / path, shallow=False) for path in source_files)


# //// 列出属于发行内容的文件并跳过运行时缓存 [@x380kkm 2026-09-03] ////
def install_files(root: Path) -> list[Path]:
    return sorted(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in IGNORED_INSTALL_NAMES for part in path.relative_to(root).parts)
        and path.suffix.lower() not in IGNORED_INSTALL_SUFFIXES
    )


# //// 记录受管文件基线以识别用户编辑 [@x380kkm 2026-10-19] ////
def target_fingerprint(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    files = [Path(".")] if path.is_file() else install_files(path)
    return {str(relative): sha256((path if relative == Path(".") else path / relative).read_bytes()).hexdigest()
            for relative in files}


# //// 将安装记录的位置固定到当前安装根 [@x380kkm 2026-10-19] ////
def target_key(target: Path, root: Path) -> str:
    return target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)


# //// 生成各组件拥有目标的安装差异 [@x380kkm 2026-09-03] ////
def build_install_plan(
    components: list[Component],
    manifest_path: Path,
    *,
    host_root: Path | None = None,
    agents_root: Path | None = None,
) -> tuple[list[InstallTarget], dict[str, Path]]:
    roots = resolve_install_roots(host_root, agents_root)
    state = read_install_state(roots["host"])
    owned = state["targets"]
    repo_root = manifest_path.parent.parent.resolve()
    records: list[InstallTarget] = []
    for component in components:
        raw_targets = component.install.get("targets", [])
        if not isinstance(raw_targets, list):
            raise ManifestError(f"{component.id}.install.targets 必须是数组")
        for index, raw_target in enumerate(raw_targets):
            if not isinstance(raw_target, dict):
                raise ManifestError(f"{component.id}.install.targets[{index}] 必须是对象")
            root_name = raw_target.get("root")
            if root_name not in roots:
                raise ManifestError(f"{component.id}.install.targets[{index}].root 必须是 host 或 agents")
            source_root_name = raw_target.get("source_root", "repo")
            if source_root_name not in {"repo", "component"}:
                raise ManifestError(f"{component.id}.install.targets[{index}].source_root 必须是 repo 或 component")
            source_root = repo_root if source_root_name == "repo" else component.source
            source = resolve_relative(source_root, raw_target.get("source"), f"{component.id}.install.targets[{index}].source")
            target = resolve_relative(roots[root_name], raw_target.get("path"), f"{component.id}.install.targets[{index}].path")
            relative_target = target.relative_to(roots[root_name])
            if len(relative_target.parts) < 2 or relative_target.parts[0] not in {"skills", "tools", "rules"}:
                raise ManifestError(f"安装目标需要位于 skills, tools 或 rules 的独立子目录中: {target}")
            state_entry = owned.get(target_key(target, roots["host"]), owned.get(str(target)))
            ownership = "managed" if isinstance(state_entry, dict) and state_entry.get("component") == component.id else "external"
            if not source.exists():
                action = "missing-source"
            elif not target.exists():
                action = "create"
            elif ownership == "external":
                action = "conflict"
            elif content_matches(source, target):
                action = "current"
            elif state_entry.get("fingerprint") != target_fingerprint(target):
                action = "modified"
            else:
                action = "replace"
            records.append(InstallTarget(component.id, source, target, str(component.install.get("owner", "unspecified")), action, ownership))
    _ensure_unique_targets(records)
    return records, roots


# //// 阻止两个组件声明同一个宿主目标 [@x380kkm 2026-09-03] ////
def _ensure_unique_targets(records: list[InstallTarget]) -> None:
    seen: dict[Path, str] = {}
    for record in records:
        for target, previous in seen.items():
            if target.is_relative_to(record.target) or record.target.is_relative_to(target):
                raise ManifestError(f"安装目标重叠: {record.target} ({previous}, {record.component_id})")
        seen[record.target] = record.component_id


# //// 把安装计划转换为 JSON 可序列化记录 [@x380kkm 2026-09-03] ////
def install_plan_records(records: list[InstallTarget]) -> list[dict[str, Any]]:
    return [{
        "component": record.component_id,
        "source": str(record.source),
        "target": str(record.target),
        "owner": record.owner,
        "ownership": record.ownership,
        "action": record.action,
    } for record in records]


# //// 预先复制全部候选以避免来源错误造成部分安装 [@x380kkm 2026-09-03] ////
def stage_install_targets(records: list[InstallTarget]) -> list[tuple[InstallTarget, Path]]:
    staged: list[tuple[InstallTarget, Path]] = []
    for record in records:
        if record.action == "missing-source":
            raise InstallError(f"安装来源不存在: {record.source}")
        if record.action == "conflict":
            raise InstallError(f"目标已有外部内容, 请先保存并合并: {record.target}")
        if record.action == "modified":
            raise InstallError(f"安装目标含有本机编辑, 请先保存并合并: {record.target}")
    for record in records:
        if record.action == "current":
            continue
        record.target.parent.mkdir(parents=True, exist_ok=True)
        stage_root = Path(tempfile.mkdtemp(prefix=".clean-vibing-stage-", dir=record.target.parent))
        staged_path = stage_root / record.target.name
        if record.source.is_dir():
            shutil.copytree(
                record.source,
                staged_path,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".git", "cleanread", ".codegraph", "node_modules"
                ),
            )
        else:
            shutil.copy2(record.source, staged_path)
        staged.append((record, staged_path))
    return staged


# //// 原子替换已准备的目标并在失败时恢复旧内容 [@x380kkm 2026-09-03] ////
def apply_install_plan(
    records: list[InstallTarget],
    roots: dict[str, Path],
    *,
    sources: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    staged = stage_install_targets(records)
    completed: list[tuple[InstallTarget, Path | None, str]] = []
    try:
        for record, staged_path in staged:
            record.target.parent.mkdir(parents=True, exist_ok=True)
            backup, mode = replace_staged_target(record.target, staged_path)
            completed.append((record, backup, mode))
        write_install_state(records, roots["host"], sources or {})
    except Exception:
        for record, backup, mode in reversed(completed):
            restore_target(record.target, backup, mode)
        raise
    finally:
        for _, staged_path in staged:
            if staged_path.parent.exists():
                shutil.rmtree(staged_path.parent, ignore_errors=True)
    for _, backup, _ in completed:
        if backup is not None:
            _remove_path(backup)
    return install_plan_records(records)


# //// 用目录重命名或文件级同步替换一个已准备目标 [@x380kkm 2026-09-03] ////
def replace_staged_target(target: Path, staged: Path) -> tuple[Path | None, str]:
    if not target.exists():
        staged.replace(target)
        return None, "created"
    backup = target.with_name(f".{target.name}.clean-vibing-backup-{uuid4().hex}")
    try:
        target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            backup.replace(target)
            raise
        return backup, "moved"
    except PermissionError:
        if not target.is_dir() or not staged.is_dir():
            raise
        shutil.copytree(target, backup)
        synchronize_directory(staged, target)
        return backup, "synced"


# //// 从事务备份恢复被替换的目标 [@x380kkm 2026-09-03] ////
def restore_target(target: Path, backup: Path | None, mode: str) -> None:
    if backup is None:
        _remove_path(target)
        return
    if mode == "synced":
        synchronize_directory(backup, target)
        _remove_path(backup)
        return
    _remove_path(target)
    if backup.exists():
        backup.replace(target)


# //// 让目标目录的发行文件与候选目录保持一致 [@x380kkm 2026-09-03] ////
def synchronize_directory(source: Path, target: Path) -> None:
    source_files = set(install_files(source))
    target_files = set(install_files(target))
    for relative in sorted(target_files - source_files, key=lambda path: len(path.parts), reverse=True):
        (target / relative).unlink()
    for relative in source_files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    directories = sorted(
        (path for path in target.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


# //// 写入 manager 自有目标的最新所有权记录 [@x380kkm 2026-09-03] ////
def write_install_state(
    records: list[InstallTarget],
    codex_root: Path,
    sources: dict[str, str],
) -> None:
    state = read_install_state(codex_root)
    targets = state["targets"]
    for record in records:
        if record.action != "missing-source":
            targets[target_key(record.target, codex_root)] = {
                "component": record.component_id, "owner": record.owner,
                "fingerprint": target_fingerprint(record.target),
            }
    registered_sources = state.get("sources", {})
    if not isinstance(registered_sources, dict):
        registered_sources = {}
    registered_sources.update(sources)
    state["sources"] = registered_sources
    path = install_state_path(codex_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


# //// 删除一次安装事务创建的文件或目录 [@x380kkm 2026-09-03] ////
def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()
