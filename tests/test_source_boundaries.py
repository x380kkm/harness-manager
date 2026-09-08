# audience: internal
# # source-boundary-tests
"""本地 Git 夹具核对真实存储位置, 固定对象解释和离线读取边界."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from harness_manager.sources import SourceError, SourceReader


# //// 在临时目录中执行无外部副作用的 Git 夹具命令 [@x380kkm 2026-09-06] ////
def git(root: Path, *arguments: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=disabled-hooks",
                             "-c", "user.name=Harness fixture", "-c", "user.email=fixture@example.invalid",
                             *arguments], capture_output=True, text=True, encoding="utf-8", check=True, env=environment)
    return result.stdout.strip()


# //// 创建带真实提交的本地来源 [@x380kkm 2026-09-06] ////
def repository(root: Path) -> str:
    root.mkdir()
    git(root, "init")
    (root / "rules.md").write_text("original-content", encoding="utf-8")
    git(root, "add", "rules.md")
    git(root, "commit", "-m", "保存来源样本")
    return git(root, "rev-parse", "HEAD")

# //// 创建测试目录链接并返回其解除入口 [@x380kkm 2026-09-08] ////
def directory_link(link: Path, target: Path) -> Callable[[], None]:
    if os.name == "nt":
        link_text, target_text = str(link).replace("'", "''"), str(target).replace("'", "''")
        subprocess.run(["pwsh", "-Command", "$ErrorActionPreference = 'Stop'\n"
                        f"New-Item -ItemType Junction -Path '{link_text}' -Target '{target_text}' | Out-Null"],
                       capture_output=True, encoding="utf-8", check=True)
        return link.rmdir
    link.symlink_to(target, target_is_directory=True)
    return link.unlink


# //// 核对声明之外的 Git 存储边界 [@x380kkm 2026-09-06] ////
class SourceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.allowed = self.base / "allowed"
        self.allowed.mkdir()
        self.outside = self.base / "outside"
        self.revision = repository(self.outside)
        self.reader = SourceReader(self.allowed, [])

    # //// 生成指定夹具的固定版本来源 [@x380kkm 2026-09-06] ////
    def source(self, root: Path) -> dict:
        return {"resolver": {"id": "manager.source/git"}, "locator": str(root), "constraint": self.revision}

    # //// gitdir 文件不能引入未批准的外部对象库 [@x380kkm 2026-09-06] ////
    def test_external_gitdir_requires_explicit_read_root(self) -> None:
        (self.allowed / ".git").write_text("gitdir: " + str(self.outside / ".git"), encoding="utf-8")
        with self.assertRaisesRegex(SourceError, "超出进程批准"):
            self.reader.read(self.source(self.allowed), "rules.md")
        authorized = SourceReader(self.allowed, [self.outside])
        self.assertEqual(authorized.read(self.source(self.allowed), "rules.md")["content"], "original-content")

    # //// commondir 保持在同一进程的读取授权中 [@x380kkm 2026-09-06] ////
    def test_common_directory_outside_read_roots_is_rejected(self) -> None:
        directory = self.allowed / ".git"
        directory.mkdir()
        (directory / "commondir").write_text(str(self.outside / ".git"), encoding="utf-8")
        (directory / "HEAD").write_text(self.revision, encoding="utf-8")
        with self.assertRaisesRegex(SourceError, "超出进程批准"):
            self.reader.read(self.source(self.allowed), "rules.md")

    # //// 共享克隆的对象库需要单独授权 [@x380kkm 2026-09-06] ////
    def test_alternates_are_checked_recursively(self) -> None:
        clone = self.allowed / "clone"
        git(self.base, "clone", "--shared", str(self.outside), str(clone))
        with self.assertRaisesRegex(SourceError, "超出进程批准"):
            self.reader.read(self.source(clone), "rules.md")
        authorized = SourceReader(self.allowed, [self.outside])
        self.assertEqual(authorized.read(self.source(clone), "rules.md")["content"], "original-content")

    # //// 引用目录的实际位置接受授权且进入来源观察 [@x380kkm 2026-09-08] ////
    def test_reference_directory_link_requires_explicit_authority(self) -> None:
        root = self.allowed / "repository"
        revision = repository(root)
        references = self.base / "external-refs"
        references.mkdir()
        (references / "selected").write_text(revision + "\n", encoding="utf-8")
        self.addCleanup(directory_link(root / ".git/refs/fixture", references))
        source = {**self.source(root), "constraint": "refs/fixture/selected"}
        with patch("harness_manager.sources.git_read") as read:
            with self.assertRaisesRegex(SourceError, "超出进程批准"):
                self.reader.read(source, "rules.md")
            read.assert_not_called()
        authorized = SourceReader(self.allowed, [references])
        result = authorized.read(source, "rules.md")
        self.assertEqual(result["content"], "original-content")
        self.assertIn(str(references.resolve()), result["accessPaths"])

    # //// 文件与 Git 解析器按声明版本执行读取 [@x380kkm 2026-09-08] ////
    def test_source_resolver_version_is_checked_before_reading(self) -> None:
        root = self.allowed / "repository"
        revision = repository(root)
        for resolver in ("manager.source/path", "manager.source/git"):
            with self.subTest(resolver=resolver):
                source = {"resolver": {"id": resolver, "range": "^2.0.0"}, "locator": str(root), "constraint": revision}
                with self.assertRaisesRegex(SourceError, "版本约束"):
                    self.reader.read(source, "rules.md")
                source["resolver"]["range"] = "^1.0.0"
                self.assertEqual(self.reader.read(source, "rules.md")["content"], "original-content")

    # //// 附加配置在 Git 消费前取得独立授权 [@x380kkm 2026-09-08] ////
    def test_included_configurations_require_explicit_authority(self) -> None:
        for name in ("plain", "conditional"):
            with self.subTest(include=name):
                root = self.allowed / name
                revision = repository(root)
                extra = self.base / (name + ".gitconfig")
                extra.write_text("invalid fixture configuration\n", encoding="utf-8")
                key = "include.path" if name == "plain" else f"includeIf.gitdir/i:{(root / '.git').as_posix()}.path"
                git(root, "config", key, str(extra))
                source = {**self.source(root), "constraint": revision}
                with self.assertRaisesRegex(SourceError, "超出进程批准"):
                    self.reader.read(source, "rules.md")
                extra.write_text("[fixture]\nvalue = included\n", encoding="utf-8")
                authorized = SourceReader(self.allowed, [extra])
                result = authorized.read(source, "rules.md")
                self.assertEqual(result["content"], "original-content")
                self.assertIn(str(extra.resolve()), result["accessPaths"])
                with self.assertRaises(SourceError):
                    self.reader.authorize_snapshot(result["accessPaths"])

    # //// 已授权附加配置中的相对引用继续逐级核对 [@x380kkm 2026-09-08] ////
    def test_nested_configuration_includes_preserve_their_directory(self) -> None:
        root = self.allowed / "repository"
        revision = repository(root)
        extra = self.base / "outer.gitconfig"
        nested = self.base / "inner.gitconfig"
        extra.write_text('[include]\npath = inner.gitconfig\n', encoding="utf-8")
        nested.write_text("[fixture]\nvalue = nested\n", encoding="utf-8")
        git(root, "config", "include.path", str(extra))
        source = {**self.source(root), "constraint": revision}
        with self.assertRaisesRegex(SourceError, "超出进程批准"):
            SourceReader(self.allowed, [extra]).read(source, "rules.md")
        result = SourceReader(self.allowed, [extra, nested]).read(source, "rules.md")
        self.assertIn(str(nested.resolve()), result["accessPaths"])

    # //// 附加配置的用户目录展开沿用 Git 的实际路径 [@x380kkm 2026-09-08] ////
    @unittest.skipUnless(os.name == "nt", "Windows 分别提供 Git HOME 与 USERPROFILE.")
    def test_included_home_path_uses_git_expansion(self) -> None:
        root = self.allowed / "repository"
        revision = repository(root)
        git_home, profile = self.base / "git-home", self.base / "profile"
        git_home.mkdir()
        profile.mkdir()
        (profile / "shared.config").write_text("[fixture]\nvalue = profile\n", encoding="utf-8")
        included = git_home / "shared.config"
        included.write_text("invalid fixture configuration\n", encoding="utf-8")
        git(root, "config", "include.path", "~/shared.config")
        source = {**self.source(root), "constraint": revision}
        with patch.dict(os.environ, {"HOME": str(git_home), "USERPROFILE": str(profile)}):
            with self.assertRaisesRegex(SourceError, "超出进程批准"):
                SourceReader(self.allowed, [profile]).read(source, "rules.md")
            included.write_text("[fixture]\nvalue = git\n", encoding="utf-8")
            result = SourceReader(self.allowed, [git_home]).read(source, "rules.md")
            self.assertEqual(result["content"], "original-content")
            self.assertIn(str(included.resolve()), result["accessPaths"])

    # //// 对象前缀和包目录中的链接遵守同一授权 [@x380kkm 2026-09-08] ////
    def test_object_directory_links_require_explicit_authority(self) -> None:
        target = self.outside / ".git/objects"
        for name, relative in (("loose", "ab"), ("packed", "pack/nested")):
            with self.subTest(location=name):
                root = self.allowed / name
                root.mkdir()
                git(root, "init")
                link = root / ".git/objects" / relative
                self.addCleanup(directory_link(link, target))
                with patch("harness_manager.sources.git_read") as read:
                    with self.assertRaisesRegex(SourceError, "超出进程批准"):
                        self.reader.read(self.source(root), "rules.md")
                    read.assert_not_called()
                authorized = SourceReader(self.allowed, [self.outside])
                location = authorized._git_location(root)
                self.assertIn(target.resolve(), location.access_paths)
                with self.assertRaises(SourceError):
                    self.reader.authorize_snapshot([str(path) for path in location.access_paths])

    # //// 已授权对象目录的循环链接保持有界检查 [@x380kkm 2026-09-08] ////
    def test_authorized_object_directory_cycle_is_bounded(self) -> None:
        git(self.allowed, "init")
        objects = self.allowed / ".git/objects"
        self.addCleanup(directory_link(objects / "ab", objects))
        self.reader.authorize_git_source(self.allowed)

    # //// 对象文件的符号链接在 Git 读取前接受授权核对 [@x380kkm 2026-09-08] ////
    def test_object_file_link_requires_explicit_authority(self) -> None:
        git(self.allowed, "init")
        prefix = self.allowed / ".git/objects/ab"
        prefix.mkdir()
        target = self.outside / "object-data"
        target.write_text("authorized fixture content", encoding="utf-8")
        link = prefix / ("c" * 38)
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"当前宿主无法建立文件符号链接: {error}")
        self.addCleanup(link.unlink)
        with patch("harness_manager.sources.git_read") as read:
            with self.assertRaisesRegex(SourceError, "超出进程批准"):
                self.reader.read(self.source(self.allowed), "rules.md")
            read.assert_not_called()

    # //// 固定提交保留原对象而非本机 replace 解释 [@x380kkm 2026-09-06] ////
    def test_replace_refs_do_not_change_fixed_content(self) -> None:
        root = self.allowed / "repository"
        revision = repository(root)
        (root / "rules.md").write_text("replacement-content", encoding="utf-8")
        git(root, "add", "rules.md")
        git(root, "commit", "-m", "保存替换样本")
        replacement = git(root, "rev-parse", "HEAD")
        git(root, "replace", revision, replacement)
        source = {**self.source(root), "constraint": revision}
        result = self.reader.read(source, "rules.md")
        self.assertEqual(result["content"], "original-content")
        self.assertEqual(result["commit"], revision)

    # //// 缺失的 promisor 对象保持缺失并返回明确错误 [@x380kkm 2026-09-06] ////
    def test_partial_clone_read_does_not_fetch_missing_blob(self) -> None:
        git(self.outside, "config", "uploadpack.allowFilter", "true")
        clone = self.allowed / "partial"
        git(self.base, "clone", "--filter=blob:none", "--no-checkout", self.outside.as_uri(), str(clone))
        packs = set((clone / ".git" / "objects" / "pack").glob("*.pack"))
        with self.assertRaises(SourceError):
            self.reader.read(self.source(clone), "rules.md")
        self.assertEqual(set((clone / ".git" / "objects" / "pack").glob("*.pack")), packs)


if __name__ == "__main__":
    unittest.main()
