# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# audience: internal
# cleanscan.lib.agentapi
"""cleanscan.lib.agentapi —— 面向 AI 代理的精简交互层，复用 scope/analyze/boundary 机制。

人用的 cleanscan 出可视化图（html、dot、mermaid）；代理要的是排好序、带 ``path:line`` 锚点、带
不确定标注、token 受控的答案。本层把同一套有界切片机制收敛成三类问题优先的答案：

  * ``impact(seed)``     —— 改动 seed 会牵连谁（反向依赖切片），按耦合度排序、带锚点。
  * ``interface(target)``—— 这块代码对外依赖的接口（target 到 dependency 跨越边），带置信度与锚点。
  * ``seam(seed)``       —— seed 邻域里的切点（桥与关节节点），即可拆分位置，带锚点。

每个答案都内联截断信息（``truncated``/``elided``/``frontier``）与边的置信度，使代理不会在被静默
封顶或歧义未决的切片上行动。所有结果都是普通 dict，调用方（scan.py）直接 json 序列化。
"""
from __future__ import annotations

from lib import analyze, boundary, scope
from lib.graph import Budget, Graph


#### 取一个节点的 path:line 锚点，缺失回退 label [@380kkm 2026-06-16] ####
def _anchor(g: Graph, nid: str) -> str:
    n = g.nodes.get(nid)
    if n is None:
        return nid
    ev = n.evidence
    if ev is not None and ev.path:
        return f"{ev.path}:{ev.line}" if ev.line is not None else ev.path
    return n.attrs.get("path") or n.label or nid
#### /取锚点 ####


#### 把切片的截断信息汇总为精简 dict（代理据此知道还有多少节点未展开） [@380kkm 2026-06-16] ####
def _bounds(g: Graph) -> dict:
    return {"truncated": bool(g.truncated), "depth_bounded": bool(g.depth_bounded),
            "fully_included_depth": g.frontier_depth if g.truncated or g.depth_bounded else None,
            "elided": int(g.elided), "frontier_remaining": sum(g.frontier.values())}
#### /截断信息 ####


#### impact：改动 seed 符号会直接牵连谁——按符号级调用边反查，带同名歧义标注 [@380kkm 2026-06-16] ####
def impact(st, seed: str, top: int = 25) -> dict:
    """直接查符号级反向边（edges.dst_name = seed），这是最可靠的「谁依赖 X」，避开较弱的文件级
    import 图。跨文件同名调用的 dst_symbol_id 未解析，故当 seed 名在多个文件有定义时，调用方可能
    指向不同的同名符号——结果给出 ambiguous 标注，代理据 import 关系再确认。本实现取一阶直接调用方
    （最可操作的影响面；要追传递影响就对每个调用方再跑一次）。"""
    conn = st.conn
    # seed 的定义位置（可能多处同名）
    defs = [{"anchor": f"{r['path']}:{r['start_line']}", "kind": r["kind"]}
            for r in conn.execute(
                "SELECT f.path, s.start_line, s.kind FROM symbols s JOIN files f ON f.id=s.file_id "
                "WHERE s.name=? ORDER BY f.path", (seed,)).fetchall()]
    ambiguous = len(defs) > 1
    # 反向调用方：按名匹配未解析边（dst_name），并按 dst_symbol_id 匹配已解析到同名符号的边
    rows = conn.execute(
        "SELECT f.path AS cpath, e.relation, s.name AS caller, s.start_line AS cline, s.kind AS ckind "
        "FROM edges e JOIN files f ON f.id=e.file_id "
        "LEFT JOIN symbols s ON s.id=e.src_symbol_id "
        "WHERE e.relation IN ('calls','uses_type','extends','implements','imports') "
        "  AND (e.dst_name=? OR e.dst_symbol_id IN (SELECT id FROM symbols WHERE name=?)) "
        "ORDER BY f.path, s.start_line", (seed, seed)).fetchall()
    seen: set[tuple] = set()
    dependents = []
    for r in rows:
        caller = r["caller"] or "(模块级)"
        anchor = f"{r['cpath']}:{r['cline']}" if r["cline"] is not None else r["cpath"]
        # 按「调用方 + 关系」去重：同一调用方即便依赖多个同名目标，也只是一个受影响的调用方
        key = (anchor, caller, r["relation"])
        if key in seen:
            continue
        seen.add(key)
        dependents.append({"anchor": anchor, "caller": caller,
                           "kind": r["ckind"] or "?", "relation": r["relation"]})
    dependents.sort(key=lambda d: (d["anchor"], d["caller"]))
    total = len(dependents)
    return {
        "question": "impact", "seed": seed,
        "definitions": defs,
        "ambiguous": ambiguous,
        "ambiguity_note": ("seed 名在多个文件有定义；下列调用方可能指向不同的同名符号，"
                           "请据各文件的 import 关系确认归属") if ambiguous else None,
        "direct_dependent_count": total,
        "direct_dependents": dependents[:top],
        "omitted": max(0, total - top),
        "transitive_hint": "本结果是一阶直接调用方；要追传递影响，对感兴趣的调用方再跑一次 impact",
    }
#### /impact ####


# 置信度从坏到好的序：聚合一个依赖符号的多条边时取最坏，且与边的遍历顺序无关
_CONF_RANK = {"unresolved": 0, "ambiguous": 1, "unique": 2, "direct": 3}


#### interface：这块代码对外依赖的接口——target 到 dependency 跨越边，带置信度 [@380kkm 2026-06-16] ####
def interface(st, target_root: str | None, dep_root, alias: str | None,
              max_nodes: int = 200, depth: int = 4, top: int = 40) -> dict:
    z = boundary.make_zoning(st, target_root, dep_root)
    budget = Budget(max_nodes=max_nodes, max_depth=max(2, depth), direction="out")
    g = boundary.build(st, z, budget, alias=alias, dep_depth=1)
    # 把跨越边按「被依赖的 dependency 符号」聚合，列出谁用它、置信度如何
    by_dst: dict[str, dict] = {}
    for c in boundary.crossings(g):
        entry = by_dst.setdefault(c.dst, {
            "dependency": (g.nodes[c.dst].label if c.dst in g.nodes else c.dst),
            "anchor": _anchor(g, c.dst), "relations": set(),
            "confidence": c.confidence, "used_by": set(),
        })
        entry["used_by"].add(_anchor(g, c.src))
        entry["relations"].add(c.relation)
        # 取最坏置信度，与遍历顺序无关（代理最需要看到的是不确定性）
        if _CONF_RANK.get(c.confidence, 0) < _CONF_RANK.get(entry["confidence"], 0):
            entry["confidence"] = c.confidence
    items = []
    for dst, e in by_dst.items():
        items.append({**e, "relations": sorted(e["relations"]),
                      "used_by": sorted(e["used_by"]), "used_by_count": len(e["used_by"])})
    # 被依赖越多越是核心接口，排前面
    items.sort(key=lambda e: (-e["used_by_count"], e["anchor"]))
    total = len(items)
    return {
        "question": "interface", "target_root": target_root if target_root is not None else "(whole index)",
        "interface_count": total,
        "interface": items[:top],
        "omitted": max(0, total - top),
        "note": "confidence ambiguous/unresolved 表示跨文件同名未消歧，代理应据 import 关系再确认",
        "bounds": _bounds(g),
    }
#### /interface ####


#### seam：seed 邻域里的切点——桥与关节节点，即可拆分处 [@380kkm 2026-06-16] ####
def seam(st, seed: str, alias: str | None, max_nodes: int = 80, depth: int = 6) -> dict:
    budget = Budget(max_nodes=max_nodes, max_depth=depth, direction="both")
    g = scope.scan(st, seed, budget, alias=alias)
    m = analyze.metrics(g)
    bridges = [{"from": _anchor(g, a), "to": _anchor(g, b), "relation": rel}
               for a, b, rel in m.bridges]
    cut_nodes = [{"anchor": _anchor(g, nid),
                  "label": (g.nodes[nid].label if nid in g.nodes else nid)}
                 for nid in m.cut_nodes]
    return {
        "question": "seam", "seed": seed,
        "bridges": bridges, "cut_nodes": cut_nodes,
        "cycle_count": len(m.cycles),
        "hint": "bridges 是移除后会切开依赖图的边、cut_nodes 是关节节点，都是拆分的候选缝",
        "bounds": _bounds(g),
    }
#### /seam ####
