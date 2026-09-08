# audience: internal
# # declaration-resolution
# 发布与成员引用在传入声明中解析, 组合链保留每个成员的所属发布与来源作用域.

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import document_identity, matches_version as _matches_version


# //// 按问题及其引用来源记录声明诊断 [@x380kkm 2026-09-08] ////
def diagnose(diagnostics: list[dict], code: str, subject: str, message: str, *, origin: str | None = None) -> None:
    diagnostic = {"code": code, "subject": subject, "message": message}
    if origin is not None:
        diagnostic["origin"] = origin
    if diagnostic not in diagnostics:
        diagnostics.append(diagnostic)


# //// 匹配静态发布约束 [@x380kkm 2026-09-06] ////
def matches_version(version: str, constraint: str | None) -> bool | None:
    try:
        return _matches_version(version, constraint)
    except ValueError:
        return None


# //// 从内容名称或稳定身份生成显示文本 [@x380kkm 2026-09-06] ////
def contribution_name(member: dict) -> str:
    payload = member.get("payload")
    name = payload.get("name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) and name.strip() else member["id"]


# //// 保存发布索引, 点契约和引用诊断 [@x380kkm 2026-09-08] ////
@dataclass
class DeclarationIndex:
    plugins: dict[str, list[dict]]
    diagnostics: list[dict] = field(default_factory=list)
    point_contracts: list[dict] = field(default_factory=list)

    # //// 选择约束内的唯一发布 [@x380kkm 2026-09-06] ////
    def resolve_plugin(self, identifier: str, selectors: list[dict] | None = None, *, origin: str | None = None) -> dict | None:
        candidates = self.plugins.get(identifier, [])
        for selector in selectors or []:
            matched = []
            for plugin in candidates:
                release = plugin["release"]
                if selector.get("channel") and release.get("channel") != selector["channel"]:
                    continue
                result = matches_version(release["version"], selector.get("constraint"))
                if result is None:
                    diagnose(self.diagnostics, "unknown_version_constraint", identifier,
                             f"无法静态解释发布约束 {selector.get('constraint')}.", origin=origin)
                    return None
                if result:
                    matched.append(plugin)
            candidates = matched
        if len(candidates) == 1:
            return candidates[0]
        code = "ambiguous_plugin" if candidates else "missing_plugin"
        diagnose(self.diagnostics, code, identifier, "引用需要匹配唯一的 Plugin 发布.", origin=origin)
        return None

    # //// 解析组合成员及其完整引用链 [@x380kkm 2026-09-06] ////
    def resolve_reference(self, reference: str, constraint: str | None = None, *, origin: str | None = None) -> list[tuple[dict, dict]] | None:
        chain: list[tuple[dict, dict]] = []
        visited: set[str] = set()
        while True:
            identifier, separator, local_id = reference.partition("#")
            if not separator:
                diagnose(self.diagnostics, "invalid_reference", reference, "成员引用需要 Plugin 身份与局部 ID.", origin=origin)
                return None
            plugin = self.resolve_plugin(identifier, [{"constraint": constraint}], origin=origin)
            if plugin is None:
                return None
            identity = f"{document_identity(plugin)}#{local_id}"
            if identity in visited:
                diagnose(self.diagnostics, "reference_cycle", reference, "组合成员引用形成循环.", origin=origin)
                return None
            visited.add(identity)
            members = [item for item in plugin["contributions"] if item["id"] == local_id]
            if len(members) != 1:
                diagnose(self.diagnostics, "missing_or_ambiguous_contribution", reference,
                         "成员引用需要匹配唯一的内容声明.", origin=origin)
                return None
            member = members[0]
            chain.append((plugin, member))
            if "ref" not in member:
                return chain
            reference, constraint = member["ref"], member.get("constraint")


# //// 建立发布与点契约的内存索引 [@x380kkm 2026-09-08] ////
def index_declarations(documents: list[dict]) -> DeclarationIndex:
    plugins: dict[str, list[dict]] = {}
    point_contracts = []
    for document in documents:
        if document.get("kind") == "Plugin":
            plugins.setdefault(document["id"], []).append(document)
        elif document.get("kind") == "PointContract":
            point_contracts.append(document)
    return DeclarationIndex(plugins, point_contracts=point_contracts)
