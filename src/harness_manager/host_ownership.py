# audience: internal
# # host-ownership
"""宿主归属记录保存指令覆盖原文与 Skill 开关字段. 调和只处理调用者提供的文件字节."""
from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import tomlkit
from tomlkit.items import Comment, Whitespace

from .content_plan import SKILL_POINT
from .host_hooks import HOOK_POINT, reconcile_hooks
from .storage_errors import StorageConflictError, StorageValidationError

RULE_FILE = "AGENTS.override.md"
CONFIG_FILE = "config.toml"
SKILL_FIELDS = frozenset({"path", "enabled"})
TARGET_FILES = frozenset({RULE_FILE, CONFIG_FILE, "hooks.json"})


# //// 从快照取得可恢复的文件字节 [@x380kkm 2026-09-07] ////
def decode_file(value: str | None) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageValidationError("宿主原文需要 Base64 文本或空值.")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise StorageValidationError("宿主原文需要有效的 Base64 文本.") from error


# //// 将归属原文存为 JSON 可保存的内容 [@x380kkm 2026-09-07] ////
def encode_file(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")


# //// 核对固定文件已由调用者提供当前基线 [@x380kkm 2026-09-07] ////
def current_file(files: dict, name: str) -> bytes | None:
    if name not in files:
        raise StorageValidationError(f"宿主调和需要 {name} 的当前文件基线.")
    return decode_file(files[name])


# //// 按配置目录规范化 Skill 路径文本 [@x380kkm 2026-09-07] ////
def skill_key(value: str, root: Path) -> str:
    if not isinstance(value, str) or not value:
        raise StorageValidationError("Skill 归属需要明确的目录路径.")
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        path = root / path
    if path.name.casefold() == "skill.md":
        path = path.parent
    return os.path.normcase(os.path.normpath(str(path)))


# //// 解析文件字节并保留 TOML 注释 [@x380kkm 2026-09-07] ////
def config_document(content: bytes | None):
    try:
        return tomlkit.parse((content or b"").decode("utf-8-sig"))
    except (UnicodeError, ValueError) as error:
        raise StorageValidationError("Skill 归属调和需要可解析的 UTF-8 TOML 配置.") from error


# //// 取得现有 Skill 数组和所属表 [@x380kkm 2026-09-07] ////
def skill_config(document) -> tuple[Mapping | None, list | None]:
    skills = document.get("skills")
    if skills is None:
        return None, None
    if not isinstance(skills, Mapping):
        raise StorageValidationError("skills 配置需要 TOML 表.")
    rows = skills.get("config")
    if rows is not None and (not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows)):
        raise StorageValidationError("skills.config 需要配置对象数组.")
    return skills, rows


# //// 按原始数组顺序汇集同目录的配置行 [@x380kkm 2026-09-07] ////
def rows_by_path(rows: list | None, root: Path) -> dict:
    grouped = {}
    for index, row in enumerate(rows or []):
        if isinstance(row.get("path"), str) and row["path"]:
            grouped.setdefault(skill_key(row["path"], root), []).append((index, row))
    return grouped


# //// 提取开关归属涉及的字段及其存在性 [@x380kkm 2026-09-07] ////
def owned_fields(row: Mapping) -> dict:
    fields = {key: row[key] for key in SKILL_FIELDS if key in row}
    if ("path" in fields and not isinstance(fields["path"], str)
            or "enabled" in fields and type(fields["enabled"]) is not bool):
        raise StorageValidationError("受管 Skill 的 path 与 enabled 需要路径文本和布尔值.")
    return fields


# //// 收集待移除载体上的独立注释与行末注释 [@x380kkm 2026-09-07] ////
def carrier_comments(value) -> list[str]:
    comments = []
    trivia = getattr(value, "trivia", None)
    if trivia is not None and trivia.comment:
        comments.append(trivia.comment)
    container = getattr(value, "value", None)
    for _, item in getattr(container, "body", []):
        if isinstance(item, Comment):
            comments.append(item.as_string().rstrip("\r\n"))
        elif not isinstance(item, Whitespace):
            item_trivia = getattr(item, "trivia", None)
            if item_trivia is not None and item_trivia.comment:
                comments.append(item_trivia.comment)
    return comments


# //// 在保留的表中保存移除字段上的注释 [@x380kkm 2026-09-07] ////
def keep_comments(table, comments: list[str]) -> None:
    for comment in comments:
        table.add(tomlkit.comment(comment.lstrip().removeprefix("#").lstrip()))


# //// 恢复受管字段并保留行上的其他内容 [@x380kkm 2026-09-07] ////
def restore_fields(row, fields: dict, parent) -> None:
    for key in SKILL_FIELDS:
        if key in fields:
            row[key] = fields[key]
        elif key in row:
            keep_comments(parent, carrier_comments(row.item(key)))
            del row[key]


# //// 核对每个已接管目录的行数与最近写入字段 [@x380kkm 2026-09-07] ////
def check_owned_rows(entries: dict, grouped: dict) -> None:
    for key, record in entries.items():
        if (not isinstance(record, dict) or set(record) != {"before", "last"}
                or not isinstance(record["before"], list) or not isinstance(record["last"], list)
                or not record["last"] or record["before"] and len(record["before"]) != len(record["last"])):
            raise StorageValidationError("Skill 归属需要完整的前值与最近写入字段.")
        for fields in [*record["before"], *record["last"]]:
            if not isinstance(fields, dict) or set(fields) - SKILL_FIELDS:
                raise StorageValidationError("Skill 归属仅保存 path 与 enabled 字段.")
            owned_fields(fields)
        if [owned_fields(row) for _, row in grouped.get(key, [])] != record["last"]:
            raise StorageConflictError("host-skill-fields")


# //// 恢复失去归属的 Skill 开关并保留其他字段 [@x380kkm 2026-09-07] ////
def release_skills(document, rows, grouped: dict, entries: dict, managed: set[str]) -> None:
    removed = []
    for key, record in entries.items():
        if key in managed:
            continue
        matches = grouped[key]
        if record["before"]:
            for (_, row), fields in zip(matches, record["before"]):
                restore_fields(row, fields, document)
        else:
            for index, row in matches:
                if set(row) - SKILL_FIELDS:
                    raise StorageConflictError("host-skill-added-fields")
                keep_comments(document, carrier_comments(row))
                removed.append(index)
    for index in sorted(removed, reverse=True):
        del rows[index]


# //// 为当前受管 Skill 采用编译后的独立开关字段 [@x380kkm 2026-09-07] ////
def apply_skill_fields(rows, grouped: dict, desired: dict, entries: dict, managed: set[str]) -> dict:
    result = {}
    for key in sorted(managed):
        wanted = [owned_fields(row) for _, row in desired.get(key, [])]
        if not wanted or any(set(fields) != SKILL_FIELDS for fields in wanted):
            raise StorageValidationError("编译配置需要每个受管 Skill 的 path 与 enabled.")
        existing = [row for _, row in grouped.get(key, [])]
        before = entries[key]["before"] if key in entries else [owned_fields(row) for row in existing]
        if existing and len(existing) != len(wanted):
            raise StorageConflictError("host-skill-row-count")
        if existing:
            for row, fields in zip(existing, wanted):
                row.update(fields)
        else:
            for fields in wanted:
                rows.append(fields)
        result[key] = {"before": before, "last": wanted}
    return result


# //// 撤去由管理器创建且已空的配置容器 [@x380kkm 2026-09-07] ////
def release_empty_tables(document, skills, rows, record: dict) -> None:
    if rows is not None and not rows and record["createdConfig"]:
        keep_comments(document, carrier_comments(rows))
        del skills["config"]
    if skills is not None and not skills and record["createdSkills"] and not carrier_comments(skills):
        del document["skills"]


# //// 以当前 TOML 为底稿调和受管 Skill 字段 [@x380kkm 2026-09-07] ////
def reconcile_skills(targets: dict, contributions: list, baseline: dict, ownership: dict, root: Path) -> None:
    active = [entry for entry in contributions if entry.get("point") == SKILL_POINT]
    managed = {skill_key(entry.get("path"), root) for entry in active}
    previous = ownership.get("skills")
    if not managed and previous is None:
        if CONFIG_FILE in targets:
            raise StorageValidationError("配置输出需要明确的受管 Skill 路径.")
        return
    current = config_document(current_file(baseline, CONFIG_FILE))
    skills, rows = skill_config(current)
    record = previous or {"createdSkills": skills is None, "createdConfig": rows is None, "entries": {}}
    if (not isinstance(record, dict) or set(record) != {"createdSkills", "createdConfig", "entries"}
            or type(record["createdSkills"]) is not bool or type(record["createdConfig"]) is not bool
            or not isinstance(record["entries"], dict)):
        raise StorageValidationError("Skill 归属记录的配置容器信息无效.")
    grouped = rows_by_path(rows, root)
    check_owned_rows(record["entries"], grouped)
    release_skills(current, rows, grouped, record["entries"], managed)
    if managed:
        if CONFIG_FILE not in targets:
            raise StorageValidationError("受管 Skill 需要编译后的 config.toml.")
        _, compiled = skill_config(config_document(targets[CONFIG_FILE]))
        desired = rows_by_path(compiled, root)
        if skills is None:
            skills = current["skills"] = tomlkit.table()
        if rows is None:
            rows = skills["config"] = tomlkit.aot()
        record["entries"] = apply_skill_fields(rows, rows_by_path(rows, root), desired, record["entries"], managed)
        ownership["skills"] = record
    else:
        release_empty_tables(current, skills, rows, record)
        ownership.pop("skills", None)
    targets[CONFIG_FILE] = tomlkit.dumps(current).encode("utf-8")


# //// 恢复或接续整份指令覆盖文件的归属 [@x380kkm 2026-09-07] ////
def reconcile_rules(targets: dict, baseline: dict, ownership: dict) -> None:
    previous = ownership.get(RULE_FILE)
    if RULE_FILE not in targets and previous is None:
        return
    current = current_file(baseline, RULE_FILE)
    if previous is not None:
        if not isinstance(previous, dict) or not {"before", "last"} <= set(previous) <= {"before", "last", "source"}:
            raise StorageValidationError("指令归属需要原文与最近写入内容.")
        if current != decode_file(previous["last"]):
            raise StorageConflictError("host-instructions")
    if RULE_FILE in targets:
        before = previous["before"] if previous is not None else encode_file(current)
        ownership[RULE_FILE] = {"before": before, "last": encode_file(targets[RULE_FILE])}
        if previous is not None and "source" in previous:
            ownership[RULE_FILE]["source"] = deepcopy(previous["source"])
    else:
        targets[RULE_FILE] = decode_file(previous["before"])
        ownership.pop(RULE_FILE)


# //// 调和当前编译输出及被撤销的宿主字段归属 [@x380kkm 2026-09-07] ////
def reconcile(targets: dict[str, bytes], contributions: list, baselineFiles: dict,
              ownership: dict, configRoot: Path) -> tuple[dict, dict]:
    if (not isinstance(targets, dict) or not targets.keys() <= TARGET_FILES
            or any(not isinstance(value, bytes) for value in targets.values())
            or not isinstance(ownership, dict) or not ownership.keys() <= {RULE_FILE, "skills", "hooks"}
            or not isinstance(contributions, list) or any(not isinstance(entry, dict) for entry in contributions)
            or not Path(configRoot).is_absolute()):
        raise StorageValidationError("宿主归属调和需要固定目标, 独立开关和绝对配置目录.")
    result, updated = dict(targets), deepcopy(ownership)
    reconcile_rules(result, baselineFiles, updated)
    reconcile_skills(result, contributions, baselineFiles, updated, Path(configRoot))
    if "hooks" in updated or "hooks.json" in result or any(entry.get("point") == HOOK_POINT for entry in contributions):
        reconcile_hooks(result, contributions, current_file(baselineFiles, "hooks.json"), updated)
    return result, updated
