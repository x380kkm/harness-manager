# audience: internal
# # codex-host-observation
"""宿主配置通过字段白名单投影. 开关, 安装记录和缓存分别保留其来源位置与证据范围."""
from __future__ import annotations

from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .codex_inventory_io import InventoryInput, object_field, text_field

CONFIG_FIELDS = ("model", "model_provider", "model_reasoning_effort", "approval_policy", "sandbox_mode",
                 "project_doc_max_bytes", "project_doc_fallback_filenames", "personality", "service_tier")


# //// 投影指定配置字段中的普通标量 [@x380kkm 2026-09-06] ////
def configuration_summary(config: dict) -> dict:
    summary = {}
    for key in CONFIG_FIELDS:
        value = config.get(key)
        if isinstance(value, (str, bool, int)):
            summary[key] = text_field(value) if isinstance(value, str) else value
        elif isinstance(value, list) and all(isinstance(entry, str) for entry in value):
            summary[key] = [text_field(entry, limit=100) for entry in value[:30]]
    summary["featureFlags"] = {name: flag for name, flag in object_field(config.get("features")).items()
                               if isinstance(flag, bool)}
    return summary


# //// 识别端点传输与本机范围并保留认证值在来源中 [@x380kkm 2026-09-06] ////
def endpoint_summary(config: dict) -> dict:
    summary = {"runtime": "运行状态未知", "environmentKeys": sorted(object_field(config.get("env")))}
    if isinstance(config.get("command"), str):
        summary["transport"] = "stdio"
        arguments = config.get("args", [])
        summary["argumentCount"] = len(arguments) if isinstance(arguments, list) else 0
    elif isinstance(config.get("url"), str):
        summary["transport"] = "HTTP"
        try:
            host = urlsplit(config["url"]).hostname
            summary["endpointScope"] = "本机" if host in {"localhost", "127.0.0.1", "::1"} else "远端"
        except ValueError:
            summary["endpointScope"] = "地址格式需要检查"
    return summary


# //// 读取用户级宿主设置并拆分端点和插件开关 [@x380kkm 2026-09-06] ////
def scan_configuration(state: InventoryInput, root: Path) -> tuple[dict, dict[str, str]]:
    path = root / "config.toml"
    content = state.read_text(path, root)
    config = {}
    if content is not None:
        try:
            config = tomllib.loads(content)
        except (ValueError, RecursionError):
            state.diagnose(path, "invalid-toml", "Codex 配置需要有效的 TOML 内容.")
    if not path.exists():
        state.diagnose(path, "missing-config", "此用户根下的 Codex 配置文件缺少来源.")
        return config, {}
    carrier = state.add(path, "Codex 用户配置", "config", "模型, 权限和内容发现设置的安全摘要.",
                        status="配置文件存在; 生效值由宿主解析", details=configuration_summary(config))
    for name, value in object_field(config.get("mcp_servers")).items():
        if not isinstance(value, dict):
            continue
        enabled = value.get("enabled", True)
        status = "配置禁用" if enabled is False else "配置启用; 运行状态未知"
        endpoint = state.add(path, name, "mcp", "MCP 端点声明.", locator="mcp:" + name,
                             status=status, details=endpoint_summary(value))
        state.connect(carrier, endpoint, "声明端点")
    plugins = {}
    for name, value in object_field(config.get("plugins")).items():
        enabled = value.get("enabled") if isinstance(value, dict) else value
        status = "配置启用" if enabled is True else "配置禁用" if enabled is False else "配置声明存在"
        plugins[name] = state.add(path, name, "plugin", "用户配置中的插件开关.", locator="plugin:" + name,
                                 status=status, details={"observation": "配置开关", "enabled": enabled if isinstance(enabled, bool) else None,
                                                        "runtime": "运行状态未知"})
        state.connect(carrier, plugins[name], "声明插件开关")
    skills = object_field(config.get("skills")).get("config", [])
    config["_skill_settings"] = skills if isinstance(skills, list) else []
    return config, plugins


# //// 读取生命周期事件数量并保留命令在宿主文件中 [@x380kkm 2026-09-06] ////
def scan_hooks(state: InventoryInput, root: Path) -> None:
    path = root / "hooks.json"
    if not path.exists():
        return
    config = state.read_json(path, root)
    hooks = object_field(config.get("hooks"))
    events = {}
    for name, entries in hooks.items():
        if isinstance(entries, list):
            events[name] = sum(len(entry["hooks"]) for entry in entries
                               if isinstance(entry, dict) and isinstance(entry.get("hooks"), list))
    state.add(path, "Codex 生命周期设置", "hook", "按事件统计已登记的处理项.",
              status="配置文件存在", details={"eventHandlers": events, "handlerCount": sum(events.values())})


# //// 观察插件缓存的固定目录层级 [@x380kkm 2026-09-06] ////
def plugin_directories(state: InventoryInput, cache: Path) -> list[Path]:
    if not cache.is_dir():
        return []
    result = []
    try:
        for marketplace in sorted(cache.iterdir()):
            if not marketplace.is_dir() or not state.contains(marketplace, cache):
                continue
            for plugin in sorted(marketplace.iterdir()):
                if plugin.is_dir() and not plugin.name.startswith((".", "plugin-install-", "plugin-backup-")) and state.contains(plugin, cache):
                    result.append(plugin)
                    if len(result) >= 500:
                        state.diagnose(cache, "scan-budget", "插件目录达到盘点预算.")
                        return result
    except OSError:
        state.diagnose(cache, "unreadable-directory", "插件缓存目录无法完整列出.")
    return result


# //// 读取插件包内声明的端点配置 [@x380kkm 2026-09-06] ////
def scan_plugin_endpoints(state: InventoryInput, version: Path, manifest: dict, plugin: str) -> None:
    declaration = manifest.get("mcpServers")
    path = version / ".codex-plugin/plugin.json"
    if isinstance(declaration, str):
        relative = Path(declaration)
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            state.diagnose(path, "external-plugin-config", "插件端点配置需要包内相对路径.")
            return
        path = version / relative
        declarations = object_field(state.read_json(path, version).get("mcpServers"))
    else:
        declarations = object_field(declaration)
    for name, value in declarations.items():
        if isinstance(value, dict):
            node = state.add(path, name, "mcp", "插件缓存中的 MCP 端点声明.", locator="mcp:" + name,
                             scope="plugin", status="插件缓存声明; 运行状态未知", details=endpoint_summary(value))
            state.connect(plugin, node, "声明插件端点")


# //// 将安装记录和各缓存版本作为独立来源登记 [@x380kkm 2026-09-06] ////
def scan_plugins(state: InventoryInput, root: Path, configured: dict[str, str]) -> list[tuple[Path, str]]:
    cache = root / "plugins/cache"
    skill_roots = []
    for plugin in plugin_directories(state, cache):
        key = plugin.name + "@" + plugin.parent.name
        record_path = plugin / ".codex-remote-plugin-install.json"
        installation = None
        if record_path.exists():
            record = state.read_json(record_path, cache)
            if isinstance(record.get("remote_plugin_id"), str):
                installation = state.add(record_path, key, "plugin", "远端插件安装记录.", status="已安装记录; 缓存版本另行观察",
                                         details={"observation": "安装记录", "runtime": "运行状态未知"})
                if key in configured:
                    state.connect(configured[key], installation, "对应安装记录")
        try:
            versions = sorted(path for path in plugin.iterdir() if path.is_dir() and not path.name.startswith("."))
        except OSError:
            state.diagnose(plugin, "unreadable-directory", "插件版本目录无法列出.")
            continue
        for version in versions[:100]:
            manifest_path = version / ".codex-plugin/plugin.json"
            if not manifest_path.is_file() or not state.contains(version, cache):
                continue
            manifest = state.read_json(manifest_path, cache)
            if not manifest:
                continue
            name = text_field(manifest.get("name"), plugin.name, 160)
            details = {"observation": "缓存版本", "plugin": key, "marketplace": plugin.parent.name,
                       "version": text_field(manifest.get("version"), version.name, 100),
                       "versionDirectory": version.name, "hasInstallationRecord": installation is not None,
                       "runtime": "运行状态未知"}
            if key in configured:
                details["configuredEnabled"] = state.items[configured[key]]["details"]["enabled"]
            cached = state.add(manifest_path, name + " · " + details["version"], "plugin",
                               text_field(manifest.get("description"), "插件缓存清单."),
                               status="缓存版本存在", details=details)
            if installation:
                state.connect(installation, cached, "对应缓存版本")
            if key in configured:
                state.connect(configured[key], cached, "对应缓存版本")
            scan_plugin_endpoints(state, version, manifest, cached)
            skill_roots.append((version / "skills", cached))
    return skill_roots
