# audience: internal
# # catalog-layers
"""用户目录承载默认声明和项目个人设置, 项目目录承载共享声明. 来源访问由读取授权核对."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path

from .declarations import diagnose
from .graph import build_graph
from .protocol import document_identity, validate_document
from .storage import Store
from .storage_errors import StorageError

LOCAL_RESOLVERS = frozenset({"manager.source/path", "manager.source/git"})


# //// 遍历声明中有明确路径语义的字段 [@x380kkm 2026-09-08] ////
def document_locations(document: dict):
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            contract = value.get("contract", {})
            if isinstance(contract, dict) and contract.get("id") == "manager.scope":
                selector = value.get("selector", {})
                if isinstance(selector, dict):
                    for key in ("path", "project"):
                        if key in selector:
                            yield selector, key, "scope"
            if isinstance(value.get("resolver"), dict) and value["resolver"].get("id") in LOCAL_RESOLVERS and "locator" in value:
                yield value, "locator", "source"
            if isinstance(contract, dict) and contract.get("id") == "manager.card/source" and isinstance(value.get("payload"), dict):
                if "path" in value["payload"]:
                    yield value["payload"], "path", "receipt"
            pending.extend(child for key, child in value.items() if key not in {"payload", "options", "inputSchema", "outputSchema"})


# //// 在项目目录中固定相对范围与来源位置 [@x380kkm 2026-09-08] ////
def resolve_project_locations(document: dict, root: Path, *, relative: bool = True) -> None:
    for carrier, key, kind in document_locations(document):
        if key == "project" or kind == "source":
            continue
        values = carrier[key] if isinstance(carrier[key], list) else [carrier[key]]
        resolved = []
        for value in values:
            if isinstance(value, str) and not (kind == "scope" and value == "current") and "://" not in value:
                path = Path(value)
                if relative and not path.is_absolute():
                    path = root / path
                if path.is_absolute():
                    path = Path(os.path.abspath(path))
                    value = path.as_posix() if kind == "scope" else str(path)
            resolved.append(value)
        carrier[key] = resolved if isinstance(carrier[key], list) else resolved[0]


# //// 将项目内部位置转换成共享声明中的相对值 [@x380kkm 2026-09-08] ////
def portable_project_document(document: dict, root: Path) -> dict:
    result = deepcopy(document)
    for carrier, key, kind in document_locations(result):
        values = carrier[key] if isinstance(carrier[key], list) else [carrier[key]]
        converted = []
        for value in values:
            if isinstance(value, str) and key == "project" and value == root.as_uri():
                value = "current"
            elif isinstance(value, str) and "://" not in value:
                path = Path(value)
                if path.is_absolute():
                    path = Path(os.path.abspath(path))
                if path.is_absolute() and path.is_relative_to(root):
                    value = path.relative_to(root).as_posix()
            converted.append(value)
        carrier[key] = converted if isinstance(carrier[key], list) else converted[0]
    return result


# //// 列出项目声明依赖的外部来源、范围和原文位置 [@x380kkm 2026-09-08] ////
def external_project_locations(documents: list[dict], root: Path) -> dict[str, list[str]]:
    found = {"source": set(), "scope": set(), "receipt": set()}
    for document in documents:
        for carrier, key, kind in document_locations(document):
            values = carrier[key] if isinstance(carrier[key], list) else [carrier[key]]
            for value in values:
                if not isinstance(value, str):
                    continue
                if kind == "scope" and value == "current":
                    continue
                if key == "project":
                    if value not in {"current", root.as_uri()}:
                        found[kind].add(value)
                elif value.startswith("file:"):
                    found[kind].add(value)
                elif "://" not in value:
                    path = Path(value).expanduser() if kind == "source" else Path(value)
                    resolved = (root / path).resolve()
                    if not resolved.is_relative_to(root):
                        found[kind].add(str(resolved))
    return {kind: sorted(paths) for kind, paths in found.items()}


# //// 从明确项目路径定位个人配置存储 [@x380kkm 2026-09-08] ////
def project_private_store(user: Store, project: Path) -> Store:
    uri = project.as_uri()
    key = sha256((uri.casefold() if project.drive else uri).encode("utf-8")).hexdigest()
    return Store(user.workspace, document_identity, validate_document, catalog_directory=user.directory / "projects" / key)


# //// 保存目录组合中的明确诊断 [@x380kkm 2026-09-06] ////
class CatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# //// 保存一次目录组合的声明与归属 [@x380kkm 2026-09-06] ////
@dataclass
class CatalogView:
    documents: list[dict] = field(default_factory=list)
    origins: dict[str, list[dict]] = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)
    layers: dict[str, int] = field(default_factory=dict)


# //// 将本地来源的相对位置固定到声明目录 [@x380kkm 2026-09-06] ////
def resolve_local_locator(source: object, root: Path) -> None:
    if not isinstance(source, dict) or source.get("resolver", {}).get("id") not in {"manager.source/path", "manager.source/git"}:
        return
    locator = source.get("locator")
    if isinstance(locator, str) and "://" not in locator:
        source["locator"] = os.path.abspath(root / Path(locator).expanduser())


# //// 在目录的实际归属中解释来源与项目范围 [@x380kkm 2026-09-06] ////
def resolve_document(document: dict, store: Store, scope: str, diagnostics: list[dict], *, project: Path | None = None) -> dict | None:
    result = deepcopy(document)
    root = project if scope != "user" and project is not None else store.workspace
    resolve_project_locations(result, root, relative=scope in {"project", "project-local"})
    if result["kind"] == "Plugin":
        for source in result.get("sources", []):
            resolve_local_locator(source["source"], root)
        for contribution in result["contributions"]:
            resolve_local_locator(contribution.get("source"), root)
    if scope in {"project", "project-local"} and result["kind"] == "PluginBinding":
        selector = result["target"].setdefault("selector", {})
        project_uri = root.as_uri()
        selected = selector.get("project", project_uri)
        if selected not in (project_uri, "current"):
            values = selected if isinstance(selected, list) else [selected]
            if project_uri not in values and "current" not in values:
                diagnose(diagnostics, "project_scope_mismatch", result["id"], "项目目录中的绑定需要适用于该项目位置.")
                return None
        selector["project"] = project_uri
    return result


# //// 组合用户默认与显式项目声明 [@x380kkm 2026-09-06] ////
class Catalogs:
    def __init__(self, user_root: Path, workspace: Path | None = None) -> None:
        self.user = Store(user_root, document_identity, validate_document)
        self.project = Store(workspace, document_identity, validate_document) if workspace is not None else None
        if self.project is not None and self.project.workspace == self.user.workspace:
            raise CatalogError("same_catalog_root", "用户目录与项目目录需要独立位置.")
        self.project_local = None
        if self.project is not None:
            self.project_local = project_private_store(self.user, self.project.workspace)

    # //// 返回各层独立的声明存储入口 [@x380kkm 2026-09-07] ////
    def layers(self) -> dict[str, Store]:
        return {scope: store for scope, store in (("user", self.user), ("project", self.project),
                                                 ("project-local", self.project_local)) if store is not None}

    # //// 用实际项目位置形成范围上下文 [@x380kkm 2026-09-07] ////
    def context(self, scope: str) -> dict[str, str]:
        self.select(scope)
        if scope == "user":
            return {}
        return {"project": self.project.workspace.as_uri(), "path": str(self.project.workspace)}

    # //// 按声明归属选择可写目录 [@x380kkm 2026-09-06] ////
    def select(self, scope: str) -> Store:
        selected = self.layers().get(scope)
        if selected is not None:
            return selected
        raise CatalogError("catalog_scope", "目录范围需要 user, 或已经选定项目的 project 和 project-local.")

    # //// 取得指定配置层可使用的声明组合 [@x380kkm 2026-09-06] ////
    def for_scope(self, scope: str | None = None) -> CatalogView:
        if scope is not None:
            self.select(scope)
        excluded = {"user": ("project", "project-local"), "project": ("project-local",)}.get(scope, ())
        return self.effective({name: [] for name in excluded})

    # //// 合成指定目录快照的来源和引用诊断 [@x380kkm 2026-09-08] ////
    def _diagnostics(self, inputs: dict[str, list[dict]]) -> list[dict]:
        view = self.effective(inputs)
        return [*view.diagnostics, *build_graph(view.documents)["diagnostics"]]

    # //// 返回编辑层诊断及当前项目各下层新增的影响 [@x380kkm 2026-09-08] ////
    def edit_diagnostics(self, scope: str, before: list[dict], after: list[dict]) -> list[dict]:
        layers = self.layers()
        names = list(layers)
        position = names.index(scope)
        inputs = {name: [] for name in names}
        inputs.update({name: layers[name].snapshot() for name in names[:position]})
        inputs[scope] = before
        reported = self._diagnostics({**inputs, scope: after})
        result = [{**note, "scope": scope} for note in reported]
        for name in names[position + 1:]:
            try:
                inputs[name] = layers[name].snapshot()
            except StorageError as error:
                result.append({"code": "scope_impact_unavailable", "severity": "warning", "scope": name,
                               "subject": name, "message": f"当前范围的影响检查未完成: {error}"})
                break
            previous = self._diagnostics(inputs)
            for note in self._diagnostics({**inputs, scope: after}):
                if note not in previous and note not in reported:
                    result.append({**note, "scope": name})
                    reported.append(note)
        return result

    # //// 从各层取得一次有效声明组合 [@x380kkm 2026-09-06] ////
    def effective(self, inputs: dict[str, list[dict]] | None = None) -> CatalogView:
        view = CatalogView()
        grouped: dict[str, list[tuple[dict, dict]]] = {}
        for layer, (scope, store) in enumerate(self.layers().items()):
            documents = inputs[scope] if inputs is not None and scope in inputs else store.snapshot()
            for document in documents:
                resolved = resolve_document(document, store, scope, view.diagnostics,
                                            project=self.project.workspace if self.project is not None else None)
                if resolved is None:
                    continue
                identity = document_identity(document)
                origin = {"scope": scope, "path": str(store.catalog), "document": document}
                grouped.setdefault(identity, []).append((resolved, origin))
                view.layers[identity] = layer
        for identity, values in grouped.items():
            view.origins[identity] = [origin for _, origin in values]
            if all(document["kind"] == "CardCollection" for document, _ in values):
                view.documents.append(values[-1][0])
                continue
            canonical = {json.dumps(document, sort_keys=True, ensure_ascii=False) for document, _ in values}
            if len(canonical) > 1:
                diagnose(view.diagnostics, "catalog_identity_conflict", identity,
                         "配置层包含内容不同的相同身份, 请明确保留各自独立身份.")
                view.layers.pop(identity, None)
                continue
            view.documents.append(values[0][0])
        return view
