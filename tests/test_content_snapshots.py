# audience: internal
# # content-snapshot-tests
# 真实来源, 独立用户目录与重启后的管理实例核对读取链的正文, 配置和访问边界.
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from harness_manager.content import ContentError
from harness_manager.service import Manager
from harness_manager.sources import SourceError


# //// 定义带有独立入口的可读取贡献 [@x380kkm 2026-09-06] ////
def member(identifier: str, point: str, entry: str, **payload) -> dict:
    return {"id": identifier, "point": point, "contract": {"id": point, "range": "^1.0.0"},
            "source": "source:files", "payload": {"entry": entry, **payload}}


# //// 创建来源包与其内容集合 [@x380kkm 2026-09-06] ////
def package(identifier: str, root: Path, members: list[dict], version: str = "1.0.0") -> dict:
    return {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": identifier,
            "release": {"version": version}, "sources": [{"id": "files", "source": {
                "resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": str(root)}}],
            "contributions": members}


# //// 定义明确使用范围的绑定 [@x380kkm 2026-09-06] ////
def binding(identifier: str, plugin: dict, selector: dict, **values) -> dict:
    return {"apiVersion": "manager.x380kkm/v1", "kind": "PluginBinding", "id": identifier,
            "plugin": {"id": plugin["id"], "constraint": plugin["release"]["version"]},
            "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": selector}, **values}


# //// 验证所选方法的完整读取与继续入口 [@x380kkm 2026-09-06] ////
class ContentSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "profile"
        self.project = self.root / "project"
        self.source = self.root / "source"
        for path in (self.user, self.project, self.source):
            path.mkdir()
        self.manager = Manager(self.project, [self.source], user_root=self.user)
        (self.source / "SKILL.md").write_text("# 分析方法\n\n" + "依据项目输入整理证据.\n" * 80, encoding="utf-8")
        (self.source / "notes.md").write_text("项目配套术语", encoding="utf-8")
        self.ref = "plugin:methods#analysis"
        self.plugin = package("plugin:methods", self.source, [
            member("analysis", "skill.x380kkm/deployment", "SKILL.md", name="analysis", description="整理实际证据."),
            member("notes", "context.x380kkm/task", "notes.md", skill=self.ref),
            member("other", "skill.x380kkm/deployment", "unrelated.md", name="other", description="另一项独立方法."),
        ])
        self.save(self.plugin)
        self.usage = binding("binding:user-methods", self.plugin, {"user": "current"}, options={"terminology": "项目术语"})
        self.save(self.usage)

    # //// 在所选归属中保存真实声明 [@x380kkm 2026-09-06] ////
    def save(self, document: dict, baseline: dict | None = None, scope: str = "user") -> None:
        self.manager.apply_document(self.manager.preview_document(document, baseline, scope)["plan"])

    # //// 汇集方法和配套内容并保留其他 Skill 的隔离 [@x380kkm 2026-09-06] ////
    def test_read_result_contains_options_method_and_matching_context(self) -> None:
        result = self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(result["kind"], "ContentReadResult")
        self.assertEqual(result["readiness"], "ready")
        self.assertEqual(len(result["units"]), 3)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["units"][0]["content"]["selections"][0]["options"], {"terminology": "项目术语"})
        contents = [unit["content"] for unit in result["units"]]
        self.assertIn("项目配套术语", contents)
        self.assertIn((self.source / "SKILL.md").read_bytes().decode("utf-8"), contents)
        self.assertFalse(any("unrelated.md" in ref for ref in result["requiredUnits"]))
        snapshot = self.manager.content_snapshot(result["snapshot"])
        self.assertEqual(snapshot["request"], {"ref": self.ref, "version": "1.0.0"})
        self.assertIn(self.usage["id"], [item["identity"] for item in snapshot["inputs"]])

    # //// 跨进程续读保留开始时的正文与配置 [@x380kkm 2026-09-06] ////
    def test_continue_after_source_and_binding_changes_uses_captured_snapshot(self) -> None:
        original = (self.source / "SKILL.md").read_bytes().decode("utf-8")
        pending = self.manager.open_content(self.ref, "1.0.0", budget=1)
        self.assertEqual(pending["units"], [])
        self.assertEqual(pending["readiness"], "needs-content")
        (self.source / "SKILL.md").write_text("来源已经更新", encoding="utf-8")
        changed = deepcopy(self.usage)
        changed["options"]["terminology"] = "另一组术语"
        self.save(changed, self.usage)
        restarted = Manager(self.project, [self.source], user_root=self.user)
        complete = restarted.continue_content(pending["continuation"])
        self.assertEqual(complete["snapshot"], pending["snapshot"])
        self.assertEqual(complete["previous"], pending["id"])
        self.assertEqual(complete["selection"], pending["selection"])
        self.assertEqual(complete["readiness"], "ready")
        self.assertIn(original, [unit["content"] for unit in complete["units"]])
        snapshot = restarted.content_snapshot(complete["snapshot"])
        self.assertEqual(snapshot["configurations"][0]["options"]["terminology"], "项目术语")
        fresh = restarted.open_content(self.ref, "1.0.0")
        self.assertIn("来源已经更新", [unit["content"] for unit in fresh["units"]])

    # //// 分次返回的单元共同满足同一份必需集合 [@x380kkm 2026-09-06] ////
    def test_partial_reads_accumulate_without_repeating_units(self) -> None:
        first = self.manager.open_content(self.ref, "1.0.0", budget=900)
        self.assertGreater(len(first["units"]), 0)
        self.assertEqual(first["readiness"], "needs-content")
        second = self.manager.continue_content(first["continuation"])
        before = {unit["ref"] for unit in first["units"]}
        after = {unit["ref"] for unit in second["units"]}
        self.assertFalse(before.intersection(after))
        self.assertEqual(before | after, set(second["requiredUnits"]))
        self.assertEqual(second["readiness"], "ready")
        with self.assertRaises(ContentError) as caught:
            self.manager.continue_content(second["id"])
        self.assertEqual(caught.exception.code, "content_continuation_complete")

    # //// 无法推进的续读保留原入口并避免空回执累积 [@x380kkm 2026-09-06] ////
    def test_insufficient_continuation_keeps_existing_receipt(self) -> None:
        first = self.manager.open_content(self.ref, "1.0.0", budget=1)
        existing = set(self.manager.observations.directory.iterdir())
        with self.assertRaises(ContentError) as caught:
            self.manager.continue_content(first["continuation"], budget=1)
        self.assertEqual(caught.exception.code, "content_budget_unmet")
        self.assertGreater(caught.exception.details["minimumBudget"], 1)
        self.assertEqual(set(self.manager.observations.directory.iterdir()), existing)

    # //// 缓存正文继续受当前来源读取授权约束 [@x380kkm 2026-09-06] ////
    def test_snapshot_does_not_preserve_revoked_read_authority(self) -> None:
        pending = self.manager.open_content(self.ref, "1.0.0", budget=1)
        restricted = Manager(self.project, user_root=self.user)
        with self.assertRaises(SourceError):
            restricted.continue_content(pending["continuation"])
        with self.assertRaises(SourceError):
            restricted.content_snapshot(pending["snapshot"])

    # //// 配套正文缺失时保持内容不齐备 [@x380kkm 2026-09-06] ////
    def test_missing_task_context_keeps_required_unit_pending(self) -> None:
        (self.source / "notes.md").unlink()
        result = self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(result["readiness"], "needs-content")
        self.assertEqual(len(result["missing"]), 1)
        self.assertTrue(result["missing"][0].endswith("/notes.md"))
        self.assertNotIn("continuation", result)
        self.assertIn("content.required-unit-unavailable", [item["code"] for item in result["diagnostics"]])

    # //// 核对目录发布与必需配套内容的选择边界 [@x380kkm 2026-09-08] ////
    def test_unselected_release_preserves_required_exclusion(self) -> None:
        selected = deepcopy(self.plugin)
        selected["contributions"][1]["criticality"] = {"default": "required"}
        self.save(selected, self.plugin)
        usage = deepcopy(self.usage)
        usage["selection"] = {"exclude": ["notes"]}
        self.save(usage, self.usage)
        before = self.manager.open_content(self.ref, "1.0.0")
        alternate = deepcopy(selected)
        alternate["release"]["version"] = "2.0.0"
        alternate["contributions"][1]["payload"]["skill"] = "plugin:methods#other"
        self.save(alternate)
        after = self.manager.open_content(self.ref, "1.0.0", budget=1)
        complete = self.manager.continue_content(after["continuation"])
        self.assertEqual(before["readiness"], "needs-content")
        self.assertEqual(complete["readiness"], before["readiness"])
        self.assertEqual(complete["missing"], before["missing"])
        self.assertEqual(complete["missing"], ["plugin:methods#notes"])
        self.assertEqual(complete["selection"], before["selection"])
        self.assertEqual(self.manager.read_statistics()["total"], 0)

    # //// 保留固定版本中范围无法解释的必需内容 [@x380kkm 2026-09-08] ////
    def test_unselected_release_preserves_required_scope_gap(self) -> None:
        selected = deepcopy(self.plugin)
        selected["contributions"][1].update(criticality={"default": "required"}, scope={
            "contract": {"id": "manager.scope", "range": "^2.0.0"}, "selector": {}})
        self.save(selected, self.plugin)
        alternate = deepcopy(self.plugin)
        alternate["release"]["version"] = "2.0.0"
        self.save(alternate)
        result = self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(result["readiness"], "needs-content")
        self.assertEqual(result["missing"], ["plugin:methods#notes"])
        self.assertIn("required_unavailable", [item["code"] for item in result["diagnostics"]])

    # //// 同一说明位置使用项目级的明确覆盖 [@x380kkm 2026-09-06] ////
    def test_instruction_slot_uses_narrower_binding(self) -> None:
        for scope, text in (("user", "通用执行说明"), ("project", "项目执行说明")):
            (self.source / (scope + ".md")).write_text(text, encoding="utf-8")
            plugin = package("plugin:" + scope, self.source, [member("rules", "context.x380kkm/instruction", scope + ".md", slot="execution")])
            self.save(plugin, scope=scope)
            self.save(binding("binding:" + scope, plugin, {}), scope=scope)
        result = self.manager.open_content(self.ref, "1.0.0")
        contents = [unit["content"] for unit in result["units"]]
        self.assertIn("项目执行说明", contents)
        self.assertNotIn("通用执行说明", contents)
        self.assertEqual(result["readiness"], "ready")

    # //// 同源规则的每个绑定范围参与位置选择 [@x380kkm 2026-09-08] ////
    def test_shared_rule_alias_preserves_narrower_binding(self) -> None:
        point = "context.x380kkm/instruction"
        source = package("plugin:source", self.source, [{"id": "rule", "point": point,
            "contract": {"id": point, "range": "^1.0.0"}, "payload": {"slot": "execution", "text": "项目规则"}}])
        competing = deepcopy(source)
        competing["id"] = "plugin:competing"
        competing["contributions"][0]["payload"]["text"] = "通用规则"
        self.save(source)
        self.save(competing)
        self.save(binding("binding:competing", competing, {}))
        for name, selector in (("a", {}), ("z", {"project": "current"})):
            alias = package("plugin:" + name, self.source, [{"id": "rule", "ref": "plugin:source#rule", "constraint": "1.0.0"}])
            self.save(alias)
            self.save(binding("binding:" + name, alias, selector))
        result = self.manager.open_content(self.ref, "1.0.0")
        texts = [unit["content"].get("text") for unit in result["units"] if isinstance(unit["content"], dict)]
        self.assertEqual(texts.count("项目规则"), 1)
        self.assertNotIn("通用规则", texts)
        snapshot = self.manager.content_snapshot(result["snapshot"])
        references = {entry["ref"] for entry in snapshot["configurations"]}
        self.assertTrue({"plugin:a#rule", "plugin:z#rule"}.issubset(references))

    # //// 拒绝同一规则位置中的不同来源和不同版本 [@x380kkm 2026-09-08] ////
    def test_instruction_slot_rejects_distinct_content_and_versions(self) -> None:
        point = "context.x380kkm/instruction"
        first = package("plugin:rules", self.source, [{"id": "rule", "point": point,
            "contract": {"id": point, "range": "^1.0.0"}, "payload": {"slot": "execution", "text": "当前规则"}}])
        second = deepcopy(first)
        second["release"]["version"] = "2.0.0"
        other = deepcopy(first)
        other["id"] = "plugin:other-rules"
        for document in (first, second, other):
            self.save(document)
        left = package("plugin:left", self.source, [{"id": "rule", "ref": "plugin:rules#rule", "constraint": "1.0.0"}])
        self.save(left)
        self.save(binding("binding:left", left, {}))
        previous = None
        for target, version in (("plugin:other-rules#rule", "1.0.0"), ("plugin:rules#rule", "2.0.0")):
            with self.subTest(target=target, version=version):
                right = package("plugin:right", self.source, [{"id": "rule", "ref": target, "constraint": version}])
                self.save(right, previous)
                if previous is None:
                    self.save(binding("binding:right", right, {}))
                previous = right
                with self.assertRaises(ContentError) as caught:
                    self.manager.open_content(self.ref, "1.0.0")
                self.assertEqual(caught.exception.code, "instruction_slot_conflict")

    # //// 组合包选择保持外层版本和原始内容身份 [@x380kkm 2026-09-06] ////
    def test_bundle_snapshot_keeps_selection_and_content_versions_separate(self) -> None:
        bundle = package("plugin:bundle", self.source, [{"id": "alias", "ref": self.ref, "constraint": "1.0.0"}], "2.0.0")
        self.save(bundle)
        self.save(binding("binding:bundle", bundle, {"user": "current"}, options={"mode": "combined"}))
        result = self.manager.open_content("plugin:bundle#alias", "2.0.0")
        snapshot = self.manager.content_snapshot(result["snapshot"])
        self.assertEqual(snapshot["request"], {"ref": "plugin:bundle#alias", "version": "2.0.0"})
        self.assertEqual(result["selection"]["ref"], self.ref)
        self.assertIn("plugin:methods@1.0.0", result["selection"]["artifact"]["source"])
        self.assertIn("项目配套术语", [unit["content"] for unit in result["units"]])

    # //// 载体声明和调用选择的相对单元都计入齐备性 [@x380kkm 2026-09-06] ////
    def test_explicit_required_resources_and_requested_resources_are_complete(self) -> None:
        for name in ("terms.md", "algorithm.md"):
            (self.source / name).write_text(name + " 完整正文", encoding="utf-8")
        changed = deepcopy(self.plugin)
        changed["contributions"][0]["extensions"] = [{"contract": {"id": "carrier.x380kkm/required-units", "range": "^1.0.0"},
                                                       "payload": {"entries": ["terms.md"]}}]
        self.save(changed, self.plugin)
        result = self.manager.open_content(self.ref, "1.0.0", resources=["algorithm.md"])
        self.assertEqual(result["readiness"], "ready")
        self.assertIn("terms.md 完整正文", [unit["content"] for unit in result["units"]])
        self.assertIn("algorithm.md 完整正文", [unit["content"] for unit in result["units"]])

    # //// 禁用的选择无法形成使用快照 [@x380kkm 2026-09-06] ////
    def test_disabled_selection_cannot_open_method(self) -> None:
        changed = {**self.usage, "enabled": False}
        self.save(changed, self.usage)
        with self.assertRaises(ContentError) as caught:
            self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(caught.exception.code, "content_selection_unavailable")

    # //// 未提供解释器的用途版本保留明确缺口 [@x380kkm 2026-09-06] ////
    def test_unknown_usage_contract_cannot_claim_method_readiness(self) -> None:
        changed = deepcopy(self.plugin)
        changed["contributions"][0]["contract"]["range"] = "^2.0.0"
        self.save(changed, self.plugin)
        with self.assertRaises(ContentError) as caught:
            self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(caught.exception.code, "content_use_contract")

    # //// 错误使用选项在正文采集前产生可定位诊断 [@x380kkm 2026-09-06] ////
    def test_invalid_usage_options_cannot_open_method(self) -> None:
        changed = deepcopy(self.plugin)
        changed["options"] = {"schema": {"type": "object", "properties": {"terminology": {"type": "array"}}}}
        self.save(changed, self.plugin)
        with self.assertRaises(ContentError) as caught:
            self.manager.open_content(self.ref, "1.0.0")
        self.assertIn("invalid_options", [item["code"] for item in caught.exception.details["diagnostics"]])

    # //// 包级强内容引用也参与方法齐备性 [@x380kkm 2026-09-06] ////
    def test_package_content_requirement_is_required(self) -> None:
        changed = deepcopy(self.plugin)
        changed["requirements"] = [{"id": "required-terms", "target": "plugin:missing#terms", "strength": "required",
                                    "missing": {"visibility": "reported", "acquisition": "none"}}]
        self.save(changed, self.plugin)
        result = self.manager.open_content(self.ref, "1.0.0")
        self.assertEqual(result["readiness"], "needs-content")
        self.assertIn("plugin:missing#terms", result["missing"])

    # //// 结构载体保留完整 JSON 模型内容 [@x380kkm 2026-09-06] ////
    def test_archify_carrier_returns_complete_structural_value(self) -> None:
        model = {"schema_version": 1, "diagram_type": "architecture", "meta": {"title": "方法关系"},
                 "components": [{"id": "input", "type": "external", "label": "输入"}]}
        (self.source / "method.archify.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        changed = deepcopy(self.plugin)
        changed["contributions"][0]["payload"].update(entry="method.archify.json", carrier={
            "contract": {"id": "carrier.x380kkm/archify-ir", "range": "^1.0.0"}, "mediaType": "application/vnd.archify+json"})
        self.save(changed, self.plugin)
        result = self.manager.open_content(self.ref, "1.0.0")
        structural = next(unit for unit in result["units"] if unit["mediaType"] == "application/vnd.archify+json")
        self.assertEqual(structural["content"], model)


# //// 运行完整内容快照验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
