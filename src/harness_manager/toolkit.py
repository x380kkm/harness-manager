# harness-toolkit
"""组件清单由发行包和所选配置提供. 下载来源, 安装副本和构建记录位于配置层的 toolkit 目录."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .components.acquisition import acquire_sources, prepare_sources, source_plan, source_location, update_sources
from .components.graph import build_graph_ir
from .components.installer import (
    InstallError, apply_install_plan, build_install_plan, install_plan_records,
    read_install_state, install_state_path,
)
from .components.manifest import load_manifest, topological_order
from .components.runtime import check_component_update, doctor_records
from .toolkit_catalog import register_skills, retire_skills
from .execution_preferences import register_execution_preferences
from .session_memory import register_session_memory
from .toolkit_rules import register_rules

BUNDLE = Path(__file__).parent / "bundled"
DEFAULT_MANIFEST = BUNDLE / "manager" / "components.json"
RETIRED_COMPONENTS = {"source-adapter-skill"}


# //// 定位独立配置层的受管组件目录 [@x380kkm 2026-10-19] ////
def toolkit_root(catalogs, scope: str) -> Path:
    if scope not in {"user", "project"}:
        raise ValueError("toolkit 的 scope 需要 user 或 project.")
    store = catalogs.select(scope)
    root = store.directory / "toolkit"
    if root.resolve() != root:
        raise ValueError(f"受管工具目录需要位于实际配置层中: {root}")
    return root


# //// 组合组件来源, 安装副本与内容登记 [@x380kkm 2026-10-19] ////
class Toolkit:
    def __init__(self, manager) -> None:
        self.manager = manager

    # //// 读取安装选择与可共享的定制清单 [@x380kkm 2026-10-19] ////
    def configuration(self, scope: str, manifest: str | None, components: list[str] | None):
        root = toolkit_root(self.manager.catalogs, scope)
        settings_path = root.parent / "toolkit.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
        selected_manifest = manifest or (self.manager.catalogs.select(scope).workspace / settings["manifest"] if settings.get("manifest") else None)
        path = self.manager.reader.approved_path(selected_manifest) if selected_manifest else DEFAULT_MANIFEST
        raw = json.loads(path.read_text(encoding="utf-8"))
        selected = components if components is not None else settings.get("components", raw.get("defaults"))
        if selected is not None and (not isinstance(selected, list) or not all(isinstance(item, str) for item in selected)):
            raise ValueError("组件选择需要名称数组.")
        if path == DEFAULT_MANIFEST:
            selected = [item for item in selected if item not in RETIRED_COMPONENTS] if selected is not None else None
            settings = {**settings, "components": [item for item in settings.get("components", [])
                                                   if item not in RETIRED_COMPONENTS]}
        loaded = load_manifest(path, root)
        loaded = [replace(component, source=source_location(component, root)[1], source_origin="managed-git")
                  if component.install.get("git") and not component.source_origin.startswith("env:") else component
                  for component in loaded]
        ordered = topological_order(loaded, set(selected) if selected is not None else None)
        return root, path, ordered, settings

    # //// 列出所选组件及其真实运行位置 [@x380kkm 2026-10-19] ////
    def list(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, _ = self.configuration(scope, manifest, components)
        return {"root": str(root), "manifest": str(path), "components": [
            {"id": component.id, "name": component.name, "source": str(component.source),
             "sourceAvailable": component.source.is_dir(), "sourceOrigin": component.source_origin,
             "git": component.install.get("git"), "dependencies": list(component.dependencies),
             "checks": {"version": list(component.version_command), "health": list(component.health_command),
                        "update": component.update.get("check")}} for component in ordered]}

    # //// 预览下载, 安装目标和来源命令 [@x380kkm 2026-10-19] ////
    def plan(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, _ = self.configuration(scope, manifest, components)
        records, _ = build_install_plan(ordered, DEFAULT_MANIFEST, host_root=root, agents_root=root)
        return {"root": str(root), "scope": scope, "manifest": str(path),
                "sources": source_plan(ordered, root), "targets": install_plan_records(records),
                "bindings": {"host": "harness-manager"}}

    # //// 执行组件自己的健康命令 [@x380kkm 2026-10-19] ////
    def doctor(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, _ = self.configuration(scope, manifest, components)
        return {"root": str(root), "components": doctor_records(ordered, path)}

    # //// 查询独立上游的更新候选 [@x380kkm 2026-10-19] ////
    def check(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, _ = self.configuration(scope, manifest, components)
        return {"root": str(root), "components": [check_component_update(component, path) for component in ordered],
                "installation": self.plan(components, scope, manifest)}

    # //// 从组件依赖生成 Archify 结构输入 [@x380kkm 2026-10-19] ////
    def graph(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        _, _, ordered, _ = self.configuration(scope, manifest, components)
        return build_graph_ir(ordered, {}, {})

    # //// 获取来源并同步安装内容与 Manager 绑定 [@x380kkm 2026-10-19] ////
    def install(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, settings = self.configuration(scope, manifest, components)
        records, _ = build_install_plan(ordered, DEFAULT_MANIFEST, host_root=root, agents_root=root)
        self._check_targets(records)
        acquired = acquire_sources(ordered, root)
        if root.is_dir():
            self._authorize(root)
        builds = prepare_sources(ordered, root, path)
        records, roots = build_install_plan(ordered, DEFAULT_MANIFEST, host_root=root, agents_root=root)
        self._check_targets(records)
        sources = {component.id: self._relative_source(component.source, root) for component in ordered
                   if component.install.get("git") and component.source.exists()}
        applied = apply_install_plan(records, roots, sources=sources)
        self._authorize(root)
        registered = register_skills(self.manager, records, scope)
        if any(component.id == "agent-rules" for component in ordered):
            registered.extend(register_rules(self.manager, root, scope))
        if any(component.id == "agent-manager" for component in ordered):
            registered.extend(register_execution_preferences(self.manager, scope))
        if any(component.id == "session-memory" for component in ordered):
            registered.extend(register_session_memory(self.manager, root, scope))
        retired = self._retire_components(root, scope) if path == DEFAULT_MANIFEST else []
        selected = list(dict.fromkeys([*settings.get("components", []), *(component.id for component in ordered)]))
        configuration = {"components": selected}
        if path != DEFAULT_MANIFEST:
            workspace = self.manager.catalogs.select(scope).workspace
            configuration["manifest"] = self._relative_source(path, workspace)
        self._save_configuration(root, configuration)
        health = doctor_records(ordered, path)
        return {"status": "installed" if all(record["status"] == "ok" for record in health) else "failed",
                "root": str(root), "sources": acquired, "builds": builds, "targets": applied,
                "registered": registered, "retired": retired, "health": health}

    # //// 解除移除组件的来源登记和目标所有权并保留本机文件 [@x380kkm 2026-10-19] ////
    def _retire_components(self, root: Path, scope: str) -> list[dict]:
        retired = retire_skills(self.manager, RETIRED_COMPONENTS, scope)
        state = read_install_state(root)
        targets = state["targets"]
        preserved = [key for key, value in targets.items() if value.get("component") in RETIRED_COMPONENTS]
        if preserved:
            state["targets"] = {key: value for key, value in targets.items() if key not in preserved}
            path = install_state_path(root)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        return [*retired, *({"target": key, "action": "unmanaged", "files": "preserved"} for key in preserved)]

    # //// 更新独立来源后同步受管副本并保留修改冲突 [@x380kkm 2026-10-19] ////
    def update(self, components: list[str] | None = None, scope: str = "user", manifest: str | None = None) -> dict:
        root, path, ordered, _ = self.configuration(scope, manifest, components)
        records, _ = build_install_plan(ordered, DEFAULT_MANIFEST, host_root=root, agents_root=root)
        self._check_targets(records)
        source_plan_records = source_plan(ordered, root)
        if any(record["action"] not in {"existing", "external"} for record in source_plan_records):
            raise InstallError("组件来源尚未安装或来源已改变, 请先查看 toolkit.plan 并执行 install.")
        receipts = update_sources(ordered, path)
        if any(record["status"] not in {"current", "updated", "dependency-ready"} for record in receipts):
            return {"status": "failed", "components": receipts}
        result = self.install(components, scope, manifest)
        return {**result, "status": "updated" if result["status"] == "installed" else "failed", "components": receipts}

    # //// 在文件写入前保护未接管内容和本机编辑 [@x380kkm 2026-10-19] ////
    def _check_targets(self, records) -> None:
        for record in records:
            if record.action in {"modified", "conflict"}:
                raise InstallError(f"受管目录存在需要合并的内容: {record.target} ({record.action}).")
            if record.source.exists():
                if not record.source.is_relative_to(BUNDLE):
                    self.manager.reader.check_access(record.source)
                for path in ([record.source] if record.source.is_file() else record.source.rglob("*")):
                    if path.is_symlink():
                        raise InstallError(f"安装来源包含符号链接, 请确认来源内容: {path}")

    # //// 授权当前 Manager 读取自身安装目录 [@x380kkm 2026-10-19] ////
    def _authorize(self, root: Path) -> None:
        self.manager.reader.roots = tuple(dict.fromkeys([*self.manager.reader.roots, root]))

    # //// 将本机内部路径保存成可搬移的相对位置 [@x380kkm 2026-10-19] ////
    @staticmethod
    def _relative_source(path: Path, root: Path) -> str:
        return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)

    # //// 保存安装选择并隔离下载与运行产物 [@x380kkm 2026-10-19] ////
    @staticmethod
    def _save_configuration(root: Path, settings: dict) -> None:
        path = root.parent / "toolkit.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        (root / ".gitignore").write_text("*\n", encoding="utf-8")
        ignored = root.parent / ".gitignore"
        patterns = ["toolkit/", "backups/", "checkpoints/", "hosts/", "observations/", "session-memory/", "catalog.lock"]
        existing = ignored.read_text(encoding="utf-8") if ignored.is_file() else ""
        missing = [pattern for pattern in patterns if pattern not in existing.splitlines()]
        if missing:
            prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
            ignored.write_text(prefix + "\n".join(missing) + "\n", encoding="utf-8")
