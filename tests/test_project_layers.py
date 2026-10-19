# audience: internal
# # project-layer-tests
# 临时目录核对项目共享与个人配置的隔离, 继承优先级和实际项目范围.

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from harness_manager.catalogs import Catalogs
from harness_manager.content_plan import plan_content
from harness_manager.content_snapshots import ContentSnapshots
from harness_manager.observations import ObservationStore
from harness_manager.projection import discover
from harness_manager.protocol import document_identity
from harness_manager.sources import SourceReader
from test_projection import make_binding, make_plugin


# //// 检查用户默认和项目双层配置的组合 [@x380kkm 2026-09-07] ////
class ProjectLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user, self.project = self.root / "user", self.root / "project"
        self.user.mkdir()
        self.project.mkdir()
        self.catalogs = Catalogs(self.user, self.project)
        self.plugin = make_plugin()
        self.plugin["sources"][0]["source"]["locator"] = str(self.project / "skills")

    # //// 在一个明确配置层提交声明 [@x380kkm 2026-09-07] ////
    def save(self, scope: str, document: dict, baseline: dict | None = None) -> None:
        store = self.catalogs.select(scope)
        store.apply(store.preview_put(document, baseline))

    # //// 按所选配置层求值有效内容 [@x380kkm 2026-09-07] ////
    def discover(self, scope: str) -> dict:
        view = self.catalogs.for_scope(scope)
        context = {"user": "me", **self.catalogs.context(scope)}
        return discover(view.documents, context, layers=view.layers)

    # //// 读取空配置只计算稳定路径和实际项目上下文 [@x380kkm 2026-09-07] ////
    def test_empty_layers_keep_project_and_user_directories_untouched(self) -> None:
        for scope in self.catalogs.layers():
            self.assertEqual(self.catalogs.for_scope(scope).documents, [])
        private = self.catalogs.select("project-local")
        self.assertEqual(private.workspace, self.user)
        self.assertTrue(private.directory.is_relative_to(self.user / ".harness/projects"))
        self.assertEqual(Catalogs(self.user, self.project).project_local.directory, private.directory)
        self.assertEqual(self.catalogs.context("project-local"), {"project": self.project.as_uri(), "path": str(self.project)})
        self.assertEqual(self.catalogs.context("user"), {})
        self.assertEqual(list(self.user.iterdir()), [])
        self.assertEqual(list(self.project.iterdir()), [])

    # //// 项目个人来源按项目位置解释并独立保存在用户目录 [@x380kkm 2026-09-07] ////
    def test_private_sources_and_bindings_resolve_against_actual_project(self) -> None:
        self.plugin["sources"][0]["source"]["locator"] = "skills"
        binding = make_binding("binding:private", self.plugin, {})
        self.save("project-local", self.plugin)
        self.save("project-local", binding)
        view = self.catalogs.effective()
        self.assertEqual(view.documents[0]["sources"][0]["source"]["locator"], str(self.project / "skills"))
        self.assertEqual(view.documents[1]["target"]["selector"], {"project": self.project.as_uri()})
        self.assertEqual(self.catalogs.project_local.snapshot()[1], binding)
        self.assertEqual(self.catalogs.for_scope("project").documents, [])
        self.assertEqual(self.catalogs.for_scope("user").documents, [])
        self.assertEqual(list(self.project.iterdir()), [])
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(Catalogs(self.user, other).effective().documents, [])

    # //// 个人设置覆盖共享字段且字段移除后恢复继承 [@x380kkm 2026-09-07] ////
    def test_private_values_override_shared_scope_and_restore_inheritance(self) -> None:
        self.save("user", self.plugin)
        defaults = make_binding("binding:user", self.plugin, {"user": "current"},
                                options={"format": {"width": 80, "style": "plain"}, "mode": "user"})
        shared = make_binding("binding:shared", self.plugin, {"path": str(self.project)},
                              options={"format": {"width": 100}, "mode": "shared"})
        private = make_binding("binding:private", self.plugin, {}, options={"format": {"width": 120}})
        for scope, binding in (("user", defaults), ("project", shared), ("project-local", private)):
            self.save(scope, binding)
        expected = {"format": {"width": 120, "style": "plain"}, "mode": "shared"}
        self.assertEqual(self.discover("project-local")["candidates"][0]["options"], expected)
        self.assertEqual(self.discover("project")["candidates"][0]["options"]["format"]["width"], 100)
        self.assertEqual(self.discover("user")["candidates"][0]["options"]["format"]["width"], 80)
        inherited = deepcopy(private)
        inherited.pop("options")
        self.save("project-local", inherited, private)
        self.assertEqual(self.discover("project-local")["candidates"][0]["options"],
                         self.discover("project")["candidates"][0]["options"])

    # //// 启用状态与成员选择使用各层显式覆盖 [@x380kkm 2026-09-07] ////
    def test_private_enabled_and_member_selection_override_shared_values(self) -> None:
        self.save("user", self.plugin)
        shared = make_binding("binding:shared", self.plugin, {}, enabled=False, selection={"exclude": ["method"]})
        private = make_binding("binding:private", self.plugin, {}, enabled=True, selection={"include": ["method"]})
        self.save("project", shared)
        self.save("project-local", private)
        self.assertEqual(self.discover("project")["candidates"], [])
        self.assertEqual(len(self.discover("project-local")["candidates"]), 1)
        self.catalogs.project_local.apply(self.catalogs.project_local.preview_remove(private["id"], private))
        self.assertEqual(self.discover("project-local")["candidates"], [])

    # //// 同层冲突与跨层来源差异保留明确诊断 [@x380kkm 2026-09-07] ////
    def test_same_layer_conflicts_and_conflicting_releases_remain_visible(self) -> None:
        self.save("project", self.plugin)
        self.save("project-local", self.plugin)
        identity = document_identity(self.plugin)
        view = self.catalogs.effective()
        self.assertEqual(len(view.documents), 1)
        self.assertEqual(view.layers[identity], 2)
        self.assertEqual([origin["scope"] for origin in view.origins[identity]], ["project", "project-local"])
        changed = deepcopy(self.plugin)
        changed["contributions"][0]["payload"]["description"] = "个人来源正文"
        self.save("project-local", changed, self.plugin)
        self.assertEqual(self.catalogs.effective().diagnostics[0]["code"], "catalog_identity_conflict")
        self.assertEqual(self.catalogs.effective().documents, [])
        self.save("project-local", self.plugin, changed)
        for name in ("first", "second"):
            self.save("project-local", make_binding("binding:" + name, self.plugin, {}, options={"mode": name}))
        result = self.discover("project-local")
        self.assertEqual(result["candidates"], [])
        self.assertIn("binding_conflict", {item["code"] for item in result["diagnostics"]})

    # //// 完整方法按配置层选择同一语义位置的说明 [@x380kkm 2026-09-07] ////
    def test_method_plan_uses_private_instruction_slot(self) -> None:
        self.plugin["contributions"][0]["payload"].pop("entry")
        self.save("user", self.plugin)
        self.save("user", make_binding("binding:method", self.plugin, {}))
        for scope in self.catalogs.layers():
            plugin = make_plugin("plugin:rules/" + scope)
            member = plugin["contributions"][0]
            member["point"] = "context.x380kkm/instruction"
            member["contract"]["id"] = member["point"]
            member["payload"]["slot"] = "execution"
            member["payload"].pop("entry")
            self.save(scope, plugin)
            self.save(scope, make_binding("binding:rules/" + scope, plugin, {}))
        view = self.catalogs.effective()
        plan = plan_content(view.documents, self.catalogs.context("project-local"),
                            self.plugin["id"] + "#method", None, [], layers=view.layers)
        instructions = [entry.content.summary["ref"] for entry in plan.entries if entry.purpose == "instruction"]
        self.assertEqual(instructions, ["plugin:rules/project-local#method"])
        snapshots = ContentSnapshots(ObservationStore(self.user), SourceReader(self.project, []))
        result = snapshots.open(view, self.catalogs.context("project-local"),
                                self.plugin["id"] + "#method", None, 65536, [])
        snapshot = snapshots.inspect(result["snapshot"])
        private_inputs = [item for item in snapshot["inputs"] if item["identity"] == "binding:rules/project-local"]
        self.assertEqual(private_inputs[0]["origins"], [{"scope": "project-local", "path": str(self.catalogs.project_local.catalog)}])


# //// 运行项目个人与共享层验证 [@x380kkm 2026-09-07] ////
if __name__ == "__main__":
    unittest.main()
