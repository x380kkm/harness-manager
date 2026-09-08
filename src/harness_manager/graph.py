# audience: internal
# # declaration-graph
# 图由传入声明与引用解析结果派生, 选择失败的引用保留独立的未解析节点.

from __future__ import annotations

from typing import Any

from .declarations import contribution_name, diagnose, index_declarations, matches_version
from .protocol import document_identity, document_name


# //// 提取支持的工具端点能力 [@x380kkm 2026-09-06] ////
def _capabilities(member: dict) -> list[str]:
    contract = member.get("contract", {})
    if member.get("point") != "tool.x380kkm/endpoint" or contract.get("id") != "tool.x380kkm/endpoint":
        return []
    if matches_version("1.0.0", contract.get("range")) is not True:
        return []
    payload = member.get("payload")
    capabilities = payload.get("capabilities") if isinstance(payload, dict) else None
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and item.strip() for item in capabilities):
        return []
    return list(dict.fromkeys(capabilities))


# //// 提取配套正文所属的 Skill 引用 [@x380kkm 2026-09-06] ////
def _context_target(member: dict) -> str | None:
    contract = member.get("contract", {})
    payload = member.get("payload")
    if (member.get("point") == "context.x380kkm/task" and contract.get("id") == "context.x380kkm/task"
            and matches_version("1.0.0", contract.get("range")) is True and isinstance(payload, dict)
            and isinstance(payload.get("skill"), str)):
        return payload["skill"]
    return None


# //// 提取声明内的显式目标 [@x380kkm 2026-09-06] ////
def _relations(subject: str, declaration: dict) -> list[dict]:
    relations = [{"id": f"{subject}::relation:{item['id']}", "from": subject,
                  "to": item["target"], "label": item["contract"]["id"]}
                 for item in declaration.get("relations", [])]
    relations.extend({"id": f"{subject}::requirement:{item['id']}", "from": subject,
                      "to": item["target"], "label": "requires"}
                     for item in declaration.get("requirements", []))
    return relations


# //// 构造静态声明关系图 [@x380kkm 2026-09-06] ////
def build_graph(documents: list[dict]) -> dict:
    index = index_declarations(documents)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict] = []
    targets: dict[str, set[str]] = {}
    unresolved_edges: set[str] = set()
    for document in documents:
        identifier = document_identity(document)
        logical_id = document.get("id") or document.get("point")
        if identifier in nodes:
            diagnose(index.diagnostics, "duplicate_graph_identity", identifier, "多个声明使用相同的存储身份.")
        nodes[identifier] = {"id": identifier, "label": document_name(document),
                             "kind": document["kind"], "documentId": identifier}
        targets.setdefault(logical_id, set()).add(identifier)
    for document in documents:
        if document.get("kind") == "PluginBinding":
            identifier, target = document_identity(document), document["plugin"]["id"]
            selected = index.resolve_plugin(target, [document["plugin"]], origin=identifier)
            target = document_identity(selected) if selected else target
            edges.append({"id": f"{identifier}::binding", "from": identifier,
                          "to": target, "label": "uses"})
            if selected is None:
                unresolved_edges.add(f"{identifier}::binding")
        if document.get("kind") != "Plugin":
            continue
        identifier = document_identity(document)
        edges.extend(_relations(identifier, document))
        for member in document["contributions"]:
            reference = f"{identifier}#{member['id']}"
            logical_ref = f"{document['id']}#{member['id']}"
            targets.setdefault(logical_ref, set()).add(reference)
            nodes[reference] = {"id": reference, "label": contribution_name(member),
                                "kind": member.get("point", "contribution-reference"), "documentId": identifier}
            edges.append({"id": f"{reference}::owner", "from": identifier,
                          "to": reference, "label": "contains"})
            edges.extend(_relations(reference, member))
            context_target = _context_target(member)
            if context_target is not None:
                edges.append({"id": f"{reference}::context", "from": reference, "to": context_target, "label": "supplements"})
            for capability in _capabilities(member):
                nodes.setdefault(capability, {"id": capability, "label": capability,
                                              "kind": "capability", "documentId": None})
                edges.append({"id": f"{reference}::provides:{capability}", "from": reference,
                              "to": capability, "label": "provides"})
            if "ref" in member:
                target_plugin_id, _, target_member = member["ref"].partition("#")
                selected = index.resolve_plugin(target_plugin_id, [{"constraint": member.get("constraint")}], origin=reference)
                target = f"{document_identity(selected)}#{target_member}" if selected else member["ref"]
                edges.append({"id": f"{reference}::reference", "from": reference,
                              "to": target, "label": "references"})
                if selected is None:
                    unresolved_edges.add(f"{reference}::reference")
                index.resolve_reference(logical_ref, document["release"]["version"], origin=reference)
    for edge in edges:
        if edge["id"] in unresolved_edges:
            nodes.setdefault(edge["to"], {"id": edge["to"], "label": edge["to"],
                                          "kind": "unresolved", "documentId": None})
            continue
        if edge["to"] not in nodes or nodes[edge["to"]]["kind"] == "unresolved":
            candidates = targets.get(edge["to"], set())
            if len(candidates) == 1:
                edge["to"] = next(iter(candidates))
            else:
                code = "ambiguous_graph_target" if candidates else "missing_graph_target"
                diagnose(index.diagnostics, code, edge["id"], f"目标 {edge['to']} 需要唯一的内容声明.")
                nodes.setdefault(edge["to"], {"id": edge["to"], "label": edge["to"],
                                              "kind": "unresolved", "documentId": None})
    return {"nodes": sorted(nodes.values(), key=lambda node: node["id"]),
            "edges": sorted(edges, key=lambda edge: edge["id"]), "diagnostics": index.diagnostics}
