# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# lib.config
"""cleanread 配置 + 项目本地存储库解析（仅依赖标准库）。

存储模型（v2）：内容存储库放在一个可见、项目本地的文件夹中，名为 ``cleanread/``（默认
``./cleanread``，在 init 时选定），随仓库一起迁移、便于分享。另有一个轻量的每用户 hub 注册表位于
``~/.cleanread``（环境变量 ``CLEANREAD_HOME`` 可覆盖，见 hub_dir），只记录已激活 store 的路径，
不存放任何内容，仅供浏览与复用。

存储库布局：
  cleanread/
    cleanread.json        共享配置（alias、languages、exts、ignore）            [提交]
    source.db            索引 + symbols/edges                                  [提交]
    refs/                共享的精选 ref                                        [提交]
    traces/              共享的静态、持久查询模式                             [提交]
    user/                每用户配置（机器路径、覆盖项）                        [gitignore]
    short/               短期、按版本打标签、不归档                           [gitignore]
      refs/  rdc/  traces/   本版本的 ref 副本、rdc（ref 依赖快照）、动态 trace
    .gitignore           忽略 user/ + short/（source.db 仍提交）

解析顺序：
  store  = --store 参数 > CLEANREAD_STORE 环境变量 > 从 cwd 向上找 ``cleanread/`` 目录
  root   = --root 参数 > user/config.json 的 "root" > cleanread.json 的 "root" > store.parent
  config = cleanread.json（共享）叠加 user/config.json（每用户）

已有 source.db 的目录可以提供只读检索的缺省配置. 自动发现止于当前工作树边界.
隐式选择的存储根与显式项目根一致; --store 与 --root 共同给出时采用调用者的路径绑定.

保持导入安全：导入时无任何副作用。
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


#### 按文件路径加载同目录的 config_docs，并把其公共名再导出到本模块 [@380kkm 2026-06-05] ####
def _load_config_docs():
    """config.py 可被作为 ``lib.config``（包成员）或经文件路径作 ``cleanread_config``（cleanscan
    的隔离加载）两种方式载入。两种方式下相对或绝对 import 都不可靠，故按 ``__file__`` 所在目录的文件
    路径加载 config_docs，再把其 ``load_view_hide`` 等名（含 ``_read_json``）绑入本模块命名空间，
    使外部一律以 ``config.X`` 调用、且 ``_read_json`` 为全模块唯一来源。
    """
    path = Path(__file__).resolve().parent / "config_docs.py"
    spec = importlib.util.spec_from_file_location("cleanread_config_docs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = ("_read_json", "_load_section",
             "_VIEW_HIDE_KEYS", "validate_view_hide", "load_view_hide",
             "_MODULES_KEYS", "_MODULE_ZONE_KEYS", "validate_modules", "load_modules",
             "_MACRO_STRIP_KEYS", "validate_macro_strip", "load_macro_strip")
    g = globals()
    for n in names:
        g[n] = getattr(mod, n)


_load_config_docs()


#### 项目本地存储库目录名 [@380kkm 2026-06-05] ####
STORE_DIRNAME = "cleanread"

#### 语言到扩展名的预设映射（内置） [@380kkm 2026-06-05] ####
LANG_EXTS: dict[str, list[str]] = {
    "cpp": [".h", ".hpp", ".hh", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx"],
    "python": [".py", ".pyi"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "csharp": [".cs"],
    "java": [".java"],
    "rust": [".rs"],
    "gdscript": [".gd"],
    "shader": [".hlsl", ".usf", ".ush"],
    "glsl": [".glsl", ".vert", ".frag", ".comp", ".geom", ".tesc", ".tese"],
    "docs": [".md", ".json", ".ini"],
}

#### 默认忽略的 glob 通配 [@380kkm 2026-06-05] ####
DEFAULT_IGNORE_GLOBS: list[str] = [
    "ThirdParty/*",
    "Intermediate/*",
    "*/node_modules/*",
]

#### 写入存储库的 .gitignore 内容：把每用户 + 短期内容排除出 git [@380kkm 2026-06-05] ####
STORE_GITIGNORE = "# cleanread store — per-user + short-term content is NOT shared\nuser/\nshort/\n"


#### 一个项目的完整解析后配置 [@380kkm 2026-06-05] ####
@dataclass
class ProjectConfig:
    alias: str
    # cleanread/ 目录
    store: Path
    # 被索引的源码树
    root: Path
    # <store>/source.db
    db_path: Path
    # <store>/refs（共享的精选 ref）
    refs_dir: Path
    # <store>/traces（静态、持久 trace）
    traces_dir: Path
    # <store>/user（每用户，gitignore）
    user_dir: Path
    # <store>/short（临时，gitignore）
    short_dir: Path
    # <store>/short/refs（按版本打标签的 ref 副本）
    short_refs_dir: Path
    # <store>/short/rdc（按版本打标签的 rdc 副本）
    short_rdc_dir: Path
    # <store>/short/traces（动态、带日期的 trace）
    short_traces_dir: Path
    languages: list[str]
    exts: list[str]
    ignore_globs: list[str] = field(default_factory=list)
#### /项目解析后配置 ####


# //// 识别具有配置或已建索引的存储目录 [@x380kkm 2026-09-14] ////
def is_store(path: Path) -> bool:
    return path.is_dir() and any((path / name).is_file() for name in ("cleanread.json", "source.db"))


# //// 在当前工作树内定位最近的存储目录 [@x380kkm 2026-09-14] ////
def find_store(start: Path | None = None) -> Path | None:
    """CLEANREAD_STORE 环境变量覆盖此查找，指向一个显式的存储库路径。"""
    env = os.environ.get("CLEANREAD_STORE")
    if env:
        p = Path(env)
        return p if is_store(p) else None
    cur = Path(start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cand = d / STORE_DIRNAME
        if is_store(cand):
            return cand
        # 也接受当前就处在存储库目录内部的情形
        if d.name == STORE_DIRNAME and is_store(d):
            return d
        if (d / ".git").exists():
            break
    return None


#### 由存储库根目录推导各子路径 [@380kkm 2026-06-05] ####
def _store_paths(store: Path) -> dict[str, Path]:
    return {
        "db_path": store / "source.db",
        "refs_dir": store / "refs",
        "traces_dir": store / "traces",
        "user_dir": store / "user",
        "short_dir": store / "short",
        "short_refs_dir": store / "short" / "refs",
        "short_rdc_dir": store / "short" / "rdc",
        "short_traces_dir": store / "short" / "traces",
    }


#### 由语言列表收集去重后的默认扩展名 [@380kkm 2026-06-05] ####
def default_exts_for(languages: list[str]) -> list[str]:
    exts: list[str] = []
    for lang in languages:
        for ext in LANG_EXTS.get(lang.lower(), []):
            if ext not in exts:
                exts.append(ext)
    return exts


#### 读取已提交的共享配置 <store>/cleanread.json [@380kkm 2026-06-05] ####
def load_shared(store: Path) -> dict:
    return _read_json(store / "cleanread.json")  # noqa: F821


#### 读取每用户的 gitignore 配置 <store>/user/config.json [@380kkm 2026-06-05] ####
def load_user(store: Path) -> dict:
    return _read_json(store / "user" / "config.json")  # noqa: F821


#### 在 location 下创建一个全新的 cleanread/ 存储库 [@380kkm 2026-06-05] ####
def init_store(location: Path, alias: str | None = None,
               languages: list[str] | None = None, exts: list[str] | None = None,
               ignore_globs: list[str] | None = None, root: Path | None = None) -> Path:
    """写入 cleanread.json（共享）+ 子目录骨架 + .gitignore，返回存储库路径。

    幂等：已存在的文件不会被覆盖。
    """
    store = Path(location).resolve() / STORE_DIRNAME
    paths = _store_paths(store)
    for d in (store, paths["refs_dir"], paths["traces_dir"], paths["user_dir"],
              paths["short_dir"], paths["short_refs_dir"], paths["short_rdc_dir"],
              paths["short_traces_dir"]):
        d.mkdir(parents=True, exist_ok=True)

    cfg_file = store / "cleanread.json"
    if not cfg_file.exists():
        langs = languages or []
        payload = {
            "alias": alias or Path(location).resolve().name,
            "languages": langs,
            "exts": exts if exts is not None else default_exts_for(langs),
            "ignore_globs": ignore_globs if ignore_globs is not None else list(DEFAULT_IGNORE_GLOBS),
        }
        # 仅当显式作为共享默认提供时才记录源码根
        if root is not None:
            payload["root"] = str(root)
        cfg_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    gi = store / ".gitignore"
    if not gi.exists():
        gi.write_text(STORE_GITIGNORE, encoding="utf-8")

    return store


#### 写入每用户配置（机器路径、覆盖项），被 gitignore [@380kkm 2026-06-05] ####
def save_user(store: Path, data: dict) -> None:
    user_cfg = store / "user" / "config.json"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


#### 由存储库 + 源码根解析出一个 ProjectConfig [@380kkm 2026-06-05] ####
def resolve_project(root: str | None = None, store: str | None = None) -> ProjectConfig:
    """store：显式 --store > CLEANREAD_STORE 环境变量 > 从 ``root``（若给定）或 cwd 向上查找。

    找不到时抛出 SystemError 并给出指引。
    root：显式 --root > user 配置 "root" > shared 配置 "root" > store.parent。
    """
    store_path: Path | None
    if store:
        store_path = Path(store).resolve()
        if not is_store(store_path) and store_path.name != STORE_DIRNAME:
            # 允许把 --store 指向一个包含 cleanread/ 的项目目录
            cand = store_path / STORE_DIRNAME
            if is_store(cand):
                store_path = cand
    else:
        start = Path(root).resolve() if root else None
        store_path = find_store(start)

    if store_path is None:
        raise SystemError(
            "no cleanread store found. Run mr-init to create one "
            "(default ./cleanread), or pass --store PATH / set CLEANREAD_STORE."
        )
    store_path = store_path.resolve()
    if not is_store(store_path):
        raise SystemError(f"no cleanread configuration or index at {store_path}")

    shared = load_shared(store_path)
    user = load_user(store_path)
    paths = _store_paths(store_path)

    configured_root = user.get("root") or shared.get("root")
    stored_root = Path(configured_root).resolve() if configured_root else store_path.parent
    if root and not store and Path(root).resolve() != stored_root:
        raise SystemError(
            f"cleanread store {store_path} belongs to {stored_root}, requested root is {Path(root).resolve()}; "
            "select the matching --store or use the current project's CodeGraph index."
        )

    # 源码根：--root > user > shared > store.parent
    root_val = root or user.get("root") or shared.get("root")
    root_path = Path(root_val).resolve() if root_val else store_path.parent

    alias = user.get("alias") or shared.get("alias") or store_path.parent.name
    languages = user.get("languages") or shared.get("languages") or []
    exts = user.get("exts") or shared.get("exts") or default_exts_for(languages)
    ignore_globs = user.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = shared.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = list(DEFAULT_IGNORE_GLOBS)

    return ProjectConfig(
        alias=alias,
        store=store_path,
        root=root_path,
        db_path=paths["db_path"],
        refs_dir=paths["refs_dir"],
        traces_dir=paths["traces_dir"],
        user_dir=paths["user_dir"],
        short_dir=paths["short_dir"],
        short_refs_dir=paths["short_refs_dir"],
        short_rdc_dir=paths["short_rdc_dir"],
        short_traces_dir=paths["short_traces_dir"],
        languages=list(languages),
        exts=list(exts),
        ignore_globs=list(ignore_globs),
    )


#### 每用户 hub（env 目录）：已激活存储库路径的注册表 [@380kkm 2026-06-05] ####
def hub_dir() -> Path:
    """每用户 hub 目录：CLEANREAD_HOME 环境变量，否则 ~/.cleanread。"""
    env = os.environ.get("CLEANREAD_HOME")
    return Path(env) if env else (Path.home() / ".cleanread")


#### hub 注册表文件路径 [@380kkm 2026-06-05] ####
def _hub_registry_path() -> Path:
    return hub_dir() / "stores.json"


#### 返回 hub 注册表 [@380kkm 2026-06-05] ####
def list_stores() -> dict:
    """形状：{ "<store 绝对路径>": {alias, root, updated} }。"""
    p = _hub_registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


#### 在每用户 hub 注册表中记录（或刷新）一个存储库 [@380kkm 2026-06-05] ####
def register_store(store: Path, alias: str | None = None, root: Path | None = None) -> None:
    reg = list_stores()
    reg[str(Path(store).resolve())] = {
        "alias": alias or "",
        "root": str(Path(root).resolve()) if root else "",
        "updated": int(time.time()),
    }
    p = _hub_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


#### 从 hub 注册表中移除一个存储库（不动磁盘上的存储库） [@380kkm 2026-06-05] ####
def unregister_store(store: Path) -> bool:
    reg = list_stores()
    key = str(Path(store).resolve())
    if key in reg:
        del reg[key]
        _hub_registry_path().write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
        return True
    return False


#### 判断 path 是否为不宜就地放置存储库、索引的系统位置 [@380kkm 2026-06-05] ####
def is_system_location(path: Path) -> bool:
    """覆盖盘符根（C:\\、W:\\、/）、用户家目录，以及家目录正下方的常见外壳文件夹
    （Desktop/Documents/Downloads/OneDrive）。
    """
    p = Path(path).resolve()
    # 文件系统根、盘符根
    if p.parent == p:
        return True
    home = Path.home().resolve()
    if p == home:
        return True
    return p.parent == home and p.name.lower() in {
        "desktop", "documents", "downloads", "onedrive",
    }
