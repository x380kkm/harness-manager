# audience: internal
# # card-relations
"""有向参考关系保存一份适配正文, 两侧查询由该声明生成. 用户与项目绑定选择各自正文, 来源 Skill 保持独立."""
from __future__ import annotations

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .card_subjects import CardError, CardSubjects, POINTS
from .content_plan import TASK_CONTEXT_POINT
from .protocol import document_identity
from .storage_errors import StorageConflictError
from .usage import usage_document


# //// 为有向卡片对分配跨层稳定身份 [@x380kkm 2026-09-07] ////
def relation_id(source: str, target: str) -> str:
    return "plugin:adapter/" + uuid5(NAMESPACE_URL, source + "\n" + target).hex


# //// 从配套声明取得关系归属与正文 [@x380kkm 2026-09-07] ////
def relation_payload(document: dict) -> dict | None:
    if document["kind"] != "Plugin" or len(document.get("contributions", [])) != 1:
        return None
    member = document["contributions"][0]
    payload = member.get("payload")
    if member.get("point") != TASK_CONTEXT_POINT or not isinstance(payload, dict):
        return None
    adapter = payload.get("adapter")
    if not isinstance(adapter, dict) or not all(isinstance(adapter.get(key), str) for key in ("from", "to")):
        return None
    if document["id"] != relation_id(adapter["from"], adapter["to"]):
        return None
    return payload


# //// 构造由来源卡片触发的可选参考说明 [@x380kkm 2026-09-07] ////
def relation_document(source: dict, target: dict, text: str, scope: str) -> dict:
    payload = {"name": source["name"] + " / " + target["name"], "text": text,
               "skill" if source["kind"] == "skill" else "subject": source["ref"],
               "adapter": {"from": source["id"], "to": target["id"],
                           "targetRef": target["ref"], "targetVersion": target["version"]}}
    return {"apiVersion": "manager.x380kkm/v1", "kind": "Plugin", "id": relation_id(source["id"], target["id"]),
            "release": {"version": "local:" + scope}, "metadata": {"name": payload["name"]},
            "contributions": [{"id": "adapter", "point": TASK_CONTEXT_POINT,
                               "contract": {"id": TASK_CONTEXT_POINT, "range": "^1.0.0"}, "payload": payload,
                               "relations": [{"id": "reference", "contract": {"id": "manager.relation/suggests", "range": "^1.0.0"},
                                              "target": target["ref"], "strength": "advisory"}]}]}


# //// 维护参考关系的一份正文与独立范围绑定 [@x380kkm 2026-09-07] ////
class CardRelations:
    def __init__(self, frame: CardSubjects) -> None:
        self.frame = frame

    # //// 取得关系在指定目录中的完整读取基线 [@x380kkm 2026-09-07] ////
    def _baseline(self, source: str, target: str, scope: str) -> dict:
        documents = self.frame.layer_documents.get(scope, [])
        plugin = relation_id(source, target)
        identity = plugin + "@local:" + scope
        document = next((value for value in documents if document_identity(value) == identity), None)
        binding = next((value for value in documents if value["kind"] == "PluginBinding"
                        and value["id"] == f"binding:{scope}/{plugin}"), None)
        return {"document": document, "binding": binding}

    # //// 保留当前范围内的关系来源与继承状态 [@x380kkm 2026-09-07] ////
    def _edge(self, source: str, target: str) -> dict | None:
        scope = self.frame.scope
        baseline = self._baseline(source, target, scope)
        if baseline["document"] is None and scope == "project-local":
            scope = "project"
            baseline = self._baseline(source, target, scope)
        if baseline["document"] is None and scope == "project":
            scope = "user"
            baseline = self._baseline(source, target, scope)
        document = baseline["document"]
        if document is None:
            return None
        payload = relation_payload(document)
        if payload is None:
            return None
        source_item, target_item = self.frame.items.get(source, {}), self.frame.items.get(target, {})
        binding = baseline["binding"]
        inherited = scope == "user" and self.frame.scope != "user"
        return {"id": document_identity(document), "from": source, "to": target,
                "fromName": source_item.get("name", source), "toName": target_item.get("name", target),
                "text": payload.get("text", ""), "scope": scope, "inherited": inherited,
                "enabled": binding is not None and binding.get("enabled", True),
                "baseline": None if inherited else deepcopy(baseline),
                "fromRef": payload.get("skill", payload.get("subject")),
                "targetRef": payload["adapter"].get("targetRef"),
                "targetVersion": payload["adapter"].get("targetVersion")}

    # //// 从关系声明生成候选项和双向索引 [@x380kkm 2026-09-07] ////
    def describe(self, identity: str) -> dict:
        subject = self.frame.subject(identity)
        candidates = []
        for other, item in self.frame.items.items():
            if other == identity or item["kind"] not in POINTS:
                continue
            try:
                candidates.append(self.frame.subject(other))
            except CardError:
                continue
        edges = self.edges()
        return {"subject": subject, "scope": self.frame.scope,
                "candidates": sorted(candidates, key=lambda item: (item["kind"], item["name"].casefold(), item["id"])),
                "outgoing": [edge for edge in edges if edge["from"] == identity],
                "incoming": [edge for edge in edges if edge["to"] == identity]}

    # //// 汇集当前范围内实际选择的关系声明 [@x380kkm 2026-09-07] ////
    def edges(self) -> list[dict]:
        pairs = set()
        for document in self.frame.view.documents:
            payload = relation_payload(document)
            if payload is not None:
                pairs.add((payload["adapter"]["from"], payload["adapter"]["to"]))
        edges = [self._edge(source, target) for source, target in sorted(pairs)]
        return [edge for edge in edges if edge is not None]

    # //// 核对关系正文与绑定的联合修改基线 [@x380kkm 2026-09-07] ////
    def _check_baseline(self, source: str, target: str, baseline: dict | None) -> dict:
        current = self._baseline(source, target, self.frame.scope)
        expected = {"document": None, "binding": None} if baseline is None else baseline
        if not isinstance(expected, dict) or set(expected) != {"document", "binding"}:
            raise CardError("relation_baseline", "关系修改需要正文与范围绑定的读取基线.")
        if expected != current:
            raise StorageConflictError(relation_id(source, target) + "@local:" + self.frame.scope)
        return current

    # //// 在协作锁内保留关系完整的本层绑定集合 [@x380kkm 2026-09-07] ////
    def _save(self, plans: list[dict], plugin: str) -> list[dict]:
        # //// 核对并发添加或移出的同关系绑定 [@x380kkm 2026-09-07] ////
        def verify_current(documents: list[dict]) -> None:
            expected = {document_identity(value): value for value in self.frame.local
                        if value["kind"] == "PluginBinding" and value["plugin"]["id"] == plugin}
            current = {document_identity(value): value for value in documents
                       if value["kind"] == "PluginBinding" and value["plugin"]["id"] == plugin}
            if expected != current:
                raise StorageConflictError(plugin)

        return self.frame.store.apply_many(plans, verify_current=verify_current)

    # //// 保存单向关系和从来源触发的适配正文 [@x380kkm 2026-09-07] ////
    def set(self, source_id: str, target_id: str, text: str | None, baseline: dict | None, enabled: bool) -> dict:
        if self.frame.scope != "user":
            from .card_sharing import set_project_relation
            project = CardSubjects(self.frame.catalogs, self.frame.codex, "project-local")
            result = set_project_relation(project, source_id, target_id, text, baseline, enabled)
            refreshed = CardRelations(CardSubjects(self.frame.catalogs, self.frame.codex, "project-local"))
            return {**result, "relation": refreshed._edge(source_id, target_id)}
        if source_id == target_id:
            raise CardError("relation_self", "请选择另一张卡片作为参考目标.")
        source, target = self.frame.subject(source_id), self.frame.subject(target_id)
        if text is None:
            text = f"使用本 skill 时可以参考使用 **{target['name']}** skill"
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 1048576:
            raise CardError("relation_text", "参考关系需要完整适配说明, 正文上限为 1 MiB.")
        if type(enabled) is not bool:
            raise CardError("relation_enabled", "关系启用状态需要布尔值.")
        previous = self._check_baseline(source_id, target_id, baseline)
        document = relation_document(source, target, text, self.frame.scope)
        binding = usage_document(document, {"state": "enabled" if enabled else "disabled"}, previous["binding"], self.frame.scope)
        plans = self.frame.source_plans([source_id, target_id])
        plans.extend((self.frame.store.preview_put(document, previous["document"]),
                      self.frame.store.preview_put(binding, previous["binding"])))
        results = self._save(plans, document["id"])
        edge = {"id": document_identity(document), "from": source_id, "to": target_id,
                "fromName": source["name"], "toName": target["name"], "text": text,
                "scope": self.frame.scope, "inherited": False, "enabled": enabled,
                "baseline": {"document": document, "binding": binding},
                "fromRef": source["ref"], "targetRef": target["ref"], "targetVersion": target["version"]}
        return {"changed": any(result["changed"] for result in results), "scope": self.frame.scope, "relation": edge}

    # //// 同时移出本层关系正文与其范围绑定 [@x380kkm 2026-09-07] ////
    def remove(self, source: str, target: str, baseline: dict | None) -> dict:
        if self.frame.scope != "user":
            from .card_sharing import remove_project_relation
            project = CardSubjects(self.frame.catalogs, self.frame.codex, "project-local")
            return remove_project_relation(project, source, target, baseline)
        previous = self._check_baseline(source, target, baseline)
        if previous["document"] is None:
            raise CardError("relation_scope", "当前层没有可移出的参考关系.")
        plans = [self.frame.store.preview_remove(document_identity(value), value)
                 for value in previous.values() if value is not None]
        results = self._save(plans, previous["document"]["id"])
        return {"changed": any(result["changed"] for result in results), "scope": self.frame.scope,
                "from": source, "to": target, "removed": True}
