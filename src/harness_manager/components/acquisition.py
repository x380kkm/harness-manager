# component-sources
"""Git 安装来源保存在受管目录, 更新使用组件声明的分支与构建入口."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .installer import InstallError, resolve_relative
from .manifest import Component, read_command
from .runtime import (run_git, run_component_command, result_summary, check_component_update,
                      has_git_source, apply_git_fast_forward, apply_component_update)


# //// 解析受管来源的仓库和包目录 [@x380kkm 2026-10-19] ////
def source_location(component: Component, root: Path) -> tuple[Path, Path]:
    repository = resolve_relative(root / "sources", component.id, "component.id")
    package = resolve_relative(repository, component.install["git"].get("subpath", "."), "install.git.subpath")
    return repository, package


# //// 列出来源获取动作并核对现有来源身份 [@x380kkm 2026-10-19] ////
def source_plan(components: list[Component], root: Path) -> list[dict]:
    records = []
    for component in components:
        git = component.install.get("git")
        if git is None:
            continue
        if not isinstance(git, dict) or not isinstance(git.get("url"), str) or not git["url"] or git["url"].startswith("-"):
            raise InstallError(f"{component.id}.install.git.url 需要有效的 Git 来源.")
        repository, package = source_location(component, root)
        if not component.source_origin.startswith("env:") and (component.source / component.update.get("repository", ".")).resolve() != repository:
            raise InstallError(f"{component.id}.update.repository 需要定位到所声明的 Git 来源.")
        if component.source_origin.startswith("env:"):
            action = "external" if component.source.is_dir() else "missing-external"
        elif not repository.exists():
            action = "clone"
        else:
            remote = run_git(repository, "remote", "get-url", "origin")
            action = "existing" if (repository / ".git").exists() and remote.is_ok and remote.stdout == git["url"] else "conflict"
        records.append({"id": component.id, "action": action, "url": git["url"],
                        "repository": str(repository), "source": str(component.source),
                        "commands": component.install.get("commands", [])})
    return records


# //// 在临时目录克隆来源并保留失败构建的可重试 checkout [@x380kkm 2026-10-19] ////
def acquire_sources(components: list[Component], root: Path) -> list[dict]:
    records = source_plan(components, root)
    for record in records:
        if record["action"] in {"conflict", "missing-external"}:
            raise InstallError(f"组件来源需要人工确认: {record['id']} ({record['action']}).")
    by_id = {component.id: component for component in components}
    for record in records:
        if record["action"] != "clone":
            continue
        component = by_id[record["id"]]
        repository, package = source_location(component, root)
        repository.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".clone-", dir=repository.parent) as temporary:
            checkout = Path(temporary) / "checkout"
            branch = component.update.get("branch")
            args = ["clone", "--single-branch", *(["--branch", branch] if branch else []), "--", record["url"], str(checkout)]
            result = run_git(repository.parent, *args, timeout=600)
            if not result.is_ok:
                raise InstallError(f"获取 {component.id} 失败: {result_summary(result)}")
            checkout.rename(repository)
        if not package.is_dir():
            raise InstallError(f"Git 来源缺少组件目录: {package}")
    return records


# //// 为当前提交准备依赖和构建产物并保存完成标记 [@x380kkm 2026-10-19] ////
def prepare_sources(components: list[Component], root: Path, manifest: Path) -> list[dict]:
    path = root / "managed" / "builds.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    receipts = []
    for component in components:
        commands = component.install.get("commands", [])
        if not commands or not has_git_source(component):
            continue
        revision = run_git(component.source, "rev-parse", "HEAD")
        if not revision.is_ok:
            raise InstallError(f"读取 {component.id} 来源提交失败: {result_summary(revision)}")
        marker = {"revision": revision.stdout, "commands": commands}
        if state.get(component.id) == marker and run_component_command(component.health_command, component, manifest).is_ok:
            continue
        for command in commands:
            result = run_component_command(read_command(command, "install.commands", component.id), component, manifest, timeout=600)
            receipts.append({"id": component.id, "command": list(result.argv), "exit": result.returncode})
            if not result.is_ok:
                raise InstallError(f"构建 {component.id} 失败, 可重新执行 install: {result.output[-6000:]}")
        state[component.id] = marker
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return receipts


# //// 快进 Git 来源或调用组件自身的发行版更新入口 [@x380kkm 2026-10-19] ////
def update_sources(components: list[Component], manifest: Path) -> list[dict]:
    receipts = []
    for component in components:
        if not component.install.get("git"):
            continue
        check = check_component_update(component, manifest)
        if check["status"] == "current":
            receipt = {"id": component.id, "status": "current", "check": check}
        elif check["status"] not in {"different", "available"}:
            receipt = {"id": component.id, "status": "failed", "check": check}
        elif has_git_source(component):
            result = apply_git_fast_forward(component)
            receipt = {"id": component.id, "status": "updated" if result.is_ok else "failed",
                       "check": check, "detail": result_summary(result)}
        else:
            receipt = apply_component_update(component, manifest)
        receipts.append(receipt)
        if receipt["status"] == "failed":
            break
    return receipts
