# audience: internal
# # catalog-layer-tests
# 独立临时目录承载用户默认与项目设置, 核对继承结果, 写入位置和来源授权.

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from harness_manager.service import Manager, ServiceError
from harness_manager.sources import SourceError
from test_projection import make_binding, make_plugin
from test_service import create_skill


# //// 在独立目录中验证用户与项目的使用关系 [@x380kkm 2026-09-06] ////
class CatalogLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "profile"
        self.project = self.root / "project"
        self.user.mkdir()
        self.project.mkdir()

    # //// 在指定归属中保存声明 [@x380kkm 2026-09-06] ////
    def save(self, manager: Manager, document: dict, scope: str = "user") -> dict:
        return manager.apply_document(manager.preview_document(document, scope=scope)["plan"])

    # //// 用户级内容在未选定项目时完成登记与发现 [@x380kkm 2026-09-06] ////
    def test_user_catalog_operates_without_project(self) -> None:
        manager = Manager(user_root=self.user)
        self.assertIsNone(manager.snapshot_catalog()["workspace"])
        self.assertFalse((self.user / ".harness").exists())
        plugin = make_plugin()
        self.save(manager, plugin)
        binding = make_binding("binding:user", plugin, {"user": "current"})
        self.save(manager, binding)
        self.assertEqual(manager.discover_content()["candidates"][0]["ref"], plugin["id"] + "#method")
        self.assertTrue((self.user / ".harness/catalog.json").is_file())
        self.assertFalse((self.project / ".harness").exists())

    # //// 用户维护入口独立于项目目录的读取状态 [@x380kkm 2026-09-06] ////
    def test_unreadable_project_keeps_user_editing_available(self) -> None:
        project_catalog = self.project / ".harness"
        project_catalog.mkdir()
        (project_catalog / "catalog.json").write_text("unfinished project edit", encoding="utf-8")
        manager = Manager(self.project, user_root=self.user)
        plugin = make_plugin()
        self.save(manager, plugin)
        self.assertEqual(manager.list_documents()["documents"][0]["id"], plugin["id"] + "@1.0.0")
        manager.apply_document(manager.preview_remove(plugin["id"] + "@1.0.0", plugin)["plan"])
        self.assertEqual(manager.list_documents()["documents"], [])

    # //// 局部选项覆盖默认值并在移除后恢复继承 [@x380kkm 2026-09-06] ////
    def test_project_options_compose_with_user_defaults(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        plugin = make_plugin()
        self.save(manager, plugin)
        defaults = make_binding("binding:user", plugin, {"user": "current"}, options={"format": {"width": 80}, "mode": "plain"})
        self.save(manager, defaults)
        local = make_binding("binding:project", plugin, {}, options={"format": {"width": 100}})
        self.save(manager, local, "project")
        selected = manager.discover_content(detail="full")["candidates"][0]
        self.assertEqual(selected["options"], {"format": {"width": 100}, "mode": "plain"})
        self.assertEqual(local["target"]["selector"], {})
        catalog = manager.snapshot_catalog("project")
        self.assertEqual(catalog["diagnostics"], [])
        self.assertEqual([record["id"] for record in catalog["documents"]], [local["id"]])
        inherited = [node for node in catalog["graph"]["nodes"] if node.get("inherited")]
        self.assertTrue(inherited)
        self.assertTrue(all(node["documentId"] is None and node["scope"] == "user" for node in inherited))
        user_only = Manager(user_root=self.user).discover_content(detail="full")["candidates"][0]
        self.assertEqual(user_only["options"]["format"]["width"], 80)
        plan = manager.preview_remove(local["id"], local, "project")["plan"]
        manager.apply_document(plan)
        self.assertEqual(manager.discover_content(detail="full")["candidates"][0]["options"], user_only["options"])
        self.assertEqual(manager.read_document(defaults["id"])["document"], defaults)

    # //// 项目目录限定绑定的实际作用位置 [@x380kkm 2026-09-06] ////
    def test_project_binding_cannot_apply_to_other_context(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        plugin = make_plugin()
        self.save(manager, plugin)
        self.save(manager, make_binding("binding:local", plugin, {}), "project")
        self.assertEqual(len(manager.discover_content()["candidates"]), 1)
        self.assertEqual(manager.discover_content({"project": "file:///another/project"})["candidates"], [])

    # //// 计划固定原目录并拒绝在另一个项目提交 [@x380kkm 2026-09-06] ////
    def test_plan_cannot_move_to_another_project(self) -> None:
        first = Manager(self.project, user_root=self.user)
        second_root = self.root / "other"
        second_root.mkdir()
        second = Manager(second_root, user_root=self.user)
        plan = first.preview_document(make_plugin(), scope="project")["plan"]
        with self.assertRaises(ServiceError) as caught:
            second.apply_document(plan)
        self.assertEqual(caught.exception.code, "catalog_changed")
        self.assertFalse((second_root / ".harness").exists())

    # //// 不同内容使用相同身份时保留冲突而非隐式选择 [@x380kkm 2026-09-06] ////
    def test_conflicting_identity_is_excluded_from_effective_content(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        plugin = make_plugin()
        self.save(manager, plugin)
        changed = deepcopy(plugin)
        changed["contributions"][0]["payload"]["description"] = "项目独立说明"
        self.save(manager, changed, "project")
        result = manager.effective_catalog()
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["diagnostics"][0]["code"], "catalog_identity_conflict")
        self.assertEqual({origin["scope"] for origins in result["origins"].values() for origin in origins}, {"user", "project"})

    # //// 用户目录归属与来源读取授权分别核对 [@x380kkm 2026-09-06] ////
    def test_user_catalog_root_does_not_grant_source_read_access(self) -> None:
        path = create_skill(self.user)
        authorized = Manager(read_roots=[path.parent], user_root=self.user)
        plugin = authorized.import_file(str(path))["document"]
        self.save(authorized, plugin)
        manager = Manager(user_root=self.user)
        with self.assertRaises(SourceError):
            manager.read_content(plugin["id"] + "#example-skill", "local")
        result = authorized.read_content(plugin["id"] + "#example-skill", "local")
        self.assertEqual(result["units"][0]["content"], path.read_bytes().decode("utf-8"))

    # //// 相对来源按原目录解释并继续执行读取授权 [@x380kkm 2026-09-06] ////
    def test_relative_user_source_is_stable_inside_project(self) -> None:
        path = create_skill(self.user)
        manager = Manager(self.project, [path.parent], user_root=self.user)
        plugin = manager.import_file(str(path))["document"]
        plugin["sources"][0]["source"]["locator"] = "example-skill"
        self.save(manager, plugin)
        unit = manager.read_content(plugin["id"] + "#example-skill", "local")["units"][0]
        self.assertEqual(Path(unit["path"]), path)
        self.assertEqual(manager.read_document(plugin["id"] + "@local")["document"]["sources"][0]["source"]["locator"], "example-skill")


# //// 运行目录继承与写入边界验证 [@x380kkm 2026-09-06] ////
if __name__ == "__main__":
    unittest.main()
