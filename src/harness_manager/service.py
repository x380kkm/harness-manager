# audience: internal
# # harness-service
"""桌面与外部接口调用同一静态管理用例. 读取授权来自进程入口, 绑定范围只参与内容选择."""
from __future__ import annotations

import getpass
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from .agent_interface import AgentInterface
from .catalogs import Catalogs, resolve_document
from .card_operations import CardOperations, card_inventory
from .card_subjects import CardSubjects
from .card_collections import collections_from_view, validate_shared_collections
from .codex_inventory import CodexInventory
from .companion_contexts import CompanionContexts
from .content import read_content_unit
from .content_snapshots import ContentSnapshots
from .declarations import index_declarations
from .graph import build_graph
from .importers import import_document
from .observations import ObservationStore
from .projection import discover, project_content
from .read_statistics import ReadStatistics
from .module_inventory import modules_from_view
from .inventory_groups import group_inventory
from .host_control import HostControl
from .module_operations import ModuleOperations
from .project_relocation import ProjectRelocation
from .protocol import document_identity, document_name, schema
from .rule_editing import describe_rule
from .sources import SourceReader
from .storage_errors import StorageConflictError, StorageError
from .usage import describe_usage, find_plugin, usage_document


# //// 对外管理错误保存结构化诊断 [@x380kkm 2026-09-06] ////
class ServiceError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code, self.details = code, details


# //// 校验接口的文本参数 [@x380kkm 2026-09-06] ////
def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ServiceError("invalid_params", f"{field} 参数需要文本.")
    return value


# //// 组合静态存储, 来源读取与声明查询 [@x380kkm 2026-09-06] ////
class Manager:
    def __init__(self, workspace: Path | None = None, read_roots: list[Path] | None = None,
                 *, user_root: Path | None = None) -> None:
        self.catalogs = Catalogs(user_root if user_root is not None else Path.home(), workspace)
        self.store = self.catalogs.user
        self.workspace = self.catalogs.project.workspace if self.catalogs.project is not None else None
        self.reader = SourceReader(self.workspace or Path.cwd(), read_roots or [])
        self.observations = ObservationStore(self.store.workspace)
        self.codex = CodexInventory(self.store.workspace)
        self.contexts = CompanionContexts(self.catalogs)
        self.cards = CardOperations(self.catalogs, self.codex)
        self.statistics = ReadStatistics(self.store.workspace)
        self.host = HostControl(self.catalogs, self.codex, self.reader)
        self.modules = ModuleOperations(self.catalogs, self.codex)
        self.relocation = ProjectRelocation(self.catalogs)
        self.agent = AgentInterface(self)

    # //// 从固定宿主位置盘点当前用户内容 [@x380kkm 2026-09-06] ////
    def snapshot_codex(self) -> dict:
        try:
            documents = self.store.snapshot()
        except StorageError:
            result = self.codex.snapshot([])
            result["diagnostics"].append({"code": "catalog-unavailable", "path": str(self.store.catalog),
                                          "message": "用户内容库读取失败, 登记匹配结果需要重新确认."})
            return result
        return self.codex.snapshot(documents)

    # //// 组合卡片使用状态与有明确采集范围的统计 [@x380kkm 2026-09-07] ////
    def snapshot_cards(self, scope: str = "user", refresh: bool = False) -> dict:
        if type(refresh) is not bool:
            raise ServiceError("invalid_params", "refresh 需要布尔值.")
        if refresh:
            self.codex.invalidate()
        frame = CardSubjects(self.catalogs, self.codex, scope)
        result = card_inventory(frame)
        modules, edges, notes = modules_from_view(frame.view, scope, result["items"], frame.store.catalog)
        local_bindings = {document["plugin"]["id"]: document for document in frame.local
                          if document["kind"] == "PluginBinding" and document["id"] == f"binding:{scope}/{document['plugin']['id']}"}
        for module in modules:
            binding = local_bindings.get(module["details"]["pluginId"])
            module["details"]["configuredState"] = "inherit" if binding is None or "enabled" not in binding else "enabled" if binding["enabled"] else "disabled"
        view = frame.view
        projected, _ = project_content(view.documents, self.content_context(None, scope), layers=view.layers)
        module_names = {module["details"]["pluginId"]: module["name"] for module in modules}
        used_by = {}
        for entry in projected:
            owner = entry.summary["plugin"]
            if owner in module_names:
                used_by.setdefault(entry.summary["content"], {})[owner] = module_names[owner]
        for managed in result["cardManagement"].values():
            uses = used_by.get(managed.get("ref"), {})
            uses = {key: value for key, value in uses.items() if key != managed.get("pluginId")}
            managed["moduleUses"] = [{"id": key, "name": value} for key, value in uses.items()]
            if uses:
                managed["effectiveEnabled"] = True
        result["items"].extend(modules)
        result["edges"].extend(edges)
        result["diagnostics"].extend(notes)
        if modules:
            result["groups"] = group_inventory(result["items"], result["edges"], self.codex.root, self.codex.user_root)
        result["collections"], collection_diagnostics = collections_from_view(view, scope, result["items"])
        result["diagnostics"].extend(collection_diagnostics)
        result["workspace"] = str(self.workspace) if self.workspace else None
        result["projectLocations"] = {key: str(store.catalog) for key, store in self.catalogs.layers().items() if key != "user"}
        try:
            result["hostControl"] = self.host.status(scope)
        except StorageError as error:
            result["hostControl"] = {"scope": scope, "available": False, "reason": str(error)}
        for relation in result.get("managerRelations", []):
            if relation["enabled"]:
                result["edges"].append({"from": relation["from"], "to": relation["to"], "label": "参考",
                                        "relation": "adapter-reference", "scope": relation["scope"], "text": relation["text"]})
        try:
            result["statistics"] = self.statistics.summary()
        except (sqlite3.Error, OSError, ValueError, StorageError):
            result["statistics"] = None
            result["diagnostics"].append({"code": "statistics-unavailable", "message": "读取统计当前无法访问."})
        return result

    # //// 为外部 Agent 返回有界本机内容摘要 [@x380kkm 2026-09-06] ////
    def list_codex(self, query: str = "", kind: str = "", limit: int = 20, cursor: int = 0) -> dict:
        require_text(query, "query")
        require_text(kind, "kind")
        if type(limit) is not int or type(cursor) is not int or not 1 <= limit <= 100 or cursor < 0:
            raise ServiceError("invalid_params", "limit 需要在 1 至 100 之间, cursor 需要非负整数.")
        snapshot = self.snapshot_codex()
        candidates = [item for item in snapshot["items"] if (not kind or item["kind"] == kind)
                      and query.casefold() in " ".join(str(item.get(key, "")) for key in ("name", "summary", "path")).casefold()]
        end = cursor + limit
        return {"root": snapshot["root"], "scannedAt": snapshot["scannedAt"], "total": len(candidates),
                "items": [{key: value for key, value in item.items() if key != "content"} for item in candidates[cursor:end]],
                "nextCursor": end if end < len(candidates) else None, "diagnostics": snapshot["diagnostics"]}

    # //// 按来源类别查询有界内容组 [@x380kkm 2026-09-07] ////
    def list_codex_groups(self, query: str = "", origin: str = "user", limit: int = 20, cursor: int = 0) -> dict:
        require_text(query, "query")
        require_text(origin, "origin")
        if origin not in {"user", "official", ""}:
            raise ServiceError("invalid_params", "origin 需要 user, official 或空文本.")
        if type(limit) is not int or type(cursor) is not int or not 1 <= limit <= 100 or cursor < 0:
            raise ServiceError("invalid_params", "limit 需要在 1 至 100 之间, cursor 需要非负整数.")
        snapshot = self.snapshot_codex()
        items = {item["id"]: item for item in snapshot["items"]}
        terms = query.casefold().split()
        candidates = []
        for group in snapshot["groups"]:
            if origin and group["origin"] != origin:
                continue
            searchable = " ".join([group["name"], *(str(items[identity].get(key, "")) for identity in group["itemIds"] for key in ("name", "path", "summary"))]).casefold()
            if all(term in searchable for term in terms):
                candidates.append(group)
        end = cursor + limit
        return {"groups": candidates[cursor:end], "total": len(candidates), "nextCursor": end if end < len(candidates) else None,
                "scannedAt": snapshot["scannedAt"], "diagnostics": snapshot["diagnostics"]}

    # //// 按观察身份读取本机内容及直接关联 [@x380kkm 2026-09-06] ////
    def read_codex(self, id: str) -> dict:
        require_text(id, "id")
        snapshot = self.snapshot_codex()
        item = next((item for item in snapshot["items"] if item["id"] == id), None)
        if item is None:
            raise ServiceError("not_found", "本机盘点中没有该内容, 请重新读取列表.")
        return {"item": item, "edges": [edge for edge in snapshot["edges"] if id in (edge["from"], edge["to"])],
                "scannedAt": snapshot["scannedAt"]}

    # //// 将完整本机载体准备为用户登记草稿 [@x380kkm 2026-09-06] ////
    def import_codex(self, id: str) -> dict:
        document = self.codex.prepare_import(require_text(id, "id"))
        return self._import_result(document, "user")

    # //// 从同一目录快照生成列表与关系图 [@x380kkm 2026-09-06] ////
    def snapshot_catalog(self, scope: str = "user") -> dict[str, Any]:
        store = self.catalogs.select(scope)
        documents = store.snapshot()
        records = []
        for document in documents:
            metadata = document.get("metadata", {})
            records.append({
                "id": document_identity(document), "kind": document["kind"],
                "name": document_name(document), "summary": metadata.get("description", ""),
                "version": document.get("release", {}).get("version", document.get("contract", {}).get("version", "")),
                "target": document.get("target", {}).get("selector", {}), "path": str(store.catalog), "scope": scope,
            })
        if scope != "user":
            view = self.catalogs.effective({scope: documents, **({"project-local": []} if scope == "project" else {})})
            graph = build_graph(view.documents)
            graph["diagnostics"] = [*view.diagnostics, *graph["diagnostics"]]
            for node in graph["nodes"]:
                origins = view.origins.get(node.get("documentId"), [])
                if origins and all(origin["scope"] != scope for origin in origins):
                    node.update(inherited=True, scope=origins[-1]["scope"], inheritedDocumentId=node["documentId"], documentId=None)
        else:
            graph = build_graph(documents)
        return {"workspace": str(self.workspace) if self.workspace else None, "catalog": str(store.catalog),
                "scope": scope, "documents": records,
                "graph": graph, "diagnostics": graph["diagnostics"]}

    # //// 从当前磁盘目录提供筛选后的简短元数据 [@x380kkm 2026-09-06] ////
    def list_documents(self, query: str = "", kind: str = "", scope: str = "user", limit: int = 20, cursor: int = 0) -> dict[str, Any]:
        require_text(query, "query")
        require_text(kind, "kind")
        if type(limit) is not int or type(cursor) is not int or not 1 <= limit <= 100 or cursor < 0:
            raise ServiceError("invalid_params", "limit 需要在 1 至 100 之间, cursor 需要非负整数.")
        result = self.snapshot_catalog(scope)
        del result["graph"]
        result["documents"] = [record for record in result["documents"]
                               if (not kind or record["kind"] == kind)
                               and query.casefold() in json.dumps(record, ensure_ascii=False).casefold()]
        total = len(result["documents"])
        result["documents"] = result["documents"][cursor:cursor + limit]
        result.update(total=total, nextCursor=cursor + limit if cursor + limit < total else None)
        return result

    # //// 按存储身份读取声明与编辑基线 [@x380kkm 2026-09-06] ////
    def read_document(self, id: str, scope: str = "user") -> dict[str, Any]:
        for document in self.catalogs.select(scope).snapshot():
            if document_identity(document) == id:
                return {"document": document, "baseline": document, "scope": scope}
        raise ServiceError("not_found", f"目录中没有这个声明: {id}")

    # //// 读取规则的片段编辑字段与声明基线 [@x380kkm 2026-09-08] ////
    def describe_rule(self, id: str, ref: str, scope: str = "user") -> dict:
        record = self.read_document(id, scope)
        document = resolve_document(record["document"], self.catalogs.select(scope), scope, [], project=self.workspace)
        return {**record, **describe_rule(document, ref, self.host, scope)}

    # //// 预览声明编辑并附带引用诊断 [@x380kkm 2026-09-06] ////
    def preview_document(self, document: dict, baseline: dict | None = None, scope: str = "user") -> dict:
        store = self.catalogs.select(scope)
        try:
            plan = store.preview_put(document, baseline)
        except StorageConflictError as error:
            error.details["scope"] = scope
            raise
        before = store.snapshot()
        others = [item for item in before if document_identity(item) != plan["id"]]
        if scope == "project":
            validate_shared_collections([*others, document])
        return {"plan": {**plan, "scope": scope, "catalog": str(store.catalog)},
                "diagnostics": self.catalogs.edit_diagnostics(scope, before, [*others, document])}

    # //// 预览登记移除并保留来源内容 [@x380kkm 2026-09-06] ////
    def preview_remove(self, id: str, baseline: dict, scope: str = "user") -> dict:
        store = self.catalogs.select(scope)
        try:
            plan = store.preview_remove(id, baseline)
        except StorageConflictError as error:
            error.details["scope"] = scope
            raise
        before = store.snapshot()
        others = [item for item in before if document_identity(item) != id]
        if scope == "project":
            validate_shared_collections(others)
        return {"plan": {**plan, "scope": scope, "catalog": str(store.catalog)},
                "diagnostics": self.catalogs.edit_diagnostics(scope, before, others)}

    # //// 在计划指定的目录中提交变化 [@x380kkm 2026-09-06] ////
    def apply_document(self, plan: dict) -> dict:
        if not isinstance(plan, dict) or "scope" not in plan:
            raise ServiceError("invalid_plan", "提交计划需要固定的目录范围.")
        scope = plan["scope"]
        store = self.catalogs.select(scope)
        if plan.get("catalog") != str(store.catalog):
            raise ServiceError("catalog_changed", "当前目录与计划中的写入位置不同, 请重新预览.")
        content = {key: value for key, value in plan.items() if key not in {"scope", "catalog"}}

        # //// 核对提交后的项目共享引用 [@x380kkm 2026-09-07] ////
        def validate_shared_edit(documents: list[dict]) -> None:
            if scope == "project":
                candidate = [document for document in documents if document_identity(document) != content["id"]]
                if content["after"] is not None:
                    candidate.append(content["after"])
                validate_shared_collections(candidate)

        try:
            result = store.apply_many([content], verify_current=validate_shared_edit)[0]
            return {**result, "scope": scope, "catalog": str(store.catalog)}
        except StorageConflictError as error:
            error.details["scope"] = scope
            raise

    # //// 从真实来源准备声明并报告重复身份 [@x380kkm 2026-09-06] ////
    def import_file(self, path: str, kind: str | None = None, scope: str = "user") -> dict:
        document = import_document(self.reader, path, kind)
        return self._import_result(document, scope)

    # //// 返回来源草稿与已有登记身份诊断 [@x380kkm 2026-09-06] ////
    def _import_result(self, document: dict, scope: str) -> dict:
        store = self.catalogs.select(scope)
        identity = document_identity(document)
        diagnostics = []
        if any(document_identity(item) == identity for item in store.snapshot()):
            diagnostics.append({"code": "existing_identity", "subject": identity,
                                "message": "该身份已经登记. 请编辑已有声明, 或为独立来源设置不同身份."})
        return {"document": document, "baseline": None, "scope": scope, "diagnostics": diagnostics}

    # //// 解释用户默认与当前项目声明的有效组合 [@x380kkm 2026-09-06] ////
    def effective_catalog(self) -> dict:
        view = self.catalogs.effective()
        graph = build_graph(view.documents)
        return {"documents": view.documents, "origins": view.origins,
                "diagnostics": [*view.diagnostics, *graph["diagnostics"]], "graph": graph}

    # //// 取得用户默认与显式项目的查询上下文 [@x380kkm 2026-09-06] ////
    def content_context(self, context: dict | None, scope: str | None = None) -> dict:
        if context is not None and not isinstance(context, dict):
            raise ServiceError("invalid_context", "使用上下文需要 JSON 对象.")
        target = {"user": getpass.getuser()}
        if self.workspace is not None and scope != "user":
            target.update(self.catalogs.context(scope or "project-local"))
        target.update(context or {})
        return target

    # //// 按上下文和内容类型返回候选摘要或来源详情 [@x380kkm 2026-09-08] ////
    def discover_content(self, context: dict | None = None, query: str = "", limit: int = 20, cursor: int = 0,
                         scope: str | None = None, point: str = "", detail: Literal["summary", "full"] = "summary") -> dict:
        require_text(query, "query")
        require_text(point, "point")
        if detail not in ("summary", "full"):
            raise ServiceError("invalid_params", "detail 需要 summary 或 full.")
        target = self.content_context(context, scope)
        view = self.catalogs.for_scope(scope)
        result = discover(view.documents, target, query, limit, cursor, layers=view.layers, point=point)
        result["diagnostics"] = [*view.diagnostics, *result["diagnostics"]]
        if detail == "summary":
            fields = ("ref", "name", "description", "point", "version")
            result["candidates"] = [{key: candidate[key] for key in fields} for candidate in result["candidates"]]
        for candidate in result["candidates"]:
            params = {"ref": candidate["ref"], "version": candidate["version"]}
            method = "content.read"
            if candidate["point"] == "skill.x380kkm/deployment":
                method = "content.open"
                params["context"] = target
            if scope is not None:
                params["scope"] = scope
            candidate["read"] = {"method": method, "params": params}
        return result

    # //// 直接列出当前范围可使用的 Skill 摘要 [@x380kkm 2026-09-09] ////
    def list_skills(self, context: dict | None = None, query: str = "", limit: int = 20, cursor: int = 0,
                    scope: str = "user") -> dict:
        return self.discover_content(context or {"host": "harness-manager"}, query, limit, cursor, scope,
                                     point="skill.x380kkm/deployment", detail="summary")

    # //// 取得方法与有效配套内容的完整读取快照 [@x380kkm 2026-09-06] ////
    def open_content(self, ref: str, version: str | None = None, context: dict | None = None,
                     budget: int = 65536, resources: list[str] | None = None, scope: str | None = None) -> dict:
        result = self._open_content(ref, version, context, budget, resources, scope, "agent")
        return self._record_complete_read(result)

    # //// 为面板采集独立于使用统计的读取预览 [@x380kkm 2026-09-07] ////
    def preview_content(self, ref: str, version: str | None = None, context: dict | None = None,
                        budget: int = 65536, resources: list[str] | None = None, scope: str | None = None) -> dict:
        return self._open_content(ref, version, context, budget, resources, scope, "preview")

    # //// 在同一内容协议下固定读取用途 [@x380kkm 2026-09-07] ////
    def _open_content(self, ref: str, version: str | None, context: dict | None, budget: int,
                      resources: list[str] | None, scope: str | None, consumer: str) -> dict:
        require_text(ref, "ref")
        if version is not None:
            require_text(version, "version")
        reader = ContentSnapshots(self.observations, self.reader)
        return reader.open(self.catalogs.for_scope(scope), self.content_context(context, scope), ref, version, budget,
                           [] if resources is None else resources, consumer)

    # //// 在原读取快照中继续返回必需单元 [@x380kkm 2026-09-06] ////
    def continue_content(self, continuation: str, budget: int = 65536) -> dict:
        require_text(continuation, "continuation")
        return self._record_complete_read(ContentSnapshots(self.observations, self.reader).continue_read(continuation, budget))

    # //// 对完整 Agent 读取按快照去重记录统计 [@x380kkm 2026-09-07] ////
    def _record_complete_read(self, result: dict) -> dict:
        if result.get("readiness") != "ready":
            return result
        record = self.observations.read(result["snapshot"])
        if record.get("consumer") != "agent":
            return result
        try:
            self.statistics.record(result, record["snapshot"])
        except (sqlite3.Error, OSError, ValueError, StorageError):
            result.setdefault("diagnostics", []).append({"code": "statistics-unavailable", "message": "内容读取完成, 统计记录无法保存.",
                                                          "subject": result["snapshot"], "severity": "warning"})
        return result

    # //// 返回有明确时间窗口和采集范围的读取统计 [@x380kkm 2026-09-07] ////
    def read_statistics(self, days: int = 7) -> dict:
        return self.statistics.summary(days)

    # //// 读取内容快照固定的配置与来源描述 [@x380kkm 2026-09-06] ////
    def content_snapshot(self, id: str) -> dict:
        require_text(id, "id")
        return ContentSnapshots(self.observations, self.reader).inspect(id)

    # //// 从唯一的内容引用取得完整语义单元 [@x380kkm 2026-09-06] ////
    def read_content(self, ref: str, version: str | None = None, resource: str | None = None, budget: int = 65536,
                     expected_revision: str | None = None, scope: str | None = None) -> dict:
        require_text(ref, "ref")
        if version is not None:
            require_text(version, "version")
        if resource is not None:
            require_text(resource, "resource")
        if expected_revision is not None:
            require_text(expected_revision, "expected_revision")
        if not isinstance(budget, int) or isinstance(budget, bool) or not 1 <= budget <= 1024 * 1024:
            raise ServiceError("invalid_budget", "读取预算需要 1 至 1048576 之间的字节数.")
        view = self.catalogs.for_scope(scope)
        index = index_declarations(view.documents)
        index.diagnostics.extend(view.diagnostics)
        return read_content_unit(index, self.reader, ref, version, resource, budget, expected_revision)

    # //// 描述当前发布的使用方式与独立绑定 [@x380kkm 2026-09-06] ////
    def describe_usage(self, plugin: str, scope: str = "user", context: dict | None = None) -> dict:
        require_text(plugin, "plugin")
        store = self.catalogs.select(scope)
        result = describe_usage(self.catalogs.for_scope(scope), store.snapshot(), plugin, scope, self.content_context(context, scope))
        return {**result, "catalog": str(store.catalog)}

    # //// 预览使用设置并求值相应的候选变化 [@x380kkm 2026-09-06] ////
    def preview_usage(self, plugin: str, settings: dict, baseline: dict | None = None, scope: str = "user") -> dict:
        require_text(plugin, "plugin")
        selected = find_plugin(self.catalogs.for_scope(scope), plugin)
        document = usage_document(selected, settings, baseline, scope)
        try:
            result = self.preview_document(document, baseline, scope)
        except StorageConflictError as error:
            error.details["document"] = document
            raise
        documents = [item for item in self.catalogs.select(scope).snapshot() if document_identity(item) != document["id"]]
        inputs = {scope: [*documents, document]}
        if scope == "user":
            inputs["project"] = []
        if scope in {"user", "project"}:
            inputs["project-local"] = []
        view = self.catalogs.effective(inputs)
        evaluation = describe_usage(view, inputs[scope], plugin, scope, self.content_context(None, scope))
        result["diagnostics"] = evaluation["diagnostics"]
        result["effective"] = evaluation["effective"]
        return result

    # //// 提供桌面与外部接口共用的实际方法入口 [@x380kkm 2026-09-08] ////
    def handlers(self) -> dict:
        return {
            "agent.capabilities": self.agent.capabilities, "agent.help": self.agent.help,
            "project.relocate_preview": self.relocation.preview, "project.relocate_apply": self.relocation.apply,
            "host.initialize": self.host.initialize, "host.status": self.host.status, "host.inspect": self.host.inspect, "host.set_enabled": self.host.set_enabled,
            "host.preview": self.host.preview, "host.preview_restore": self.host.preview_restore, "host.apply": self.host.apply,
            "module.describe": self.modules.describe, "module.preview": self.modules.preview, "module.apply": self.modules.apply,
            "card.inventory": self.snapshot_cards, "card.describe": self.cards.describe,
            "card.configure": self.cards.configure, "card.relations": self.cards.relations,
            "card.set_relation": self.cards.set_relation, "card.remove_relation": self.cards.remove_relation,
            "card.set_shared": self.cards.set_shared,
            "statistics.reads": self.read_statistics, "content.preview": self.preview_content,
            "context.describe": self.contexts.describe, "context.preview": self.contexts.preview,
            "context.preview_remove": self.contexts.preview_remove, "context.apply": self.contexts.apply,
            "codex.snapshot": self.snapshot_codex, "codex.list": self.list_codex, "codex.groups": self.list_codex_groups,
            "codex.read": self.read_codex, "codex.import": self.import_codex,
            "catalog.snapshot": self.snapshot_catalog,
            "catalog.list": self.list_documents, "document.read": self.read_document, "rule.describe": self.describe_rule,
            "document.preview": self.preview_document, "document.preview_remove": self.preview_remove,
            "document.apply": self.apply_document, "document.import": self.import_file,
            "catalog.graph": lambda scope="user": self.snapshot_catalog(scope)["graph"],
            "catalog.effective": self.effective_catalog, "protocol.schema": schema,
            "catalog.discover": self.discover_content, "content.read": self.read_content,
            "skill.list": self.list_skills,
            "content.open": self.open_content, "content.continue": self.continue_content,
            "content.snapshot": self.content_snapshot,
            "usage.describe": self.describe_usage, "usage.preview": self.preview_usage,
        }

    # //// 将公开方法映射到固定用例 [@x380kkm 2026-09-08] ////
    def invoke(self, method: str, params: dict | None = None) -> Any:
        handler = self.handlers().get(method)
        if handler is None:
            raise ServiceError("unknown_method", f"未知管理方法: {method}")
        if params is not None and not isinstance(params, dict):
            raise ServiceError("invalid_params", "方法参数需要 JSON 对象.")
        result = handler(**(params or {}))
        mutations = {"card.configure", "card.set_relation", "card.remove_relation", "card.set_shared", "document.apply", "context.apply", "module.apply"}
        if method in mutations:
            arguments = params or {}
            scope = arguments.get("scope", arguments.get("plan", {}).get("scope", result.get("scope", "user")))
            if method == "card.set_shared" or scope == "project":
                scope = "project-local"
            result["hostSync"] = self.host.synchronize_active_scopes(scope)
        return result
