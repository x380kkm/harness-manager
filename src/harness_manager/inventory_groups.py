# audience: internal
# # inventory-groups
"""展示组依据载体身份和明确归属关系聚合观察. 来源分类表示目录归属, 原始节点与状态继续由盘点载体保存."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

OFFICIAL_MARKETS = {"openai-bundled", "openai-primary-runtime", "openai-curated", "openai-curated-remote"}
KIND_CATEGORIES = {"module": "module", "rule": "rule", "hook": "hook", "tool": "tool", "mcp": "tool",
                   "skill": "skill", "instruction": "instruction", "config": "config", "plugin": "plugin", "source": "source"}


# //// 从插件载体位置取得市场内身份 [@x380kkm 2026-09-07] ////
def plugin_key(item: dict, root: Path) -> str | None:
    if item["kind"] != "plugin":
        return None
    path = Path(item["path"])
    if path == root / "config.toml" and item["details"].get("observation") == "配置开关":
        name, separator, market = item["name"].rpartition("@")
        return item["name"] if name and separator and market else None
    try:
        parts = path.relative_to(root / "plugins/cache").parts
    except ValueError:
        return None
    if (len(parts) == 3 and parts[2] == ".codex-remote-plugin-install.json"
            or len(parts) == 5 and parts[3:] == (".codex-plugin", "plugin.json")):
        return parts[1] + "@" + parts[0]
    return None


# //// 将插件的明确成员关联到同一个市场内身份 [@x380kkm 2026-09-07] ////
def plugin_members(items: dict[str, dict], edges: list[dict], root: Path) -> dict[str, str]:
    owners = {identifier: key for identifier, item in items.items() if (key := plugin_key(item, root))}
    members = dict(owners)
    candidates: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        target = items.get(edge["to"])
        if edge["from"] in owners and target and target["scope"] == "plugin":
            if edge["label"] in {"包含 Skill", "声明插件端点"}:
                candidates[target["id"]].add(owners[edge["from"]])
    for identifier, item in items.items():
        owner = item["details"].get("pluginId")
        if item["scope"] == "plugin" and owner in owners:
            candidates[identifier].add(owners[owner])
    for identifier, keys in candidates.items():
        if len(keys) == 1:
            members[identifier] = next(iter(keys))
    return members


# //// 选择代表插件配置的节点并取得包名称 [@x380kkm 2026-09-07] ////
def plugin_description(members: list[dict], key: str) -> dict:
    order = {"配置开关": 0, "安装记录": 1, "缓存版本": 2}
    primary = min((item for item in members if item["kind"] == "plugin"),
                  key=lambda item: (order.get(item["details"].get("observation"), 3), item["path"], item["id"]))
    cached = sorted((item for item in members if item["details"].get("observation") == "缓存版本"),
                    key=lambda item: (item["path"], item["id"]))
    name = key.rpartition("@")[0]
    if cached:
        name = cached[0]["name"].removesuffix(" · " + cached[0]["details"].get("version", ""))
    return {"name": name, "category": "plugin", "primaryId": primary["id"],
            "origin": "official" if key.rpartition("@")[2] in OFFICIAL_MARKETS else "user"}


# //// 保留独立组件身份并定位宿主配置载体 [@x380kkm 2026-09-07] ////
def component_roots(items: dict[str, dict], edges: list[dict], root: Path) -> dict[str, str]:
    components = {identifier: item for identifier, item in items.items() if item["kind"] == "component"}
    representatives = {identifier: identifier for identifier in components}
    configuration = next((item for item in items.values()
                          if item["kind"] == "config" and Path(item["path"]) == root / "config.toml"), None)
    if configuration:
        for identifier, item in components.items():
            if item["details"].get("componentId") == "codex-host":
                representatives[identifier] = configuration["id"]
    return representatives


# //// 将唯一归属的安装目标与维护来源纳入组件组 [@x380kkm 2026-09-07] ////
def component_members(items: dict[str, dict], edges: list[dict], roots: dict[str, str]) -> dict[str, str]:
    members = dict(roots)
    owners: dict[str, set[str]] = defaultdict(set)
    targets = {"声明安装目标": "skill", "登记维护来源": "source"}
    for edge in edges:
        target = items.get(edge["to"])
        if edge["from"] in roots and target and target["kind"] == targets.get(edge["label"]):
            if target["kind"] == "skill" and items[edge["from"]]["details"].get("componentKind") != "skill":
                continue
            owners[target["id"]].add(roots[edge["from"]])
    for identifier, candidates in owners.items():
        if len(candidates) == 1:
            members[identifier] = next(iter(candidates))
    return members


# //// 取得组件或完整载体的显示名称与分类 [@x380kkm 2026-09-07] ////
def item_description(primary: dict, members: list[dict]) -> dict:
    category = KIND_CATEGORIES.get(primary["kind"], "source")
    name = primary["name"]
    if primary["kind"] == "component":
        component_kind = primary["details"].get("componentKind")
        if component_kind == "skill":
            category = "skill"
            skills = sorted((item for item in members if item["kind"] == "skill"), key=lambda item: item["id"])
            if len(skills) == 1:
                name = skills[0]["name"]
        elif component_kind == "host":
            category = "config"
        elif component_kind in {"code-provider", "structure-provider", "tool"}:
            category = "tool"
    return {"name": name, "category": category, "origin": "user", "primaryId": primary["id"]}


# //// 将观察节点组成稳定且互斥的展示集合 [@x380kkm 2026-09-07] ////
def group_inventory(items: list[dict], edges: list[dict], root: Path, user_root: Path) -> list[dict]:
    by_id = {item["id"]: item for item in items}
    assignments: dict[str, str] = {}
    descriptions: dict[str, dict] = {}
    plugins: dict[str, list[dict]] = defaultdict(list)
    for identifier, key in plugin_members(by_id, edges, root).items():
        plugins[key].append(by_id[identifier])
    for key, members in plugins.items():
        group = "plugin:" + root.absolute().as_uri() + "#" + key
        descriptions[group] = plugin_description(members, key)
        assignments.update({item["id"]: group for item in members})

    builtins = [item for item in items if item["kind"] == "skill"
                and Path(item["path"]).is_relative_to(root / "skills/.system")]
    if builtins:
        group = "system:" + root.absolute().as_uri()
        primary = min(builtins, key=lambda item: (item["path"], item["id"]))
        descriptions[group] = {"name": "Codex 内置 Skills", "category": "skill", "origin": "official", "primaryId": primary["id"]}
        assignments.update({item["id"]: group for item in builtins})

    roots = component_roots(by_id, edges, root)
    for identifier, primary in component_members(by_id, edges, roots).items():
        assignments.setdefault(identifier, "item:" + primary)
    for item in items:
        assignments.setdefault(item["id"], "item:" + item["id"])

    grouped: dict[str, list[dict]] = defaultdict(list)
    for identifier, key in assignments.items():
        grouped[key].append(by_id[identifier])
    result = []
    namespace = user_root.absolute().as_uri() + "#inventory/"
    for key, members in grouped.items():
        description = descriptions.get(key)
        if description is None:
            description = item_description(by_id[key.removeprefix("item:")], members)
        primary_id = description["primaryId"]
        ordered = sorted(members, key=lambda item: (item["id"] != primary_id, item["kind"], item["path"], item.get("line", 0), item["id"]))
        result.append({"id": "codex-group:" + uuid5(NAMESPACE_URL, namespace + key).hex, **description,
                       "itemIds": [item["id"] for item in ordered], "counts": dict(Counter(item["kind"] for item in members))})
    return sorted(result, key=lambda group: (group["origin"] == "official", group["category"], group["name"].casefold(), group["id"]))
