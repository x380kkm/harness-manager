# audience: internal
# # host-projection
"""宿主输出由有效绑定编译为文件字节. 来源读取保留进程授权, 宿主写入由独立存储入口处理."""
from __future__ import annotations

import getpass
import json
import math
import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import tomlkit

from .content import ContentError, read_content_unit, resolve_source
from .card_subjects import source_record
from .content_plan import INSTRUCTION_POINT, PREFERENCE_POINT, SKILL_POINT, TASK_CONTEXT_POINT, select_instructions
from .declarations import index_declarations, matches_version
from .json_codec import encode_json
from .module_contexts import module_contexts
from .native_rule_scope import inactive_global_rules
from .projection import _bindings, _plugin_selection, project_content
from .sources import MAX_CONTENT_BYTES, SourceError

RULE_POINTS = frozenset({INSTRUCTION_POINT, PREFERENCE_POINT})
HOOK_POINT = "hook.x380kkm/lifecycle"
SUPPORTED_POINTS = RULE_POINTS | {SKILL_POINT, TASK_CONTEXT_POINT, HOOK_POINT}
HOOK_EVENTS = frozenset({"SessionStart", "SessionEnd", "SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse",
                         "PermissionRequest", "PreCompact", "PostCompact", "UserPromptSubmit", "Stop", "Interrupt"})


# //// 保存宿主编译的阻断诊断 [@x380kkm 2026-09-07] ////
def compilation_error(diagnostics: list[dict], code: str, subject: str, message: str) -> None:
    diagnostics.append({"severity": "error", "code": code, "subject": subject, "message": message})


# //// 判断声明范围能否由目标根文件表达 [@x380kkm 2026-09-07] ////
def check_carrier_scope(scope: dict | None, subject: str, diagnostics: list[dict], path: str | None = None) -> None:
    selector = (scope or {}).get("selector", {})
    limited = set(selector) & {"task", "path"}
    selected_path = selector.get("path")
    if isinstance(selected_path, list) and len(selected_path) == 1:
        selected_path = selected_path[0]
    if isinstance(path, str) and isinstance(selected_path, str) and os.path.normcase(os.path.abspath(selected_path)) == os.path.normcase(os.path.abspath(path)):
        limited.discard("path")
    agents = selector.get("agent", "all")
    if agents != "all" and agents != ["all"]:
        limited.add("agent")
    if limited:
        compilation_error(diagnostics, "host_scope_adapter", subject,
                          "此范围需要按任务, Agent 或目录部署的载体适配器.")


# //// 核对实际绑定及其成员引用链的静态范围 [@x380kkm 2026-09-07] ////
def check_bound_scopes(documents: list[dict], context: dict, layers: dict, diagnostics: list[dict]) -> None:
    index = index_declarations(documents)
    for document in documents:
        if document["kind"] == "PluginBinding":
            check_carrier_scope(document["target"], document["id"], diagnostics, context.get("path"))
    for identifier, bindings in _bindings(documents, context, index.diagnostics, layers).items():
        selector = _plugin_selection(identifier, bindings, index.diagnostics)
        plugin = index.resolve_plugin(identifier, [selector]) if selector is not None else None
        for alias in plugin["contributions"] if plugin else []:
            reference = f"{plugin['id']}#{alias['id']}"
            for owner, member in index.resolve_reference(reference, plugin["release"]["version"]) or []:
                check_carrier_scope(member.get("scope"), f"{owner['id']}#{member['id']}", diagnostics, context.get("path"))


# //// 从已有绑定取得受管成员而保留实际投影控制启用 [@x380kkm 2026-09-07] ////
def managed_projection(documents: list[dict], context: dict, layers: dict) -> tuple[list, list[dict]]:
    controlled = deepcopy(documents)
    for document in controlled:
        if document["kind"] == "PluginBinding":
            document["enabled"] = True
            document.pop("selection", None)
            document.pop("selectionBaseline", None)
    return project_content(controlled, context, layers=layers)


# //// 按原始来源合并组合别名并核对版本一致性 [@x380kkm 2026-09-07] ////
def unique_sources(entries: list, diagnostics: list[dict]) -> dict:
    result = {}
    for entry in entries:
        reference = entry.summary["content"]
        previous = result.get(reference)
        if previous is not None and previous.summary["content_version"] != entry.summary["content_version"]:
            compilation_error(diagnostics, "host_source_versions", reference, "同一宿主来源需要唯一的内容版本.")
        result.setdefault(reference, entry)
    return result


# //// 合成有效规则位置并按原始内容引用去重 [@x380kkm 2026-09-08] ////
def selected_sources(entries: list, context: dict, diagnostics: list[dict]) -> dict:
    sources = unique_sources(entries, diagnostics)
    try:
        instructions = select_instructions([entry for entry in entries if entry.member["point"] == INSTRUCTION_POINT],
                                           context, diagnostics)
        entries = [entry for entry in sources.values() if entry.member["point"] != INSTRUCTION_POINT] + instructions
    except ContentError:
        compilation_error(diagnostics, "host_instruction_conflict", "AGENTS.override.md", "当前规则位置存在冲突, 请确认有效正文.")
    return unique_sources(entries, diagnostics)


# //// 读取完整正文单元并保持来源授权 [@x380kkm 2026-09-07] ////
def source_unit(entry, index, reader) -> dict:
    result = read_content_unit(index, reader, entry.summary["content"], entry.summary["content_version"],
                               None, MAX_CONTENT_BYTES, None)
    if result["unit_status"] != "ready" or len(result["units"]) != 1:
        raise ContentError("host_complete_source", "宿主输出需要完整来源单元.")
    return result["units"][0]


# //// 从已选择的指令载体取得完整文字 [@x380kkm 2026-09-07] ////
def instruction_text(entry, index, reader) -> str:
    payload = entry.member.get("payload")
    if entry.member.get("source") is not None and isinstance(payload, dict) and payload.get("entry"):
        return source_unit(entry, index, reader)["content"]
    if isinstance(payload, str):
        return payload
    key = "guidance" if entry.member["point"] == PREFERENCE_POINT else "text"
    if isinstance(payload, dict) and isinstance(payload.get(key), str):
        return payload[key]
    raise ContentError("host_instruction_text", "指令载体需要完整 text 或 guidance 正文, 或已授权的文件入口.")


# //// 在有效选择中定位适配文本声明的唯一来源 [@x380kkm 2026-09-07] ////
def adapter_subject(entry, selected: list, enabled_sources: dict):
    payload = entry.member.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or entry.member.get("source") is not None:
        raise ContentError("host_adapter_text", "静态配套说明需要完整的内嵌 text 正文.")
    references = [payload[key] for key in ("skill", "subject") if isinstance(payload.get(key), str) and payload[key]]
    if len(references) != 1:
        raise ContentError("host_adapter_subject", "静态配套说明需要唯一的 skill 或 subject 引用.")
    matches = {candidate.summary["content"]: candidate for candidate in selected
               if candidate.member["point"] in RULE_POINTS | {SKILL_POINT}
               and candidate.summary["content"] in enabled_sources
               and references[0] in {item["ref"] for item in candidate.summary["reference_chain"]}}
    if len(matches) > 1:
        raise ContentError("host_adapter_subject", "静态配套说明的来源引用需要匹配唯一的有效内容.")
    return next(iter(matches.values()), None)


# //// 生成带来源条件的完整静态适配说明 [@x380kkm 2026-09-07] ////
def adapter_text(entry, source) -> str:
    name = " ".join(source.summary["name"].splitlines())
    return f"## 使用 {name} 时\n\n{entry.member['payload']['text']}"


# //// 核对宿主可执行的 Hook 处理器字段 [@x380kkm 2026-09-07] ////
def check_hook_handler(handler: dict, event: str) -> None:
    common = {"type", "timeout", "statusMessage"}
    variants = {"command": {"command", "commandWindows", "async", "additionalContextLimit"},
                "mcp_tool": {"server", "tool", "input"}}
    kind = handler.get("type") if isinstance(handler, dict) else None
    if not isinstance(kind, str) or kind not in variants or set(handler) - common - variants[kind]:
        raise ContentError("host_hook_handler", "Hook 处理器需要当前宿主支持的 command 或 mcp_tool 字段.")
    required = ("command",) if kind == "command" else ("server", "tool")
    if any(not isinstance(handler.get(key), str) or not handler[key].strip() for key in required):
        raise ContentError("host_hook_handler", "Hook 处理器缺少可定位的命令或 MCP 工具名称.")
    if any(key in handler and not isinstance(handler[key], str) for key in ("statusMessage", "commandWindows")):
        raise ContentError("host_hook_handler", "Hook 的状态说明与平台命令需要文本.")
    if "timeout" in handler:
        timeout = handler["timeout"]
        if (type(timeout) not in {int, float} or isinstance(timeout, float) and not math.isfinite(timeout) or timeout <= 0
                or event in {"SessionEnd", "Interrupt"} and not 1 <= timeout <= 3):
            raise ContentError("host_hook_timeout", "Hook 超时需要符合事件的执行时间范围.")
    if ("async" in handler and type(handler["async"]) is not bool
            or "additionalContextLimit" in handler and (type(handler["additionalContextLimit"]) is not int or handler["additionalContextLimit"] <= 0)
            or "input" in handler and not isinstance(handler["input"], dict)
            or kind == "mcp_tool" and event == "SessionEnd"):
        raise ContentError("host_hook_handler", "Hook 处理器字段与当前事件的宿主契约不匹配.")


# //// 将显式生命周期声明转换为完整宿主事件组 [@x380kkm 2026-09-07] ////
def hook_group(entry) -> dict:
    payload = entry.member.get("payload")
    if (not isinstance(payload, dict) or set(payload) - {"name", "event", "matcher", "handlers"}
            or entry.member.get("source") is not None or not isinstance(payload.get("event"), str) or payload["event"] not in HOOK_EVENTS):
        raise ContentError("host_hook_event", "Hook 声明需要当前宿主支持的事件与完整内嵌处理器组.")
    event = payload["event"]
    handlers = payload.get("handlers")
    if not isinstance(handlers, list) or not handlers:
        raise ContentError("host_hook_handler", "Hook 事件组需要至少一个处理器.")
    matcher = payload.get("matcher")
    if matcher is not None and (not isinstance(matcher, str) or event in {"Stop", "Interrupt", "UserPromptSubmit"} and matcher not in {"", "*"}):
        raise ContentError("host_hook_matcher", "此事件无法表达指定的匹配条件.")
    for handler in handlers:
        check_hook_handler(handler, event)
    group = {"hooks": deepcopy(handlers)}
    if matcher is not None:
        group["matcher"] = matcher
    return {"event": event, "group": group}


# //// 取得本地 Skill 的实际入口文件 [@x380kkm 2026-09-08] ////
def skill_entry_path(entry, index, reader) -> Path:
    reference = entry.member.get("source")
    if reference is None:
        raise ContentError("host_skill_source", "Skill 宿主配置需要已物化的本地 SKILL.md 来源.")
    source = resolve_source(entry.owner, reference)
    if source.get("resolver", {}).get("id") != "manager.source/path":
        raise ContentError("host_skill_source", "此 Skill 来源需要物化到本地目录的载体适配器.")
    unit = source_unit(entry, index, reader)
    path = Path(unit.get("path", ""))
    if path.name.casefold() != "skill.md" or not path.is_absolute():
        raise ContentError("host_skill_entry", "Skill 宿主配置需要指向本地 SKILL.md 入口文件.")
    return path


# //// 把现有配置路径规范到 Skill 所在目录 [@x380kkm 2026-09-07] ////
def configured_skill_path(value: str, root: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    if path.name.casefold() == "skill.md":
        path = path.parent
    return os.path.normcase(str(path.resolve()))


# //// 保留 TOML 注释和其他配置并更新受管 Skill [@x380kkm 2026-09-07] ////
def skill_configuration(root: Path, states: dict[Path, bool]) -> bytes:
    path = root / "config.toml"
    if (root.is_symlink() or root.is_junction() or path.is_symlink() or path.is_junction()
            or not path.resolve().is_relative_to(root.resolve())):
        raise ContentError("host_config_boundary", "宿主配置位置需要处于所选配置目录中.")
    text = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    try:
        document = tomlkit.parse(text)
    except ValueError as error:
        raise ContentError("host_config_parse", "现有 config.toml 需要可解析的 TOML 内容.") from error
    skills = document.get("skills")
    if skills is None:
        skills = document["skills"] = tomlkit.table()
    if not isinstance(skills, Mapping):
        raise ContentError("host_skill_configuration", "现有 skills 配置需要 TOML 表.")
    configuration = skills.get("config")
    if configuration is None:
        configuration = skills["config"] = tomlkit.aot()
    if not isinstance(configuration, list) or any(not isinstance(row, Mapping) for row in configuration):
        raise ContentError("host_skill_configuration", "现有 skills.config 需要 Skill 配置数组.")
    pending = {configured_skill_path(str(entry), root): (entry, enabled) for entry, enabled in states.items()}
    found = set()
    for row in configuration:
        value = row.get("path")
        key = configured_skill_path(value, root) if isinstance(value, str) else None
        if key in pending:
            entry, enabled = pending[key]
            row["path"], row["enabled"] = entry.as_posix(), enabled
            found.add(key)
    for key, (entry, enabled) in pending.items():
        if key not in found:
            configuration.append({"path": entry.as_posix(), "enabled": enabled})
    return tomlkit.dumps(document).encode("utf-8")


# //// 核对项目文件无法撤去的全局规则来源 [@x380kkm 2026-09-07] ////
def check_inherited_rules(catalogs, selected: list, enabled_sources: dict, diagnostics: list[dict]) -> set[str]:
    user_view = catalogs.for_scope("user")
    context = {"user": getpass.getuser(), "host": "codex"}
    entries, _ = project_content(user_view.documents, context, layers=user_view.layers)
    entries.extend(module_contexts(entries))
    user_sources = selected_sources(entries, context, diagnostics)
    for reference, entry in user_sources.items():
        inherited = entry.member["point"] in RULE_POINTS
        if entry.member["point"] == TASK_CONTEXT_POINT:
            try:
                inherited = (adapter_subject(entry, entries, user_sources) is not None
                             and adapter_subject(entry, selected, enabled_sources) is not None)
            except ContentError:
                inherited = False
        if inherited and reference not in enabled_sources:
            compilation_error(diagnostics, "host_inherited_rule_scope", reference,
                              "Codex 会先读取用户级规则. 项目文件无法撤去该规则, 请调整用户级配置或把该规则限定为项目级.")
        if entry.member["point"] == HOOK_POINT:
            current = enabled_sources.get(reference)
            try:
                same = current is not None and json.dumps(hook_group(entry), sort_keys=True) == json.dumps(hook_group(current), sort_keys=True)
            except ContentError:
                same = False
            if not same:
                compilation_error(diagnostics, "host_inherited_hook_scope", reference,
                                  "Codex 会累计用户级与项目级 Hooks. 请调整用户级开关或把该 Hook 限定为项目级.")
    return {reference for reference, entry in user_sources.items() if entry.member["point"] == HOOK_POINT}


# //// 将受管成员编译为宿主可读取的规则与配置 [@x380kkm 2026-09-07] ////
def compile_host(catalogs, codex, reader, scope: str = "user", *, global_texts: dict[str, str] | None = None) -> dict:
    view = catalogs.for_scope(scope)
    context = {"user": getpass.getuser(), "host": "codex", **catalogs.context(scope)}
    selected, active_diagnostics = project_content(view.documents, context, layers=view.layers)
    managed, managed_diagnostics = managed_projection(view.documents, context, view.layers)
    selected.extend(module_contexts(selected))
    managed.extend(module_contexts(managed))
    diagnostics = [{**item, "severity": "error"} for item in [*view.diagnostics, *active_diagnostics, *managed_diagnostics]]
    check_bound_scopes(view.documents, context, view.layers, diagnostics)
    enabled_sources = selected_sources(selected, context, diagnostics)
    inherited_hooks = check_inherited_rules(catalogs, selected, enabled_sources, diagnostics) if scope != "user" else set()
    sources = unique_sources(managed, diagnostics)
    index = index_declarations(view.documents)
    targets, contributions, rules, skills, hooks, instructions = {}, [], [], {}, {}, []
    has_rules, has_hooks = False, False
    for reference, entry in sources.items():
        point = entry.member["point"]
        if point == HOOK_POINT and scope != "user" and (reference in inherited_hooks or not any(binding.layer > 0 for binding in entry.bindings)):
            continue
        enabled = reference in enabled_sources
        contract = entry.member["contract"]
        if (point not in SUPPORTED_POINTS or contract["id"] != point
                or matches_version("1.0.0", contract.get("range")) is not True):
            compilation_error(diagnostics, "host_carrier_adapter", reference,
                              "此成员需要能够说明宿主字段与归属的载体适配器.")
            continue
        try:
            if point == HOOK_POINT:
                has_hooks = True
                hook = hook_group(entry)
                if enabled:
                    hooks.setdefault(hook["event"], []).append(hook["group"])
                target = "hooks.json"
            elif point == TASK_CONTEXT_POINT:
                has_rules = True
                source = adapter_subject(entry, selected, enabled_sources)
                enabled = enabled and source is not None
                if enabled:
                    text = adapter_text(entry, source)
                    rules.append(text)
                    instructions.append({"ref": reference, "text": text, "enabled": True})
                target = "AGENTS.override.md"
            elif point in RULE_POINTS:
                has_rules = True
                text = instruction_text(entry, index, reader)
                instructions.append({"ref": reference, "text": text, "enabled": enabled,
                                     "source": deepcopy(source_record(entry.owner))})
                if isinstance(entry.member.get("payload"), dict) and "fragmentTexts" in entry.member["payload"]:
                    instructions[-1]["fragmentTexts"] = deepcopy(entry.member["payload"]["fragmentTexts"])
                if enabled:
                    rules.append(text)
                target = "AGENTS.override.md"
            else:
                skill_path = skill_entry_path(entry, index, reader)
                if skill_path in skills and skills[skill_path] != enabled:
                    raise ContentError("host_skill_conflict", "同一 Skill 目录存在互相冲突的启用状态.")
                skills[skill_path] = enabled
                target = "config.toml"
            contributions.append({"ref": reference, "name": entry.summary["name"], "point": point,
                                  "enabled": enabled, "target": target})
            if point == SKILL_POINT:
                contributions[-1]["path"] = skill_path.parent.as_posix()
            elif point == HOOK_POINT:
                contributions[-1]["hook"] = hook
        except (ContentError, SourceError, OSError, ValueError) as error:
            compilation_error(diagnostics, getattr(error, "code", "host_source_access"), reference,
                              "成员的完整正文或本地来源无法用于宿主输出, 请确认来源授权与载体配置.")
    if scope != "user":
        inactive, native_diagnostics = inactive_global_rules(codex, instructions, global_texts=global_texts)
        diagnostics.extend(native_diagnostics)
        if inactive:
            instructions = [entry for entry in instructions if entry["ref"] not in inactive]
            if not instructions:
                has_rules = False
    if has_rules:
        targets["AGENTS.override.md"] = ("# Instructions\n\n" + "\n\n".join(rules) + "\n").encode("utf-8")
    if has_hooks:
        targets["hooks.json"] = (encode_json({"hooks": hooks}, indent=2) + "\n").encode("utf-8")
    if skills:
        root = codex.root if scope == "user" else catalogs.project.workspace / ".codex"
        try:
            targets["config.toml"] = skill_configuration(root, skills)
        except (ContentError, OSError, ValueError) as error:
            compilation_error(diagnostics, getattr(error, "code", "host_config_read"), "config.toml",
                              "现有宿主配置需要可保留的 TOML 结构与读取位置.")
    diagnostics = list({(item.get("code"), item.get("subject"), item.get("message")): item for item in diagnostics}.values())
    diagnostics = [{**item, "severity": "error"} for item in diagnostics]
    if diagnostics:
        targets = {}
    result = {"targets": targets, "diagnostics": diagnostics, "contributions": contributions}
    if has_rules and not diagnostics:
        result["instructions"] = instructions
    return result
