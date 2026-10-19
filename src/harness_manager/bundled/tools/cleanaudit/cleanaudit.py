# /// script
# requires-python = ">=3.12"
# dependencies = ["tree-sitter", "tree-sitter-language-pack"]
# ///
# audience: internal
# cleanaudit
"""cleanaudit —— 在每个回合结束后、正式审计（起模型子进程逐维度检查写作规范）之前运行的确定性
预过滤器，对本回合改动的文件做进程内临时解析。

运行前提：同仓兄弟目录 ``tools/cleanread`` 存在（复用其 enrich 解析机制）；被审项目是 git 仓库。

不污染索引的关键：cleanaudit 不读、不写正式索引（``cleanread/source.db``）。它对本回合改动的
那几个文件做一次进程内临时解析（复用 cleanread 的 tree-sitter 抽取），用完即弃，绝不落盘、绝不
碰正式索引。这样审计永远看当前真相，而正式索引只由「每个任务后的正式全量重建」产生，增量解析的
任何近似都污染不到正式索引。非 git 时各命令安全退化（`plan` 对所有维度返回 `audit`）。

它解决的问题：写作规范的后审计钩子每回合末无条件起一个模型子进程，消耗 token。cleanaudit 用
``git diff`` 算出本回合改了哪些文件与行，对这些文件做进程内临时解析得到当前符号，据此判定每个
审计维度要不要真起模型调用，并只把改动符号的有界片段（从当前文件按字节切，与解析同字节源）作为
上下文，而非整份 diff 或整文件。

模块不变量：
  * 只读 git 与被审项目的当前文件，从不读写正式索引，绝不修改被审项目。
  * 维度判定是确定性的：相同 diff 与相同文件内容必得相同 plan。
  * 任何一步失败（非 git、无索引、git 报错）都 fail-open（出错时放行而非阻断）：plan 把涉及代码
    的维度标为 audit，绝不因预过滤器自身故障而漏审或卡死调用方。

CLI：
  cleanaudit changeset --root R         打印本回合改动的文件与符号（TSV）
  cleanaudit plan --root R [--dims ...]  打印每个维度 skip/audit 的判定与原因（JSON）
  cleanaudit context --root R --dim D     打印某维度要喂给模型的有界上下文
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


#### 定位 cleanread 脚本根并加入 sys.path，供 import enrich [@380kkm 2026-06-16] ####
def _cleanread_root() -> Path:
    here = Path(__file__).resolve().parent
    # 兄弟目录：tools/cleanaudit 与 tools/cleanread 同级
    cand = here.parent / "cleanread"
    if (cand / "enrich" / "extract.py").is_file():
        return cand
    env = os.environ.get("CLEANAUDIT_CLEANREAD")
    if env and (Path(env) / "enrich" / "extract.py").is_file():
        return Path(env)
    raise FileNotFoundError("找不到 cleanread（设 CLEANAUDIT_CLEANREAD 指向 cleanread 目录）")


# 进程内只装一次解析机制
_PARSE = {}


#### 懒加载 cleanread 的 enrich 解析机制（tree-sitter） [@380kkm 2026-06-16] ####
def _parse_machinery():
    if _PARSE:
        return _PARSE
    root = _cleanread_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from enrich import langreg                       # noqa: PLC0415
    from enrich.extract import _extract_file         # noqa: PLC0415
    from tree_sitter import Parser                   # noqa: PLC0415
    _PARSE.update(langreg=langreg, extract=_extract_file, Parser=Parser, parsers={})
    return _PARSE
#### /懒加载解析机制 ####


#### 对单个当前文件做临时解析，返回符号 dict 列表（不落盘、不碰索引） [@380kkm 2026-06-16] ####
def _parse_file_symbols(abs_path: Path, rel: str) -> list[dict]:
    m = _parse_machinery()
    ext = abs_path.suffix.lower()
    lang = m["langreg"].LANG_FOR_EXT.get(ext)
    if lang is None or lang not in m["langreg"].SUPPORTED_LANGS:
        return []
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if lang not in m["parsers"]:
        try:
            m["parsers"][lang] = m["Parser"](m["langreg"]._load_language(lang))
        except Exception:  # noqa: BLE001
            m["parsers"][lang] = None
    parser = m["parsers"][lang]
    if parser is None:
        return []
    try:
        rows, _edges = m["extract"](0, content, lang, parser, False, None, None)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        out.append({"path": rel, "name": r["name"], "kind": r["kind"],
                    "start_line": r["start_line"], "end_line": r["end_line"],
                    "start_byte": r["start_byte"], "end_byte": r["end_byte"]})
    return out
#### /临时解析单文件 ####


#### 在被审项目根运行一条 git 命令，失败返回 None（fail-open） [@380kkm 2026-06-16] ####
def _git(root: str, args: list[str]) -> str | None:
    try:
        res = subprocess.run(["git", "-C", root, *args], capture_output=True,
                             text=True, encoding="utf-8", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout or ""
#### /运行 git 命令 ####


# 视为源码的扩展名：只有这些文件的改动才触发代码类审计
_SRC_EXTS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
             ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".gd", ".java", ".rs", ".go"}
# 视为文档的扩展名
_DOC_EXTS = {".md", ".rst", ".txt"}


#### 取本回合改动的文件：已跟踪改动并集未跟踪新增 [@380kkm 2026-06-16] ####
def changed_files(root: str) -> list[str] | None:
    """返回相对 root 的改动文件路径列表；非 git 返回 None（调用方据此 fail-open）。用 -z 关闭
    quotepath 与转义、按 NUL 切分，正确处理非 ASCII 与含空格的文件名（否则被引号转义后扩展名
    判断失效、整类文件漏审）。"""
    diff = _git(root, ["diff", "--name-only", "-z", "HEAD"])
    if diff is None:
        return None
    # --porcelain -z：每条记录 NUL 结尾，未跟踪文件以 "?? " 开头；切分后逐条判前缀
    status = _git(root, ["status", "--porcelain", "-z"]) or ""
    files = {p for p in diff.split("\0") if p}
    for rec in status.split("\0"):
        if rec.startswith("?? "):
            files.add(rec[3:])
    # 归一成 cleanread 索引里的相对正斜杠路径
    return sorted(p.replace("\\", "/") for p in files)
#### /取本回合改动的文件 ####


#### 解析某文件的 unified diff，取被改动的行号区间（新文件侧） [@380kkm 2026-06-16] ####
def changed_line_ranges(root: str, path: str) -> list[tuple[int, int]]:
    """返回 [(起行, 止行)]（1 基，含端点）。未跟踪新文件无 HEAD 版本，返回空表示整文件皆新。"""
    out = _git(root, ["diff", "--unified=0", "HEAD", "--", path])
    if not out:
        return []
    ranges: list[tuple[int, int]] = []
    for line in out.splitlines():
        # 形如 @@ -a,b +c,d @@：取 +c,d（新文件侧）
        if line.startswith("@@"):
            try:
                plus = line.split("+", 1)[1].split("@@", 1)[0].strip()
                if "," in plus:
                    start_s, count_s = plus.split(",", 1)
                    start, count = int(start_s), int(count_s)
                else:
                    start, count = int(plus), 1
            except (ValueError, IndexError):
                continue
            if count == 0:
                # 纯删除：影响 start 一行（删除点）
                ranges.append((start, start))
            else:
                ranges.append((start, start + count - 1))
    return ranges
#### /解析改动行号区间 ####


#### 两个闭区间是否相交 [@380kkm 2026-06-16] ####
def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end
#### /区间相交 ####


#### 算出本回合改动的符号：临时解析改动文件，取与改动行相交的符号 [@380kkm 2026-06-16] ####
def changed_symbols(root: str) -> dict:
    """对改动的源码文件做进程内临时解析（不读、不写正式索引），返回 {changed_files,
    changed_src_files, changed_doc_files, symbols}；symbols 为 [{path,name,kind,start_line,
    end_line,start_byte,end_byte}]。非 git 时 git_ok=False。"""
    files = changed_files(root)
    if files is None:
        return {"git_ok": False, "changed_files": [], "changed_src_files": [],
                "changed_doc_files": [], "symbols": []}

    src = [f for f in files if Path(f).suffix.lower() in _SRC_EXTS]
    docs = [f for f in files if Path(f).suffix.lower() in _DOC_EXTS]

    root_path = Path(root)
    symbols: list[dict] = []
    for rel in src:
        abs_path = root_path / rel
        if not abs_path.is_file():
            continue  # 已删除文件：无当前符号可审
        rng = changed_line_ranges(root, rel)
        # 区分三种情况：新文件（不在 HEAD，整体纳入全部符号）；在 HEAD 但无 diff 区间（二进制或取不到，
        # 纳入零符号，不把「没拿到改动行」误当整文件改动）；有区间则按相交过滤。
        is_new = not _in_head(root, rel)
        for sym in _parse_file_symbols(abs_path, rel):
            if (not rng and is_new) or any(
                    _overlaps(sym["start_line"], sym["end_line"], r_start, r_end)
                    for r_start, r_end in rng):
                symbols.append(sym)
    return {"git_ok": True, "changed_files": files, "changed_src_files": src,
            "changed_doc_files": docs, "symbols": symbols}
#### /算出改动符号 ####


#### 判断某相对路径在 HEAD 里是否存在（区分新文件与已跟踪改动） [@380kkm 2026-06-16] ####
def _in_head(root: str, rel: str) -> bool:
    try:
        res = subprocess.run(["git", "cat-file", "-e", f"HEAD:{rel}"], cwd=root,
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True  # 拿不准时当已在 HEAD：宁可纳入零符号也不误当整文件新增
    return res.returncode == 0
#### /判断 HEAD 中是否存在 ####


# 审计维度名集合：plan 对每个维度有专门的 skip/audit 判定（见 plan 的 if/elif 链）
_DIMENSIONS = ("comment", "doc-sync", "jargon")


#### 给每个维度判定 skip/audit 并给出原因（确定性） [@380kkm 2026-06-16] ####
def plan(root: str, dims: list[str] | None) -> dict:
    dims = dims or list(_DIMENSIONS)
    try:
        cs = changed_symbols(root)
    except Exception as exc:  # noqa: BLE001
        # fail-open：预过滤器自身故障时把所有请求维度标为 audit，不漏审
        return {"ok": False, "reason": f"prefilter error: {exc}",
                "decisions": [{"dim": d, "action": "audit",
                               "reason": "预过滤失败，兜底审计"} for d in dims]}

    if not cs["git_ok"]:
        return {"ok": True, "git": False,
                "decisions": [{"dim": d, "action": "audit",
                               "reason": "非 git 仓库，无法预过滤，兜底审计"} for d in dims]}

    n_sym = len(cs["symbols"])
    n_src = len(cs["changed_src_files"])
    n_doc = len(cs["changed_doc_files"])
    decisions = []
    for d in dims:
        if d == "comment":
            if n_sym == 0:
                decisions.append({"dim": d, "action": "skip",
                                  "reason": "本回合无改动的源码符号"})
            else:
                decisions.append({"dim": d, "action": "audit",
                                  "reason": f"{n_sym} 个符号被改动", "symbol_count": n_sym})
        elif d == "doc-sync":
            if n_src == 0:
                decisions.append({"dim": d, "action": "skip", "reason": "本回合无源码改动"})
            elif n_doc > 0:
                decisions.append({"dim": d, "action": "skip",
                                  "reason": f"源码与文档同回合都有改动（{n_src} 源 / {n_doc} 文档）"})
            else:
                decisions.append({"dim": d, "action": "audit",
                                  "reason": f"{n_src} 个源文件改动但无文档同步"})
        elif d == "jargon":
            if n_sym == 0:
                decisions.append({"dim": d, "action": "skip", "reason": "本回合无新增/改动符号名"})
            else:
                decisions.append({"dim": d, "action": "audit",
                                  "reason": f"{n_sym} 个符号名需核验"})
        else:
            decisions.append({"dim": d, "action": "audit", "reason": "未知维度，兜底审计"})
    return {"ok": True, "git": True, "summary": {"symbols": n_sym, "src": n_src, "doc": n_doc},
            "decisions": decisions}
#### /维度判定 ####


#### 为某维度组装喂给模型的有界上下文：从当前文件按字节切改动符号 [@380kkm 2026-06-16] ####
def context(root: str, dim: str, max_bytes: int = 1200) -> str:
    cs = changed_symbols(root)
    root_path = Path(root)
    # 被切字节流必须与解析时同源：解析用 read_text(errors=replace) 再 encode 算偏移，故这里也用
    # read_text(errors=replace).encode 取字节，含非法 UTF-8 时偏移才与符号跨度一致（不能用 read_bytes）。
    raw_cache: dict[str, bytes] = {}
    chunks: list[str] = []
    for sym in cs["symbols"]:
        rel = sym["path"]
        if rel not in raw_cache:
            try:
                raw_cache[rel] = (root_path / rel).read_text(encoding="utf-8", errors="replace").encode("utf-8")
            except OSError:
                raw_cache[rel] = b""
        raw = raw_cache[rel]
        length = min(sym["end_byte"] - sym["start_byte"], max_bytes)
        slice_text = raw[sym["start_byte"]:sym["start_byte"] + length].decode("utf-8", "replace")
        chunks.append(f"# {rel}::{sym['name']} ({sym['kind']}, "
                      f"行 {sym['start_line']}-{sym['end_line']})\n{slice_text}")
    return "\n\n".join(chunks)
#### /组装有界上下文 ####


#### CLI 入口 [@380kkm 2026-06-16] ####
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleanaudit.py",
                                     description="cleanaudit: 后审计的确定性预过滤器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("changeset", "plan", "context"):
        sp = sub.add_parser(name)
        sp.add_argument("--root", required=True, help="被审项目根")
        if name == "plan":
            sp.add_argument("--dims", default=None, help="逗号分隔的维度子集")
        if name == "context":
            sp.add_argument("--dim", required=True, help="维度名")
    args = parser.parse_args(argv)

    if args.cmd == "changeset":
        cs = changed_symbols(args.root)
        print(f"git_ok\t{cs['git_ok']}")
        print(f"changed_src\t{len(cs['changed_src_files'])}\tchanged_doc\t{len(cs['changed_doc_files'])}")
        print("path\tname\tkind\tstart_line\tend_line")
        for s in cs["symbols"]:
            print(f"{s['path']}\t{s['name']}\t{s['kind']}\t{s['start_line']}\t{s['end_line']}")
        return 0
    if args.cmd == "plan":
        dims = [d.strip() for d in args.dims.split(",")] if args.dims else None
        print(json.dumps(plan(args.root, dims), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "context":
        print(context(args.root, args.dim))
        return 0
    return 2
#### /CLI 入口 ####


if __name__ == "__main__":
    raise SystemExit(main())
