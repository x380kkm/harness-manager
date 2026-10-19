# audience: internal
# # agent-manager-runtime
"""组件命令, 健康检查与更新回执.

命令参数来自 manifest 的数组, 在组件来源目录执行且不经过 shell. 更新检查
与更新应用分别读取 update.check 和 update.apply; manager 只在显式 update
命令中调用 apply, 并在调用后重新读取版本与健康状态.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import Component, ManifestError, read_command


# //// 保存一次外部命令的退出状态和完整输出 [@x380kkm 2026-09-03] ////
@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    # //// 判断命令是否成功退出 [@x380kkm 2026-09-03] ////
    @property
    def is_ok(self) -> bool:
        return self.returncode == 0

    # //// 合并标准输出和标准错误供诊断使用 [@x380kkm 2026-09-03] ////
    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


# //// 将组件路径占位符展开为当前机器上的实际命令参数 [@x380kkm 2026-09-03] ////
def expand_command(command: tuple[str, ...], component: Component, manifest_path: Path) -> tuple[str, ...]:
    replacements = {
        "{source}": str(component.source),
        "{repo}": str(manifest_path.parent.parent.resolve()),
        "{manifest}": str(manifest_path),
        "{python}": sys.executable,
    }
    argv = tuple(_replace_placeholders(arg, replacements) for arg in command)
    if os.name == "nt" and argv and argv[0] == "npm":
        executable = shutil.which("npm")
        entry = Path(executable).parent / "node_modules/npm/bin/npm-cli.js" if executable else None
        if entry is None or not entry.is_file():
            raise ManifestError("npm 的 Node.js 入口不可用, 请检查 Node.js 安装.")
        return ("node", str(entry), *argv[1:])
    return argv


# //// 逐项替换命令中的已声明占位符 [@x380kkm 2026-09-03] ////
def _replace_placeholders(value: str, replacements: dict[str, str]) -> str:
    for placeholder, replacement in replacements.items():
        value = value.replace(placeholder, replacement)
    return value


# //// 在组件来源目录执行版本, 健康或更新检查命令 [@x380kkm 2026-09-03] ////
def run_component_command(
    command: tuple[str, ...], component: Component, manifest_path: Path, timeout: int = 60,
) -> CommandResult:
    if not command:
        return CommandResult((), 125, "", "未声明命令")
    argv = expand_command(command, component, manifest_path)
    try:
        completed = subprocess.run(
            argv,
            cwd=component.source,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return CommandResult(argv, completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return CommandResult(argv, 124, stdout.strip(), (stderr.strip() + "\n命令超时").strip())
    except OSError as exc:
        return CommandResult(argv, 127, "", str(exc))


# //// 为文本输出提取稳定且紧凑的命令摘要 [@x380kkm 2026-09-03] ////
def result_summary(result: CommandResult, limit: int = 220) -> str:
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    if not lines:
        return f"exit {result.returncode}"
    summary = lines[-1] if result.is_ok else lines[0]
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


# //// 限制回执中的原始命令输出大小 [@x380kkm 2026-09-03] ////
def bounded_output(value: str, limit: int = 4096) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


# //// 执行 Git 命令并保留退出状态和输出 [@x380kkm 2026-09-03] ////
def run_git(repository: Path, *args: str, timeout: int = 45) -> CommandResult:
    argv = ("git", "-C", str(repository), *args)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return CommandResult(argv, completed.returncode, completed.stdout.rstrip(), completed.stderr.strip())
    except subprocess.TimeoutExpired:
        return CommandResult(argv, 124, "", "Git 查询超时")
    except OSError as exc:
        return CommandResult(argv, 127, "", str(exc))


# //// 读取 Git 本地提交与远端分支头以判断来源差异 [@x380kkm 2026-09-03] ////
def check_git_update(component: Component) -> dict[str, Any]:
    repository = component.update.get("repository", ".")
    remote = component.update.get("remote", "origin")
    branch = component.update.get("branch", "main")
    if not all(isinstance(value, str) and value for value in (repository, remote, branch)):
        return {"status": "error", "detail": "Git 更新策略字段无效"}
    if Path(repository).is_absolute():
        return {"status": "error", "detail": "update.repository 必须使用相对路径"}
    repository_path = (component.source / repository).resolve()
    local = run_git(repository_path, "rev-parse", "HEAD")
    if not local.is_ok:
        return {"status": "error", "detail": result_summary(local), "repository": str(repository_path)}
    remote_head = run_git(repository_path, "ls-remote", "--exit-code", remote, f"refs/heads/{branch}")
    if not remote_head.is_ok:
        return {
            "status": "unavailable",
            "local": local.stdout,
            "detail": result_summary(remote_head),
            "repository": str(repository_path),
        }
    remote_commit = remote_head.stdout.split()[0]
    dirty_paths, ignored_dirty = git_dirty_paths(repository_path, component)
    status = "dirty" if dirty_paths else ("current" if local.stdout == remote_commit else "different")
    return {
        "status": status,
        "local": local.stdout,
        "remote": remote_commit,
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths,
        "ignored_dirty": ignored_dirty,
        "repository": str(repository_path),
    }


# //// 对干净 Git 工作树执行一次 fetch 和 fast-forward [@x380kkm 2026-09-03] ////
def apply_git_fast_forward(component: Component) -> CommandResult:
    repository = component.update.get("repository", ".")
    remote = component.update.get("remote", "origin")
    branch = component.update.get("branch", "main")
    if not all(isinstance(value, str) and value for value in (repository, remote, branch)):
        return CommandResult((), 2, "", "Git 更新策略字段无效")
    if Path(repository).is_absolute():
        return CommandResult((), 2, "", "update.repository 必须使用相对路径")
    repository_path = (component.source / repository).resolve()
    dirty_paths, _ = git_dirty_paths(repository_path, component)
    if dirty_paths:
        return CommandResult(("git", "-C", str(repository_path), "status"), 3, "", f"工作树存在未提交更改: {', '.join(dirty_paths)}")
    timeout = command_timeout(component, "apply_timeout", 600)
    fetched = run_git(repository_path, "fetch", "--no-tags", remote, branch, timeout=timeout)
    if not fetched.is_ok:
        return fetched
    dirty_after_fetch, _ = git_dirty_paths(repository_path, component)
    if dirty_after_fetch:
        return CommandResult(("git", "-C", str(repository_path), "status"), 3, fetched.output, "fetch 后工作树出现未提交更改: " + ", ".join(dirty_after_fetch))
    ancestor = run_git(repository_path, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    if not ancestor.is_ok:
        return CommandResult(ancestor.argv, 3, fetched.output, "远端分支无法快进到当前工作树")
    merged = run_git(repository_path, "merge", "--ff-only", "FETCH_HEAD", timeout=timeout)
    output = "\n".join(part for part in (fetched.output, merged.output) if part)
    return CommandResult(merged.argv, merged.returncode, output, merged.stderr)


# //// 过滤被 manifest 标记为生成物的 Git 状态路径 [@x380kkm 2026-09-03] ////
def git_dirty_paths(repository: Path, component: Component) -> tuple[list[str], list[str]]:
    result = run_git(repository, "status", "--porcelain", "--untracked-files=all")
    if not result.is_ok:
        return [result_summary(result)], []
    ignored = component.update.get("ignored_paths", [])
    ignored = [path.strip("/\\") for path in ignored if isinstance(path, str) and path.strip("/\\")]
    dirty: list[str] = []
    ignored_dirty: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip() if len(line) >= 4 else line.strip()
        path = path.replace("\\", "/")
        path = path.split(" -> ")[-1]
        if any(path == prefix or path.startswith(prefix + "/") for prefix in ignored):
            ignored_dirty.append(path)
        else:
            dirty.append(path)
    return dirty, ignored_dirty


# //// 从更新策略中读取委托检查命令 [@x380kkm 2026-09-03] ////
def update_check_command(component: Component) -> tuple[str, ...]:
    raw = component.update.get("check")
    if isinstance(raw, dict):
        raw = raw.get("command")
    return read_command(raw, "update.check", component.id)


# //// 从更新策略中读取受控应用命令 [@x380kkm 2026-09-03] ////
def update_apply_command(component: Component) -> tuple[str, ...]:
    raw = component.update.get("apply")
    if isinstance(raw, dict):
        raw = raw.get("command")
    return read_command(raw, "update.apply", component.id)


# //// 返回组件声明的更新应用方式 [@x380kkm 2026-09-03] ////
def update_apply_kind(component: Component) -> str:
    raw = component.update.get("apply")
    if isinstance(raw, list):
        return "delegated"
    if isinstance(raw, dict) and raw.get("kind") in {"git-fast-forward", "source-aware"}:
        return str(raw["kind"])
    return "unsupported"




# //// 判断 source-aware 组件是否位于 Git checkout [@x380kkm 2026-09-03] ////
def has_git_source(component: Component) -> bool:
    repository = component.update.get("repository", ".")
    return (
        isinstance(repository, str)
        and not Path(repository).is_absolute()
        and (component.source / repository / ".git").exists()
    )


# //// 读取更新阶段的正整数超时配置 [@x380kkm 2026-09-03] ////
def command_timeout(component: Component, field: str, default: int) -> int:
    value = component.update.get(field, default)
    if not isinstance(value, int) or value <= 0:
        raise ManifestError(f"{component.id}.update.{field} 必须是正整数")
    return value


# //// 把上游更新检查的输出映射为统一状态 [@x380kkm 2026-09-03] ////
def classify_update_result(result: CommandResult) -> tuple[str, str]:
    output = result.output
    if not result.is_ok:
        return "unavailable", result_summary(result)
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("status") == "update_available":
            return "available", result_summary(result)
        if isinstance(value, dict) and value.get("status") == "silent":
            reason = str(value.get("reason", "checked"))
            if reason == "current":
                return "current", reason
            if reason in {"check-failed", "cache-unavailable", "invalid-manifest", "invalid-local-release"}:
                return "unavailable", reason
            return "checked", reason
    versions = re.findall(r"\b(?:current|latest)\s+v?([0-9][0-9A-Za-z.+-]*)", output, flags=re.IGNORECASE)
    if len(versions) >= 2:
        return ("current" if versions[0] == versions[1] else "available"), result_summary(result)
    lower = output.lower()
    if "update available" in lower or "new version" in lower:
        return "available", result_summary(result)
    if "latest" in lower or "current" in lower:
        return "current", result_summary(result)
    return "checked", result_summary(result)


# //// 按更新策略汇总一个组件的候选状态 [@x380kkm 2026-09-03] ////
def check_component_update(component: Component, manifest_path: Path) -> dict[str, Any]:
    strategy = component.update.get("strategy", "manual")
    base = {
        "id": component.id,
        "strategy": strategy,
        "policy": component.update.get("policy", "manual"),
    }
    if not component.source.is_dir():
        return base | {"status": "missing", "detail": "来源目录不存在"}
    if strategy == "source-aware":
        if has_git_source(component):
            return base | check_git_update(component)
        strategy = "delegated"
    if strategy in {"delegated", "command", "git-fast-forward"}:
        command = update_check_command(component)
        if not command:
            if strategy == "git-fast-forward":
                return base | check_git_update(component)
            return base | {"status": "unsupported", "detail": "未声明 update.check"}
        result = run_component_command(command, component, manifest_path, timeout=command_timeout(component, "timeout", 90))
        status, detail = classify_update_result(result)
        return base | {
            "status": status,
            "detail": detail,
            "check_exit": result.returncode,
            "check_output": bounded_output(result.output),
        }
    if strategy == "git":
        return base | check_git_update(component)
    if strategy in {"bundled", "host"}:
        return base | {"status": strategy, "detail": component.update.get("owner", "host")}
    return base | {"status": "manual", "detail": "由策略所有者检查"}


# //// 收集组件版本和健康状态而不把结果打印到标准输出 [@x380kkm 2026-09-03] ////
def doctor_records(
    components: list[Component],
    manifest_path: Path,
    *,
    check_dependencies: bool = True,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    status_by_id: dict[str, str] = {}
    for component in components:
        dependency_issues = [
            dep for dep in component.dependencies
            if check_dependencies and status_by_id.get(dep) != "ok"
        ]
        if not component.source.is_dir():
            version = CommandResult((), 127, "", "来源目录不存在")
            health = version
            status = "missing"
        else:
            version = (
                run_component_command(component.version_command, component, manifest_path)
                if component.version_command
                else CommandResult((), 0, "host-release", "")
            )
            health = run_component_command(
                component.health_command,
                component,
                manifest_path,
                timeout=command_timeout(component, "health_timeout", 90),
            )
            status = "ok" if version.is_ok and health.is_ok and not dependency_issues else "fail"
        status_by_id[component.id] = status
        records.append({
            "id": component.id,
            "status": status,
            "version": result_summary(version),
            "version_exit": version.returncode,
            "health": result_summary(health),
            "health_exit": health.returncode,
            "dependency_issues": dependency_issues,
        })
    return records


# //// 调用组件自己的发行版更新入口并核对结果 [@x380kkm 2026-10-19] ////
def apply_component_update(component: Component, manifest_path: Path) -> dict[str, Any]:
    command = update_apply_command(component)
    if update_apply_kind(component) == "source-aware":
        command = read_command(component.update["apply"].get("fallback"), "update.apply.fallback", component.id)
    if not command:
        return {"id": component.id, "status": "unsupported", "apply_exit": None}
    applied = run_component_command(command, component, manifest_path,
                                    timeout=command_timeout(component, "apply_timeout", 600))
    after = doctor_records([component], manifest_path, check_dependencies=False)[0]
    return {"id": component.id, "status": "updated" if applied.is_ok and after["status"] == "ok" else "failed",
            "apply_exit": applied.returncode, "apply_output": bounded_output(applied.output),
            "after": after}
