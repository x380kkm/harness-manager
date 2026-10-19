# audience: internal
# # declaration-view-tests
# 内存声明验证版本选择与开放内容经过图和发现视图后的引用及文本语义.

from copy import deepcopy
import unittest

from harness_manager.graph import build_graph
from harness_manager.projection import discover


# //// 创建携带开放内容的声明 [@x380kkm 2026-09-06] ////
def make_declaration(payload: object) -> dict:
    return {
        "apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:content",
        "release": {"version": "1.0.0"},
        "contributions": [{"id": "content", "point": "example/content",
                           "contract": {"id": "example/content", "range": "^1.0.0"},
                           "payload": payload}],
    }


# //// 验证静态声明的视图边界 [@x380kkm 2026-09-06] ////
class DeclarationViewTests(unittest.TestCase):
    # //// 保留失败发布选择的未解析目标 [@x380kkm 2026-09-06] ////
    def test_failed_release_selection_keeps_unresolved_targets(self) -> None:
        plugin = make_declaration({})
        binding = {
            "kind": "PluginBinding", "id": "binding:content",
            "plugin": {"id": plugin["id"], "constraint": "2.0.0"},
        }
        bundle = {
            "kind": "Plugin", "id": "plugin:bundle", "release": {"version": "1.0.0"},
            "contributions": [{"id": "alias", "ref": "plugin:content#content", "constraint": "2.0.0"}],
            "relations": [{"id": "related", "target": "plugin:content", "contract": {"id": "example/related"}}],
        }

        graph = build_graph([plugin, binding, bundle])
        nodes = {node["id"]: node for node in graph["nodes"]}
        edges = {edge["label"]: edge for edge in graph["edges"]}
        self.assertEqual(edges["uses"]["to"], "plugin:content")
        self.assertEqual(edges["references"]["to"], "plugin:content#content")
        for label in ("uses", "references"):
            self.assertEqual(nodes[edges[label]["to"]]["kind"], "unresolved")
            self.assertIsNone(nodes[edges[label]["to"]]["documentId"])
        self.assertEqual(edges["example/related"]["to"], "plugin:content@1.0.0")
        self.assertEqual({item["origin"] for item in graph["diagnostics"] if item["code"] == "missing_plugin"},
                         {"binding:content", "plugin:bundle@1.0.0#alias"})

    # //// 嵌套引用保留每个外层使用者的独立诊断 [@x380kkm 2026-09-08] ////
    def test_nested_reference_diagnostics_keep_outer_usage_origin(self) -> None:
        plugin = make_declaration({})
        inner = {"kind": "Plugin", "id": "plugin:inner", "release": {"version": "1.0.0"},
                 "contributions": [{"id": "alias", "ref": "plugin:content#content", "constraint": "2.0.0"}]}
        outer = {"kind": "Plugin", "id": "plugin:outer", "release": {"version": "1.0.0"},
                 "contributions": [{"id": "nested", "ref": "plugin:inner#alias", "constraint": "1.0.0"}]}
        graph = build_graph([plugin, inner, outer])
        missing = [note for note in graph["diagnostics"] if note["code"] == "missing_plugin"]
        self.assertEqual({note["origin"] for note in missing}, {"plugin:inner@1.0.0#alias", "plugin:outer@1.0.0#nested"})
        self.assertEqual({note["subject"] for note in missing}, {"plugin:content"})

    # //// 从开放内容生成稳定的图与发现文本 [@x380kkm 2026-09-06] ////
    def test_open_payload_keeps_view_names_and_content(self) -> None:
        payloads = [None, "text", ["item"], {"name": None}, {"name": ["name"], "capabilities": None},
                    {"name": " ", "capabilities": "capability:inspect"},
                    {"capabilities": ["capability:inspect"]}]
        for payload in payloads:
            with self.subTest(payload=payload):
                plugin = make_declaration(payload)
                original = deepcopy(plugin)
                binding = {
                    "kind": "PluginBinding", "id": "binding:content", "plugin": {"id": plugin["id"]},
                    "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {}},
                }
                graph = build_graph([plugin, binding])
                content = next(node for node in graph["nodes"] if node["id"] == "plugin:content@1.0.0#content")
                self.assertEqual(content["label"], "content")
                self.assertEqual(content["documentId"], "plugin:content@1.0.0")
                self.assertFalse(any(edge["label"] == "provides" for edge in graph["edges"]))
                self.assertEqual(discover([plugin, binding], {})["candidates"][0]["name"], "content")
                self.assertEqual(plugin, original)

    # //// 按工具端点契约提取能力关系 [@x380kkm 2026-09-06] ////
    def test_capabilities_follow_supported_endpoint_contract(self) -> None:
        plugin = make_declaration({"name": "Inspector", "capabilities": ["capability:inspect"]})
        member = plugin["contributions"][0]
        member["point"] = "tool.x380kkm/endpoint"
        member["contract"] = {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"}
        graph = build_graph([plugin])
        self.assertEqual([edge["to"] for edge in graph["edges"] if edge["label"] == "provides"],
                         ["capability:inspect"])
        self.assertEqual(next(node["label"] for node in graph["nodes"]
                              if node["id"] == "plugin:content@1.0.0#content"), "Inspector")
        member["contract"]["range"] = "^2.0.0"
        self.assertFalse(any(edge["label"] == "provides" for edge in build_graph([plugin])["edges"]))
        member["contract"]["range"] = "^1.0.0"
        for capabilities in (None, "capability:inspect", [None]):
            with self.subTest(capabilities=capabilities):
                member["payload"]["capabilities"] = capabilities
                self.assertFalse(any(edge["label"] == "provides" for edge in build_graph([plugin])["edges"]))


# //// 运行声明视图验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
