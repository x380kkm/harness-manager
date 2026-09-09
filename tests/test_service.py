# audience: internal
# # service-tests
"""用真实来源与临时目录核对导入, 发现, 完整读取和外部变更边界."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_manager.protocol import document_identity
from harness_manager.rpc import handle_request, serve
from harness_manager.service import Manager
from harness_manager.sources import SourceError, SourceReader


# //// 创建测试使用的真实 Skill 文件 [@x380kkm 2026-09-06] ////
def create_skill(root: Path) -> Path:
    directory = root / "example-skill"
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text("---\nname: example-skill\ndescription: 读取当前项目的资料.\n---\n\n# 方法\n\n保留来源与实际证据.\n", encoding="utf-8")
    return path


# //// 构造当前项目的使用绑定 [@x380kkm 2026-09-06] ////
def binding_for(plugin: dict, workspace: Path) -> dict:
    return {"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": "binding:test/project",
            "plugin": {"id": plugin["id"], "constraint": "local"},
            "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"},
                       "selector": {"project": workspace.as_uri()}},
            "selection": {"include": [plugin["contributions"][0]["id"]]}}


# //// 用临时目录执行静态管理用例 [@x380kkm 2026-09-06] ////
class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.manager = Manager(workspace, [self.root], user_root=self.root)

    # //// 导入保存绑定后按范围发现并完整读取来源 [@x380kkm 2026-09-06] ////
    def test_import_binding_discovery_and_full_read(self) -> None:
        path = create_skill(self.root)
        source_text = path.read_bytes().decode("utf-8")
        plugin = self.manager.import_file(str(path))["document"]
        self.assertFalse((self.root / ".harness").exists())
        plan = self.manager.preview_document(plugin)["plan"]
        self.manager.invoke("document.apply", {"plan": plan})
        self.assertEqual(path.read_bytes().decode("utf-8"), source_text)
        binding = binding_for(plugin, self.manager.workspace)
        self.manager.apply_document(self.manager.preview_document(binding)["plan"])
        result = self.manager.discover_content()
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["version"], "local")
        read = self.manager.read_content(candidate["ref"], candidate["version"])
        self.assertEqual(read["units"][0]["content"], source_text)
        self.assertEqual(read["unit_status"], "ready")
        other = self.manager.discover_content({"project": "workspace://another-project"})
        self.assertEqual(other["candidates"], [])

    # //// 中文名称保留独立的可登记身份 [@x380kkm 2026-09-06] ////
    def test_chinese_skill_names_keep_distinct_identities(self) -> None:
        path = create_skill(self.root)
        identities = []
        for name in ("项目结构分析", "资源解析"):
            path.write_text(f"---\nname: {name}\n---\n方法正文", encoding="utf-8")
            identities.append(self.manager.import_file(str(path))["document"]["id"])
        self.assertNotEqual(*identities)

    # //// 读取预算保持语义单元完整 [@x380kkm 2026-09-06] ////
    def test_small_budget_returns_pending_without_truncated_text(self) -> None:
        path = create_skill(self.root)
        plugin = self.manager.import_file(str(path))["document"]
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        ref = plugin["id"] + "#example-skill"
        result = self.manager.read_content(ref, "local", budget=10)
        self.assertEqual(result["unit_status"], "pending")
        self.assertEqual(result["units"], [])
        self.assertGreater(result["missing"][0]["required_bytes"], 10)

    # //// 工具入口作为静态声明读取并保留原生命令 [@x380kkm 2026-09-06] ////
    def test_tool_reference_reads_payload_without_source_execution(self) -> None:
        plugin = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:test/tool",
                  "release": {"version": "1.0.0"},
                  "sources": [{"id": "upstream", "source": {"resolver": {"id": "manager.source/git", "range": "^1.0.0"},
                               "locator": "https://example.invalid/tool"}}],
                  "contributions": [{"id": "tool", "point": "tool.x380kkm/endpoint",
                                     "contract": {"id": "tool.x380kkm/endpoint", "range": "^1.0.0"},
                                     "source": "source:upstream", "payload": {"command": {"runtime": "node", "entry": "tool.mjs"}}}]}
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        result = self.manager.read_content("plugin:test/tool#tool", "1.0.0")
        self.assertEqual(result["units"][0]["mediaType"], "application/json")
        self.assertEqual(json.loads(result["units"][0]["content"])["command"]["entry"], "tool.mjs")

    # //// 声明路径与作用范围保持在进程读取授权之外 [@x380kkm 2026-09-06] ////
    def test_catalog_data_cannot_grant_outside_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory)
            path = create_skill(outside)
            authorized = Manager(self.manager.workspace, [self.root, outside], user_root=self.root)
            plugin = authorized.import_file(str(path))["document"]
            self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
            with self.assertRaises(SourceError):
                self.manager.read_content(plugin["id"] + "#example-skill", "local")
            result = authorized.read_content(plugin["id"] + "#example-skill", "local")
            self.assertEqual(result["unit_status"], "ready")
            with self.assertRaises(SourceError):
                self.manager.read_content(plugin["id"] + "#example-skill", "local",
                                          expected_revision=result["units"][0]["revision"])

    # //// 内容引用的附加入口保持在来源目录内 [@x380kkm 2026-09-06] ////
    def test_relative_resource_cannot_escape_source_directory(self) -> None:
        path = create_skill(self.root)
        plugin = self.manager.import_file(str(path))["document"]
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        (self.root / "private.txt").write_text("只属于上层目录", encoding="utf-8")
        with self.assertRaises(SourceError):
            self.manager.read_content(plugin["id"] + "#example-skill", "local", "../private.txt")

    # //// 外部编辑进入同一目录且保留冲突基线 [@x380kkm 2026-09-06] ////
    def test_external_catalog_edit_is_observed(self) -> None:
        path = create_skill(self.root)
        plugin = self.manager.import_file(str(path))["document"]
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        catalog = self.root / ".harness" / "catalog.json"
        documents = json.loads(catalog.read_text(encoding="utf-8"))
        documents["documents"][0]["metadata"]["name"] = "外部工具修改"
        catalog.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.manager.list_documents()["documents"][0]["name"], "外部工具修改")
        current = self.manager.read_document(document_identity(plugin))["document"]
        self.assertEqual(current["metadata"]["name"], "外部工具修改")

    # //// 列表与关系图保持同一次磁盘读取的内容 [@x380kkm 2026-09-06] ////
    def test_catalog_snapshot_uses_one_source_read(self) -> None:
        plugin = self.manager.import_file(str(create_skill(self.root)))["document"]
        with patch.object(self.manager.store, "snapshot", side_effect=[[plugin], []]) as snapshot:
            result = self.manager.invoke("catalog.snapshot")
        snapshot.assert_called_once()
        identity = document_identity(plugin)
        self.assertEqual(result["documents"][0]["id"], identity)
        self.assertIn(identity, [node["id"] for node in result["graph"]["nodes"]])

    # //// 重复登记冲突返回可供恢复的对象身份 [@x380kkm 2026-09-06] ////
    def test_new_document_conflict_identifies_existing_document(self) -> None:
        plugin = self.manager.import_file(str(create_skill(self.root)))["document"]
        self.manager.apply_document(self.manager.preview_document(plugin)["plan"])
        response = handle_request(self.manager, {"id": 1, "method": "document.preview", "params": {"document": plugin}})
        self.assertEqual(response["error"]["code"], "catalog-conflict")
        identity = response["error"]["details"]["documentId"]
        self.assertEqual(self.manager.read_document(identity)["baseline"], plugin)

    # //// 逐行调用隔离非法输入并继续处理后续请求 [@x380kkm 2026-09-06] ////
    def test_rpc_recovers_after_bad_request(self) -> None:
        incoming = io.StringIO('bad-json\n{"id":2,"method":"catalog.list"}\n')
        outgoing = io.StringIO()
        serve(self.manager, incoming, outgoing)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn("error", responses[0])
        self.assertEqual(responses[1]["id"], 2)
        self.assertEqual(responses[1]["result"]["documents"], [])

    # //// 无效输入返回错误并保留后续调用 [@x380kkm 2026-09-06] ////
    def test_rpc_rejects_bad_input_without_stopping(self) -> None:
        invalid = ['{"id":NaN,"method":"catalog.list"}', '{"id":"\\ud800","method":"catalog.list"}',
                   '{"id":1,"method":"catalog.list","params":{"query":9}}']
        for line in invalid:
            with self.subTest(request=line):
                incoming = io.StringIO(line + '\n{"id":2,"method":"catalog.list"}\n')
                outgoing = io.StringIO()
                serve(self.manager, incoming, outgoing)
                responses = [json.loads(value) for value in outgoing.getvalue().splitlines()]
                self.assertIn("error", responses[0])
                self.assertEqual(responses[1]["id"], 2)
                outgoing.getvalue().encode("utf-8")

    # //// 无法传输的声明文本在预览前被拒绝 [@x380kkm 2026-09-06] ////
    def test_import_rejects_surrogate_text_without_creating_catalog(self) -> None:
        path = create_skill(self.root)
        plugin = self.manager.import_file(str(path))["document"]
        plugin["metadata"]["name"] = "\ud800"
        imported = self.root / "declaration.json"
        imported.write_text(json.dumps(plugin), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.manager.import_file(str(imported))
        self.assertFalse((self.root / ".harness").exists())

    # //// 实际子进程保留 UTF-8 请求与响应 [@x380kkm 2026-09-06] ////
    def test_cli_rpc_subprocess(self) -> None:
        path = create_skill(self.root)
        request = {"id": "导入", "method": "document.import", "params": {"path": str(path)}}
        result = subprocess.run([sys.executable, "-m", "harness_manager.cli", "--user-root", str(self.root),
                                 "--read-root", str(self.root), "rpc"],
                                input=json.dumps(request, ensure_ascii=False) + "\n", text=True, encoding="utf-8",
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["id"], "导入")
        self.assertEqual(response["result"]["document"]["metadata"]["description"], "读取当前项目的资料.")

    # //// 固定 Git 来源与工作树编辑分别读取 [@x380kkm 2026-09-06] ////
    def test_git_revision_reads_committed_content(self) -> None:
        repository = self.root / "repository"
        repository.mkdir()
        path = repository / "rules.md"
        path.write_text("固定来源内容", encoding="utf-8")
        (repository / "other.md").write_text("固定来源内容", encoding="utf-8")
        def git(*arguments: str) -> str:
            result = subprocess.run(["git", "-C", str(repository), "-c", "core.hooksPath=disabled-hooks",
                                     "-c", "user.name=Harness test", "-c", "user.email=fixture@example.invalid", *arguments],
                                    capture_output=True, text=True, encoding="utf-8", check=True)
            return result.stdout.strip()
        git("init")
        git("add", "rules.md", "other.md")
        git("commit", "-m", "保存读取样本")
        revision = git("rev-parse", "HEAD")
        path.write_text("工作树内容", encoding="utf-8")
        source = {"resolver": {"id": "manager.source/git"}, "locator": str(repository), "constraint": revision}
        result = SourceReader(self.root, []).read(source, "rules.md")
        self.assertEqual(result["content"], "固定来源内容")
        self.assertEqual(result["commit"], revision)
        self.assertTrue(result["revision"].startswith("git:" + revision + ":"))
        other = SourceReader(self.root, []).read(source, "other.md")
        self.assertEqual(other["content"], result["content"])
        self.assertNotEqual(other["revision"], result["revision"])


if __name__ == "__main__":
    unittest.main()
