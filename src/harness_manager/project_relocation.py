# audience: internal
# # project-relocation
"""项目搬移重连个人目录并转换明确来源位置. 各宿主独立选择关系来源, 搬移集合合并这些选择.
旧目录和宿主存档保留, 多目录保存失败按对象基线回滚.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import getpass
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .catalogs import Catalogs, document_locations, external_project_locations, portable_project_document, project_private_store
from .card_relations import relation_payload
from .card_sharing import reference_plugin
from .card_subjects import CardError
from .host_paths import project_host_storage
from .host_profiles import CODEX, HostProfile
from .host_storage import HostStorage, RECOVERY_STATES
from .codex_inventory import CodexInventory
from .project_hook_relocation import relocate_host_hooks
from .projection import project_content, scope_may_match
from .protocol import document_identity
from .sharing_storage import apply_sharing
from .storage import Store
from .storage_errors import StorageConflictError


# //// 保留项目搬移中的明确冲突和输入错误 [@x380kkm 2026-09-08] ////
class RelocationError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.details = code, details or {}


# //// 转换位于旧项目内的路径或项目 URI [@x380kkm 2026-09-08] ////
def relocate_value(value, old: Path, current: Path, source_root: Path | None = None):
    if not isinstance(value, str):
        return value
    if value == old.as_uri() or old.drive and value.casefold() == old.as_uri().casefold():
        return current.as_uri()
    if "://" in value:
        return value
    path = source_root / Path(value).expanduser() if source_root is not None else Path(value)
    if path.is_absolute():
        path = Path(os.path.abspath(path))
    if path.is_absolute() and path.is_relative_to(old):
        return str(current / path.relative_to(old))
    return value


# //// 转换声明中具有路径语义的字段 [@x380kkm 2026-09-08] ////
def relocate_document(document: dict, old: Path, current: Path, *, portable: bool, source_root: Path | None = None) -> dict:
    result = deepcopy(document)
    for carrier, key, kind in document_locations(result):
        value = carrier[key]
        root = source_root if kind == "source" else None
        carrier[key] = [relocate_value(item, old, current, root) for item in value] if isinstance(value, list) else relocate_value(value, old, current, root)
    return portable_project_document(result, current) if portable else result


# //// 取得声明中的发布身份与静态依赖 [@x380kkm 2026-09-08] ////
def referenced_plugins(documents: list[dict]) -> set[str]:
    result = set()
    for document in documents:
        if document["kind"] == "Plugin":
            result.add(document["id"])
        if document["kind"] == "PluginBinding":
            result.add(document["plugin"]["id"])
        relation_keys = ("requirements",) if relation_payload(document) is not None else ("requirements", "relations")
        for carrier in [document, *document.get("contributions", [])]:
            if isinstance(carrier.get("ref"), str):
                result.add(carrier["ref"].partition("#")[0])
            for relation in [value for key in relation_keys for value in carrier.get(key, [])]:
                if isinstance(relation.get("target"), str):
                    result.add(relation["target"].partition("#")[0])
    return result


# //// 按可达关系和绑定身份保留来源选择诊断 [@x380kkm 2026-09-08] ////
def relation_source_diagnostics(documents: list[dict], plugins: set[str], diagnostics: list[dict]) -> list[dict]:
    relations = {document["id"] for document in documents
                 if (payload := relation_payload(document)) is not None
                 and (document["id"] in plugins or reference_plugin(payload.get("skill", payload.get("subject", ""))) in plugins)}
    bindings = {document["id"] for document in documents
                if document["kind"] == "PluginBinding" and document["plugin"]["id"] in relations}
    return [note for note in diagnostics if note["subject"] in bindings
            or any(note["subject"] == plugin or note["subject"].startswith((plugin + "#", plugin + "/")) for plugin in relations)]


# //// 汇集内容读取和原生投放及声明指定的宿主 [@x380kkm 2026-09-10] ////
def relocation_hosts(documents: list[dict]) -> list[str]:
    hosts = {"codex", "harness-manager"}
    for document in documents:
        scopes = [document.get("target"), *(member.get("scope") for member in document.get("contributions", []))]
        for scope in scopes:
            selected = (scope or {}).get("selector", {}).get("host", [])
            hosts.update(value for value in (selected if isinstance(selected, list) else [selected])
                         if isinstance(value, str) and value and value != "current")
    return sorted(hosts)


# //// 沿同一宿主选定的参考关系汇集来源身份 [@x380kkm 2026-09-10] ////
def host_source_plugins(view, documents: dict[str, list[dict]], context: dict) -> set[str]:
    entries, diagnostics = project_content(view.documents, context, layers=view.layers)
    relations = [(entry.owner["id"], payload) for entry in entries if (payload := relation_payload(entry.owner)) is not None]
    identities = {document_identity(value) for scope in ("project", "project-local") for value in documents[scope]}
    location = {key: value for key, value in context.items() if key != "host"}
    bindings = [value for value in view.documents if value["kind"] == "PluginBinding" and scope_may_match(value["target"], location)]
    bound = {value["plugin"]["id"] for value in bindings}
    applicable = {value["plugin"]["id"] for value in bindings if scope_may_match(value["target"], context)}
    roots = [value for value in view.documents if document_identity(value) in identities
             and (value["kind"] != "PluginBinding" or scope_may_match(value["target"], context))
             and (value["kind"] != "Plugin" or value["id"] not in bound or value["id"] in applicable)]
    linked = referenced_plugins(roots)
    while True:
        related = [value for value in documents["user"]
                   if value.get("id") in linked or value.get("plugin", {}).get("id") in linked]
        discovered = linked | referenced_plugins(related)
        for plugin, payload in relations:
            if reference_plugin(payload.get("skill", payload.get("subject", ""))) in linked:
                discovered.update((plugin, reference_plugin(payload["adapter"].get("targetRef", ""))))
        if discovered == linked:
            unresolved = relation_source_diagnostics(view.documents, linked, diagnostics)
            if unresolved:
                raise RelocationError("relocation_conflict", "项目参考关系的来源选择需要明确的范围与条件.", {"diagnostics": unresolved})
            return linked
        linked = discovered


# //// 合并各宿主独立闭合的项目来源 [@x380kkm 2026-09-10] ////
def project_source_plugins(catalogs: Catalogs, documents: dict[str, list[dict]]) -> set[str]:
    view = catalogs.effective(documents)
    context = {"user": getpass.getuser(), **catalogs.context("project-local")}
    linked = set()
    for host in relocation_hosts(view.documents):
        linked.update(host_source_plugins(view, documents, {**context, "host": host}))
    return linked


# //// 将原项目的有效绑定对应到搬移后的保存值 [@x380kkm 2026-09-10] ////
def relocated_bindings(originals: dict, desired: dict, old_private: list[dict]) -> dict:
    sources = {**originals, "project-local": old_private}
    return {scope: {value["id"]: (value, desired[scope].get(value["id"]))
                    for value in sources[scope] if value["kind"] == "PluginBinding"}
            for scope in ("user", "project", "project-local")}


# //// 根据目标存储的身份规则建立对象集合 [@x380kkm 2026-09-08] ////
def records_by_id(store, documents: list[dict]) -> dict:
    return {store.identity(document): document for document in documents}


# //// 为私密宿主存档生成跨进程冲突基线 [@x380kkm 2026-09-08] ////
def host_records_token(documents: list[dict]) -> str:
    content = json.dumps(sorted(documents, key=lambda value: value["id"]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(content.encode("utf-8")).hexdigest()


# //// 将宿主重连公开为记录身份、上下文和冲突基线 [@x380kkm 2026-09-08] ////
def public_preview(prepared: dict) -> dict:
    result = deepcopy(prepared)
    plan = result["plan"]
    old_records = plan["oldHost"].pop("documents")
    plan["oldHost"]["baselineToken"] = host_records_token(old_records)
    plan["oldHost"]["records"] = [{"id": value["id"], "kind": value["kind"]} for value in old_records]
    plan["baselines"]["host"] = {"baselineToken": host_records_token(plan["baselines"]["host"])}
    for stage in plan["stages"]:
        if stage["scope"] == "host":
            entries = []
            for entry in stage["entries"]:
                before, after = entry["before"] or {}, entry["after"]
                changes = [{"path": "/" + key, "before": before.get(key), "after": after[key]}
                           for key in ("scopeId", "targetRoot", "configSubdir") if before.get(key) != after[key]]
                entries.append({"id": entry["id"], "kind": after["kind"], "changes": changes})
            stage["entries"] = entries
    result["changes"] = [{"scope": stage["scope"], "catalog": stage["catalog"],
                          "documents": [{"id": entry["id"], "changes": entry["changes"]} for entry in stage["entries"]]}
                         for stage in plan["stages"]]
    return result


# //// 公开补偿失败的位置并保持宿主存档正文留在原目录 [@x380kkm 2026-09-08] ////
def relocation_recovery(error: CardError, old_private, old_host, stores) -> RelocationError:
    details = deepcopy(error.details)
    if "stages" in details:
        details["stages"] = [{"scope": "host", "catalog": str(stores["host"].catalog),
                               "recordIds": sorted(stage["after"])} if stage["scope"] == "host" else stage
                              for stage in details["stages"]]
    details["preserved"] = [str(old_private.catalog), str(old_host.store.catalog)]
    return RelocationError("relocation_recovery", "配置重连遇到协作变化, 旧目录与未恢复内容保留供确认.", details)


# //// 按项目位置生成宿主存档入口 [@x380kkm 2026-09-08] ////
def project_host(user_root: Path, project: Path, profile: HostProfile = CODEX) -> HostStorage:
    return project_host_storage(user_root, project, CodexInventory(user_root).root, profile)


# //// 从宿主存档恢复旧 Windows 目录的原始拼写 [@x380kkm 2026-09-08] ////
def previous_project_host(user: Store, old: Path, profile: HostProfile = CODEX) -> HostStorage:
    host = project_host(user.workspace, old, profile)
    if not old.drive or host.store.catalog.exists():
        return host
    matches = []
    for directory in sorted((user.directory / "hosts").glob(f"{profile.id}-*")):
        catalog = Store(user.workspace, document_identity, lambda value: None, catalog_directory=directory)
        locations = {value["targetRoot"] for value in catalog.snapshot()
                     if value.get("configSubdir") == profile.config_subdir and isinstance(value.get("targetRoot"), str)
                     and Path(value["targetRoot"]) == old}
        for location in locations:
            candidate = project_host(user.workspace, Path(location), profile)
            if candidate.store.catalog != catalog.catalog:
                raise RelocationError("relocation_host_conflict", "旧位置宿主存档的目录身份与记录位置需要保持一致.")
            matches.append(candidate)
    if len(matches) > 1:
        raise RelocationError("relocation_host_conflict", "旧位置对应多个宿主存档, 请先确认保留的记录.")
    return matches[0] if matches else host


# //// 提供固定目录集合供跨目录保存使用 [@x380kkm 2026-09-08] ////
class RelocationStores:
    def __init__(self, stores: dict) -> None:
        self.stores = stores

    def select(self, scope: str):
        return self.stores[scope]


# //// 预览和提交用户明确选择的项目位置重连 [@x380kkm 2026-09-08] ////
class ProjectRelocation:
    def __init__(self, catalogs: Catalogs) -> None:
        self.catalogs = catalogs

    # //// 固定旧项目位置与当前项目配置存储 [@x380kkm 2026-09-08] ////
    def _context(self, old_workspace: str):
        if self.catalogs.project is None:
            raise RelocationError("relocation_project", "项目搬移需要先选择当前项目目录.")
        if not isinstance(old_workspace, str) or not Path(old_workspace).is_absolute():
            raise RelocationError("relocation_path", "旧项目位置需要明确的绝对路径.")
        declared_old = Path(os.path.abspath(old_workspace))
        old, current = declared_old.resolve(), self.catalogs.project.workspace
        if old != declared_old:
            raise RelocationError("relocation_path", "旧项目位置需要其原始实际路径, 请确认目录链接对应的位置.")
        if (old == Path(old.anchor) or old == self.catalogs.user.workspace or old == current
                or old.is_relative_to(current) or current.is_relative_to(old)):
            raise RelocationError("relocation_path", "旧项目与当前项目需要两个独立的项目目录.")
        old_host = previous_project_host(self.catalogs.user, old)
        old = old_host.target_root
        old_private = project_private_store(self.catalogs.user, old)
        current_host = project_host(self.catalogs.user.workspace, current)
        stores = {**self.catalogs.layers(), "host": current_host.store}
        return old, current, old_private, old_host, current_host, stores

    # //// 公开来源选择和经过私密存档隔离的对象差异 [@x380kkm 2026-09-08] ////
    def preview(self, old_workspace: str, source_ids: list[str] | None = None) -> dict:
        return public_preview(self._prepare(old_workspace, source_ids))

    # //// 生成旧位置来源和目标目录的完整对象差异 [@x380kkm 2026-09-08] ////
    def _prepare(self, old_workspace: str, source_ids: list[str] | None) -> dict:
        old, current, old_private, old_host, current_host, stores = self._context(old_workspace)
        originals = {scope: store.snapshot() for scope, store in stores.items()}
        old_documents, host_documents = old_private.snapshot(), old_host.store.snapshot()
        if any(record.get("status") in RECOVERY_STATES for record in [*host_documents, *originals["host"]]):
            raise RelocationError("relocation_recovery", "项目宿主存在未完成的应用事务, 请先恢复对应位置的备份.")
        desired = {scope: records_by_id(store, originals[scope]) for scope, store in stores.items()}
        for scope in ("project", "project-local"):
            desired[scope] = {identity: relocate_document(value, old, current, portable=True)
                              for identity, value in desired[scope].items()}
        for value in old_documents:
            updated = relocate_document(value, old, current, portable=True)
            identity = document_identity(updated)
            if identity in desired["project-local"] and desired["project-local"][identity] != updated:
                raise RelocationError("relocation_conflict", "新旧个人目录包含不同的同名声明, 请先选择要保留的内容.", {"documentId": identity})
            desired["project-local"][identity] = updated
        source_documents = {scope: list(desired[scope].values()) for scope in self.catalogs.layers()}
        relocated_user = {identity: relocate_document(value, old, current, portable=False, source_root=self.catalogs.user.workspace)
                          for identity, value in desired["user"].items()}
        source_documents["user"] = list(relocated_user.values())
        linked = project_source_plugins(self.catalogs, source_documents)
        candidates = {document_identity(value): value for value in originals["user"]
                      if (value.get("id") in linked or value.get("plugin", {}).get("id") in linked)
                      and relocated_user[document_identity(value)] != value}
        if source_ids is None:
            selected = sorted(candidates)
        elif (not isinstance(source_ids, list) or any(not isinstance(value, str) for value in source_ids)
              or len(set(source_ids)) != len(source_ids) or set(source_ids) - candidates.keys()):
            raise RelocationError("relocation_sources", "用户来源选择需要使用预览返回的唯一 documentId.")
        else:
            selected = sorted(source_ids)
        for identity in selected:
            desired["user"][identity] = relocated_user[identity]
        binding_moves = relocated_bindings(originals, desired, old_documents)
        for value in host_documents:
            updated = {**deepcopy(value), **current_host.context}
            if "hookStateRoot" not in value:
                updated.pop("hookStateRoot", None)
            updated = relocate_host_hooks(updated, old, current, binding_moves)
            identity = value["id"]
            if identity in desired["host"] and desired["host"][identity] != updated:
                raise RelocationError("relocation_host_conflict", "当前位置已有不同的宿主恢复记录, 请先确认保留的记录.", {"recordId": identity})
            desired["host"][identity] = updated
        effective = self.catalogs.effective({scope: list(desired[scope].values()) for scope in self.catalogs.layers()})
        conflicts = [note for note in effective.diagnostics if note["code"] in {"catalog_identity_conflict", "project_scope_mismatch"}]
        if conflicts:
            raise RelocationError("relocation_conflict", "搬移后的声明范围或同名来源仍有冲突, 请调整来源选择.", {"diagnostics": conflicts})
        stages = []
        for scope in ("project-local", "user", "project", "host"):
            before = records_by_id(stores[scope], originals[scope])
            entries = [stores[scope].preview_put(value, before.get(identity)) for identity, value in desired[scope].items()
                       if before.get(identity) != value]
            if entries:
                stages.append({"scope": scope, "catalog": str(stores[scope].catalog), "entries": entries})
        plan = {"oldWorkspace": str(old), "workspace": str(current), "userRoot": str(self.catalogs.user.workspace),
                "sourceIds": selected, "oldPrivate": {"catalog": str(old_private.catalog), "documents": old_documents},
                "oldHost": {"catalog": str(old_host.store.catalog), "documents": host_documents},
                "baselines": originals, "stages": stages}
        involved = [value for value in effective.documents
                    if value.get("id") in linked or value.get("plugin", {}).get("id") in linked]
        external = external_project_locations(involved, current)
        warnings = ["搬移保存配置定位和宿主记录; 宿主文件的后续变化通过 host.preview 与 host.apply 确认."]
        if external["source"]:
            warnings.append("配置仍引用项目外的来源, 读取前需要提供对应文件与来源授权.")
        if external["scope"]:
            warnings.append("配置仍含项目外的固定范围, 请确认它们对应的用户或项目位置.")
        if external["receipt"]:
            warnings.append("配置仍保留项目外的原文位置, 规则接管需要核对这些来源.")
        return {"plan": plan, "sourceCandidates": [{"documentId": identity, "name": value.get("metadata", {}).get("name", identity),
                                                      "selected": identity in selected} for identity, value in candidates.items()],
                "changes": [{"scope": stage["scope"], "catalog": stage["catalog"],
                             "documents": [{"id": entry["id"], "changes": entry["changes"]} for entry in stage["entries"]]} for stage in stages],
                "hostApplyRequired": ["project-local", *(["user"] if selected else [])],
                "externalLocations": external, "warnings": warnings}

    # //// 在来源锁内核对多目录差异并补偿失败写入 [@x380kkm 2026-09-08] ////
    def apply(self, plan: dict) -> dict:
        keys = {"oldWorkspace", "workspace", "userRoot", "sourceIds", "oldPrivate", "oldHost", "baselines", "stages"}
        if not isinstance(plan, dict) or set(plan) != keys:
            raise RelocationError("relocation_plan", "项目搬移需要完整预览计划.")
        old, current, old_private, old_host, _, stores = self._context(plan["oldWorkspace"])
        if plan["workspace"] != str(current) or plan["userRoot"] != str(self.catalogs.user.workspace):
            raise RelocationError("relocation_context", "搬移计划属于其他用户或项目位置.")
        with ExitStack() as stack:
            for source in sorted((old_private, old_host.store), key=lambda store: str(store.catalog)):
                stack.enter_context(source._locked_catalog())
            prepared = self._prepare(plan["oldWorkspace"], plan["sourceIds"])
            if public_preview(prepared)["plan"] != plan:
                raise StorageConflictError("project-relocation")
            plan = prepared["plan"]
            frame = SimpleNamespace(catalogs=RelocationStores(stores), layer_documents=plan["baselines"])

            # //// 核对来源和全部目标的已提交对象组合 [@x380kkm 2026-09-08] ////
            def guard(scope, expected, current_documents, targets):
                if old_private.snapshot() != plan["oldPrivate"]["documents"] or old_host.store.snapshot() != plan["oldHost"]["documents"]:
                    raise StorageConflictError("project-relocation-source")
                for name, store in stores.items():
                    if records_by_id(store, current_documents[name]) != records_by_id(store, expected[name]):
                        raise StorageConflictError("project-relocation-" + name)

            stages = [(stage["scope"], {entry["id"]: entry["after"] for entry in stage["entries"]}) for stage in plan["stages"]]
            try:
                changed = apply_sharing(frame, stages, guard)
            except CardError as error:
                if error.code != "sharing_recovery":
                    raise
                raise relocation_recovery(error, old_private, old_host, stores) from error
        return {"changed": changed, "oldWorkspace": str(old), "workspace": str(current),
                "preserved": [str(old_private.catalog), str(old_host.store.catalog)],
                "hostApplyRequired": prepared["hostApplyRequired"], "hostFilesChanged": False,
                "catalogs": [stage["catalog"] for stage in plan["stages"]]}
