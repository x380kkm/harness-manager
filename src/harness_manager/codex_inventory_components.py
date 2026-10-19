# audience: internal
# # codex-component-observation
"""组件清单和安装来源记录各自保存维护归属. 来源路径只参与关联, 组件命令由其维护入口执行."""
from __future__ import annotations

from pathlib import Path

from .codex_inventory_io import InventoryInput, object_field, text_field


# //// 读取组件来源登记并关联独立维护位置 [@x380kkm 2026-09-06] ////
def scan_components(state: InventoryInput, root: Path, user_root: Path) -> None:
    path = root / "manager/components.json"
    if not path.exists():
        return
    manifest = state.read_json(path, root)
    entries = manifest.get("components", [])
    if not isinstance(entries, list):
        state.diagnose(path, "invalid-components", "组件清单需要 components 数组.")
        return
    record_path = root / "managed/clean-vibing.json"
    records = object_field(state.read_json(record_path, root).get("sources"))
    components = {}
    for entry in entries[:500]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        key = entry["id"]
        identity = object_field(entry.get("identity"))
        install = object_field(entry.get("install"))
        update = object_field(entry.get("update"))
        capabilities = entry.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, list) else []
        details = {"componentId": key, "componentKind": text_field(identity.get("kind")),
                   "owner": text_field(install.get("owner")), "updateStrategy": text_field(update.get("strategy")),
                   "runtime": "运行状态未知", "capabilities": [text_field(value) for value in capabilities
                                                                    if isinstance(value, str)][:50]}
        component = state.add(path, text_field(identity.get("name"), key), "component", "独立组件的维护归属与来源登记.",
                              locator="component:" + key, details=details)
        components[key] = component
        source = records.get(key)
        if isinstance(source, str) and source and "://" not in source:
            location = Path(source)
            if location.is_absolute():
                source_node = state.add(location, location.name or key, "source", "组件管理器登记的维护来源.",
                                        locator="registered-source", status="来源已登记; 内容由组件维护入口读取",
                                        details={"recordPath": str(record_path), "observation": "维护来源登记"})
                state.connect(component, source_node, "登记维护来源")
        for target in install.get("targets", []) if isinstance(install.get("targets"), list) else []:
            if not isinstance(target, dict):
                continue
            target_kind = target.get("root")
            if not isinstance(target_kind, str):
                continue
            target_root = {"agents": user_root / ".agents", "codex": root}.get(target_kind)
            relative = target.get("path")
            if target_root is None or not isinstance(relative, str):
                continue
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                continue
            location = target_root / relative_path
            for node in tuple(state.items.values()):
                if node["kind"] == "skill" and Path(node["path"]).parent == location:
                    state.connect(component, node["id"], "声明安装目标")
    for entry in entries[:500]:
        if not isinstance(entry, dict) or entry.get("id") not in components:
            continue
        dependencies = object_field(entry.get("dependencies"))
        for relation, label in (("required", "依赖"), ("optional", "可选配合")):
            values = dependencies.get(relation, [])
            for target in values if isinstance(values, list) else []:
                if isinstance(target, str) and target in components:
                    state.connect(components[entry["id"]], components[target], label)
