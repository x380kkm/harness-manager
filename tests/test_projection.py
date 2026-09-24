# audience: internal
# # projection-tests
# 用静态声明组合验证目标隔离, 覆写和引用关系, 所有样例保存在内存中.

from copy import deepcopy
import unittest

from harness_manager.graph import build_graph
from harness_manager.projection import discover


# //// 创建带独立来源的普通内容声明 [@x380kkm 2026-09-06] ////
def make_plugin(identifier: str = "plugin:tools/methods", names: tuple[str, ...] = ("method",), version: str = "1.0.0") -> dict:
    return {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": identifier,
        "release": {"version": version},
        "sources": [{"id": "content", "source": {
            "resolver": {"id": "manager.source/path", "range": "^1.0.0"},
            "locator": "/independent/methods", "subpath": "skills"}}],
        "contributions": [{"id": name, "point": "skill.x380kkm/deployment",
                           "contract": {"id": "skill.x380kkm/deployment", "range": "^1.0.0"},
                           "source": "source:content", "payload": {
                               "name": name, "description": f"使用 {name} 方法.", "entry": f"{name}/SKILL.md"}}
                          for name in names],
    }


# //// 创建明确目标的使用绑定 [@x380kkm 2026-09-06] ////
def make_binding(identifier: str, plugin: dict, selector: dict, **values: object) -> dict:
    return {"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": identifier,
            "plugin": {"id": plugin["id"]},
            "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": selector},
            **values}


# //// 验证目标选择与引用合成 [@x380kkm 2026-09-06] ////
class ProjectionTests(unittest.TestCase):
    # //// 同层数组选项的布尔值与数字差异产生明确冲突 [@x380kkm 2026-09-10] ////
    def test_nested_option_value_conflict_is_not_resolved_by_binding_order(self) -> None:
        plugin = make_plugin()
        for left, right in (([False], [0]), ([{"flag": True}], [{"flag": 1}])):
            for first, second in ((left, right), (right, left)):
                bindings = [make_binding("binding:first", plugin, {}, options={"flags": first}),
                            make_binding("binding:second", plugin, {}, options={"flags": second})]
                result = discover([plugin, *bindings], {})
                self.assertEqual(result["candidates"], [])
                self.assertIn("binding_conflict", {note["code"] for note in result["diagnostics"]})

    # //// 本地来源版本与语义发布按各自约束独立选择 [@x380kkm 2026-09-10] ////
    def test_local_and_semantic_releases_preserve_explicit_selection(self) -> None:
        release = make_plugin()
        for local_version in ("local", "local:project"):
            local = make_plugin(version=local_version)
            binding = make_binding("binding:versions", release, {})
            for constraint, expected in (("1.0.0", "1.0.0"), ("^1.0.0", "1.0.0"), (local_version, local_version)):
                with self.subTest(local=local_version, constraint=constraint):
                    binding["plugin"]["constraint"] = constraint
                    result = discover([release, local, binding], {})
                    self.assertEqual([item["version"] for item in result["candidates"]], [expected])
                    self.assertEqual(result["diagnostics"], [])
            binding["plugin"]["constraint"] = "not a version range"
            result = discover([local, release, binding], {})
            self.assertEqual(result["candidates"], [])
            self.assertEqual({item["code"] for item in result["diagnostics"]}, {"unknown_version_constraint"})

    # //// 仅向已绑定且适用的目标返回候选 [@x380kkm 2026-09-06] ////
    def test_scope_isolation_and_catalog_only_content(self) -> None:
        plugin = make_plugin()
        binding = make_binding("binding:project/method", plugin,
                               {"project": "game", "agent": ["reviewer"], "path": "W:/game/src"})
        target = {"project": "game", "agent": "reviewer", "path": "w:\\game\\src\\scene.py"}
        self.assertEqual(len(discover([plugin, binding], target)["candidates"]), 1)
        for changes in ({"project": "other"}, {"agent": "coder"}, {"path": "W:/game/src2/scene.py"}):
            with self.subTest(changes=changes):
                self.assertEqual(discover([plugin, binding], target | changes)["candidates"], [])
        self.assertEqual(discover([plugin], target)["candidates"], [])

    # //// 明确不匹配的字段先于缺少上下文的字段排除范围 [@x380kkm 2026-09-10] ////
    def test_excluded_scope_suppresses_missing_context_in_any_selector_order(self) -> None:
        plugin = make_plugin()
        for key, selection, actual in (("host", "harness-manager", "codex"),
                                        ("project", "other", "current-project"),
                                        ("task", "writing", "review")):
            for selector in ({"agent": "worker", key: selection}, {key: selection, "agent": "worker"}):
                with self.subTest(selector=selector):
                    binding = make_binding("binding:other", plugin, selector)
                    result = discover([plugin, binding], {key: actual})
                    self.assertEqual(result["candidates"], [])
                    self.assertEqual(result["diagnostics"], [])
                    applicable = discover([plugin, binding], {key: selection})
                    self.assertEqual({item["code"] for item in applicable["diagnostics"]}, {"missing_context"})

    # //// 引用链中已排除的别名保留目标成员的上下文独立性 [@x380kkm 2026-09-10] ////
    def test_excluded_alias_does_not_require_its_target_context(self) -> None:
        plugin = make_plugin()
        plugin["contributions"][0]["scope"] = {
            "contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"task": "review"}}
        wrapper = make_plugin("plugin:wrapper")
        wrapper["contributions"] = [{"id": "alias", "ref": f"{plugin['id']}#method", "scope": {
            "contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": "harness-manager"}}}]
        binding = make_binding("binding:wrapper", wrapper, {})
        result = discover([plugin, wrapper, binding], {"host": "codex"})
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["diagnostics"], [])

    # //// 其他宿主的必需别名保持未知目标诊断的范围 [@x380kkm 2026-09-10] ////
    def test_out_of_scope_required_alias_does_not_resolve_unavailable_target(self) -> None:
        wrapper = make_plugin("plugin:wrapper")
        wrapper["contributions"] = [{"id": "alias", "ref": "plugin:missing#method", "criticality": {"default": "required"}, "scope": {
            "contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"host": "codex"}}}]
        binding = make_binding("binding:wrapper", wrapper, {})
        result = discover([wrapper, binding], {"host": "harness-manager"})
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["diagnostics"], [])
        active = discover([wrapper, binding], {"host": "codex"})
        self.assertIn("required_unavailable", {note["code"] for note in active["diagnostics"]})

    # //// 使用相同规则发现不同主题的普通 Skill [@x380kkm 2026-09-06] ////
    def test_skill_topics_use_the_same_selection_rules(self) -> None:
        plugin = make_plugin(names=("clean-writer-cn", "database-analysis"))
        binding = make_binding("binding:task/methods", plugin, {"task": "coding"})
        result = discover([plugin, binding], {"task": "coding"}, limit=1)
        following = discover([plugin, binding], {"task": "coding"}, cursor=result["next_cursor"])
        self.assertEqual([item["name"] for item in result["candidates"] + following["candidates"]],
                         ["clean-writer-cn", "database-analysis"])
        self.assertEqual(discover([plugin, binding], {"task": "coding"}, query="database")["candidates"][0]["name"],
                         "database-analysis")

    # //// 按范围合成选项并恢复被上层排除的内容 [@x380kkm 2026-09-06] ////
    def test_narrower_bindings_override_individual_fields(self) -> None:
        plugin = make_plugin(names=("method", "companion"))
        plugin["options"] = {"schema": {"type": "object"}, "defaults": {"format": {"width": 60}, "mode": "plain"}}
        user = make_binding("binding:user/methods", plugin, {"user": "current"},
                            selection={"exclude": ["method"]}, options={"format": {"width": 80, "dialect": "local"}})
        project = make_binding("binding:project/methods", plugin, {"project": "game"},
                               selection={"include": ["method"]}, options={"format": {"width": 100}})
        documents = [plugin, user, project]
        before = deepcopy(documents)
        result = discover(documents, {"user": "me", "project": "game"})
        self.assertEqual({item["name"] for item in result["candidates"]}, {"method", "companion"})
        self.assertEqual(result["candidates"][0]["options"],
                         {"format": {"width": 100, "dialect": "local"}, "mode": "plain"})
        result["candidates"][0]["options"]["format"]["width"] = 10
        self.assertEqual(documents, before)

    # //// 相互冲突的设置保留诊断而非按文档顺序选择 [@x380kkm 2026-09-06] ////
    def test_equal_and_incomparable_overrides_report_conflicts(self) -> None:
        plugin = make_plugin()
        for selectors in (({"project": "game"}, {"project": "game"}),
                          ({"project": "game", "host": "desktop"}, {"project": "game", "agent": "reviewer"})):
            with self.subTest(selectors=selectors):
                first = make_binding("binding:one", plugin, selectors[0], options={"mode": "first"})
                second = make_binding("binding:two", plugin, selectors[1], options={"mode": "second"})
                target = {"project": "game", "host": "desktop", "agent": "reviewer"}
                result = discover([plugin, first, second], target)
                self.assertEqual(result["candidates"], [])
                self.assertIn("binding_conflict", {item["code"] for item in result["diagnostics"]})
                self.assertEqual(result, discover([plugin, second, first], target))

    # //// 组合引用保持来源身份与多层引用链 [@x380kkm 2026-09-06] ////
    def test_composition_keeps_original_sources_and_references(self) -> None:
        original = make_plugin("plugin:owner/methods")
        middle = make_plugin("plugin:bundle/middle")
        middle["contributions"] = [{"id": "shared", "ref": f"{original['id']}#method"}]
        bundle = make_plugin("plugin:bundle/project")
        bundle["contributions"] = [{"id": "analysis", "ref": f"{middle['id']}#shared"}]
        binding = make_binding("binding:project/analysis", bundle, {"project": "game"})
        result = discover([original, middle, bundle, binding], {"project": "game"})
        candidate = result["candidates"][0]
        self.assertEqual(candidate["content"], f"{original['id']}#method")
        self.assertEqual(candidate["source"]["bindings"], original["sources"])
        self.assertEqual(candidate["source"]["reference"], "source:content")
        self.assertEqual([item["ref"] for item in candidate["reference_chain"]],
                         [f"{bundle['id']}#analysis", f"{middle['id']}#shared", f"{original['id']}#method"])

    # //// 循环和缺失的组合成员保持为诊断 [@x380kkm 2026-09-06] ////
    def test_reference_cycles_and_missing_members(self) -> None:
        first, second = make_plugin("plugin:first"), make_plugin("plugin:second")
        first["contributions"] = [{"id": "method", "ref": "plugin:second#method"}]
        second["contributions"] = [{"id": "method", "ref": "plugin:first#method"}]
        binding = make_binding("binding:cycle", first, {})
        result = discover([first, second, binding], {})
        self.assertEqual(result["candidates"], [])
        self.assertIn("reference_cycle", {item["code"] for item in result["diagnostics"]})
        first["contributions"][0]["ref"] = "plugin:second#missing"
        result = discover([first, second, binding], {})
        self.assertEqual(result["candidates"], [])
        self.assertIn("missing_or_ambiguous_contribution", {item["code"] for item in result["diagnostics"]})

    # //// 下层排除保留上层必要性与选择基线 [@x380kkm 2026-09-06] ////
    def test_required_selection_and_accepted_members_are_preserved(self) -> None:
        plugin = make_plugin(names=("method", "new-preference"))
        user = make_binding("binding:user/methods", plugin, {"user": "me"}, selection={"require": ["method"]},
                            selectionBaseline={"release": {"version": "1.0.0"}, "included": ["method"]})
        project = make_binding("binding:project/methods", plugin, {"user": "me", "project": "game"},
                               selection={"exclude": ["method"]})
        result = discover([plugin, user, project], {"user": "me", "project": "game"})
        self.assertEqual(result["candidates"], [])
        self.assertIn("required_excluded", {item["code"] for item in result["diagnostics"]})

    # //// 未知范围与条件阻止扩大候选集合 [@x380kkm 2026-09-06] ////
    def test_unknown_scope_and_conditions_keep_diagnostics(self) -> None:
        plugin = make_plugin()
        binding = make_binding("binding:unknown", plugin, {"team": "all"})
        result = discover([plugin, binding], {})
        self.assertEqual(result["candidates"], [])
        self.assertIn("unknown_scope", {item["code"] for item in result["diagnostics"]})
        binding["target"]["selector"] = {}
        plugin["contributions"][0]["activation"] = {
            "contract": {"id": "external.activation", "range": "*"}, "mode": "conditional",
            "when": {"contract": {"id": "external.condition", "range": "*"}, "expression": True}}
        result = discover([plugin, binding], {})
        self.assertEqual(result["candidates"], [])
        self.assertIn("unknown_condition", {item["code"] for item in result["diagnostics"]})

    # //// 图中多发布身份和缺失引用保持可解释 [@x380kkm 2026-09-06] ////
    def test_graph_links_document_versions_and_missing_targets(self) -> None:
        first = make_plugin(version="1.0.0")
        second = make_plugin(version="2.0.0")
        binding = make_binding("binding:version", first, {})
        binding["plugin"]["constraint"] = "^1.0.0"
        first["relations"] = [{"id": "optional", "contract": {"id": "manager.relation/suggests", "range": "*"},
                               "target": "plugin:missing"}]
        result = build_graph([first, second, binding])
        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes[f"{first['id']}@1.0.0#method"]["documentId"], f"{first['id']}@1.0.0")
        self.assertIn(f"{second['id']}@2.0.0", nodes)
        self.assertIsNone(nodes["plugin:missing"]["documentId"])
        self.assertIn("missing_graph_target", {item["code"] for item in result["diagnostics"]})
        self.assertEqual(discover([first, second, binding], {})["candidates"][0]["version"], "1.0.0")


# //// 运行静态派发验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
