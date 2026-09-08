# audience: internal
# # mcp-tests
"""实际 MCP 客户端核对工具契约, 写入用例与可恢复错误."""
from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path

from mcp import Client, StdioServerParameters

from harness_manager.agent_connection import connection_config
from harness_manager.mcp_server import create_server
from harness_manager.service import Manager
from test_projection import make_plugin
from test_service import create_skill


# //// 通过真实协议客户端使用同一管理核心 [@x380kkm 2026-09-06] ////
class McpTests(unittest.IsolatedAsyncioTestCase):
    # //// 完整 MCP 直接返回管理核心的后续分页 [@x380kkm 2026-09-08] ////
    async def test_catalog_second_page_preserves_core_slice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(user_root=Path(directory))
            for index in range(3):
                document = make_plugin(f"plugin:page/{index}")
                manager.apply_document(manager.preview_document(document)["plan"])
            async with Client(create_server(manager)) as client:
                first = await client.call_tool("catalog_list", {"limit": 1})
                self.assertFalse(first.is_error, first.content)
                self.assertEqual(first.structured_content["total"], 3)
                second = await client.call_tool("catalog_list", {"limit": 1, "cursor": first.structured_content["next_cursor"]})
                self.assertFalse(second.is_error, second.content)
                result = second.structured_content
                self.assertEqual([document["id"] for document in result["documents"]], ["plugin:page/1@1.0.0"])
                self.assertEqual(result["nextCursor"], 2)
                self.assertEqual(result["next_cursor"], result["nextCursor"])

    # //// 生成的连接描述直接启动精简 stdio 服务 [@x380kkm 2026-09-08] ////
    async def test_generated_connection_starts_stdio_and_serves_help(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = connection_config("harness_manager", user_root=root)["mcp_servers"]["harness_manager"]
            parameters = StdioServerParameters(**configuration)
            async with Client(parameters) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools), 4)
                records = await client.call_tool("manager_query", {"method": "catalog.list"})
                self.assertFalse(records.is_error, records.content)
                self.assertEqual(records.structured_content["documents"], [])
                help_topic = await client.call_tool("agent_help", {"topic": "cli"})
                self.assertFalse(help_topic.is_error, help_topic.content)
                self.assertIn("host apply", help_topic.structured_content["text"])
            self.assertFalse((root / ".harness").exists())

    # //// 精简协议按需提供输入结构并拒绝只读路由中的写入 [@x380kkm 2026-09-08] ////
    async def test_compact_discovery_resources_and_query_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = Manager(user_root=root)
            async with Client(create_server(manager, compact=True)) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual({tool.name for tool in tools}, {"agent_capabilities", "agent_help", "manager_query", "manager_action"})
                catalog = await client.call_tool("agent_capabilities", {"group": "host", "limit": 2})
                self.assertEqual(len(catalog.structured_content["methods"]), 2)
                self.assertIsNotNone(catalog.structured_content["nextCursor"])
                method = await client.call_tool("agent_capabilities", {"method": "card.configure"})
                self.assertIn("state", method.structured_content["inputSchema"]["required"])
                self.assertIn("host-if-enabled", method.structured_content["writes"])
                overview = await client.read_resource("harness://agent/overview")
                topics = json.loads(overview.contents[0].text)["topics"]
                self.assertTrue(any(topic["id"] == "host" for topic in topics))
                host = await client.read_resource("harness://agent/host")
                self.assertIn("planId", json.loads(host.contents[0].text)["text"])
                rejected = await client.call_tool("manager_query", {"method": "host.initialize"})
                self.assertTrue(rejected.is_error)
                self.assertFalse((root / ".harness").exists())
                initialized = await client.call_tool("manager_action", {"method": "host.initialize"})
                self.assertFalse(initialized.is_error, initialized.content)
                self.assertEqual(initialized.structured_content["initialBackupId"], "initial")

    async def test_import_preview_apply_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = create_skill(root)
            manager = Manager(read_roots=[root], user_root=root)
            async with Client(create_server(manager)) as client:
                imported = await client.call_tool("document_import", {"path": str(path)})
                self.assertFalse(imported.is_error)
                document = imported.structured_content["document"]
                preview = await client.call_tool("document_preview", {"document": document})
                self.assertFalse(preview.is_error)
                plan = preview.structured_content["plan"]
                applied = await client.call_tool("document_apply", {"plan": plan})
                self.assertFalse(applied.is_error)
                self.assertEqual(len(manager.list_documents()["documents"]), 1)
                content = await client.call_tool("content_read", {"ref": document["id"] + "#example-skill", "version": "local"})
                self.assertFalse(content.is_error)
                self.assertEqual(content.structured_content["units"][0]["content"], path.read_bytes().decode("utf-8"))
                description = await client.call_tool("usage_describe", {"plugin": document["id"] + "@local"})
                self.assertFalse(description.is_error)
                settings = {"id": description.structured_content["suggestedId"], "acceptance": "current", "members": {"example-skill": "include"}}
                prepared = await client.call_tool("usage_preview", {"plugin": document["id"] + "@local", "settings": settings})
                self.assertFalse(prepared.is_error)
                await client.call_tool("document_apply", {"plan": prepared.structured_content["plan"]})
                contexts = await client.call_tool("context_describe", {"plugin": document["id"] + "@local"})
                self.assertFalse(contexts.is_error)
                context_settings = {"name": "方法说明", "text": "Use the declared project vocabulary.", "enabled": True,
                                    "skill": contexts.structured_content["skills"][0]["ref"], "selector": {"user": "current"}}
                context_plan = await client.call_tool("context_preview", {"plugin": document["id"] + "@local", "settings": context_settings})
                self.assertFalse(context_plan.is_error)
                context_saved = await client.call_tool("context_apply", {"plan": context_plan.structured_content["plan"]})
                self.assertFalse(context_saved.is_error)
                opened = await client.call_tool("content_open", {"ref": document["id"] + "#example-skill", "version": "local", "budget": 1})
                self.assertFalse(opened.is_error)
                first = opened.structured_content
                self.assertEqual(first["readiness"], "needs-content")
                continued = await client.call_tool("content_continue", {"continuation": first["continuation"]})
                self.assertFalse(continued.is_error)
                self.assertEqual(continued.structured_content["snapshot"], first["snapshot"])
                self.assertEqual(continued.structured_content["readiness"], "ready")
                self.assertTrue(any(isinstance(unit["content"], dict) and unit["content"].get("text") == context_settings["text"]
                                    for unit in continued.structured_content["units"]))
                snapshot = await client.call_tool("content_snapshot", {"id": first["snapshot"]})
                self.assertFalse(snapshot.is_error)
                self.assertEqual(snapshot.structured_content["kind"], "ContentSnapshot")
                missing = await client.call_tool("document_read", {"id": "plugin:missing@local"})
                self.assertTrue(missing.is_error)


if __name__ == "__main__":
    unittest.main()
