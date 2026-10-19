# audience: internal
# # agent-manager-manifest
"""组件清单的数据模型, 路径解析与依赖排序.

来源目录先读取清单声明的环境变量, 再按清单所在目录解析相对路径. 每个组件
使用 identity, install, runtime, health, update, capabilities 与 dependencies
分组, 使 provider 和宿主适配保持独立.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# //// 组件清单错误携带可直接修正的配置说明 [@x380kkm 2026-09-03] ////
class ManifestError(ValueError):
    pass


# //// 保存解析后的组件及其可替换来源 [@x380kkm 2026-09-03] ////
@dataclass(frozen=True)
class Component:
    id: str
    name: str
    kind: str
    source: Path
    source_origin: str
    install: dict[str, Any]
    version_command: tuple[str, ...]
    health_command: tuple[str, ...]
    update: dict[str, Any]
    dependencies: tuple[str, ...]
    optional_dependencies: tuple[str, ...]
    replaceable: bool
    capabilities: tuple[str, ...]


# //// 读取必需的对象字段 [@x380kkm 2026-09-03] ////
def read_object(value: object, field: str, component_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{component_id}.{field} 必须是对象")
    return value


# //// 读取可以省略的分组并提供空对象默认值 [@x380kkm 2026-09-03] ////
def read_optional_object(value: object, field: str, component_id: str) -> dict[str, Any]:
    if value is None:
        return {}
    return read_object(value, field, component_id)


# //// 从环境变量或相对路径解析一个组件来源目录 [@x380kkm 2026-09-03] ////
def resolve_source(raw: object, manifest_path: Path, component_id: str) -> tuple[Path, str]:
    source = read_object(raw, "identity.source", component_id)
    env_name = source.get("env")
    relative = source.get("relative", source.get("path"))
    home_relative = source.get("home_relative")
    if env_name is not None and (not isinstance(env_name, str) or not env_name):
        raise ManifestError(f"{component_id}.identity.source.env 必须是非空字符串")
    if not isinstance(relative, str) or not relative:
        raise ManifestError(f"{component_id}.identity.source.relative 必须是非空字符串")
    if home_relative is not None and (not isinstance(home_relative, str) or not home_relative):
        raise ManifestError(f"{component_id}.identity.source.home_relative 必须是非空字符串")
    if Path(relative).is_absolute():
        raise ManifestError(f"{component_id}.identity.source.relative 必须使用相对路径")
    prefer_home = source.get("prefer_home", False)
    if not isinstance(prefer_home, bool):
        raise ManifestError(f"{component_id}.identity.source.prefer_home 必须是布尔值")
    prefer_registered = source.get("prefer_registered", False)
    if not isinstance(prefer_registered, bool):
        raise ManifestError(f"{component_id}.identity.source.prefer_registered 必须是布尔值")
    if env_name and os.environ.get(env_name):
        return Path(os.environ[env_name]).expanduser().resolve(), f"env:{env_name}"
    relative_source = (manifest_path.parent / relative).resolve()
    if prefer_registered:
        registered_source = read_registered_source(manifest_path, component_id)
        if registered_source is not None:
            return registered_source, "manager-state"
    if prefer_home and home_relative:
        home_source = (Path.home() / home_relative).resolve()
        if home_source.exists():
            return home_source, f"home:{home_relative}"
    if relative_source.exists():
        return relative_source, f"relative:{relative}"
    if home_relative:
        home_source = (Path.home() / home_relative).resolve()
        if home_source.exists():
            return home_source, f"home:{home_relative}"
    registered_source = read_registered_source(manifest_path, component_id)
    if registered_source is not None:
        return registered_source, "manager-state"
    return relative_source, f"relative:{relative}"


# //// 从 manager 自有状态读取外部组件来源 [@x380kkm 2026-09-03] ////
def read_registered_source(manifest_path: Path, component_id: str) -> Path | None:
    state_path = manifest_path.parent.parent / "managed" / "clean-vibing.json"
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    sources = state.get("sources") if isinstance(state, dict) else None
    raw = sources.get(component_id) if isinstance(sources, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    candidate = (manifest_path.parent.parent / Path(raw).expanduser()).resolve()
    return candidate if candidate.exists() else None


# //// 校验并读取一个无 Shell 插值的命令参数列表 [@x380kkm 2026-09-03] ////
def read_command(raw: object, field: str, component_id: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(arg, str) and arg for arg in raw):
        raise ManifestError(f"{component_id}.{field} 必须是字符串数组")
    return tuple(raw)


# //// 从 runtime 或 health 分组读取命令 [@x380kkm 2026-09-03] ////
def read_group_command(entry: dict[str, Any], group: str, field: str, component_id: str) -> tuple[str, ...]:
    block = read_object(entry.get(group), group, component_id)
    raw = block.get(field, block.get(f"{field}_command"))
    return read_command(raw, f"{group}.{field}", component_id)


# //// 读取必需依赖与可选依赖 [@x380kkm 2026-09-03] ////
def read_dependencies(entry: dict[str, Any], component_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dependencies = entry.get("dependencies", {})
    if isinstance(dependencies, list):
        required_raw = dependencies
        optional_raw = []
    else:
        raw = read_object(dependencies, "dependencies", component_id)
        required_raw = raw.get("required", [])
        optional_raw = raw.get("optional", [])
    if not isinstance(required_raw, list) or not all(isinstance(dep, str) and dep for dep in required_raw):
        raise ManifestError(f"{component_id}.dependencies.required 必须是字符串数组")
    if not isinstance(optional_raw, list) or not all(isinstance(dep, str) and dep for dep in optional_raw):
        raise ManifestError(f"{component_id}.dependencies.optional 必须是字符串数组")
    required = tuple(dict.fromkeys(required_raw))
    optional = tuple(dep for dep in dict.fromkeys(optional_raw) if dep not in required)
    return required, optional


# //// 将单个清单项转换为组件对象 [@x380kkm 2026-09-03] ////
def read_component(entry: object, manifest_path: Path, seen: set[str]) -> Component:
    if not isinstance(entry, dict):
        raise ManifestError("components 中的每一项必须是对象")
    component_id = entry.get("id")
    if not isinstance(component_id, str) or not component_id:
        raise ManifestError("组件 id 必须是非空字符串")
    if component_id in seen:
        raise ManifestError(f"组件 id 重复: {component_id}")
    seen.add(component_id)

    identity = read_object(entry.get("identity"), "identity", component_id)
    install = read_optional_object(entry.get("install"), "install", component_id)
    update = read_optional_object(entry.get("update"), "update", component_id)
    name = identity.get("name")
    kind = identity.get("kind")
    if not isinstance(name, str) or not name:
        raise ManifestError(f"{component_id}.identity.name 必须是非空字符串")
    if not isinstance(kind, str) or not kind:
        raise ManifestError(f"{component_id}.identity.kind 必须是非空字符串")
    if "strategy" not in update:
        update["strategy"] = "manual"
    if not isinstance(update.get("strategy"), str) or not update["strategy"]:
        raise ManifestError(f"{component_id}.update.strategy 必须是非空字符串")
    capabilities = entry.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and item for item in capabilities):
        raise ManifestError(f"{component_id}.capabilities 必须是字符串数组")
    source, source_origin = resolve_source(identity.get("source"), manifest_path, component_id)
    dependencies, optional_dependencies = read_dependencies(entry, component_id)
    return Component(
        id=component_id,
        name=name,
        kind=kind,
        source=source,
        source_origin=source_origin,
        install=dict(install),
        version_command=read_group_command(entry, "runtime", "version", component_id),
        health_command=read_group_command(entry, "health", "command", component_id),
        update=dict(update),
        dependencies=dependencies,
        optional_dependencies=optional_dependencies,
        replaceable=bool(install.get("replaceable", False)),
        capabilities=tuple(capabilities),
    )


# //// 读取清单并验证组件引用 [@x380kkm 2026-09-03] ////
def load_manifest(manifest_path: Path, install_root: Path | None = None) -> list[Component]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"找不到清单: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"清单 JSON 无效: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ManifestError("清单 schema 必须为 1")
    entries = raw.get("components")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("清单必须包含 components 数组")
    seen: set[str] = set()
    location = install_root / "manager" / "components.json" if install_root is not None else manifest_path
    components = [read_component(entry, location, seen) for entry in entries]
    component_ids = {component.id for component in components}
    for component in components:
        missing = [dep for dep in component.dependencies if dep not in component_ids]
        missing += [dep for dep in component.optional_dependencies if dep not in component_ids]
        if missing:
            raise ManifestError(f"{component.id} 引用了未知依赖: {', '.join(missing)}")
    topological_order(components)
    return components


# //// 按依赖顺序返回全部组件或给定组件的依赖闭包 [@x380kkm 2026-09-03] ////
def topological_order(components: list[Component], selected: set[str] | None = None) -> list[Component]:
    by_id = {component.id: component for component in components}
    if selected:
        unknown = selected - by_id.keys()
        if unknown:
            raise ManifestError(f"未知组件: {', '.join(sorted(unknown))}")
    ordered: list[Component] = []
    active: set[str] = set()
    complete: set[str] = set()

    # //// 深度优先加入一个组件及其必需依赖 [@x380kkm 2026-09-03] ////
    def visit(component_id: str) -> None:
        if component_id in complete:
            return
        if component_id in active:
            raise ManifestError(f"组件依赖形成环: {component_id}")
        active.add(component_id)
        for dependency in by_id[component_id].dependencies:
            visit(dependency)
        active.remove(component_id)
        complete.add(component_id)
        ordered.append(by_id[component_id])

    for component in components:
        if selected is None or component.id in selected:
            visit(component.id)
    return ordered
