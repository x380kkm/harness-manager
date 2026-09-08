# audience: internal
# # module-operations
"""模块表单保存独立来源的固定版本引用. 来源和模块在同一目录原子提交, 使用范围由独立绑定维护."""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid4, uuid5

from .card_subjects import CardError, CardSubjects
from .catalogs import Catalogs
from .codex_inventory import CodexInventory
from .declarations import contribution_name, index_declarations
from .inventory_groups import item_description
from .module_inventory import MEMBER_KINDS, POINT_KINDS, PRESENTATION_CONTRACT, is_module, module_presentation
from .protocol import document_identity, document_name
from .storage_errors import StorageConflictError

# //// 返回模块表单的明确输入错误 [@x380kkm 2026-09-07] ////
class ModuleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 根据发布和成员生成稳定选择身份 [@x380kkm 2026-09-07] ////
def contribution_id(reference: str, version: str) -> str:
    return "contribution:" + uuid5(NAMESPACE_URL, reference + "@" + version).hex


# //// 取得当前范围中可以组合的来源与明确缺口 [@x380kkm 2026-09-07] ////
def module_candidates(frame: CardSubjects) -> tuple[list[dict], dict[str, dict]]:
    choices, sources = {}, {}
    origins = {identity: group["origin"] for group in frame.inventory["groups"] for identity in group["itemIds"]}
    for item in frame.items.values():
        kind = item_description(item, [item])["category"]
        if kind not in {"rule", "skill", "hook", "tool"}:
            continue
        candidate = {"itemId": item["id"], "name": item["name"], "kind": kind, "supported": False,
                     "path": item["path"], "scope": item["scope"], "origin": origins.get(item["id"], "user")}
        if item["kind"] in {"rule", "skill", "hook"}:
            try:
                subject = frame.subject(item["id"])
                sources[item["id"]] = frame.definition(item["id"])
                candidate.update(supported=True, ref=subject["ref"], version=subject["version"])
            except CardError as error:
                candidate["reason"] = str(error)
        else:
            candidate["reason"] = "此项是本机观察, 需要先声明可引用的工具或 Hook 接口."
        choices[item["id"]] = candidate
    known = {(value.get("ref"), value.get("version")) for value in choices.values() if value["supported"]}
    for document in frame.view.documents:
        if document["kind"] != "Plugin":
            continue
        annotations = (module_presentation(document) or {}).get("members", {})
        for member in document["contributions"]:
            if "ref" in member:
                continue
            annotation = annotations.get(member["id"], {}) if isinstance(annotations, dict) else {}
            kind = POINT_KINDS.get(member["point"], annotation.get("kind") if isinstance(annotation, dict) else None)
            if kind not in MEMBER_KINDS - {"source"}:
                continue
            reference, version = document["id"] + "#" + member["id"], document["release"]["version"]
            if (reference, version) in known:
                continue
            identity = contribution_id(reference, version)
            choices[identity] = {"itemId": identity, "name": contribution_name(member), "kind": kind,
                                 "supported": True, "ref": reference, "version": version,
                                 "documentId": document_identity(document), "origin": "user"}
            sources[identity] = document
    return sorted(choices.values(), key=lambda item: (not item["supported"], item["kind"], item["name"].casefold(), item["itemId"])), sources


# //// 将表单成员校验为有序且唯一的选择 [@x380kkm 2026-09-07] ////
def validate_settings(name: str, description: str, members: list[dict]) -> None:
    if not isinstance(name, str) or not name.strip() or len(name) > 160 or not isinstance(description, str) or len(description) > 4000:
        raise ModuleError("module_settings", "模块需要名称, 名称上限 160 字, 说明上限 4000 字.")
    if not isinstance(members, list) or not 1 <= len(members) <= 100:
        raise ModuleError("module_members", "请选择 1 至 100 个成员.")
    identities = []
    for member in members:
        if (not isinstance(member, dict) or set(member) - {"itemId", "role"} or not isinstance(member.get("itemId"), str)
                or not member["itemId"] or ("role" in member and (not isinstance(member["role"], str) or len(member["role"]) > 1000))):
            raise ModuleError("module_member", "成员需要 itemId 和可选的 Agent 使用说明.")
        identities.append(member["itemId"])
    if len(set(identities)) != len(identities):
        raise ModuleError("module_members", "同一成员只能选择一次.")


# //// 从独立来源引用构造模块并保留成员作用域和载体说明 [@x380kkm 2026-09-07] ////
def module_document(name: str, description: str, selected: list[dict], previous: dict | None, plugin_id: str) -> dict:
    document = deepcopy(previous) if previous else {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin",
                "id": plugin_id, "release": {"version": "local"}, "contributions": []}
    document.setdefault("metadata", {}).update(name=name.strip(), description=description)
    prior = {member["ref"]: member for member in document["contributions"]}
    presentation = module_presentation(document)
    if presentation is None:
        presentation = {}
        document.setdefault("extensions", []).append({"contract": {"id": PRESENTATION_CONTRACT, "range": "^1.0.0"},
                                                      "payload": presentation})
    annotations = presentation.get("members", {})
    annotations = annotations if isinstance(annotations, dict) else {}
    contributions, updated = [], {}
    for choice in selected:
        member = deepcopy(prior.get(choice["ref"], {"id": "member:" + uuid5(NAMESPACE_URL, choice["itemId"]).hex}))
        member.update(ref=choice["ref"], constraint=choice["version"])
        annotation = deepcopy(annotations.get(member["id"], {}))
        annotation = annotation if isinstance(annotation, dict) else {}
        annotation.update(itemId=choice["itemId"], kind=choice["kind"])
        if "role" in choice:
            annotation["role"] = choice["role"]
        contributions.append(member)
        updated[member["id"]] = annotation
    document["contributions"], presentation["members"] = contributions, updated
    return document


# //// 为面板和 Agent 提供模块组合的预览及保存 [@x380kkm 2026-09-07] ////
class ModuleOperations:
    def __init__(self, catalogs: Catalogs, codex: CodexInventory) -> None:
        self.catalogs, self.codex = catalogs, codex

    # //// 选择模块卡片或具体发布对应的声明 [@x380kkm 2026-09-07] ////
    def _document(self, frame: CardSubjects, identity: str | None) -> dict | None:
        if identity is None:
            return None
        if not isinstance(identity, str):
            raise ModuleError("module_id", "模块身份需要文本.")
        matches = [value for value in frame.view.documents if is_module(value)
                   and identity in {document_identity(value), "module:" + uuid5(NAMESPACE_URL, document_identity(value)).hex}]
        if len(matches) != 1:
            raise ModuleError("module_missing", "模块声明需要重新读取.")
        return matches[0]

    # //// 返回表单当前值和可采用的独立来源 [@x380kkm 2026-09-07] ////
    def describe(self, id: str | None = None, scope: str = "user") -> dict:
        frame = CardSubjects(self.catalogs, self.codex, scope)
        document = self._document(frame, id)
        candidates, _ = module_candidates(frame)
        local = frame.local_definition(document) if document else None
        editable = document is None or local is not None and all("ref" in member for member in document["contributions"])
        selected = []
        if document:
            annotations = (module_presentation(document) or {}).get("members", {})
            annotations = annotations if isinstance(annotations, dict) else {}
            index = index_declarations(frame.view.documents)
            for member in document["contributions"]:
                chain = index.resolve_reference(document["id"] + "#" + member["id"], document["release"]["version"])
                choice = next((candidate for candidate in candidates if chain and candidate.get("ref") == chain[-1][0]["id"] + "#" + chain[-1][1]["id"]
                               and candidate.get("version") == chain[-1][0]["release"]["version"]), None)
                if choice is None:
                    choice = {"itemId": contribution_id(document["id"] + "#" + member["id"], document["release"]["version"]),
                              "name": member["id"], "kind": "source", "supported": False,
                              "reason": "成员来源需要重新确认, 可在声明编辑器中维护引用."}
                    candidates.append(choice)
                annotation = annotations.get(member["id"], {})
                selected.append({"itemId": choice["itemId"],
                                 "role": annotation.get("role", "") if isinstance(annotation, dict) else ""})
        return {"id": document_identity(document) if document else None, "scope": scope, "editable": editable,
                "document": deepcopy(local or document), "baseline": deepcopy(local), "candidates": candidates,
                "settings": {"name": document_name(document) if document else "", "description": document.get("metadata", {}).get("description", "") if document else "", "members": selected},
                "reason": "" if editable else "请在模块来源层维护组合; 内联内容通过声明编辑器维护.",
                "diagnostics": deepcopy(frame.view.diagnostics)}

    # //// 形成模块和必要来源的共同写入计划 [@x380kkm 2026-09-07] ////
    def _prepare(self, frame: CardSubjects, name: str, description: str, members: list[dict], previous: dict | None, plugin_id: str) -> dict:
        validate_settings(name, description, members)
        candidates, sources = module_candidates(frame)
        choices = {item["itemId"]: item for item in candidates}
        selected = []
        for member in members:
            candidate = choices.get(member["itemId"])
            if candidate is None or not candidate["supported"]:
                raise ModuleError("module_source", "请选择具有明确内容或接口声明的成员.")
            if candidate["ref"].partition("#")[0] == plugin_id:
                raise ModuleError("module_cycle", "模块成员需要引用独立来源.")
            selected.append({**candidate, **member})
        if len({choice["ref"] for choice in selected}) != len(selected):
            raise ModuleError("module_members", "同一来源入口只能选择一次.")
        document = module_document(name, description, selected, previous, plugin_id)
        source_ids = [choice["itemId"] for choice in selected if choice["itemId"] in frame.items]
        plans = frame.source_plans(source_ids)
        planned = {entry["id"] for entry in plans}
        guards = []
        for choice in selected:
            source = sources[choice["itemId"]]
            identity = document_identity(source)
            if frame.scope == "project" and not any(document_identity(value) == identity for value in frame.project):
                raise ModuleError("module_shared_source", "共享模块的成员需要先加入项目共享设置, 或将模块保存到项目个人设置.")
            guards.append({"itemId": choice["itemId"], "document": deepcopy(source)})
            if identity not in planned and frame.local_definition(source) is not None:
                original = frame.local_definition(source)
                plans.append(frame.store.preview_put(original, original))
                planned.add(identity)
        plans.append(frame.store.preview_put(document, previous))
        identity = document_identity(document)
        return {"scope": frame.scope, "catalog": str(frame.store.catalog), "documentId": identity,
                "settings": {"name": name, "description": description, "members": deepcopy(members)},
                "baseline": deepcopy(previous), "sources": guards, "entries": plans}

    # //// 预览新组合或当前层模块的编辑 [@x380kkm 2026-09-07] ////
    def preview(self, name: str, description: str, members: list[dict], scope: str = "user",
                id: str | None = None, baseline: dict | None = None) -> dict:
        frame = CardSubjects(self.catalogs, self.codex, scope)
        current = self._document(frame, id)
        previous = frame.local_definition(current) if current else None
        if current is not None and (previous is None or any("ref" not in member for member in previous["contributions"])):
            raise ModuleError("module_readonly", "请在模块来源层维护引用组合; 内联内容通过声明编辑器维护.")
        if baseline != previous:
            raise StorageConflictError(document_identity(current) if current else id or "module:new")
        plugin_id = previous["id"] if previous else "plugin:module/" + uuid4().hex
        plan = self._prepare(frame, name, description, members, previous, plugin_id)
        documents = {document_identity(value): value for value in frame.local}
        documents.update({entry["id"]: entry["after"] for entry in plan["entries"]})
        return {"plan": plan, "id": "module:" + uuid5(NAMESPACE_URL, plan["documentId"]).hex,
                "documentId": plan["documentId"], "document": deepcopy(plan["entries"][-1]["after"]),
                "diagnostics": self.catalogs.edit_diagnostics(scope, frame.local, list(documents.values()))}

    # //// 在固定目录核对来源及模块基线后原子保存 [@x380kkm 2026-09-07] ////
    def apply(self, plan: dict) -> dict:
        if not isinstance(plan, dict) or set(plan) != {"scope", "catalog", "documentId", "settings", "baseline", "sources", "entries"}:
            raise ModuleError("module_plan", "模块计划需要固定目录, 来源基线与组合差异.")
        frame = CardSubjects(self.catalogs, self.codex, plan["scope"])
        if plan["catalog"] != str(frame.store.catalog):
            raise ModuleError("module_catalog", "计划写入位置与当前配置层不同.")
        settings, previous = plan["settings"], plan["baseline"]
        if previous is not None and not isinstance(previous, dict):
            raise ModuleError("module_plan", "模块读取基线需要完整声明.")
        if not isinstance(settings, dict) or set(settings) != {"name", "description", "members"}:
            raise ModuleError("module_plan", "模块计划需要完整表单值.")
        if previous is not None:
            current = self._document(frame, plan["documentId"])
            if frame.local_definition(current) != previous:
                raise StorageConflictError(plan["documentId"])
            if any("ref" not in member for member in previous["contributions"]):
                raise ModuleError("module_plan", "模块表单维护独立来源引用.")
        if not isinstance(plan["documentId"], str) or "@" not in plan["documentId"]:
            raise ModuleError("module_plan", "模块计划需要具体发布身份.")
        plugin_id = previous["id"] if previous else plan["documentId"].rpartition("@")[0]
        if previous is None and not plugin_id.startswith("plugin:module/"):
            raise ModuleError("module_plan", "新模块需要独立模块身份.")
        expected = self._prepare(frame, **settings, previous=previous, plugin_id=plugin_id)
        if plan["sources"] != expected["sources"]:
            raise StorageConflictError(plan["documentId"])
        if plan != expected:
            raise ModuleError("module_plan", "模块计划与当前成员组合的差异不一致.")

        # //// 在目录锁内再次核对全部选定来源 [@x380kkm 2026-09-07] ////
        def verify_current(documents: list[dict]) -> None:
            fresh = CardSubjects(self.catalogs, self.codex, plan["scope"])
            _, sources = module_candidates(fresh)
            if any(sources.get(guard["itemId"]) != guard["document"] for guard in plan["sources"]):
                raise StorageConflictError(plan["documentId"])

        results = frame.store.apply_many(plan["entries"], verify_current=verify_current)
        return {"id": "module:" + uuid5(NAMESPACE_URL, plan["documentId"]).hex, "documentId": plan["documentId"],
                "scope": plan["scope"], "catalog": str(frame.store.catalog), "changed": any(result["changed"] for result in results),
                "document": deepcopy(plan["entries"][-1]["after"])}
