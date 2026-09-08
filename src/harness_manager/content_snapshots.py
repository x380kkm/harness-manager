# audience: internal
# # content-snapshots
"""读取快照固定已采集正文和生效配置. 续读重查来源授权, 每个回执只累计同一快照中的完整单元."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .carriers import content_carrier
from .catalogs import CatalogView
from .content import ContentError, read_member_unit, resolve_source
from .content_plan import ContentPlan, PlannedEntry, content_diagnostic, plan_content
from .json_codec import encode_json
from .observations import MAX_OBSERVATION_BYTES, ObservationStore, observation_id
from .protocol import document_identity, validate_document
from .sources import SourceError, SourceReader

MAX_CONTENT_BUDGET = 1024 * 1024


# //// 固定 JSON 输入的规范化修订 [@x380kkm 2026-09-06] ////
def content_fingerprint(value) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# //// 校验完整单元的传输预算 [@x380kkm 2026-09-06] ////
def validate_budget(budget: int) -> None:
    if type(budget) is not int or not 1 <= budget <= MAX_CONTENT_BUDGET:
        raise ContentError("invalid_budget", "读取预算需要 1 至 1048576 之间的字节数.")


# //// 为一个原始入口生成版本内的单元身份 [@x380kkm 2026-09-06] ////
def unit_reference(entry: PlannedEntry, resource: str | None) -> str:
    content = entry.content
    payload = content.member["payload"]
    path = payload.get("entry", "payload") if isinstance(payload, dict) else "payload"
    if resource is not None:
        path = (Path(path).parent / resource).as_posix()
    return f"{document_identity(content.owner)}#{content.member['id']}/{path}"


# //// 汇集实际参与读取的声明与使用选项 [@x380kkm 2026-09-06] ////
def configuration_inputs(plan: ContentPlan, view: CatalogView) -> tuple[list[dict], list[dict]]:
    identities = set()
    configurations = []
    for entry in plan.entries:
        content = entry.content
        inputs = []
        for binding in content.bindings:
            identities.add(binding.subject)
            inputs.append({"binding": binding.subject, "fingerprint": content_fingerprint(binding.value)})
        identities.update(document_identity(owner) for owner, _ in content.chain)
        configurations.append({"ref": content.summary["ref"], "version": content.summary["version"],
                               "bindingInputs": inputs, "options": content.summary["options"]})
    contracts = {(entry.content.member["point"], entry.content.member["contract"]["id"]) for entry in plan.entries}
    identities.update(document_identity(document) for document in view.documents
                      if document["kind"] == "PointContract" and (document["point"], document["contract"]["id"]) in contracts)
    identities.update(document_identity(document) for document in view.documents if document["kind"] == "PluginBinding")
    inputs = [{"identity": document_identity(document), "document": deepcopy(document),
               "origins": [{"scope": origin["scope"], "path": origin["path"]}
                           for origin in view.origins.get(document_identity(document), [])]}
              for document in view.documents if document_identity(document) in identities]
    return inputs, configurations


# //// 采集正文并在同一 Git 来源中固定提交 [@x380kkm 2026-09-06] ////
def capture_unit(reader: SourceReader, entry: PlannedEntry, resource: str | None, git_locks: dict) -> dict:
    content = entry.content
    member = content.member
    source_ref = member.get("source")
    source_key = None
    if source_ref is not None and isinstance(member["payload"], dict) and member["payload"].get("entry"):
        source = deepcopy(resolve_source(content.owner, source_ref))
        source_key = content_fingerprint(source)
        if source_key in git_locks:
            source["constraint"] = git_locks[source_key]
        member = {**member, "source": source}
    raw = read_member_unit(reader, content.owner, member, resource)
    if "commit" in raw:
        git_locks[source_key] = raw["commit"]
    carrier, media_type = content_carrier(member, raw["mediaType"])
    value = carrier.decode(raw["content"])
    unit = {"ref": unit_reference(entry, resource), "mediaType": media_type, "content": value}
    source_identity = f"{document_identity(content.owner)}#{content.member['id']}"
    resolved = {"ref": content.summary["content"], "artifact": {"source": source_identity, "revision": raw["revision"]},
                "entry": raw.get("entry", "payload")}
    return {"unit": unit, "content": resolved, "accessPaths": raw.get("accessPaths", [])}


# //// 保存读取快照并生成可续读的完整单元回执 [@x380kkm 2026-09-06] ////
class ContentSnapshots:
    def __init__(self, store: ObservationStore, reader: SourceReader) -> None:
        self.store, self.reader = store, reader

    # //// 采集所选方法与当前适用的配套内容 [@x380kkm 2026-09-06] ////
    def open(self, view: CatalogView, context: dict, ref: str, version: str | None, budget: int,
             resources: list[str], consumer: str = "agent") -> dict:
        validate_budget(budget)
        if not isinstance(resources, list) or any(not isinstance(path, str) or not path for path in resources):
            raise ContentError("invalid_resources", "附加资源需要相对文件路径列表.")
        plan = plan_content(view.documents, context, ref, version, resources, layers=view.layers)
        identity = observation_id("content-snapshot")
        inputs, configurations = configuration_inputs(plan, view)
        captured_bytes = len(encode_json([inputs, configurations]).encode("utf-8"))
        if captured_bytes > MAX_OBSERVATION_BYTES:
            raise ContentError("content_snapshot_capacity", "使用配置超过读取快照的存储上限.")
        configuration = {"ref": identity + "/configuration", "mediaType": "application/json",
                         "content": {"target": context, "selections": configurations}}
        captured = [{"unit": configuration, "accessPaths": []}]
        descriptors = [{"ref": configuration["ref"], "purpose": "configuration", "state": "available"}]
        required = [configuration["ref"]]
        diagnostics = [*plan.diagnostics, *(content_diagnostic(item["code"], item["subject"], item["message"], "warning") for item in view.diagnostics)]
        git_locks = {}
        selection = None
        for entry in plan.entries:
            for resource in [None, *entry.resources]:
                unit_ref = unit_reference(entry, resource)
                if unit_ref in required:
                    continue
                required.append(unit_ref)
                descriptor = {"ref": unit_ref, "purpose": entry.purpose if resource is None else "reference", "state": "available"}
                try:
                    result = capture_unit(self.reader, entry, resource, git_locks)
                except (ContentError, SourceError, OSError, ValueError) as error:
                    if entry.content is plan.selected and resource is None:
                        raise
                    descriptor["state"] = "unavailable"
                    diagnostics.append(content_diagnostic("content.required-unit-unavailable", unit_ref, str(error)))
                else:
                    captured_bytes += len(encode_json(result).encode("utf-8"))
                    if captured_bytes > MAX_OBSERVATION_BYTES:
                        raise ContentError("content_snapshot_capacity", "完整读取快照超过 16 MiB, 需要选择独立的较小内容入口.")
                    captured.append(result)
                    descriptor["content"] = result["content"]
                    if entry.content is plan.selected and resource is None:
                        selection = result["content"]
                descriptors.append(descriptor)
        if selection is None:
            selected_ref = unit_reference(PlannedEntry(plan.selected, "method"), None)
            selection = next(item["content"] for item in captured if item["unit"]["ref"] == selected_ref)
        for missing in plan.missing:
            if missing not in required:
                required.append(missing)
                descriptors.append({"ref": missing, "purpose": "reference", "state": "unavailable"})
        snapshot = {"apiVersion": "manager.x380kkm/v1", "kind": "ContentSnapshot", "id": identity,
                    "request": {"ref": ref, "version": plan.selected.summary["version"]}, "selection": selection,
                    "target": {"contract": {"id": "manager.scope", "range": "^1.0.0"}, "selector": context},
                    "inputs": inputs, "configurations": configurations, "units": descriptors,
                    "requiredUnits": required, "diagnostics": diagnostics, "createdAt": datetime.now(timezone.utc).isoformat()}
        validate_document(snapshot)
        record = {"id": identity, "snapshot": snapshot, "captured": captured, "consumer": consumer}
        self.store.save(identity, record)
        return self._emit(record, set(), budget)

    # //// 核对快照与当前来源授权 [@x380kkm 2026-09-06] ////
    def _load(self, identity: str) -> dict:
        record = self.store.read(identity)
        snapshot = record.get("snapshot")
        validate_document(snapshot)
        if snapshot["id"] != identity or snapshot["kind"] != "ContentSnapshot":
            raise ContentError("content_snapshot_identity", "读取快照与请求身份不同.")
        paths = sorted({path for item in record["captured"] for path in item["accessPaths"]})
        self.reader.authorize_snapshot(paths)
        git_roots = set()
        for item in record["captured"]:
            if "content" in item and item["content"]["artifact"]["revision"].startswith("git:"):
                if not item["accessPaths"]:
                    raise SourceError("Git 读取快照需要来源根目录.")
                git_roots.add(item["accessPaths"][0])
        for root in git_roots:
            self.reader.authorize_git_source(root)
        return record

    # //// 读取固定配置与单元描述 [@x380kkm 2026-09-06] ////
    def inspect(self, identity: str) -> dict:
        return self._load(identity)["snapshot"]

    # //// 累计同一快照的读取链并继续返回单元 [@x380kkm 2026-09-06] ////
    def continue_read(self, previous: str, budget: int) -> dict:
        validate_budget(budget)
        current = self.store.read(previous)
        validate_document(current)
        if current["kind"] != "ContentReadResult":
            raise ContentError("content_read_identity", "续读需要内容读取回执身份.")
        if current.get("continuation") != previous:
            raise ContentError("content_continuation_complete", "此回执已经返回全部可续读单元.")
        record = self._load(current["snapshot"])
        known_units = {item["unit"]["ref"]: json.dumps(item["unit"], sort_keys=True, ensure_ascii=False)
                       for item in record["captured"]}
        delivered, visited = set(), set()
        while True:
            if current["id"] in visited or current["snapshot"] != record["id"] or current["selection"] != record["snapshot"]["selection"]:
                raise ContentError("content_read_chain", "读取链需要保持同一快照与内容选择.")
            visited.add(current["id"])
            if set(current["requiredUnits"]) != set(record["snapshot"]["requiredUnits"]):
                raise ContentError("content_read_chain", "读取链中的必需单元集合与快照不同.")
            for unit in current["units"]:
                if known_units.get(unit["ref"]) != json.dumps(unit, sort_keys=True, ensure_ascii=False):
                    raise ContentError("content_read_chain", "读取回执中的正文与固定单元不同.")
            delivered.update(unit["ref"] for unit in current["units"])
            if "previous" not in current:
                break
            current = self.store.read(current["previous"])
            validate_document(current)
        return self._emit(record, delivered, budget, previous)

    # //// 在预算内返回完整单元并保存读取回执 [@x380kkm 2026-09-06] ////
    def _emit(self, record: dict, delivered: set[str], budget: int, previous: str | None = None) -> dict:
        snapshot = record["snapshot"]
        result = {"apiVersion": "manager.x380kkm/v1", "kind": "ContentReadResult", "id": observation_id("content-read"),
                  "snapshot": snapshot["id"], "selection": snapshot["selection"], "units": [],
                  "requiredUnits": snapshot["requiredUnits"], "diagnostics": list(snapshot["diagnostics"])}
        pending = False
        required_budgets = []
        for item in record["captured"]:
            unit = item["unit"]
            if unit["ref"] in delivered:
                continue
            size = len((unit["content"] if isinstance(unit["content"], str) else encode_json(unit["content"])).encode("utf-8"))
            if size > budget:
                pending = True
                required_budgets.append(size)
                action = "请增加续读预算." if size <= MAX_CONTENT_BUDGET else "单元超过读取预算上限, 需要载体提供独立的较小入口."
                result["diagnostics"].append(content_diagnostic("content.required-unit-budget", unit["ref"],
                                                               f"完整单元需要 {size} 字节, {action}", "info"))
                continue
            result["units"].append(unit)
            delivered.add(unit["ref"])
            budget -= size
        result["missing"] = [reference for reference in snapshot["requiredUnits"] if reference not in delivered]
        result["readiness"] = "needs-content" if result["missing"] else "ready"
        if previous is not None:
            result["previous"] = previous
        if pending:
            result["continuation"] = result["id"]
        if previous is not None and pending and not result["units"]:
            raise ContentError("content_budget_unmet", "当前预算无法容纳任何剩余完整单元, 请使用原续读入口增加预算.",
                               {"continuation": previous, "minimumBudget": min(required_budgets)})
        validate_document(result)
        self.store.save(result["id"], result)
        return result
