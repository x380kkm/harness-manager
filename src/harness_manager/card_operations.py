# audience: internal
# # card-operations
"""卡片启停修改 Manager 的使用绑定. 来源定义与宿主加载状态分别保留, 双向关系查询读取同一份适配声明."""
from __future__ import annotations

from copy import deepcopy

from .card_bindings import applicable_bindings, card_binding, configured_binding, plugin_bindings
from .card_relations import CardRelations
from .card_subjects import CardError, CardSubjects, POINTS
from .card_hook_state import hook_scope, observe_hook, save_hook_request
from .card_sharing import configure_project, set_shared as share_card, sharing_baseline, sharing_portability
from .catalogs import Catalogs
from .codex_inventory import CodexInventory
from .declarations import contribution_name
from .projection import ScopedValue, resolve_scope, resolve_values
from .protocol import document_identity
from .storage_errors import StorageConflictError
from .storage_errors import StorageError


# //// 把显式绑定转换为用户可选的启用状态 [@x380kkm 2026-09-07] ////
def configured_state(binding: dict | None) -> str:
    if binding is None or "enabled" not in binding:
        return "inherit"
    return "enabled" if binding["enabled"] else "disabled"


# //// 汇总每层适用绑定的开关并保留多绑定诊断 [@x380kkm 2026-09-10] ////
def layer_state(frame: CardSubjects, plugin: str, scope: str, diagnostics: list[dict]) -> str | None:
    bindings = applicable_bindings(frame, plugin, scope)
    states = {configured_state(binding) for binding in bindings}
    if len(bindings) > 1:
        diagnostics.append({"code": "card_binding_ambiguous", "subject": plugin, "scope": scope,
                            "message": "当前层有多个适用的使用绑定, 快捷修改需要选择具体绑定."})
    return None if len(states) > 1 else next(iter(states), "inherit")


# //// 合成当前范围的 Manager 使用状态 [@x380kkm 2026-09-07] ////
def effective_enabled(frame: CardSubjects, plugin: str) -> tuple[bool | None, list[dict]]:
    values, diagnostics, bindings = [], [], []
    for document in frame.view.documents:
        if document["kind"] != "PluginBinding" or document["plugin"]["id"] != plugin:
            continue
        selected = resolve_scope(document["target"], frame.context, document["id"], diagnostics)
        if selected is not None:
            bindings.append(document)
            if "enabled" in document:
                values.append(ScopedValue(selected, document["id"], document["enabled"], layer=frame.view.layers.get(document["id"], 0)))
    if not bindings:
        return None, diagnostics
    valid, enabled = resolve_values(values, diagnostics, plugin + "/enabled")
    return (enabled is not False if valid else None), diagnostics


# //// 描述卡片在两个配置层中的可编辑状态 [@x380kkm 2026-09-07] ////
def card_management(frame: CardSubjects, identity: str) -> dict:
    document = frame.definition(identity)
    subject = frame.subject(identity)
    binding = card_binding(frame, document["id"], frame.scope)
    enabled, diagnostics = effective_enabled(frame, document["id"])
    user = layer_state(frame, document["id"], "user", diagnostics)
    project = layer_state(frame, document["id"], "project", diagnostics)
    private = layer_state(frame, document["id"], "project-local", diagnostics)
    result = {"supported": True, "kind": frame.item(identity)["kind"], "scope": frame.scope,
            "pluginId": document["id"], "documentId": document_identity(document),
            "name": subject["name"], "ref": subject["ref"], "version": subject["version"],
            "availability": subject["availability"],
            "contents": [{"id": member["id"], "name": contribution_name(member), "point": member.get("point"),
                          "ref": member.get("ref", document["id"] + "#" + member["id"])}
                         for member in document["contributions"]],
            "userState": user,
            "projectState": project if frame.catalogs.project else None,
            "sharedState": project if frame.catalogs.project else None,
            "privateState": private if frame.catalogs.project else None,
            "shared": bool(plugin_bindings(frame.project, document["id"])),
            "effectiveEnabled": enabled, "diagnostics": diagnostics,
            "configBaseline": deepcopy({"document": frame.local_definition(document), "binding": binding, "source": document})}
    if result["kind"] == "hook":
        native = observe_hook(frame, document)
        result.update(nativeScope=native["scope"], nativeState=native["state"], nativeEnabled=native["enabled"], nativeHandlers=native["handlers"],
                      pendingNativeState=native["request"]["enabled"] if native["request"] else None)
        result["configBaseline"]["native"] = native["baseline"]
        if native["state"] != "absent":
            result["effectiveEnabled"] = native["enabled"]
    return result


# //// 从一次读取的来源和声明生成卡片管理状态 [@x380kkm 2026-09-07] ////
def card_inventory(frame: CardSubjects) -> dict:
    result = frame.inventory
    result["cardManagement"] = {}
    for identity, item in frame.items.items():
        if item["kind"] not in POINTS:
            continue
        try:
            result["cardManagement"][identity] = card_management(frame, identity)
        except (CardError, StorageError) as error:
            result["cardManagement"][identity] = {"supported": False, "kind": item["kind"], "scope": frame.scope,
                                                 "reason": str(error), "code": error.code}
    result["scope"] = frame.scope
    result["managerRelations"] = CardRelations(frame).edges()
    return result


# //// 为面板和外部客户端提供卡片级管理动作 [@x380kkm 2026-09-07] ////
class CardOperations:
    def __init__(self, catalogs: Catalogs, codex: CodexInventory) -> None:
        self.catalogs, self.codex = catalogs, codex

    # //// 合并本机观察与 Manager 的独立管理状态 [@x380kkm 2026-09-07] ////
    def inventory(self, scope: str = "user") -> dict:
        return card_inventory(CardSubjects(self.catalogs, self.codex, scope))

    # //// 返回一次快捷修改需要的来源与读取基线 [@x380kkm 2026-09-07] ////
    def describe(self, id: str, scope: str = "user") -> dict:
        frame = CardSubjects(self.catalogs, self.codex, scope)
        subject, management = frame.subject(id), card_management(frame, id)
        result = {"subject": subject, "management": management, "scope": scope,
                "configBaseline": management["configBaseline"],
                "bindings": [deepcopy(value) for value in frame.view.documents
                             if value["kind"] == "PluginBinding" and value["plugin"]["id"] == management["pluginId"]]}
        if scope != "user":
            result["sharingBaseline"] = sharing_baseline(frame, id)
        return result

    # //// 原子保存独立来源与明确启用范围 [@x380kkm 2026-09-07] ////
    def configure(self, id: str, state: str, scope: str = "user", baseline: dict | None = None) -> dict:
        if state not in {"enabled", "disabled", "inherit"}:
            raise CardError("card_state", "启用状态需要 enabled, disabled 或 inherit.")
        frame = CardSubjects(self.catalogs, self.codex, scope)
        document = frame.definition(id)
        current = card_management(frame, id)["configBaseline"]
        if baseline is None:
            if current["binding"] is not None:
                raise StorageConflictError(current["binding"]["id"])
            baseline = current
        if not isinstance(baseline, dict) or set(baseline) != set(current):
            raise CardError("card_baseline", "启用修改需要卡片来源与本层绑定的读取基线.")
        if baseline["source"] != document:
            raise StorageConflictError(document_identity(document))
        if baseline["document"] != current["document"] or baseline["binding"] != current["binding"]:
            raise StorageConflictError(document_identity(baseline["binding"] or document))
        if baseline.get("native") != current.get("native"):
            raise StorageConflictError("host-hook-native-state")
        if "native" in current and scope != "user" and state != "inherit" and hook_scope(frame, document) == "user":
            raise CardError("host_inherited_hook_scope", "此 Hook 由用户配置提供, 请在用户配置中调整原生开关.")
        if scope == "project":
            project = CardSubjects(self.catalogs, self.codex, "project-local")
            changed, saved = configure_project(project, id, state, current)
            return self._configured_result(id, scope, changed, state, current, saved)
        plans = []
        previous = baseline["binding"]
        if state == "inherit":
            if previous is not None:
                plans.append(frame.store.preview_remove(previous["id"], previous))
        else:
            plans.extend(frame.source_plans([id]))
            binding = configured_binding(frame, document, state, scope, previous)
            plans.append(frame.store.preview_put(binding, previous))
        if plans and current["document"] is not None and all(plan["id"] != document_identity(document) for plan in plans):
            plans.insert(0, frame.store.preview_put(current["document"], baseline["document"]))
        # //// 在提交时确认快捷操作仍选择同一绑定 [@x380kkm 2026-09-10] ////
        def verify_binding(documents):
            if card_binding(frame, document["id"], scope, documents) != previous:
                raise StorageConflictError(document["id"])

        results = frame.store.apply_many(plans, verify_current=verify_binding) if plans else []
        return self._configured_result(id, scope, any(result["changed"] for result in results), state, current,
                                       binding if state != "inherit" else None)

    # //// 返回声明保存结果与原生开关请求 [@x380kkm 2026-09-10] ////
    def _configured_result(self, identity: str, scope: str, changed: bool, state: str, baseline: dict, binding: dict | None) -> dict:
        result = {**self.describe(identity, scope), "changed": changed}
        if state == "inherit" and binding is None and baseline["binding"] is None:
            return result
        if "native" in baseline:
            binding_id = (binding or baseline["binding"] or {}).get("id", f"binding:{scope}/{result['management']['pluginId']}")
            try:
                save_hook_request(self.catalogs, self.codex.root, scope, result["management"]["ref"],
                                  None if state == "inherit" else state == "enabled", baseline["native"], binding, binding_id)
            except (StorageError, ValueError, OSError) as error:
                result["hostSync"] = {"status": "blocked", "message": "声明已保存, 原生开关请求需要重新读取.",
                                      "diagnostics": [{"code": getattr(error, "code", "host_hook_request"), "message": str(error)}]}
            result.update(self.describe(identity, scope))
        return result

    # //// 发布当前项目设置或转回私人维护 [@x380kkm 2026-09-07] ////
    def set_shared(self, id: str, shared: bool, baseline: dict | None = None) -> dict:
        frame = CardSubjects(self.catalogs, self.codex, "project-local")
        changed = share_card(frame, id, shared, baseline)
        current = CardSubjects(self.catalogs, self.codex, "project-local")
        return {**self.describe(id, "project-local"), "changed": changed, **sharing_portability(current, id)}

    # //// 从同一关系声明读取使用与被使用两侧 [@x380kkm 2026-09-07] ////
    def relations(self, id: str, scope: str = "user") -> dict:
        return CardRelations(CardSubjects(self.catalogs, self.codex, scope)).describe(id)

    # //// 保存一条参考关系与对应的使用说明 [@x380kkm 2026-09-07] ////
    def set_relation(self, from_id: str, to_id: str, text: str | None = None, scope: str = "user",
                     baseline: dict | None = None, enabled: bool = True) -> dict:
        return CardRelations(CardSubjects(self.catalogs, self.codex, scope)).set(from_id, to_id, text, baseline, enabled)

    # //// 移出本层的关系与绑定并恢复上层选择 [@x380kkm 2026-09-07] ////
    def remove_relation(self, from_id: str, to_id: str, scope: str = "user", baseline: dict | None = None) -> dict:
        return CardRelations(CardSubjects(self.catalogs, self.codex, scope)).remove(from_id, to_id, baseline)
