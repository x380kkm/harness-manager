# toolkit-tests
# 临时用户目录, 项目目录与本地 Git 来源验证组件安装, 内容读取, 更新和目录搬移.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_manager.components.installer import InstallError, read_install_state
from harness_manager.service import Manager


# //// 验证隔离配置中的组件安装和来源更新 [@x380kkm 2026-10-19] ////
class ToolkitTests(unittest.TestCase):
    # //// 创建相互独立的用户目录与项目目录 [@x380kkm 2026-10-19] ////
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user = self.root / "user"
        self.project = self.root / "project"
        self.user.mkdir()
        self.project.mkdir()

    # //// 在本地仓库执行 Git 并保留失败输出 [@x380kkm 2026-10-19] ////
    def git(self, repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments], capture_output=True,
            text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    # //// 写入一份可供实际读取的 Skill [@x380kkm 2026-10-19] ////
    def write_skill(self, path: Path, body: str) -> None:
        path.write_text(
            "---\nname: fixture-skill\ndescription: 临时来源的项目方法.\n---\n\n"
            "# Fixture Skill\n\n" + body + "\n", encoding="utf-8",
        )

    # //// 通过候选实际提供的入口读取完整正文 [@x380kkm 2026-10-19] ////
    def read_skill(self, manager: Manager, scope: str, query: str) -> str:
        discovered = manager.list_skills(
            context={"host": "harness-manager"}, scope=scope, query=query,
        )["candidates"]
        candidates = [candidate for candidate in discovered
                      if candidate["ref"].startswith(f"plugin:toolkit/{scope}/")]
        self.assertEqual(len(candidates), 1)
        read = candidates[0]["read"]
        result = manager.invoke(read["method"], read["params"])
        self.assertEqual(result["readiness"], "ready", result["diagnostics"])
        return "\n".join(
            unit["content"] for unit in result["units"] if isinstance(unit["content"], str)
        )

    # //// 核对安装保持宿主原生目录缺失 [@x380kkm 2026-10-19] ////
    def assert_hosts_absent(self) -> None:
        for root in (self.user, self.project):
            self.assertFalse((root / ".codex").exists())
            self.assertFalse((root / ".claude").exists())

    # //// 创建使用实际克隆与快进更新的本地 Skill 来源 [@x380kkm 2026-10-19] ////
    def create_source(self, scope: str = "user") -> tuple[Path, Path]:
        repository = self.root / (scope + "-upstream")
        repository.mkdir()
        self.git(repository, "init", "-b", "main")
        self.git(repository, "config", "user.name", "Toolkit Fixture")
        self.git(repository, "config", "user.email", "toolkit@example.invalid")
        self.write_skill(repository / "SKILL.md", "来源正文甲.")
        self.git(repository, "add", "SKILL.md")
        self.git(repository, "commit", "-m", "添加来源正文")
        owner = self.user if scope == "user" else self.project
        manifest = owner / "components.json"
        manifest.write_text(json.dumps({"schema": 1, "components": [{
            "id": "fixture-skill",
            "identity": {"name": "Fixture Skill", "kind": "skill", "source": {
                "relative": "../sources/fixture-skill",
            }},
            "install": {"git": {"url": str(repository)}, "targets": [{
                "root": "host", "source_root": "component", "source": ".",
                "path": "skills/fixture-skill",
            }]},
            "runtime": {},
            "health": {"command": ["{python}", "-c", "from pathlib import Path; assert Path('SKILL.md').is_file()"]},
            "update": {"strategy": "git-fast-forward", "repository": ".",
                       "remote": "origin", "branch": "main", "apply": {"kind": "git-fast-forward"}},
        }]}, ensure_ascii=False), encoding="utf-8")
        return repository, manifest

    # //// 内置 Skill 在重复安装和进程重建后保持可读 [@x380kkm 2026-10-19] ////
    def test_内置安装与重启读取(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        installed = manager.toolkit.install(["agent-manager"])
        self.assertEqual(installed["status"], "installed", installed)
        content = self.read_skill(manager, "user", "agent-manager")
        self.assertIn("harness-manager toolkit install", content)
        manager.toolkit.install(["agent-manager"])
        restarted = Manager(self.project, user_root=self.user)
        self.assertEqual(self.read_skill(restarted, "user", "agent-manager"), content)
        self.assert_hosts_absent()

    # //// 默认安装计划仅包含发行包提供的 Skill [@x380kkm 2026-10-19] ////
    def test_默认组件计划(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        plan = manager.toolkit.plan()
        skills = {record["component"] for record in plan["targets"] if "/skills/" in record["target"].replace("\\", "/")}
        self.assertEqual(skills, {"agent-manager", "clean-tools-skill", "codegraph-skill", "archify-skill"})

    def test_交接组件安装与用户目录迁移(self) -> None:
        manager = Manager(self.project, user_root=self.user)
        installed = manager.toolkit.install(["agent-handoff"])
        self.assertEqual(installed["status"], "installed", installed)
        content = self.read_skill(manager, "user", "agent-handoff")
        relocated = self.root / "relocated-user"
        shutil.copytree(self.user, relocated)
        moved = Manager(self.project, user_root=relocated)
        self.assertEqual(self.read_skill(moved, "user", "agent-handoff"), content)
        script = relocated / ".harness/toolkit/skills/agent-handoff/scripts/resolve_paths.js"
        result = subprocess.run(
            ["node", str(script), "--project", str(self.project), "--codex-home", str(relocated / ".codex")],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(json.loads(result.stdout)["codexSessions"]), relocated / ".codex/sessions")
        self.assert_hosts_absent()

    # //// 旧选择更新时解除移除组件并保留安装目录中的个人正文 [@x380kkm 2026-10-19] ////
    def test_移除组件的旧选择更新保留个人内容(self) -> None:
        repository, manifest = self.create_source()
        configuration = json.loads(manifest.read_text(encoding="utf-8"))
        configuration["components"][0]["id"] = "source-adapter-skill"
        manifest.write_text(json.dumps(configuration), encoding="utf-8")
        manager = Manager(self.project, [self.root], user_root=self.user)
        installed = manager.toolkit.install(["source-adapter-skill"], manifest=str(manifest))
        toolkit = Path(installed["root"])
        target = toolkit / "skills/fixture-skill/SKILL.md"
        self.write_skill(target, "安装副本中的个人正文.")
        settings = toolkit.parent / "toolkit.json"
        settings.write_text(json.dumps({"components": ["agent-manager", "source-adapter-skill"]}), encoding="utf-8")
        updated = manager.toolkit.update()
        self.assertEqual(updated["status"], "updated", updated)
        self.assertIn("个人正文", target.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8"))["components"], ["agent-manager", "agent-rules"])
        self.assertFalse(any(document.get("plugin", {}).get("id", document["id"]).endswith("/source-adapter-skill")
                             for document in manager.catalogs.user.snapshot()))
        self.assertNotIn("skills/fixture-skill", read_install_state(toolkit)["targets"])
        self.assertTrue(any(record["files"] == "preserved" for record in updated["retired"]))
        self.assertEqual(manager.toolkit.update()["retired"], [])
        self.assert_hosts_absent()

    # //// 上游提交同步安装副本且来源工作树编辑阻止更新 [@x380kkm 2026-10-19] ////
    def test_Git_获取与更新及来源编辑保护(self) -> None:
        repository, manifest = self.create_source()
        manager = Manager(self.project, [self.root], user_root=self.user)
        installed = manager.toolkit.install(["fixture-skill"], manifest=str(manifest))
        self.assertEqual(installed["status"], "installed", installed)
        toolkit = Path(installed["root"])
        target = toolkit / "skills/fixture-skill/SKILL.md"
        self.assertIn("来源正文甲.", self.read_skill(manager, "user", "fixture-skill"))
        self.write_skill(repository / "SKILL.md", "来源正文乙.")
        self.git(repository, "add", "SKILL.md")
        self.git(repository, "commit", "-m", "更新来源正文")
        updated = manager.toolkit.update(["fixture-skill"])
        self.assertEqual(updated["status"], "updated", updated)
        self.assertIn("来源正文乙.", target.read_text(encoding="utf-8"))
        self.assertIn("来源正文乙.", self.read_skill(Manager(self.project, user_root=self.user), "user", "fixture-skill"))
        checkout = toolkit / "sources/fixture-skill"
        self.write_skill(checkout / "SKILL.md", "来源中的个人编辑.")
        self.write_skill(repository / "SKILL.md", "来源正文丙.")
        self.git(repository, "add", "SKILL.md")
        self.git(repository, "commit", "-m", "继续更新来源正文")
        refused = manager.toolkit.update(["fixture-skill"])
        self.assertEqual(refused["status"], "failed", refused)
        self.assertIn("个人编辑", (checkout / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn("来源正文乙.", target.read_text(encoding="utf-8"))
        self.assert_hosts_absent()

    # //// 环境变量绑定的 Git 来源在快进后重新构建并同步副本 [@x380kkm 2026-10-19] ////
    def test_外部_Git_来源更新后同步构建产物(self) -> None:
        repository, manifest = self.create_source()
        (repository / ".gitignore").write_text("built.txt\n", encoding="utf-8")
        self.git(repository, "add", ".gitignore")
        self.git(repository, "commit", "-m", "声明生成文件")
        checkout = self.root / "external-checkout"
        self.git(self.root, "clone", str(repository), str(checkout))
        configuration = json.loads(manifest.read_text(encoding="utf-8"))
        component = configuration["components"][0]
        component["identity"]["source"]["env"] = "HARNESS_TOOLKIT_TEST_SOURCE"
        component["install"]["commands"] = [[
            "{python}", "-c", "from pathlib import Path; "
            "Path('built.txt').write_text(Path('SKILL.md').read_text(encoding='utf-8'), encoding='utf-8')",
        ]]
        manifest.write_text(json.dumps(configuration), encoding="utf-8")
        with patch.dict(os.environ, {"HARNESS_TOOLKIT_TEST_SOURCE": str(checkout)}):
            manager = Manager(self.project, [self.root], user_root=self.user)
            installed = manager.toolkit.install(["fixture-skill"], manifest=str(manifest))
            self.assertEqual(installed["status"], "installed", installed)
            self.assertIn("来源正文甲.", (checkout / "built.txt").read_text(encoding="utf-8"))
            self.write_skill(repository / "SKILL.md", "来源正文乙.")
            self.git(repository, "add", "SKILL.md")
            self.git(repository, "commit", "-m", "更新外部来源正文")
            updated = manager.toolkit.update(["fixture-skill"])
            self.assertEqual(updated["status"], "updated", updated)
            self.assertIn("来源正文乙.", (checkout / "built.txt").read_text(encoding="utf-8"))
            target = Path(installed["root"]) / "skills/fixture-skill/built.txt"
            self.assertIn("来源正文乙.", target.read_text(encoding="utf-8"))
            self.assertIn("来源正文乙.", self.read_skill(manager, "user", "fixture-skill"))
        self.assert_hosts_absent()

    # //// 用户和项目目录搬移后复用相对来源并保护安装副本编辑 [@x380kkm 2026-10-19] ////
    def test_目录搬移与目标编辑保护(self) -> None:
        for scope in ("user", "project"):
            with self.subTest(scope=scope):
                repository, manifest = self.create_source(scope)
                manager = Manager(self.project, [self.root], user_root=self.user)
                installed = manager.toolkit.install(["fixture-skill"], scope=scope, manifest=str(manifest))
                self.assertEqual(installed["status"], "installed", installed)
                owner = self.user if scope == "user" else self.project
                moved = self.root / (scope + "-moved")
                shutil.move(str(owner), str(moved))
                if scope == "user":
                    self.user = moved
                else:
                    self.project = moved
                restarted = Manager(self.project, [self.root], user_root=self.user)
                self.assertIn("来源正文甲.", self.read_skill(restarted, scope, "fixture-skill"))
                reinstalled = restarted.toolkit.install(["fixture-skill"], scope=scope)
                self.assertEqual(reinstalled["status"], "installed", reinstalled)
                self.assertIn("来源正文甲.", self.read_skill(restarted, scope, "fixture-skill"))
                target = Path(reinstalled["root"]) / "skills/fixture-skill/SKILL.md"
                self.write_skill(target, "安装副本中的个人编辑.")
                for operation in (restarted.toolkit.install, restarted.toolkit.update):
                    with self.assertRaises(InstallError):
                        operation(["fixture-skill"], scope=scope)
                    self.assertIn("个人编辑", target.read_text(encoding="utf-8"))
                self.assert_hosts_absent()


if __name__ == "__main__":
    unittest.main()
