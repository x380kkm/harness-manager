# audience: internal
# # card-subjects
"""卡片身份取自来源位置. 启用层保存独立绑定, 同一来源在用户与项目目录中复用相同内容身份."""
from __future__ import annotations

from copy import deepcopy
import getpass
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .catalogs import Catalogs, resolve_document
from .codex_inventory import CodexInventory
from .codex_inventory_io import item_id
from .content_plan import INSTRUCTION_POINT, SKILL_POINT
from .inventory_groups import group_inventory
from .protocol import document_identity
from .storage_errors import StorageError

SOURCE_CONTRACT = "manager.card/source"
HOOK_POINT = "hook.x380kkm/lifecycle"
POINTS = {"skill": SKILL_POINT, "rule": INSTRUCTION_POINT, "hook": HOOK_POINT}
LAYER_ORDER = ("user", "project", "project-local")


# //// 保留卡片操作的明确输入错误 [@x380kkm 2026-09-07] ////
class CardError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 提取独立来源的归属记录 [@x380kkm 2026-09-07] ////
def source_record(document: dict) -> dict | None:
    return next((entry["payload"] for entry in document.get("extensions", [])
                 if entry["contract"]["id"] == SOURCE_CONTRACT and isinstance(entry.get("payload"), dict)), None)


# //// 将局部来源声明还原到实际文件位置 [@x380kkm 2026-09-07] ////
def member_path(document: dict, member: dict, root: Path) -> Path | None:
    source = member.get("source")
    if isinstance(source, str):
        source = next((entry["source"] for entry in document.get("sources", [])
                       if "source:" + entry["id"] == source), None)
    if not isinstance(source, dict) or source.get("resolver", {}).get("id") != "manager.source/path":
        return None
    locator = source.get("locator")
    entry = member.get("payload", {}).get("entry")
    if not isinstance(locator, str) or "://" in locator or not isinstance(entry, str):
        return None
    path = Path(locator).expanduser()
    return ((path if path.is_absolute() else root / path) / source.get("subpath", "") / entry).resolve()


# //// 找到来源在发布中的准确内容入口 [@x380kkm 2026-09-07] ////
def subject_member(document: dict, item: dict, root: Path) -> dict | None:
    record = source_record(document)
    members = [member for member in document.get("contributions", []) if member.get("point") == POINTS[item["kind"]]]
    if not members:
        return None
    if record and record.get("id") == item["id"] and len(members) == 1:
        return members[0]
    if item["kind"] == "hook" and item.get("details", {}).get("sourceDeclaration") == document_identity(document) and len(members) == 1:
        return members[0]
    if item["kind"] == "skill" and len(document.get("contributions", [])) == 1:
        path = Path(item["path"]).resolve()
        matches = [member for member in members if member_path(document, member, root) == path]
        if len(matches) == 1:
            return matches[0]
    return None


# //// 为完整 Skill 或独立规则建立来源定义 [@x380kkm 2026-09-07] ////
def source_document(item: dict) -> dict:
    token = uuid5(NAMESPACE_URL, item["id"]).hex
    kind = item["kind"]
    point = POINTS[kind]
    details = item.get("details", {})
    source = {"id": item["id"], "kind": kind, "path": item["path"], "scope": item["scope"],
              "line": item.get("line", 1), "key": details.get("key", item["id"])}
    if "endLine" in details:
        source["endLine"] = details["endLine"]
    if "sections" in details:
        source["sections"] = deepcopy(details["sections"])
    if kind == "rule":
        source["originalText"] = item["content"]
        if "fragments" in details:
            source["fragments"] = deepcopy(details["fragments"])
    member = {"id": kind, "point": point, "contract": {"id": point, "range": "^1.0.0"},
              "payload": {"name": item["name"]}}
    document = {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": "plugin:card/" + token,
                "release": {"version": "local"}, "metadata": {"name": item["name"], "description": item["summary"]},
                "contributions": [member], "extensions": [{"contract": {"id": SOURCE_CONTRACT, "range": "^1.0.0"},
                                                            "payload": source}]}
    if kind == "skill":
        path = Path(item["path"])
        document["sources"] = [{"id": "local", "source": {
            "resolver": {"id": "manager.source/path", "range": "^1.0.0"}, "locator": str(path.parent)}}]
        member["source"] = "source:local"
        member["payload"].update(entry=path.name, description=item["summary"])
    elif kind == "hook":
        payload = json.loads(item["content"])
        if not isinstance(payload, dict):
            raise CardError("hook_content", "Hook 内容需要完整的事件处理对象.")
        member["payload"] = payload
    else:
        member["payload"].update(text=item["content"], slot=item["id"])
        if item["scope"] == "directory":
            member["scope"] = {"contract": {"id": "manager.scope", "range": "^1.0.0"},
                               "selector": {"path": Path(item["path"]).parent.as_posix()}}
    return document


# //// 从受管来源定义补齐本机扫描之外的卡片 [@x380kkm 2026-09-07] ////
def declared_item(document: dict, origins: list[dict], bindings: list[dict]) -> dict | None:
    record = source_record(document)
    if record is None and len(document["contributions"]) == 1:
        member = document["contributions"][0]
        if member.get("point") == HOOK_POINT:
            record = {"id": "hook:" + uuid5(NAMESPACE_URL, document_identity(document) + "#" + member["id"]).hex,
                      "kind": "hook", "path": origins[-1]["path"] if origins else "", "scope": origins[-1]["scope"] if origins else "user"}
        if member.get("point") == SKILL_POINT:
            location = member_path(document, member, Path.cwd())
            if location is not None:
                identities = {entry["payload"]["itemId"] for binding in bindings
                              if binding["plugin"]["id"] == document["id"]
                              for entry in binding.get("extensions", [])
                              if entry["contract"]["id"] == "manager.card/identity"
                              and isinstance(entry.get("payload"), dict)
                              and entry["payload"].get("ref") == document["id"] + "#" + member["id"]
                              and isinstance(entry["payload"].get("itemId"), str)}
                if len(identities) > 1:
                    raise CardError("card_identity_conflict", "同一来源的绑定包含不同卡片身份.")
                record = {"id": next(iter(identities), item_id(location)), "kind": "skill", "path": str(location), "scope": "user"}
    if (record is None or record.get("kind") not in POINTS
            or not isinstance(record.get("id"), str) or not isinstance(record.get("path"), str)):
        return None
    kind = record["kind"]
    members = [member for member in document["contributions"] if member.get("point") == POINTS[kind]]
    if len(members) != 1:
        return None
    payload = members[0].get("payload", {})
    metadata = document.get("metadata", {})
    layer = origins[-1]["scope"] if origins else "user"
    result = {"id": record["id"], "name": metadata.get("name", document["id"]), "kind": kind,
              "summary": metadata.get("description", ""), "path": record["path"], "scope": record.get("scope", "user"),
              "line": record.get("line", 1), "importable": False, "managedIds": [document_identity(document)],
              "status": "受管内容声明" if kind in {"rule", "hook"} else "已声明 Skill, 本机来源待确认",
              "availability": "inline" if kind in {"rule", "hook"} else "source-unresolved",
              "details": {"managerDeclared": True, "catalogScope": layer,
                          "sourceDeclaration": document_identity(document), "key": record.get("key", record["id"]),
                          **({"endLine": record["endLine"]} if "endLine" in record else {}),
                          **({"sections": record["sections"]} if "sections" in record else {}),
                          "runtime": "声明来源的读取仍由授权范围检查."}}
    if kind == "rule" and isinstance(payload, dict) and isinstance(payload.get("text"), str):
        result["content"] = payload["text"]
    if kind == "hook" and isinstance(payload, dict):
        result["content"] = json.dumps(payload, ensure_ascii=False, indent=2)
        result["details"].update(event=payload.get("event"), matcher=payload.get("matcher"), handlers=payload.get("handlers", []))
    return result


# //// 组合一次卡片操作所需的来源和目录快照 [@x380kkm 2026-09-07] ////
class CardSubjects:
    def __init__(self, catalogs: Catalogs, codex: CodexInventory, scope: str) -> None:
        self.catalogs, self.codex, self.scope = catalogs, codex, scope
        self.store = catalogs.select(scope)
        self.layer_stores = catalogs.layers()
        self.layer_documents = {}
        for key, store in self.layer_stores.items():
            try:
                self.layer_documents[key] = store.snapshot()
            except StorageError:
                if LAYER_ORDER.index(key) <= LAYER_ORDER.index(scope):
                    raise
                self.layer_documents[key] = []
        self.user = self.layer_documents.get("user", [])
        self.project = self.layer_documents.get("project", [])
        self.private = self.layer_documents.get("project-local", [])
        self.local = self.layer_documents.get(scope, [])
        inputs = {key: values if LAYER_ORDER.index(key) <= LAYER_ORDER.index(scope) else []
                  for key, values in self.layer_documents.items()}
        self.view = catalogs.effective(inputs)
        self.inventory = codex.snapshot(self.user)
        self.items = {item["id"]: item for item in self.inventory["items"]}
        added = 0
        bindings = [document for document in self.view.documents if document["kind"] == "PluginBinding"]
        for document in self.view.documents:
            if document["kind"] != "Plugin":
                continue
            item = declared_item(document, self.view.origins.get(document_identity(document), []), bindings)
            if item is not None and item["id"] not in self.items:
                self.items[item["id"]] = item
                added += 1
        if added:
            self.inventory["items"] = list(self.items.values())
            self.inventory["groups"] = group_inventory(self.inventory["items"], self.inventory["edges"], codex.root, codex.user_root)
            self.inventory["coverage"]["catalogCards"] = added
        self.context = {"user": getpass.getuser(), "host": "codex", **catalogs.context(scope)}
        self._definitions: dict[str, dict] = {}

    # //// 选择能够独立控制的来源卡片 [@x380kkm 2026-09-07] ////
    def item(self, identity: str) -> dict:
        item = self.items.get(identity)
        if item is None:
            raise CardError("card_missing", "卡片来源需要重新读取.")
        declared = item.get("details", {}).get("managerDeclared") is True
        if item["kind"] not in POINTS or (not isinstance(item.get("content"), str) and not (declared and item["kind"] == "skill")):
            raise CardError("card_kind", "请选择具有可读取内容的独立声明.")
        if item["kind"] == "skill" and not item["importable"] and not declared:
            raise CardError("card_source", "Skill 元数据需要有效的名称与说明.")
        return item

    # //// 复用当前范围及其继承层中的来源发布 [@x380kkm 2026-09-08] ////
    def definition(self, identity: str) -> dict:
        if identity in self._definitions:
            return self._definitions[identity]
        item = self.item(identity)
        matches = {}
        for scope, store in self.layer_stores.items():
            if LAYER_ORDER.index(scope) > LAYER_ORDER.index(self.scope):
                continue
            documents = self.layer_documents[scope]
            for document in documents:
                if document["kind"] != "Plugin" or subject_member(document, item, store.workspace) is None:
                    continue
                key = document_identity(document)
                document = resolve_document(document, store, scope, [], project=self.catalogs.project.workspace if self.catalogs.project else None)
                if key in matches and matches[key] != document:
                    raise CardError("card_source_conflict", "用户与项目中的来源定义不同, 请先确认保留的正文.")
                matches[key] = document
        if len(matches) > 1:
            raise CardError("card_source_ambiguous", "同一来源对应多份发布, 请在内容库中确认使用入口.")
        document = next(iter(matches.values()), None) or source_document(item)
        self._definitions[identity] = document
        return document

    # //// 返回用户与关系编辑器共用的卡片身份 [@x380kkm 2026-09-07] ////
    def subject(self, identity: str) -> dict:
        item, document = self.item(identity), self.definition(identity)
        member = subject_member(document, item, self.store.workspace)
        if member is None:
            raise CardError("card_source_member", "来源定义需要唯一的内容入口.")
        return {"id": identity, "name": item["name"], "kind": item["kind"], "path": item["path"],
                "line": item.get("line", 1), "sourceScope": item["scope"],
                "availability": item.get("availability", "local-observed"),
                "ref": document["id"] + "#" + member["id"], "version": document["release"]["version"]}

    # //// 找到同一发布在选定目录中的原始定义 [@x380kkm 2026-09-07] ////
    def local_definition(self, document: dict) -> dict | None:
        return next((value for value in self.local if document_identity(value) == document_identity(document)), None)

    # //// 收集一次写入需要补齐的来源定义 [@x380kkm 2026-09-07] ////
    def source_plans(self, identities: list[str]) -> list[dict]:
        plans = {}
        user_ids = {document_identity(document) for document in self.user}
        for identity in identities:
            document = self.definition(identity)
            key = document_identity(document)
            previous = self.local_definition(document)
            if previous is not None:
                plans[key] = self.store.preview_put(previous, previous)
            elif self.scope == "user" or key not in user_ids:
                plans[key] = self.store.preview_put(deepcopy(document))
        return list(plans.values())
