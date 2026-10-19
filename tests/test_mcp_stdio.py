# audience: internal
# # mcp-stdio-tests
# 临时工作区承载中文来源与实际目录文件, 子进程生命周期由官方 stdio 传输管理.

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import Client, StdioServerParameters


# //// 验证 CLI 进程的标准输入输出与 UTF-8 保存 [@x380kkm 2026-09-06] ////
class McpStdioTests(unittest.IsolatedAsyncioTestCase):
    # //// 通过真实子进程导入并读取中文内容 [@x380kkm 2026-09-06] ////
    async def test_cli_stdio_persists_utf8_in_selected_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "中文工作区"
            source_directory = workspace / "技能来源"
            source_directory.mkdir(parents=True)
            source = source_directory / "SKILL.md"
            description = "整理中文资料, 保留项目术语."
            source.write_text(
                "---\nname: unicode-materials\ndescription: " + description
                + "\n---\n\n# 中文资料整理\n\n依据来源保留术语与事实.\n",
                encoding="utf-8",
            )
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "harness_manager.cli", "--user-root", str(workspace), "--read-root", str(source_directory), "mcp"],
            )

            async with asyncio.timeout(30), Client(server) as client:
                initial = await self.call_structured_tool(client, "catalog_list")
                self.assertEqual(initial["documents"], [])
                imported = await self.call_structured_tool(client, "document_import", {"path": str(source)})
                document = imported["document"]
                self.assertEqual(document["metadata"]["description"], description)
                preview = await self.call_structured_tool(client, "document_preview", {"document": document})
                await self.call_structured_tool(client, "document_apply", {"plan": preview["plan"]})
                catalog = await self.call_structured_tool(client, "catalog_list", {"query": "中文"})
                self.assertEqual(len(catalog["documents"]), 1)
                record = catalog["documents"][0]
                loaded = await self.call_structured_tool(client, "document_read", {"id": record["id"]})
                self.assertEqual(loaded["document"], document)
                binding = {"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": "binding:unicode",
                           "plugin": {"id": document["id"], "constraint": "local"},
                           "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": {"user": "current"}}}
                plan = await self.call_structured_tool(client, "document_preview", {"document": binding})
                await self.call_structured_tool(client, "document_apply", {"plan": plan["plan"]})
                pending = await self.call_structured_tool(client, "content_open", {"ref": document["id"] + "#unicode-materials", "version": "local", "budget": 1})

            async with asyncio.timeout(30), Client(server) as client:
                completed = await self.call_structured_tool(client, "content_continue", {"continuation": pending["continuation"]})
                self.assertEqual(completed["snapshot"], pending["snapshot"])
                self.assertEqual(completed["readiness"], "ready")
                self.assertIn(source.read_bytes().decode("utf-8"), [unit["content"] for unit in completed["units"]])

            catalog_path = workspace / ".harness" / "catalog.json"
            self.assertEqual(Path(record["path"]), catalog_path.resolve())
            self.assertIn(description, catalog_path.read_text(encoding="utf-8"))

    # //// 取得工具调用的结构化结果 [@x380kkm 2026-09-06] ////
    async def call_structured_tool(self, client: Client, name: str, arguments: dict | None = None) -> dict:
        result = await client.call_tool(name, arguments)
        self.assertFalse(result.is_error, result.content)
        self.assertIsNotNone(result.structured_content)
        return result.structured_content


# //// 运行标准输入输出集成验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
