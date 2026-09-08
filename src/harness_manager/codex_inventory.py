# audience: internal
# # codex-inventory
"""用户默认盘点读取 Codex 的静态载体. 本机观察与 Manager 登记分别保存, 导入草稿引用完整来源文件."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock

from .codex_inventory_components import scan_components
from .codex_inventory_host import scan_configuration, scan_hooks, scan_plugins
from .codex_inventory_io import InventoryInput, object_field, text_field
from .importers import import_document, skill_metadata
from .inventory_groups import group_inventory
from .protocol import document_identity
from .sources import SourceReader


# //// 将本机静态文件与用户内容登记并列观察 [@x380kkm 2026-09-06] ////
class CodexInventory:
    def __init__(self, user_root: Path, codex_root: Path | None = None) -> None:
        self.user_root = user_root.expanduser().absolute()
        configured = os.environ.get("CODEX_HOME") if self.user_root.resolve() == Path.home().resolve() else None
        self.root = (codex_root or (Path(configured) if configured else self.user_root / ".codex")).expanduser().absolute()
        self._cached_state: InventoryInput | None = None
        self._cached_documents_key: str | None = None
        self._cached_result: dict | None = None
        self._managed_observation: InventoryInput | None = None
        self._root_targets: dict[Path, Path] = {}
        self._cache_lock = RLock()

    # //// 清除来源盘点缓存并使下一次请求重新读取 [@x380kkm 2026-09-07] ////
    def invalidate(self) -> None:
        with self._cache_lock:
            self._cached_state = None
            self._cached_documents_key = None
            self._cached_result = None
            self._managed_observation = None

    # //// 预先观察固定宿主入口与来源根的存在性 [@x380kkm 2026-09-07] ////
    def _observe_entrypoints(self, state: InventoryInput) -> None:
        paths = [self.root / name for name in ("config.toml", "hooks.json", "AGENTS.md", "AGENTS.override.md", "manager/components.json")]
        paths.extend(self.user_root / name for name in ("AGENTS.md", "AGENTS.override.md"))
        paths.extend([self.user_root / ".agents/skills", self.root / "skills", self.root / "plugins/cache"])
        for path in paths:
            state.observe(path)
        for root in (self.user_root, self.root, self.user_root / ".agents/skills", self.root / "skills", self.root / "plugins/cache"):
            state.pin_root(root)

    # //// 观察尚未提供清单的插件版本入口 [@x380kkm 2026-09-07] ////
    def _observe_plugin_manifests(self, state: InventoryInput) -> None:
        cache = self.root / "plugins/cache"
        for plugin in tuple(state.observed):
            if not plugin.is_relative_to(cache) or len(plugin.relative_to(cache).parts) != 2:
                continue
            if not state.contains(plugin, cache):
                continue
            try:
                versions = sorted(path for path in plugin.iterdir() if path.is_dir() and not path.name.startswith("."))
            except OSError:
                continue
            for version in versions[:100]:
                state.observe(version)
                state.observe(version / ".codex-plugin/plugin.json")

    # //// 选择可复用的来源扫描结果并按声明重新匹配管理身份 [@x380kkm 2026-09-07] ////
    def _cached_scan(self, documents: list[dict]) -> dict:
        key = json.dumps(documents, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if self._cached_state is None or not self._cached_state.is_current():
            self._cached_state = self._scan()
            if not self._cached_state.is_current():
                self._cached_state = self._scan()
                if not self._cached_state.is_current():
                    self._cached_state.diagnose(self.root, "inventory-changing", "部分来源在盘点期间发生变化, 下一次读取会重新盘点.")
            self._cached_documents_key = None
        if self._cached_documents_key != key or self._managed_observation is None or not self._managed_observation.is_current():
            state = deepcopy(self._cached_state)
            self._managed_observation = InventoryInput()
            self._mark_managed(state, documents, self._managed_observation)
            self._cached_result = self._build_snapshot(state)
            self._cached_documents_key = key
        return deepcopy(self._cached_result)

    # //// 组合盘点摘要并隔离内部缓存状态 [@x380kkm 2026-09-07] ////
    def _build_snapshot(self, state: InventoryInput) -> dict:
        items = list(state.items.values())
        counts = dict(Counter(item["kind"] for item in items))
        file_counts = {kind: len({Path(item["path"]).resolve() for item in items if item["kind"] == kind})
                       for kind in ("skill", "instruction")}
        importable_paths = {Path(item["path"]).resolve() for item in items if item["importable"]}
        registered_paths = {Path(item["path"]).resolve() for item in items if item["importable"] and item["managedIds"]}
        configuration_paths = {Path(item["path"]).resolve() for item in items if item["kind"] in ("config", "mcp", "plugin")}
        result = {"root": str(self.root), "userRoot": str(self.user_root),
                  "scannedAt": datetime.now(timezone.utc).isoformat(), "items": items, "edges": state.edges,
                  "groups": group_inventory(items, state.edges, self.root, self.user_root),
                  "diagnostics": state.diagnostics,
                  "coverage": {"counts": counts, "fileCounts": file_counts, "filesRead": len(state.read_paths),
                               "importableFiles": len(importable_paths), "registeredFiles": len(registered_paths),
                               "hostConfigurationFiles": len(configuration_paths), "observedItems": len(items),
                               "capabilities": {"inventory": True, "contentRegistration": True,
                                                "hostDeployment": False, "hostConfigurationWriteback": False},
                               "statements": ["完整 Skill 和说明按来源文件登记, 原始文档保留完整结构.",
                                              "内容登记记录来源关联, 宿主文件及其加载归属保持原状.",
                                              "配置与插件记录按文件去重统计, 端点保留为独立观察节点.",
                                              "宿主投放与配置写回由对应适配器提供.", "会话加载与端点运行状态由宿主运行观察确认."]}}
        return result

    # //// 返回内容节点, 来源关系和能力边界 [@x380kkm 2026-09-06] ////
    def snapshot(self, documents: list[dict]) -> dict:
        with self._cache_lock:
            return self._cached_scan(documents)

    # //// 从仍然存在的完整载体生成独立来源草稿 [@x380kkm 2026-09-06] ////
    def prepare_import(self, identifier: str) -> dict:
        state = self._scan()
        item = state.items.get(identifier)
        if item is None or identifier not in state.import_roots:
            raise ValueError("所选条目需要可读取的完整 Skill 或说明文件.")
        reader = SourceReader(state.import_roots[identifier], [])
        document = import_document(reader, item["path"], item["kind"])
        document["id"] += "-" + identifier.removeprefix("codex:")[:12]
        document["metadata"]["name"] = item["name"]
        if item["scope"] == "directory":
            document["metadata"]["description"] = "用户目录范围的说明文件. 来源目录: " + str(self.user_root)
            document["contributions"][0]["scope"] = {
                "contract": {"id": "manager.scope", "range": "^1.0.0"},
                "selector": {"path": self.user_root.as_posix()},
            }
        return document

    # //// 汇集固定宿主载体中的静态观察 [@x380kkm 2026-09-06] ////
    def _scan(self) -> InventoryInput:
        state = InventoryInput(self._root_targets)
        self._observe_entrypoints(state)
        config, plugins = scan_configuration(state, self.root)
        scan_hooks(state, self.root)
        self._instructions(state, self.root, "Codex 用户说明", "user", self.root)
        self._instructions(state, self.user_root, "用户目录说明", "directory", self.user_root)
        for root, scope, status in ((self.user_root / ".agents/skills", "user", "用户 Skill 路径存在"),
                                    (self.root / "skills", "codex", "Codex Skill 路径存在")):
            self._skills(state, root, scope, status, config)
        for root, plugin in scan_plugins(state, self.root, plugins):
            self._skills(state, root, "plugin", "插件缓存中的 Skill; 会话加载状态未知", config, plugin)
        scan_components(state, self.root, self.user_root)
        self._observe_plugin_manifests(state)
        self._link_same_names(state)
        return state

    # //// 保留整份说明及其来源范围 [@x380kkm 2026-09-07] ////
    def _instructions(self, state: InventoryInput, directory: Path, name: str, scope: str, root: Path) -> None:
        override = directory / "AGENTS.override.md"
        contents = {}
        for path in (override, directory / "AGENTS.md"):
            if not path.exists():
                continue
            if path.is_symlink():
                state.diagnose(path, "linked-instruction", "说明文件通过链接提供, 目标正文需要显式来源授权.")
                contents[path] = None
            else:
                contents[path] = state.read_text(path, root)
        preferred = override if (contents.get(override) or "").strip() else directory / "AGENTS.md"
        for path, content in contents.items():
            selection = "此位置的优先候选" if path == preferred else "此位置有更高优先级候选"
            if not (content or "").strip():
                selection = "文件内容为空或无法读取"
            details = {"discovery": "用户级说明候选" if scope == "user" else "用户目录范围的说明候选",
                       "selection": selection,
                       "runtime": "会话加载状态未知", "provenance": {"state": "unverified", "basis": "local-file"}}
            state.add(path, name + " · " + path.name, "instruction", "保留原始结构的完整说明文件.",
                      scope=scope, status="说明文件存在", details=details, content=content,
                      line=1, import_root=root if content is not None else None)

    # //// 读取 Skill 元数据并保留完整正文来源 [@x380kkm 2026-09-06] ////
    def _skills(self, state: InventoryInput, root: Path, scope: str, status: str, config: dict, plugin: str | None = None) -> None:
        for path in state.find(root, "SKILL.md"):
            content = state.read_text(path, root)
            metadata = {}
            valid = content is not None
            if content is not None:
                try:
                    metadata = skill_metadata(content)
                    if not isinstance(metadata.get("name", path.parent.name), str) or not isinstance(metadata.get("description", ""), str):
                        raise ValueError("Skill 名称与摘要需要文本.")
                except (ValueError, RecursionError):
                    state.diagnose(path, "invalid-skill", "Skill 头部元数据需要有效的 YAML 对象.")
                    valid = False
            name = text_field(metadata.get("name"), path.parent.name, 160)
            description = text_field(metadata.get("description"), "Skill 文件中的任务说明.")
            details = {"discovery": "插件缓存" if plugin else "用户目录", "runtime": "会话加载状态未知",
                       "version": text_field(object_field(metadata.get("metadata")).get("version"), "来源文件维护"),
                       "nameSource": "frontmatter" if "name" in metadata else "directory"}
            selected_status = status
            if plugin:
                enabled = state.items[plugin]["details"].get("configuredEnabled")
                if isinstance(enabled, bool):
                    details["pluginConfiguredEnabled"] = enabled
                if enabled is False:
                    selected_status = "所属插件配置禁用; Skill 缓存存在"
            for setting in config.get("_skill_settings", []):
                if isinstance(setting, dict) and isinstance(setting.get("path"), str):
                    target = Path(setting["path"]).expanduser()
                    if target.is_absolute() and target.resolve() in {path.resolve(), path.parent.resolve()}:
                        details["configuredEnabled"] = setting.get("enabled") if isinstance(setting.get("enabled"), bool) else None
                        if setting.get("enabled") is False:
                            selected_status = "配置禁用; 文件存在"
            node = state.add(path, name, "skill", description, scope=scope, status=selected_status, details=details,
                             content=content, line=1, import_root=root if valid else None)
            if plugin:
                details["pluginId"] = plugin
                state.connect(plugin, node, "包含 Skill")

    # //// 连接同名内容并保留各自的维护来源 [@x380kkm 2026-09-06] ////
    def _link_same_names(self, state: InventoryInput) -> None:
        names = {}
        for item in state.items.values():
            if item["kind"] == "skill":
                existing = names.setdefault(item["name"].casefold(), item["id"])
                if existing != item["id"]:
                    state.connect(existing, item["id"], "同名 Skill, 来源分别维护")

    # //// 依据来源文件匹配已有内容登记 [@x380kkm 2026-09-06] ////
    def _mark_managed(self, state: InventoryInput, documents: list[dict], observations: InventoryInput | None = None) -> None:
        by_path = {str(Path(item["path"]).resolve()): item for item in state.items.values() if item["importable"]}
        for document in documents:
            if not isinstance(document, dict) or document.get("kind") != "Plugin":
                continue
            sources = {source["id"]: source.get("source") for source in document.get("sources", [])
                       if isinstance(source, dict) and isinstance(source.get("id"), str)}
            for member in document.get("contributions", []):
                if not isinstance(member, dict):
                    continue
                reference = member.get("source", "")
                source = sources.get(reference.removeprefix("source:")) if isinstance(reference, str) else reference
                source = object_field(source)
                locator = source.get("locator")
                entry = object_field(member.get("payload")).get("entry")
                if not isinstance(locator, str) or not isinstance(entry, str) or "://" in locator:
                    continue
                if object_field(source.get("resolver")).get("id") != "manager.source/path":
                    continue
                location = Path(locator) / source.get("subpath", "") / entry
                if location.is_absolute():
                    if observations is not None:
                        for path in (Path(locator), location.parent, location):
                            observations.observe(path)
                    item = by_path.get(str(location.resolve()))
                    if item is not None:
                        identity = document_identity(document)
                        if identity not in item["managedIds"]:
                            item["managedIds"].append(identity)
