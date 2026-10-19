# audience: internal
# # cleanread-query
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""SQL 结果使用 UTF8 JSON, 超过输出预算时保存在所属 store 的 short/results.
字符范围从 0 开始, end 表示下一页起点. 首次查询返回实际行数, 续读保持已保存的结果.
查询轨迹记录语句及其文件观察, static 与 dynamic 沿用 trace 的含义.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import config, db  # noqa: E402
# 同级脚本：复用其 log 语义
import trace  # noqa: E402


DEFAULT_BUDGET = 12000
RESULTS_DIRNAME = "results"


#### 对 project db 执行 SQL，返回列名与结果行 [@380kkm 2026-06-05] ####
def execute_sql(db_path: Path, sql: str) -> tuple[list[str], list[tuple]]:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        conn.commit()
        return cols, rows
    finally:
        conn.close()


#### 匹配 SQL 里像文件路径的字符串字面量 [@380kkm 2026-06-05] ####
_LITERAL_RE = re.compile(r"""['"]([^'"]+)['"]""")


#### 找出 SQL 中引用、且存在于 files 表里的文件路径 [@380kkm 2026-06-05] ####
def referenced_paths(db_path: Path, sql: str) -> list[str]:
    """返回与 ``files`` 表存储形式一致的相对 root 路径，匹配精确相等、LIKE 模式或子串模式。"""
    literals = _LITERAL_RE.findall(sql)
    if not literals:
        return []
    if not db_path.exists():
        return []

    conn = db.connect(db_path)
    try:
        try:
            indexed = [r[0] for r in conn.execute("SELECT path FROM files").fetchall()]
        except Exception:
            return []
    finally:
        conn.close()

    indexed_set = set(indexed)
    found: list[str] = []
    seen: set[str] = set()

    #### 保序登记一个路径，已收过则跳过，返回是否首次收入 [@380kkm 2026-06-05] ####
    def add_unique(p: str) -> bool:
        if p in seen:
            return False
        found.append(p)
        seen.add(p)
        return True

    for lit in literals:
        # 剥掉 SQL LIKE 通配符、glob 字符
        core = lit.strip().strip("%").strip("*")
        if not core:
            continue
        # 1. 与已索引路径精确匹配
        if lit in indexed_set and add_unique(lit):
            continue
        if core in indexed_set and add_unique(core):
            continue
        # 2. 子串、后缀匹配
        for p in indexed:
            if core == p or core in p or p.endswith(core):
                add_unique(p)
    return found


#### 按 trace.py 的 log 语义追加一行 trace，返回行 id [@380kkm 2026-06-05] ####
def log_trace(cfg: config.ProjectConfig, sql: str, static: bool,
              task: str | None, rel_paths: list[str]) -> int:
    """对 ``rel_paths`` 解析为绝对路径读取 mtime/size 以捕获 file_state,但记录时仍存
    相对 project 的路径(与 ``files`` 表一致)。
    """
    kind = "static" if static else "dynamic"
    db_path_str = str(Path(cfg.db_path))

    conn = trace.connect(cfg)
    try:
        valid_date = None
        file_state_json = None
        if kind == "dynamic":
            valid_date = trace.today_str()
            fstate = []
            root = Path(cfg.root)
            for rel in rel_paths:
                abs_p = (root / rel)
                if abs_p.exists():
                    st = abs_p.stat()
                    fstate.append({"path": rel, "mtime": int(st.st_mtime),
                                   "size": int(st.st_size)})
                else:
                    fstate.append({"path": rel, "mtime": None, "size": None})
            file_state_json = json.dumps(fstate)
        cur = conn.execute(
            "INSERT INTO query_log(ts, project, db_path, sql_text, kind, task_tag, "
            "valid_date, file_state) VALUES(?,?,?,?,?,?,?,?)",
            (int(time.time()), cfg.alias, db_path_str, sql, kind,
             task, valid_date, file_state_json),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# //// 将 SQL 结果保留为可按字符续读的 JSON 文本. [@x380kkm 2026-09-14] ////
def serialize_result(columns: list[str], rows: list[tuple]) -> str:
    return json.dumps(
        {"columns": columns, "rows": rows},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
# //// /将 SQL 结果保留为可按字符续读的 JSON 文本. ////


# //// 解析字符预算为正整数. [@x380kkm 2026-09-14] ////
def parse_positive_budget(value: str) -> int:
    try:
        budget = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if budget <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return budget
# //// /解析字符预算为正整数. ////


# //// 解析续读偏移为非负整数. [@x380kkm 2026-09-14] ////
def parse_nonnegative_offset(value: str) -> int:
    try:
        offset = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if offset < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return offset
# //// /解析续读偏移为非负整数. ////


# //// 在存储库短期目录写入一次完整结果. [@x380kkm 2026-09-14] ////
def save_result(cfg: config.ProjectConfig, result_text: str) -> Path:
    results_dir = cfg.short_dir / RESULTS_DIRNAME
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"query-{uuid.uuid4().hex}.json"
    result_path.write_text(result_text, encoding="utf-8")
    return result_path
# //// /在存储库短期目录写入一次完整结果. ////


# //// 从结果路径推导所属存储库并读取文本. [@x380kkm 2026-09-14] ////
def load_saved_result(result: str, root: str | None,
                      store: str | None) -> tuple[config.ProjectConfig, Path, str]:
    result_path = Path(result).resolve()
    if root is not None or store is not None:
        cfg = config.resolve_project(root=root, store=store)
    else:
        try:
            cfg = config.resolve_project(store=str(result_path.parents[2]))
        except IndexError as exc:
            raise SystemError("result must be stored below <store>/short/results") from exc

    results_dir = (cfg.short_dir / RESULTS_DIRNAME).resolve()
    if result_path.parent != results_dir:
        raise SystemError(f"result must be stored below {results_dir}")
    try:
        return cfg, result_path, result_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemError(f"cannot read result {result_path}: {exc}") from exc
# //// /从结果路径推导所属存储库并读取文本. ////


# //// 构造带续读参数的结果页. [@x380kkm 2026-09-14] ////
def build_result_page(result_text: str, cfg: config.ProjectConfig, row_count: int | None,
                      result_path: Path | None, offset: int, budget: int) -> dict:
    start = min(offset, len(result_text))
    end = min(start + budget, len(result_text))
    truncated = end < len(result_text)
    continuation = None
    if truncated:
        if result_path is None:
            raise SystemError("truncated result has no saved result path")
        continuation = {
            "result": str(result_path),
            "offset": end,
            "budget": budget,
            "root": str(cfg.root),
            "store": str(cfg.store),
        }
    return {
        "output": result_text[start:end],
        "format": "json",
        "root": str(cfg.root),
        "store": str(cfg.store),
        "rowCount": row_count,
        "returnedRange": {"start": start, "end": end},
        "truncated": truncated,
        "continuation": continuation,
    }
# //// /构造带续读参数的结果页. ////


# //// 输出 JSON 信封. [@x380kkm 2026-09-14] ////
def print_result_page(page: dict) -> None:
    print(json.dumps(page, ensure_ascii=False, separators=(",", ":")))
# //// /输出 JSON 信封. ////


# //// CLI 入口执行 SQL 或读取已保存结果. [@x380kkm 2026-09-14] ////
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="query.py",
        description="cleanread: execute SQL against a project db + auto-log to trace.",
    )
    parser.add_argument("sql", nargs="?", help="the SQL statement to execute")
    parser.add_argument("--root", help="source tree root (default: store's parent)", default=None)
    parser.add_argument("--store", help="explicit cleanread store dir (default: discover from cwd)",
                        default=None)
    parser.add_argument("--budget", type=parse_positive_budget, default=DEFAULT_BUDGET,
                        help="maximum output characters")
    parser.add_argument("--result", help="saved result path to continue reading", default=None)
    parser.add_argument("--offset", type=parse_nonnegative_offset, default=0,
                        help="character offset in a saved result")
    parser.add_argument("--static", action="store_true",
                        help="log this query as a durable static pattern (default: dynamic)")
    parser.add_argument("--task", help="task tag recorded with the trace row", default=None)
    parser.add_argument("--no-log", action="store_true", dest="no_log",
                        help="do not append a row to the trace store")
    args = parser.parse_args(argv)

    if args.sql is None and args.result is None:
        parser.error("SQL or --result is required")
    if args.sql is not None and args.result is not None:
        parser.error("SQL and --result are mutually exclusive")
    if args.sql is not None and args.offset != 0:
        parser.error("--offset requires --result")

    if args.result is not None:
        try:
            cfg, result_path, result_text = load_saved_result(args.result, args.root, args.store)
        except SystemError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print_result_page(build_result_page(
            result_text, cfg, None, result_path, args.offset, args.budget,
        ))
        return 0

    try:
        cfg = config.resolve_project(root=args.root, store=args.store)
    except SystemError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    db_path = Path(cfg.db_path)
    if not db_path.exists():
        print(f"error: no index at {db_path}; run index_build.py first",
              file=sys.stderr)
        return 2

    # //// 执行 SQL 并输出带范围的结果页 [@x380kkm 2026-09-14] ////
    try:
        cols, rows = execute_sql(db_path, args.sql)
    except Exception as exc:
        print(f"error: SQL failed: {exc}", file=sys.stderr)
        return 1

    result_text = serialize_result(cols, rows)
    result_path = None
    if len(result_text) > args.budget:
        try:
            result_path = save_result(cfg, result_text)
        except OSError as exc:
            print(f"error: cannot save result: {exc}", file=sys.stderr)
            return 1
    print_result_page(build_result_page(
        result_text, cfg, len(rows), result_path, 0, args.budget,
    ))
    # //// /执行 SQL 并输出带范围的结果页 ////

    #### 自动记入 trace 库（除非被抑制） [@380kkm 2026-06-05] ####
    if not args.no_log:
        rel_paths = referenced_paths(db_path, args.sql)
        log_id = log_trace(cfg, args.sql, args.static, args.task, rel_paths)
        kind = "static" if args.static else "dynamic"
        print(f"# logged trace id={log_id} kind={kind} project={cfg.alias}",
              file=sys.stderr)
    #### /自动记 trace ####

    return 0
# //// /CLI 入口执行 SQL 或读取已保存结果. ////


if __name__ == "__main__":
    raise SystemExit(main())
