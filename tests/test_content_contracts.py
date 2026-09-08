# audience: internal
# # content-contract-tests
# 声明, 来源正文和读取快照使用独立临时目录.
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.content import ContentError
from harness_manager.host_projection import compile_host
from harness_manager.service import Manager
from test_content_snapshots import binding, member, package


# //// 核对点契约默认值在内容读取中的来源选择 [@x380kkm 2026-09-08] ////
class ContentContractTests(unittest.TestCase):
    # //// 创建包含契约默认文件入口的独立声明 [@x380kkm 2026-09-08] ////
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "source"
        self.source.mkdir()
        user = root / "profile"
        user.mkdir()
        self.manager = Manager(read_roots=[self.source], user_root=user)
        (self.source / "SKILL.md").write_text("# 检查方法\n\n根据来源正文核对配置.", encoding="utf-8")
        (self.source / "RULE.md").write_text("规则正文来自契约选定的文件.", encoding="utf-8")
        point = "context.x380kkm/instruction"
        self.plugin = package("plugin:methods", self.source, [
            member("method", "skill.x380kkm/deployment", "SKILL.md"),
            {"id": "rule", "point": point, "contract": {"id": point, "range": "^1.0.0"},
             "criticality": {"default": "required"}, "payload": {}},
        ])
        self.contract = {
            "apiVersion": "manager.x380kkm/v1", "kind": "PointContract", "point": point,
            "contract": {"id": point, "version": "1.0.0"},
            "publisher": {"id": "publisher:rules", "source": self.plugin["sources"][0]["source"],
                          "integrity": {"algorithm": "fixture", "value": "rule-contract"}},
            "payloadSchema": {}, "normalizedResult": {"id": "result:instruction", "version": "1.0.0"},
            "operations": [], "defaults": {"source": "source:files", "payload": {"entry": "RULE.md"}},
        }
        for document in (self.plugin, self.contract, binding("binding:methods", self.plugin, {})):
            self.save(document)

    # //// 保存独立目录中的声明及其读取基线 [@x380kkm 2026-09-08] ////
    def save(self, document: dict, baseline: dict | None = None) -> None:
        self.manager.apply_document(self.manager.preview_document(document, baseline)["plan"])

    # //// 单元读取, 完整续读和宿主输出使用同一契约默认入口 [@x380kkm 2026-09-08] ////
    def test_default_source_is_shared_by_read_open_continue_and_host(self) -> None:
        candidate = next(item for item in self.manager.discover_content()["candidates"]
                         if item["ref"] == "plugin:methods#rule")
        single = self.manager.read_content(**candidate["read"]["params"])
        unit = single["units"][0]
        expected = (self.source / "RULE.md").read_text(encoding="utf-8")
        self.assertEqual(unit["content"], expected)
        self.assertEqual(unit["entry"], candidate["entry"])
        pending = self.manager.open_content("plugin:methods#method", "1.0.0", budget=1)
        complete = self.manager.continue_content(pending["continuation"])
        self.assertEqual(complete["readiness"], "ready")
        self.assertIn(expected, [item["content"] for item in complete["units"]])
        snapshot = self.manager.content_snapshot(complete["snapshot"])
        descriptor = next(item for item in snapshot["units"] if item.get("content", {}).get("ref") == "plugin:methods#rule")
        self.assertEqual(descriptor["content"]["artifact"]["revision"], unit["revision"])
        compiled = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader)
        self.assertEqual(compiled["diagnostics"], [])
        self.assertIn(expected, compiled["targets"]["AGENTS.override.md"].decode("utf-8"))

    # //// 点契约版本缺口在各读取入口保留为不可用内容 [@x380kkm 2026-09-08] ////
    def test_unmatched_contract_cannot_read_raw_payload(self) -> None:
        changed = deepcopy(self.plugin)
        changed["contributions"][1]["contract"]["range"] = "^2.0.0"
        self.save(changed, self.plugin)
        with self.assertRaises(ContentError) as caught:
            self.manager.read_content("plugin:methods#rule", "1.0.0")
        self.assertEqual(caught.exception.code, "unresolved_content")
        self.assertIn("unresolved_point_contract", [item["code"] for item in caught.exception.details])
        complete = self.manager.open_content("plugin:methods#method", "1.0.0")
        self.assertEqual(complete["readiness"], "needs-content")
        self.assertEqual(complete["missing"], ["plugin:methods#rule"])

    # //// 所选方法的必填字段约束适用于单元读取和完整读取 [@x380kkm 2026-09-08] ////
    def test_selected_skill_payload_must_satisfy_point_schema(self) -> None:
        point = "skill.x380kkm/deployment"
        contract = deepcopy(self.contract)
        contract.update(point=point, contract={"id": point, "version": "1.0.0"}, defaults={},
                        payloadSchema={"type": "object", "required": ["description"],
                                       "properties": {"description": {"type": "string", "minLength": 1}}})
        self.save(contract)
        with self.assertRaises(ContentError) as single:
            self.manager.read_content("plugin:methods#method", "1.0.0")
        self.assertEqual(single.exception.code, "unresolved_content")
        self.assertIn("invalid_payload", [item["code"] for item in single.exception.details])
        with self.assertRaises(ContentError) as complete:
            self.manager.open_content("plugin:methods#method", "1.0.0")
        self.assertEqual(complete.exception.code, "content_selection_unavailable")
        self.assertIn("invalid_payload", [item["code"] for item in complete.exception.details["diagnostics"]])
        compiled = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader)
        self.assertEqual(compiled["targets"], {})
        self.assertIn("invalid_payload", [item["code"] for item in compiled["diagnostics"]])

    # //// 默认字段和局部 Schema 引用共同固定读取快照 [@x380kkm 2026-09-08] ////
    def test_defaults_and_local_schema_refs_preserve_snapshot_validation(self) -> None:
        contract = deepcopy(self.contract)
        contract["defaults"]["payload"].update(description="检查命令来源.", settings={"language": "zh"})
        contract["payloadSchema"] = {
            "$defs": {"settings": {"type": "object", "required": ["language"],
                                    "properties": {"language": {"const": "zh"}}}},
            "type": "object", "required": ["entry", "description", "settings"],
            "properties": {"description": {"type": "string", "minLength": 1}, "settings": {"$ref": "#/$defs/settings"}},
        }
        self.save(contract, self.contract)
        single = self.manager.read_content("plugin:methods#rule", "1.0.0")
        pending = self.manager.open_content("plugin:methods#method", "1.0.0", budget=1)
        compiled = compile_host(self.manager.catalogs, self.manager.codex, self.manager.reader)
        self.assertEqual(compiled["diagnostics"], [])
        changed = deepcopy(self.plugin)
        changed["contributions"][1]["payload"]["settings"] = {"language": "en"}
        self.save(changed, self.plugin)
        with self.assertRaises(ContentError) as current:
            self.manager.read_content("plugin:methods#rule", "1.0.0")
        self.assertTrue(any("/settings/language" in item["message"] for item in current.exception.details))
        fresh = self.manager.open_content("plugin:methods#method", "1.0.0")
        self.assertEqual(fresh["readiness"], "needs-content")
        self.assertEqual(fresh["missing"], ["plugin:methods#rule"])
        complete = self.manager.continue_content(pending["continuation"])
        self.assertEqual(complete["readiness"], "ready")
        self.assertIn(single["units"][0]["content"], [item["content"] for item in complete["units"]])

    # //// 字段校验失败时保留点契约默认的必要性 [@x380kkm 2026-09-08] ////
    def test_invalid_optional_payload_and_required_defaults_preserve_availability(self) -> None:
        optional = deepcopy(self.plugin)
        optional["contributions"][1].pop("criticality")
        self.save(optional, self.plugin)
        contract = deepcopy(self.contract)
        contract["payloadSchema"] = {"type": "object", "required": ["description"]}
        self.save(contract, self.contract)
        with self.assertRaises(ContentError):
            self.manager.read_content("plugin:methods#rule", "1.0.0")
        available = self.manager.open_content("plugin:methods#method", "1.0.0")
        self.assertEqual(available["readiness"], "ready")
        self.assertFalse(any(item["ref"].endswith("/RULE.md") for item in available["units"]))
        self.assertIn("invalid_payload", [item["code"] for item in available["diagnostics"]])
        required = deepcopy(contract)
        required["defaults"]["criticality"] = {"default": "required"}
        self.save(required, contract)
        pending = self.manager.open_content("plugin:methods#method", "1.0.0", budget=1)
        complete = self.manager.continue_content(pending["continuation"])
        self.assertEqual(complete["readiness"], "needs-content")
        self.assertEqual(complete["missing"], ["plugin:methods#rule"])
        self.assertEqual(self.manager.read_statistics()["total"], 1)

    # //// 无法本地解释的 Schema 产生明确诊断 [@x380kkm 2026-09-08] ////
    def test_unresolvable_payload_schemas_have_offline_diagnostics(self) -> None:
        schemas = [{"type": "unknown"}, {"$ref": "#/$defs/missing"},
                   {"$ref": "https://schemas.invalid/payload.json"}, "https://schemas.invalid/payload.json"]
        previous = self.contract
        with patch("urllib.request.urlopen", side_effect=AssertionError("Schema 读取超出本地声明.")) as retrieve:
            for schema in schemas:
                with self.subTest(schema=schema):
                    contract = deepcopy(self.contract)
                    contract["payloadSchema"] = schema
                    self.save(contract, previous)
                    previous = contract
                    with self.assertRaises(ContentError) as single:
                        self.manager.read_content("plugin:methods#rule", "1.0.0")
                    self.assertIn("unresolved_payload_schema", [item["code"] for item in single.exception.details])
                    complete = self.manager.open_content("plugin:methods#method", "1.0.0")
                    self.assertEqual(complete["readiness"], "needs-content")
                    self.assertEqual(complete["missing"], ["plugin:methods#rule"])
                    self.assertIn("unresolved_payload_schema", [item["code"] for item in complete["diagnostics"]])
            retrieve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
