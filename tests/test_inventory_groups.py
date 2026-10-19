# audience: internal
# # inventory-group-tests
# 展示聚合以明确载体和归属关系为边界, 来源中的独立状态继续可见.

from copy import deepcopy
from pathlib import Path
import unittest

from harness_manager.codex_inventory_io import InventoryInput
from harness_manager.inventory_groups import group_inventory


# //// 使用纯观察节点验证归属边界 [@x380kkm 2026-09-07] ////
class InventoryGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = Path.cwd() / "inventory-profile"
        self.root = self.user / ".codex"
        self.state = InventoryInput()

    # //// 创建带独立位置的观察节点 [@x380kkm 2026-09-07] ////
    def node(self, kind: str, path: Path, name: str, **fields) -> str:
        return self.state.add(path, name, kind, "观察正文.", **fields)

    # //// 返回以成员身份查找的展示组 [@x380kkm 2026-09-07] ////
    def memberships(self) -> dict[str, dict]:
        groups = group_inventory(list(self.state.items.values()), self.state.edges, self.root, self.user)
        return {identifier: group for group in groups for identifier in group["itemIds"]}

    # //// 同名说明载体保留各自来源身份和完整正文 [@x380kkm 2026-09-07] ////
    def test_instruction_sources_keep_distinct_identities(self) -> None:
        documents = []
        for directory in (self.root, self.user):
            path = directory / "AGENTS.md"
            document = self.node("instruction", path, "相同标题", content="# Rules\n## Detail\n")
            documents.append(document)
        before = deepcopy(self.state.items)
        membership = self.memberships()
        self.assertNotEqual(membership[documents[0]]["id"], membership[documents[1]]["id"])
        for document in documents:
            group = membership[document]
            self.assertEqual(group["itemIds"], [document])
            self.assertEqual(group["primaryId"], document)
            self.assertEqual(group["counts"], {"instruction": 1})
        self.assertEqual(self.state.items, before)

    # //// 插件内聚保留配置开关和多个缓存版本的独立状态 [@x380kkm 2026-09-07] ////
    def test_plugin_versions_installation_and_members_share_declared_key(self) -> None:
        key = "sample@openai-bundled"
        config = self.node("plugin", self.root / "config.toml", key, locator=key,
                           status="配置禁用", details={"observation": "配置开关", "enabled": False})
        plugin = self.root / "plugins/cache/openai-bundled/sample"
        installed = self.node("plugin", plugin / ".codex-remote-plugin-install.json", key,
                              status="安装记录存在", details={"observation": "安装记录"})
        ids = {config, installed}
        for version in ("1", "2"):
            directory = plugin / version
            cached = self.node("plugin", directory / ".codex-plugin/plugin.json", "Sample · " + version,
                               status="缓存版本存在", details={"observation": "缓存版本", "version": version})
            skill = self.node("skill", directory / "skills/sample/SKILL.md", "sample", scope="plugin",
                              details={"pluginId": cached})
            endpoint = self.node("mcp", directory / ".mcp.json", "sample", locator="mcp:sample", scope="plugin")
            self.state.connect(config, cached, "对应缓存版本")
            self.state.connect(installed, cached, "对应缓存版本")
            self.state.connect(cached, skill, "包含 Skill")
            self.state.connect(cached, endpoint, "声明插件端点")
            ids.update((cached, skill, endpoint))
        before = deepcopy(self.state.items)
        group = self.memberships()[config]
        self.assertEqual(set(group["itemIds"]), ids)
        self.assertEqual(group["name"], "Sample")
        self.assertEqual(group["primaryId"], config)
        self.assertEqual(group["origin"], "official")
        self.assertEqual(group["counts"], {"plugin": 4, "skill": 2, "mcp": 2})
        self.assertEqual(self.state.items, before)
        self.state.items.pop(installed)
        self.assertEqual(self.memberships()[config]["id"], group["id"])

    # //// 同名用户内容与相似市场名称保留个人归属 [@x380kkm 2026-09-07] ////
    def test_official_origin_requires_exact_market_or_builtin_location(self) -> None:
        system = self.node("skill", self.root / "skills/.system/example/SKILL.md", "example")
        another = self.node("skill", self.root / "skills/.system/another/SKILL.md", "another")
        own = self.node("skill", self.user / ".agents/skills/example/SKILL.md", "example")
        codex_skill = self.node("skill", self.root / "skills/example/SKILL.md", "example")
        mcp = self.node("mcp", self.root / "config.toml", "openaiDeveloperDocs", locator="mcp:docs")
        custom = self.node("plugin", self.root / "plugins/cache/openai-bundled-custom/example/1/.codex-plugin/plugin.json", "example")
        official = self.node("plugin", self.root / "plugins/cache/openai-bundled/example/1/.codex-plugin/plugin.json", "example")
        unknown = self.node("plugin", self.user / "unclassified/plugin.json", "example@openai-bundled")
        self.state.connect(system, own, "同名 Skill, 来源分别维护")
        membership = self.memberships()
        self.assertEqual(membership[system]["id"], membership[another]["id"])
        self.assertEqual(membership[system]["name"], "Codex 内置 Skills")
        self.assertEqual(membership[system]["origin"], "official")
        self.assertEqual(membership[official]["origin"], "official")
        for identifier in (own, codex_skill, mcp, custom, unknown):
            self.assertEqual(membership[identifier]["origin"], "user")
            self.assertEqual(membership[identifier]["itemIds"], [identifier])
        self.assertEqual(membership[mcp]["category"], "tool")

    # //// 来源归属参与聚合而组件依赖保持独立 [@x380kkm 2026-09-07] ////
    def test_component_dependencies_keep_separate_capabilities(self) -> None:
        registry = self.root / "manager/components.json"
        ids = {}
        for key, kind in (("codegraph", "code-provider"), ("codegraph-skill", "skill"),
                          ("archify", "structure-provider"), ("archify-skill", "skill"),
                          ("clean-tools-skill", "skill"), ("codex-host", "host")):
            ids[key] = self.node("component", registry, key, locator=key, details={"componentId": key, "componentKind": kind})
        config = self.node("config", self.root / "config.toml", "Codex 用户配置")
        skill = self.node("skill", self.user / ".agents/skills/codegraph/SKILL.md", "codegraph")
        source = self.node("source", self.user / "codegraph", "codegraph")
        shared = self.node("source", self.user / "shared", "shared")
        self.state.connect(ids["codegraph-skill"], ids["codegraph"], "依赖")
        self.state.connect(ids["archify-skill"], ids["archify"], "依赖")
        self.state.connect(ids["clean-tools-skill"], ids["codegraph"], "依赖")
        self.state.connect(ids["codex-host"], ids["clean-tools-skill"], "依赖")
        self.state.connect(ids["codegraph-skill"], skill, "声明安装目标")
        self.state.connect(ids["codegraph"], source, "登记维护来源")
        self.state.connect(ids["codegraph"], shared, "登记维护来源")
        self.state.connect(ids["archify"], shared, "登记维护来源")
        membership = self.memberships()
        self.assertEqual(set(membership[skill]["itemIds"]), {ids["codegraph-skill"], skill})
        self.assertEqual(set(membership[ids["codegraph"]]["itemIds"]), {ids["codegraph"], source})
        self.assertEqual(membership[ids["archify"]]["itemIds"], [ids["archify"]])
        self.assertEqual(membership[ids["archify-skill"]]["itemIds"], [ids["archify-skill"]])
        self.assertEqual(membership[ids["clean-tools-skill"]]["itemIds"], [ids["clean-tools-skill"]])
        self.assertEqual(membership[shared]["itemIds"], [shared])
        self.assertEqual(set(membership[config]["itemIds"]), {config, ids["codex-host"]})
        self.assertEqual(membership[config]["category"], "config")
        self.assertEqual(membership[ids["codegraph"]]["category"], "tool")
        self.assertEqual(membership[ids["archify"]]["category"], "tool")
        groups = group_inventory(list(reversed(self.state.items.values())), list(reversed(self.state.edges)), self.root, self.user)
        reversed_membership = {identifier: group for group in groups for identifier in group["itemIds"]}
        self.assertEqual(reversed_membership, membership)
        self.assertEqual(sum(len(group["itemIds"]) for group in groups), len(self.state.items))

    # //// 管理器的维护记录与独立 Skill 各自保留类型和名称 [@x380kkm 2026-09-07] ////
    def test_manager_installation_keeps_skill_as_independent_content(self) -> None:
        component = self.node("component", self.root / "manager/components.json", "Agent manager",
                              details={"componentId": "agent-manager", "componentKind": "manager"})
        skill = self.node("skill", self.user / ".agents/skills/agent-manager/SKILL.md", "agent-manager")
        source = self.node("source", self.user / "tools/manager", "manager")
        self.state.connect(component, skill, "声明安装目标")
        self.state.connect(component, source, "登记维护来源")
        membership = self.memberships()
        self.assertEqual(membership[component]["category"], "source")
        self.assertEqual(set(membership[component]["itemIds"]), {component, source})
        self.assertEqual(membership[skill]["name"], "agent-manager")
        self.assertEqual(membership[skill]["category"], "skill")
        self.assertEqual(membership[skill]["itemIds"], [skill])


if __name__ == "__main__":
    unittest.main()
