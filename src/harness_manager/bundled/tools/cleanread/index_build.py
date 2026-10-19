# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# index_build
"""cleanread L1 —— 第一阶段 FTS5 trigram 索引器（仅用标准库）。

以全量 DROP+CREATE 重建 <root>/.cleanread/source.db：
  * 枚举文件（root 为 git 仓库时用 git ls-files，否则用 os.walk，并按 config 的
    ignore_globs + 内置 SKIP_DIRS 过滤），
  * 加载每个文本文件并写入 `files` + `files_fts`，
  * 创建空的 `symbols`/`edges`/`meta` 表（经 db.init_schema），供 L2 后续填充，
  * 写入 meta(build_id=<unix ts>, built_at, langs, exts)。

配置驱动：扩展名与忽略 glob 来自逐项目配置（<root>/.cleanread/config.json），
经 lib.config 解析。当配置未提供扩展名（如裸 --root 无配置）时，回退到一组
合理的内置默认值，使索引仍然可用。

CLI:  index_build.py <alias|--root PATH> [--rebuild]
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db

#### os.walk 回退时绝不进入的目录 [@380kkm 2026-06-05] ####
SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".cleanread",
    "cleanread",
}

#### 项目配置未提供扩展名（裸 --root）时的回退扩展名（在调用时按已注册预设动态计算） [@380kkm 2026-06-05] ####
# 取所有当前已注册语言预设的扩展名并集，外加常见文档扩展名。在 build() 内、扩展发现之后才计算，
# 确保扩展禁用时不泄漏 DSL 扩展名、启用时能纳入扩展贡献的扩展名。
def _default_exts() -> list[str]:
    return sorted({ext for exts in config.LANG_EXTS.values() for ext in exts} | {".md"})

#### 单文件内容字节上限 [@380kkm 2026-06-05] ####
# 4 MiB
MAX_FILE_BYTES = 4 * 1024 * 1024


#### 判断 root 是否位于 git 工作树内（且 git 可用）[@380kkm 2026-06-05] ####
def is_git_repo(root: Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        return out.returncode == 0 and out.stdout.strip() == "true"
    except OSError:
        return False


#### 经 git ls-files 枚举「已跟踪 + 未跟踪但未被忽略」的文件 [@380kkm 2026-06-05] ####
def enumerate_git(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for line in out.stdout.splitlines():
        rel = line.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        paths.append(root / rel)
    return paths


#### 判断 root 相对的 posix 路径是否命中任一忽略 glob [@380kkm 2026-06-05] ####
def _matches_ignore(rel_posix: str, ignore_globs: list[str]) -> bool:
    for pat in ignore_globs:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, pat.rstrip("/*") + "/*"):
            return True
        # 当 pattern 指向任意位置的某个路径段时也算命中。
        if "/" not in pat and any(fnmatch.fnmatch(seg, pat) for seg in rel_posix.split("/")):
            return True
    return False


#### 经 os.walk 枚举文件，剪除 SKIP_DIRS 与 ignore_globs [@380kkm 2026-06-05] ####
def enumerate_walk(root: Path, ignore_globs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地剪除 skip 目录，使 os.walk 不再深入其中。
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if _matches_ignore(rel, ignore_globs):
                continue
            paths.append(full)
    return paths


#### 以 UTF-8 读取文件（替换错误字节），跳过超大或不可读文件 [@380kkm 2026-06-05] ####
def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


#### 枚举并按配置扩展名过滤出待索引文件，返回 (selected, method, exts) [@380kkm 2026-06-16] ####
def _select_files(cfg: config.ProjectConfig) -> tuple[list[Path], str, list[str]]:
    root = Path(cfg.root).resolve()
    exts = [e.lower() for e in cfg.exts] if cfg.exts else _default_exts()
    ext_set = set(exts)
    if is_git_repo(root):
        method = "git ls-files"
        candidates = enumerate_git(root)
    else:
        method = "os.walk"
        candidates = enumerate_walk(root, cfg.ignore_globs)
    selected = [p for p in candidates if p.suffix.lower() in ext_set]
    return selected, method, exts
#### /枚举并过滤待索引文件 ####


#### 执行完整 DROP+CREATE 重建，返回用于汇报的统计字典 [@380kkm 2026-06-05] ####
def build(cfg: config.ProjectConfig) -> dict:
    root = Path(cfg.root).resolve()
    db_path = Path(cfg.db_path)
    selected, method, exts = _select_files(cfg)
    enumerated = len(selected)

    #### 全量 DROP+CREATE 重建：从干净的 db 文件开始 [@380kkm 2026-06-05] ####
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    #### /全量 DROP+CREATE 重建 ####

    conn = db.connect(db_path)
    try:
        # 建立 files/files_fts 与空的 symbols/edges/meta 表
        db.init_schema(conn)

        #### 写入 files + files_fts [@380kkm 2026-06-05] ####
        indexed = 0
        for full in selected:
            content = read_text(full)
            if content is None:
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            ext = full.suffix.lower()
            cur = conn.execute(
                "INSERT OR IGNORE INTO files(path, ext, size, mtime, content) "
                "VALUES(?, ?, ?, ?, ?)",
                (rel, ext, st.st_size, int(st.st_mtime), content),
            )
            if cur.rowcount:
                conn.execute(
                    "INSERT INTO files_fts(rowid, path, content) VALUES(?, ?, ?)",
                    (cur.lastrowid, rel, content),
                )
                indexed += 1
        conn.commit()
        #### /写入 files + files_fts ####

        #### 写入 meta [@380kkm 2026-06-05] ####
        build_id = int(time.time())
        db.set_meta(conn, "build_id", build_id)
        db.set_meta(conn, "built_at", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(build_id)))
        db.set_meta(conn, "langs", ",".join(cfg.languages))
        db.set_meta(conn, "exts", ",".join(exts))
        conn.commit()
        #### /写入 meta ####
    finally:
        conn.close()

    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {
        "method": method,
        "exts": exts,
        "enumerated": enumerated,
        "indexed": indexed,
        "db_path": db_path,
        "db_size": db_size,
        "build_id": build_id,
    }


#### 经 git 取相对 root 的改动文件：工作区相对 HEAD 的改动与未跟踪新增的合集 [@380kkm 2026-06-16] ####
def _git_changed_paths(root: Path) -> set[str] | None:
    """返回相对 root 的正斜杠路径集合；非 git 或 git 失败返回 None。git 按内容比对，不依赖
    mtime/size，故不会漏掉同字节数的编辑。用 -z 关闭 quotepath 与转义，按 NUL 切分，正确处理
    非 ASCII 与含空格的文件名。git 输出路径相对仓库根，统一转换为相对 cfg.root 的路径，使 root 为仓库子目录
    时也对齐（否则已跟踪改动的路径空间不一致会导致匹配失败、保留旧内容、污染索引）。"""
    def run(args):
        try:
            r = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                               text=True, encoding="utf-8", check=False)
        except OSError:
            return None
        return r.stdout if r.returncode == 0 else None
    diff = run(["diff", "--name-only", "-z", "HEAD"])
    if diff is None:
        return None
    toplevel = run(["rev-parse", "--show-toplevel"])
    if toplevel is None:
        return None
    repo_root = Path(toplevel.strip())
    untracked = run(["ls-files", "--others", "--exclude-standard", "-z"]) or ""
    # -z 用 NUL 分隔，无尾随转义；空段丢弃
    raw = [p for p in diff.split("\0") if p] + [p for p in untracked.split("\0") if p]
    root_res = root.resolve()
    out: set[str] = set()
    for rel_to_repo in raw:
        # 先取仓库根相对路径，再转绝对路径，最后转 cfg.root 相对路径；落在 root 之外的丢弃
        try:
            abs_p = (repo_root / rel_to_repo).resolve()
            out.add(abs_p.relative_to(root_res).as_posix())
        except ValueError:
            continue
    return out
#### /经 git 取改动文件 ####


#### git 驱动的增量重建：只更新改动文件的 L1 行并清空其 L2 符号与边，返回改动路径 [@380kkm 2026-06-16] ####
def build_incremental(cfg: config.ProjectConfig) -> dict:
    """不会污染索引的增量：变更检测用 git（按内容比对，不漏掉同字节数的编辑）；非 git 或无既有
    库则回退全量 build（不依赖 mtime 判断改动）。在单个事务里：改动文件 UPSERT 进 files 与
    files_fts 并删除其 symbols 与 edges（等待 enrich --files 重新填充），消失文件清除四张表的对应行。跨文件
    边的全局一致仍交给周期性全量重建，本增量只保证改动文件自身的 L1 准确，适用于审计这类只读自身内容的场景。"""
    root = Path(cfg.root).resolve()
    db_path = Path(cfg.db_path)
    if not db_path.exists():
        return {**build(cfg), "mode": "full (no existing db)"}
    changed_rel = _git_changed_paths(root)
    if changed_rel is None:
        return {**build(cfg), "mode": "full (non-git)"}

    selected, method, exts = _select_files(cfg)
    by_rel = {p.relative_to(root).as_posix(): p for p in selected}
    current_rel = set(by_rel)

    conn = db.connect(db_path)
    updated = 0
    removed = 0
    changed_indexed: list[str] = []
    try:
        db.init_schema(conn)
        indexed_rows = {r[1]: r[0] for r in conn.execute("SELECT id, path FROM files").fetchall()}

        #### 删除已消失文件的四张表行 [@380kkm 2026-06-16] ####
        for rel, fid in list(indexed_rows.items()):
            if rel not in current_rel:
                conn.execute("DELETE FROM files_fts WHERE rowid=?", (fid,))
                conn.execute("DELETE FROM symbols WHERE file_id=?", (fid,))
                conn.execute("DELETE FROM edges WHERE file_id=?", (fid,))
                conn.execute("DELETE FROM files WHERE id=?", (fid,))
                removed += 1
        #### /删除消失文件 ####

        #### UPSERT 改动文件，清空其 L2 符号与边以待 enrich 重新填充 [@380kkm 2026-06-16] ####
        for rel in sorted(changed_rel):
            # by_rel 已按配置扩展名过滤；改动文件不在其中（被 ignore 或非配置扩展名）即跳过
            full = by_rel.get(rel)
            if full is None:
                continue
            content = read_text(full)
            if content is None:
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            ext = full.suffix.lower()
            fid = indexed_rows.get(rel)
            if fid is None:
                cur = conn.execute(
                    "INSERT INTO files(path, ext, size, mtime, content) VALUES(?,?,?,?,?)",
                    (rel, ext, st.st_size, int(st.st_mtime), content))
                fid = cur.lastrowid
            else:
                conn.execute(
                    "UPDATE files SET ext=?, size=?, mtime=?, content=? WHERE id=?",
                    (ext, st.st_size, int(st.st_mtime), content, fid))
                conn.execute("DELETE FROM files_fts WHERE rowid=?", (fid,))
                conn.execute("DELETE FROM symbols WHERE file_id=?", (fid,))
                conn.execute("DELETE FROM edges WHERE file_id=?", (fid,))
            conn.execute("INSERT INTO files_fts(rowid, path, content) VALUES(?,?,?)",
                         (fid, rel, content))
            changed_indexed.append(rel)
            updated += 1
        #### /UPSERT 改动文件 ####

        db.set_meta(conn, "incremental_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        conn.commit()  # 单事务原子提交：要么整体生效，要么异常时回滚保留旧态
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"method": f"{method} (incremental, git)", "exts": exts,
            "updated": updated, "removed": removed, "changed_paths": changed_indexed,
            "db_path": db_path, "mode": "incremental"}
#### /git 驱动的增量重建 ####


#### 复制既有存储库的索引、refs 与 traces 到目标目录 [@380kkm 2026-06-05] ####
def _copy_store_data(src_store: Path, dst_store: Path) -> list[str]:
    copied: list[str] = []
    src_db = src_store / "source.db"
    if src_db.exists():
        shutil.copy2(src_db, dst_store / "source.db")
        copied.append("source.db")
    for sub in ("refs", "traces"):
        s = src_store / sub
        if s.exists():
            shutil.copytree(s, dst_store / sub, dirs_exist_ok=True)
            copied.append(sub + "/")
    return copied


#### 把 --copy-from 规格解析为存储库目录（存储库本身，或含存储库的仓库）[@380kkm 2026-06-05] ####
def _resolve_src_store(spec: str) -> Path | None:
    found = config.find_store(Path(spec))
    if found is not None:
        return Path(found).resolve()
    cand = Path(spec)
    if (cand / "cleanread.json").exists():
        return cand.resolve()
    return None


#### CLI 入口：解析参数、按子命令执行 hub 管理、重用、重建 [@380kkm 2026-06-05] ####
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="index_build.py",
        description="cleanread L1 FTS5 trigram indexer (full DROP+CREATE rebuild).",
    )
    parser.add_argument("--root", help="source tree root (default: the store's parent / cwd)",
                        default=None)
    parser.add_argument("--store", help="explicit cleanread store dir (default: discover from cwd)",
                        default=None)
    parser.add_argument("--init", action="store_true",
                        help="create a cleanread store (default ./cleanread) before building")
    parser.add_argument("--store-at", dest="store_at", default=None,
                        help="with --init: dir to create the store in (default: --root or cwd)")
    parser.add_argument("--langs", default=None,
                        help="with --init: comma-separated languages for the new store config")
    parser.add_argument("--exts", default=None,
                        help="with --init: comma-separated extensions (default: from --langs)")
    parser.add_argument("--copy-from", dest="copy_from", default=None,
                        help="reuse-by-copy: copy an existing store's index+refs+traces into "
                             "this one (e.g. a shared engine), instead of re-indexing")
    parser.add_argument("--list-stores", action="store_true", dest="list_stores",
                        help="print the per-user hub registry of activated stores and exit")
    parser.add_argument("--forget", default=None,
                        help="remove a store from the hub registry (does NOT delete it) and exit")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="force a full rebuild (default behaviour is always a full rebuild)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="git-driven incremental update of changed files only (falls back to full "
             "rebuild on non-git repos or when no db exists); prints changed paths",
    )
    args = parser.parse_args(argv)

    #### hub 浏览、管理：无需解析存储库 [@380kkm 2026-06-05] ####
    if args.list_stores:
        reg = config.list_stores()
        if not reg:
            print("(no stores registered in the hub)")
        for path, info in sorted(reg.items()):
            print(f"{path}  alias={info.get('alias', '')}  root={info.get('root', '')}")
        return 0
    if args.forget:
        ok = config.unregister_store(Path(args.forget))
        print(f"{'forgot' if ok else 'not in registry'}: {Path(args.forget).resolve()}")
        return 0
    #### /hub 浏览、管理 ####

    #### --init：在重建前创建存储库 [@380kkm 2026-06-05] ####
    store_arg = args.store
    if args.init:
        location = Path(args.store_at) if args.store_at else (Path(args.root) if args.root else Path.cwd())
        if config.is_system_location(location):
            print(f"warning: {Path(location).resolve()} looks like a system folder (drive root / "
                  "home / Desktop). Prefer a dedicated project subfolder for the store.",
                  file=sys.stderr)
        langs = [s.strip().lower() for s in args.langs.split(",")] if args.langs else []
        exts = None
        if args.exts:
            exts = [(e if e.startswith(".") else "." + e) for e in
                    (s.strip() for s in args.exts.split(",")) if e]
        store = config.init_store(location, languages=langs, exts=exts)
        print(f"initialized store: {store}")
        store_arg = str(store)
    #### /--init ####

    try:
        cfg = config.resolve_project(root=args.root, store=store_arg)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    #### --copy-from：拉入既有存储库的 index/refs/traces 后即停 [@380kkm 2026-06-05] ####
    if args.copy_from:
        src = _resolve_src_store(args.copy_from)
        if src is None:
            print(f"error: no cleanread store found at/under {args.copy_from}", file=sys.stderr)
            return 2
        if src == Path(cfg.store).resolve():
            print("error: --copy-from points at this same store", file=sys.stderr)
            return 2
        copied = _copy_store_data(src, Path(cfg.store))
        config.register_store(cfg.store, cfg.alias, cfg.root)
        print(f"reused (copied) from {src}: {', '.join(copied) if copied else '(nothing to copy)'}")
        print(f"store     : {cfg.store}  (not rebuilt — pass --rebuild to re-index from source)")
        return 0
    #### /--copy-from ####

    #### --incremental：git 驱动增量，打印改动路径以供 enrich --files 读取 [@380kkm 2026-06-16] ####
    if args.incremental:
        stats = build_incremental(cfg)
        config.register_store(cfg.store, cfg.alias, cfg.root)
        print(f"project   : {cfg.alias}")
        print(f"mode      : {stats['mode']}")
        if stats["mode"] == "incremental":
            print(f"updated   : {stats['updated']}  removed: {stats['removed']}")
            # 机器可读：改动路径每行一个，前缀 changed\t 供钩子以 grep 读取
            for rel in stats["changed_paths"]:
                print(f"changed\t{rel}")
        else:
            print(f"indexed   : {stats.get('indexed', 0)}")
        return 0
    #### /--incremental ####

    stats = build(cfg)
    config.register_store(cfg.store, cfg.alias, cfg.root)

    print(f"project   : {cfg.alias}")
    print(f"root      : {Path(cfg.root).resolve()}")
    print(f"method    : {stats['method']}")
    print(f"exts      : {' '.join(stats['exts'])}")
    print(f"enumerated: {stats['enumerated']}")
    print(f"indexed   : {stats['indexed']}")
    print(f"db        : {stats['db_path']}")
    print(f"db size   : {stats['db_size']} bytes")
    print(f"build_id  : {stats['build_id']}")
    print(f"hub       : registered in {config.hub_dir()}")
    return 0
#### /CLI 入口 ####


if __name__ == "__main__":
    raise SystemExit(main())
