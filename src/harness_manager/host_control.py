# audience: internal
# # host-control
"""宿主预览固定编译结果和磁盘基线. 应用只接受当前服务生成的预览, 备份与恢复由宿主存储处理."""
from __future__ import annotations

import base64
from difflib import unified_diff
import tomllib
from uuid import uuid4

from .host_profiles import CODEX, HostProfile
from .host_projection import compile_host
from .host_hooks import HOOK_POINT
from .host_paths import host_storage
from .host_storage import HOOK_STATE_NAME
from .card_hook_state import hook_request_is_current
from .json_codec import json_values_equal
from .host_storage import HostStorage
from .host_ownership import reconcile, decode_file, encode_file
from .host_instructions import compose_instructions
from .host_sources import HostSourceReader, verify_source_reads
from .storage_errors import StorageConflictError, StorageValidationError, StorageError

EXCLUDED_CATALOG_SCOPES = {"user": frozenset({"project", "project-local"}), "project": frozenset({"project-local"})}


# //// 按优先顺序给出宿主的说明候选文件 [@x380kkm 2026-09-24] ////
def instruction_candidates(profile: HostProfile) -> list[str]:
    return list(dict.fromkeys([profile.rule_file, profile.instruction_file]))


# //// 汇总文件差异并限定正文展示范围 [@x380kkm 2026-09-07] ////
def file_preview(targets: dict, baseline: dict, profile: HostProfile = CODEX) -> list[dict]:
    files = []
    for name, after in targets.items():
        encoded = baseline["files"].get(name)
        before = base64.b64decode(encoded) if encoded is not None else None
        if before == after:
            continue
        record = {"name": name, "operation": "移出" if after is None else "创建" if before is None else "替换",
                  "beforeBytes": len(before) if before else 0, "afterBytes": len(after) if after else 0}
        if name in {profile.instruction_file, profile.rule_file}:
            record["diff"] = "\n".join(unified_diff((before or b"").decode("utf-8-sig", errors="replace").splitlines(),
                                                       (after or b"").decode("utf-8-sig", errors="replace").splitlines(), n=2))
        else:
            record["diff"] = "配置文件按选中版本替换. 完整字节保存在本机备份中."
        files.append(record)
    return files


# //// 固定参与宿主编译的配置层原始声明 [@x380kkm 2026-09-10] ////
class HostCatalogInputs:
    def __init__(self, catalogs, scope: str) -> None:
        self.catalogs = catalogs
        self.project = catalogs.project
        excluded = EXCLUDED_CATALOG_SCOPES.get(scope, ())
        self.documents = {name: store.snapshot() for name, store in catalogs.layers().items() if name not in excluded}

    # //// 从固定输入生成用户或项目的继承视图 [@x380kkm 2026-09-10] ////
    def for_scope(self, scope: str):
        excluded = EXCLUDED_CATALOG_SCOPES.get(scope, ())
        return self.catalogs.effective({name: [] if name in excluded else self.documents.get(name, [])
                                        for name in self.catalogs.layers()})

    # //// 使用固定配置层对应的项目位置 [@x380kkm 2026-09-10] ////
    def context(self, scope: str) -> dict:
        return self.catalogs.context(scope)


# //// 核对编译输入中的声明内容及新增和移除 [@x380kkm 2026-09-10] ////
def verify_catalog_inputs(catalogs, inputs: dict[str, list[dict]]) -> None:
    for scope, expected in inputs.items():
        if not json_values_equal(catalogs.select(scope).snapshot(), expected):
            raise StorageConflictError("host-catalog-source")


# //// 管理接管开关和带备份的宿主应用入口 [@x380kkm 2026-09-07] ////
class HostControl:
    def __init__(self, catalogs, codex, reader, profile: HostProfile = CODEX,
                 host_root=None) -> None:
        self.catalogs, self.codex, self.reader = catalogs, codex, reader
        self.profile = profile
        self.host_root = codex.root if host_root is None else host_root
        self.previews = {}

    # //// 将用户或选定项目映射到固定宿主目录 [@x380kkm 2026-09-07] ////
    def storage(self, scope: str) -> HostStorage:
        return host_storage(self.catalogs, self.host_root, scope, self.profile)

    # //// 返回接管状态和可选择的恢复记录 [@x380kkm 2026-09-07] ////
    def status(self, scope: str = "user") -> dict:
        storage = self.storage(scope)
        return {**storage.status(), "scope": scope, "targetRoot": str(storage.target_root),
                "backupRoot": str(storage.store.directory), "previewRequired": True}

    # //// 固定首次使用前的宿主配置恢复点 [@x380kkm 2026-09-07] ////
    def initialize(self, scope: str = "user") -> dict:
        self.storage(scope).initialize()
        return self.status(scope)

    # //// 合并进程授权与宿主已发现的来源目录 [@x380kkm 2026-09-08] ////
    def source_reader(self) -> HostSourceReader:
        directories = [self.catalogs.user.workspace / ".agents/skills", self.host_root / "skills",
                       self.host_root / "plugins/cache"]
        roots = [*self.reader.roots, *(path for path in directories if path.is_dir() and not path.is_symlink() and not path.is_junction())]
        return HostSourceReader(self.reader.workspace, roots)

    # //// 编译宿主配置并保存其实际来源观察 [@x380kkm 2026-09-08] ////
    def compile(self, scope: str) -> dict:
        reader = self.source_reader()
        catalogs = HostCatalogInputs(self.catalogs, scope)
        candidates = instruction_candidates(self.profile)
        global_inputs = self.storage("user").capture_instructions(candidates) if scope != "user" else None
        options = {"global_texts": {str(self.host_root / name): (decode_file(value) or b"").decode("utf-8-sig")
                                    for name, value in global_inputs.items()}} if global_inputs is not None else {}
        compiled = compile_host(catalogs, self.codex, reader, scope, profile=self.profile, **options)
        compiled["catalogReads"] = catalogs.documents
        compiled["sourceReads"] = reader.observations
        if global_inputs is not None:
            compiled["globalInstructionReads"] = global_inputs
        if "instructions" in compiled:
            storage = self.storage(scope)
            previous = storage.read_ownership().get(self.profile.rule_file)
            try:
                name, content, inputs, configurations = self.instruction_source(scope, storage, previous)
                base = None if self.profile.manages_instruction_file() else content
                rendered, source = compose_instructions(compiled["instructions"], base, storage.target_root / name,
                                                        previous.get("source") if previous else None)
                compiled["targets"][self.profile.rule_file] = rendered
                compiled.update(instructionReads=inputs, instructionSource=source, configurationReads=configurations)
            except (StorageError, ValueError, OSError) as error:
                compiled["targets"] = {}
                compiled["diagnostics"].append({"severity": "error", "code": "host_instruction_source",
                                                "subject": self.profile.rule_file, "message": str(error)})
        return compiled

    # //// 选择覆盖前的实际说明来源并保存读取基线 [@x380kkm 2026-09-08] ////
    def instruction_source(self, scope: str, storage: HostStorage, previous: dict | None) -> tuple:
        names = instruction_candidates(self.profile)
        configurations = {}
        if scope != "user":
            configurations = {"user": self.storage("user").capture_configuration(), scope: storage.capture_configuration()}
            fallbacks = []
            for files in configurations.values():
                config = tomllib.loads((decode_file(files[self.profile.config_file]) or b"").decode("utf-8-sig"))
                fallbacks = config.get("project_doc_fallback_filenames", fallbacks)
            if not isinstance(fallbacks, list) or any(not isinstance(name, str) for name in fallbacks):
                raise StorageValidationError("说明候选文件需要文件名数组.")
            names.extend(name for name in fallbacks if name not in names)
        inputs = storage.capture_instructions(names)
        candidates = dict(inputs)
        if previous is not None:
            candidates[self.profile.rule_file] = previous["before"]
        for name in names:
            content = decode_file(candidates[name])
            if content and content.decode("utf-8-sig").strip():
                return name, content, inputs, configurations
        return (self.profile.instruction_file, decode_file(candidates[self.profile.instruction_file]),
                inputs, configurations)

    # //// 在提交边界核对编译正文及其说明和配置输入 [@x380kkm 2026-09-08] ////
    def verify_compilation_inputs(self, inputs: dict, written: dict | None = None) -> None:
        written = written or {}
        verify_catalog_inputs(self.catalogs, inputs.get("catalogReads", {}))
        if any(not hook_request_is_current(self.catalogs, request) for request in inputs.get("hookRequests", {}).values()):
            raise StorageConflictError("host-hook-request-source")
        applying_scope = inputs.get("scope", "user")
        storage = self.storage(applying_scope)
        written_paths = {storage._target(name): content for name, content in written.items()}
        verify_source_reads(self.source_reader(), inputs.get("sourceReads", []), written_paths)
        for scope, files in inputs.get("configurationReads", {}).items():
            expected = dict(files)
            if scope == applying_scope and self.profile.config_file in written:
                expected[self.profile.config_file] = encode_file(written[self.profile.config_file])
            if scope == "user" and self.profile.hook_state_name in written:
                expected[self.profile.config_file] = encode_file(written[self.profile.hook_state_name])
            if self.storage(scope).capture_configuration() != expected:
                raise StorageConflictError("host-instruction-config")
        global_inputs = inputs.get("globalInstructionReads", {})
        expected_global = {name: encode_file(written[name]) if applying_scope == "user" and name in written else value
                           for name, value in global_inputs.items()}
        if global_inputs and self.storage("user").capture_instructions(list(global_inputs)) != expected_global:
            raise StorageConflictError("host-global-instruction-source")

    # //// 返回已保存的接管状态与宿主应用结果 [@x380kkm 2026-09-08] ////
    def set_enabled(self, enabled: bool, scope: str = "user", baseline: dict | None = None) -> dict:
        self.storage(scope).enable(enabled, baseline)
        result = self.synchronize_active_scopes(scope) if enabled else {"status": "disabled", "message": "接管已关闭, 宿主继续沿用已应用设置."}
        return {**self.status(scope), "hostSync": result}

    # //// 汇总宿主应用失败的范围和原因 [@x380kkm 2026-09-08] ////
    @staticmethod
    def _synchronization_failure(scope: str, error: Exception) -> dict:
        return {"status": "blocked", "message": f"配置已保存, {scope} 宿主文件应用失败.",
                "diagnostics": [{"scope": scope, "code": getattr(error, "code", "host_write"), "message": str(error)}]}

    # //// 同步保存范围及当前已接管项目的继承配置 [@x380kkm 2026-09-08] ////
    def synchronize_active_scopes(self, scope: str = "user") -> dict:
        try:
            result = self.synchronize(scope)
        except (StorageError, ValueError, OSError) as error:
            return self._synchronization_failure(scope, error)
        if scope != "user" or result["status"] not in {"applied", "unchanged"} or self.catalogs.project is None:
            return result
        project_scope = "project-local"
        try:
            status = self.storage(project_scope).status()
            if not status["initialized"] or not status["enabled"]:
                return result
            project = self.synchronize(project_scope)
        except (StorageError, ValueError, OSError) as error:
            project = self._synchronization_failure(project_scope, error)
        combined = {**result, "scopes": {scope: result, project_scope: project}}
        if project["status"] == "blocked":
            combined.update(status="blocked", message="用户设置已应用, 当前项目 (project-local) 的宿主应用需要处理以下问题.",
                            diagnostics=[{**note, "scope": project_scope} for note in project.get("diagnostics", [])])
        elif project["status"] == "applied":
            combined.update(status="applied", message="用户设置与当前项目的宿主文件已同步.")
            combined.setdefault("backupId", project["backupId"])
        elif project["status"] == "disabled":
            combined["message"] = "用户设置已应用. 当前项目接管已关闭, 项目宿主文件保持原样."
        return combined

    # //// 根据接管状态应用已保存的静态配置 [@x380kkm 2026-09-07] ////
    def synchronize(self, scope: str = "user") -> dict:
        if not self.storage(scope).status()["enabled"]:
            return {"status": "disabled", "message": "配置已保存. 接管已关闭, 宿主继续沿用上次应用的设置."}
        preview = self.preview(scope)
        if preview["planId"] is None:
            return {"status": "blocked", "message": "配置已保存, 宿主应用需要处理载体或范围问题.", "diagnostics": preview["diagnostics"]}
        prepared = self.previews[preview["planId"]]
        if not preview["files"] and prepared["ownership"] == prepared["previousOwnership"]:
            self.previews.pop(preview["planId"], None)
            return {"status": "unchanged", "message": "配置已保存, 宿主文件没有变化."}
        result = self.apply(preview["planId"])
        return {"status": "applied", "message": "配置已应用到宿主文件. 首次原配置恢复点保持不变.", "backupId": result["backupId"]}

    # //// 保存有界预览并返回仅含阅读信息的令牌 [@x380kkm 2026-09-07] ////
    def _remember(self, scope: str, targets: dict, baseline: dict, *, backup_id: str | None = None, contributions=None) -> dict:
        while len(self.previews) >= 4:
            self.previews.pop(next(iter(self.previews)))
        identity = uuid4().hex
        self.previews[identity] = {"scope": scope, "targets": targets, "baseline": baseline, "backupId": backup_id}
        return {"planId": identity, "scope": scope, "files": file_preview(targets, baseline, self.profile),
                "backupId": backup_id, "contributions": contributions or [], "diagnostics": []}

    # //// 编译有效配置并准备文件基线和字段归属 [@x380kkm 2026-09-08] ////
    def _prepare_application(self, scope: str) -> tuple[dict, dict | None]:
        compiled = self.compile(scope)
        if any(note.get("severity") == "error" for note in compiled["diagnostics"]):
            return compiled, None
        storage = self.storage(scope)
        ownership = storage.read_ownership()
        capture_targets = dict(compiled["targets"])
        if self.profile.rule_file in ownership:
            capture_targets.setdefault(self.profile.rule_file, None)
        if "skills" in ownership:
            capture_targets.setdefault(self.profile.config_file, None)
        if "hooks" in ownership or any(entry.get("point") == HOOK_POINT for entry in compiled["contributions"]):
            capture_targets.setdefault(self.profile.hook_file, None)
        if self.profile.hook_state_name and ("hooks" in ownership
                                             or any(entry.get("point") == HOOK_POINT for entry in compiled["contributions"])):
            capture_targets.setdefault(self.profile.config_file if scope == "user" else self.profile.hook_state_name, None)
        baseline = storage.capture(capture_targets)
        self.verify_compilation_inputs(compiled)
        if ownership != storage.read_ownership():
            raise StorageConflictError("host-ownership")
        config_root = storage.target_root / storage.config_subdir
        selected = dict(ownership)
        if "hookRequests" in selected:
            selected["hookRequests"] = {ref: request for ref, request in selected["hookRequests"].items()
                                       if hook_request_is_current(self.catalogs, request)}
            projected = {entry["ref"] for entry in compiled["contributions"] if entry.get("point") == HOOK_POINT}
            if selected["hookRequests"].keys() - projected:
                compiled["diagnostics"].append({"severity": "error", "code": "host_hook_request_scope", "subject": scope,
                                                "message": "Hook 开关请求的绑定范围无法提供对应宿主定义, 请重新确认使用范围."})
                return compiled, None
            if not selected["hookRequests"]:
                selected.pop("hookRequests")
        targets, updated = reconcile(compiled["targets"], compiled["contributions"], baseline["files"], selected,
                                     config_root, self.profile)
        if "instructionSource" in compiled:
            if storage.capture_instructions(list(compiled["instructionReads"])) != compiled["instructionReads"]:
                raise StorageConflictError("host-instruction-source")
            updated[self.profile.rule_file]["source"] = compiled["instructionSource"]
            baseline["instructionReads"] = compiled["instructionReads"]
        baseline["files"] = {name: baseline["files"][name] for name in targets}
        plan = {"scope": scope, "targets": targets, "baseline": baseline, "backupId": None,
                "compiledTargets": compiled["targets"], "compiledContributions": compiled["contributions"],
                "instructionSource": compiled.get("instructionSource"), "ownership": updated, "previousOwnership": ownership,
                "configurationReads": compiled.get("configurationReads", {}),
                "globalInstructionReads": compiled.get("globalInstructionReads", {}), "sourceReads": compiled.get("sourceReads", [])}
        plan["catalogReads"] = compiled["catalogReads"]
        plan["hookRequests"] = selected.get("hookRequests", {})
        return compiled, plan

    # //// 只读检查宿主文件及归属与当前声明的差异 [@x380kkm 2026-09-08] ////
    def inspect(self, scope: str = "user") -> dict:
        result = {"scope": scope, "status": "unchanged", "files": [], "ownershipChanged": False, "diagnostics": []}
        try:
            state = self.storage(scope).status()
            if state["recoveryRequired"]:
                result.update(status="blocked", diagnostics=[{"severity": "error", "code": "host_recovery_required",
                              "subject": scope, "message": "宿主存在未完成的操作, 请先选择恢复点处理."}])
            elif not state["enabled"]:
                result["status"] = "disabled"
            elif not state["initialized"]:
                result["status"] = "uninitialized"
            else:
                compiled, plan = self._prepare_application(scope)
                result["diagnostics"] = compiled["diagnostics"]
                if plan is None:
                    result["status"] = "blocked"
                else:
                    result["files"] = file_preview(plan["targets"], plan["baseline"], self.profile)
                    result["ownershipChanged"] = plan["ownership"] != plan["previousOwnership"]
                    if result["files"] or result["ownershipChanged"]:
                        result["status"] = "pending"
        except (StorageError, ValueError, OSError) as error:
            result.update(status="blocked", diagnostics=[{"severity": "error", "code": getattr(error, "code", "host_inspection"),
                          "subject": scope, "message": str(error)}])
        return result

    # //// 保存文件与归属计划并分配确认预览身份 [@x380kkm 2026-09-08] ////
    def preview(self, scope: str = "user") -> dict:
        compiled, plan = self._prepare_application(scope)
        if plan is None:
            return {"scope": scope, "planId": None, "files": [], "diagnostics": compiled["diagnostics"], "contributions": compiled["contributions"]}
        result = self._remember(scope, plan["targets"], plan["baseline"], contributions=compiled["contributions"])
        self.previews[result["planId"]].update(plan)
        result.update(ownershipChanged=plan["ownership"] != plan["previousOwnership"], diagnostics=compiled["diagnostics"])
        return result

    # //// 以当前宿主内容为基线预览选中备份 [@x380kkm 2026-09-07] ////
    def preview_restore(self, id: str, scope: str = "user") -> dict:
        preview = self.storage(scope).preview_restore(id)
        return self._remember(scope, preview["targets"], preview["baseline"], backup_id=id)

    # //// 重核编译结果后应用或恢复明确选中的文件 [@x380kkm 2026-09-07] ////
    def apply(self, plan_id: str) -> dict:
        plan = self.previews.get(plan_id)
        if plan is None:
            raise StorageValidationError("预览已过期, 请重新预览配置差异.")
        storage = self.storage(plan["scope"])
        if plan["backupId"]:
            result = storage.restore(plan["backupId"], plan["baseline"])
        else:
            if not storage.status()["enabled"]:
                raise StorageValidationError("接管已关闭, 请先开启后再应用.")
            compiled = self.compile(plan["scope"])
            if (any(note.get("severity") == "error" for note in compiled["diagnostics"]) or compiled["targets"] != plan["compiledTargets"]
                    or compiled["catalogReads"] != plan["catalogReads"]
                    or compiled["contributions"] != plan["compiledContributions"]
                    or compiled.get("instructionSource") != plan.get("instructionSource")
                    or compiled.get("sourceReads", []) != plan.get("sourceReads", [])):
                raise StorageConflictError("host-projection")
            result = storage.apply(plan["targets"], plan["baseline"], ownership=plan["ownership"],
                                   verify_inputs=lambda written: self.verify_compilation_inputs(plan, written))
        self.previews.pop(plan_id)
        return {**result, **self.status(plan["scope"])}


# //// 按宿主身份路由接管操作 [@x380kkm 2026-09-24] ////
class HostRouter:
    def __init__(self, controls: dict[str, HostControl]) -> None:
        self.controls = controls

    # //// 取得指定宿主的接管控制器 [@x380kkm 2026-09-24] ////
    def control(self, host: str = CODEX.id) -> HostControl:
        control = self.controls.get(host)
        if control is None:
            raise StorageValidationError(f"未登记的宿主身份: {host}.")
        return control

    # //// 固定首次使用前的宿主配置恢复点 [@x380kkm 2026-09-24] ////
    def initialize(self, scope: str = "user", host: str = CODEX.id) -> dict:
        return {**self.control(host).initialize(scope), "host": host}

    # //// 返回接管状态和可选择的恢复记录 [@x380kkm 2026-09-24] ////
    def status(self, scope: str = "user", host: str = CODEX.id) -> dict:
        return {**self.control(host).status(scope), "host": host}

    # //// 只读比较宿主文件与当前配置 [@x380kkm 2026-09-24] ////
    def inspect(self, scope: str = "user", host: str = CODEX.id) -> dict:
        return self.control(host).inspect(scope)

    # //// 按控制基线切换接管开关 [@x380kkm 2026-09-24] ////
    def set_enabled(self, enabled: bool, scope: str = "user", baseline: dict | None = None,
                    host: str = CODEX.id) -> dict:
        return self.control(host).set_enabled(enabled, scope, baseline)

    # //// 编译候选配置并保留本次计划 [@x380kkm 2026-09-24] ////
    def preview(self, scope: str = "user", host: str = CODEX.id) -> dict:
        return self.control(host).preview(scope)

    # //// 按备份身份编译恢复计划 [@x380kkm 2026-09-24] ////
    def preview_restore(self, id: str, scope: str = "user", host: str = CODEX.id) -> dict:
        return self.control(host).preview_restore(id, scope)

    # //// 提交当前进程持有的宿主计划 [@x380kkm 2026-09-24] ////
    def apply(self, plan_id: str, host: str = CODEX.id) -> dict:
        return self.control(host).apply(plan_id)
