# audience: internal
# # host-profiles
"""宿主档案给出单个 Agent 宿主的固定文件集, 目录布局与状态模型. 写入路径按档案取值, 同一套编译, 存档与归属逻辑服务所有宿主.

各宿主的存档按作用域身份前缀分开保存: 身份形如 `<档案 id>-user` 或 `<档案 id>-<项目摘要>`, 目录为 `.harness/hosts/<作用域身份>`, 因此既有记录不需要迁移.

`rule_file` 等于 `instruction_file` 表示受管说明就是宿主的原文说明文件, 整份正文由受管成员生成, 不按片段映射回原文.
`hook_file` 等于 `config_file` 表示该宿主的 Hook 定义内嵌在主配置中, 写入时保留主配置的其余字段.
`hook_state_name` 为空表示该宿主没有独立的逐处理器开关, 启停因此以写入与否表达: 未取得启用请求的事件组不写入, 主配置保持原样.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


# //// 描述一个宿主的固定文件集与目录布局 [@x380kkm 2026-09-24] ////
@dataclass(frozen=True)
class HostProfile:
    id: str
    config_subdir: str
    home_variable: str
    instruction_file: str
    rule_file: str
    config_file: str
    hook_file: str
    hook_state_name: str
    skills_in_config: bool
    reads_native_rules: bool
    source_names: tuple[str, ...]
    root_names: frozenset[str]
    target_names: frozenset[str]

    # //// 生成当前宿主的作用域身份 [@x380kkm 2026-09-24] ////
    def scope_id(self, suffix: str) -> str:
        return f"{self.id}-{suffix}"

    # //// 判断作用域身份是否属于当前宿主的用户范围 [@x380kkm 2026-09-24] ////
    def is_user_scope(self, scope_id: str) -> bool:
        return scope_id == self.scope_id("user")

    # //// 取得编译输出可写的目标集合 [@x380kkm 2026-09-24] ////
    def compiled_names(self) -> frozenset[str]:
        return self.target_names - {self.hook_state_name}

    # //// 判断受管说明是否就是宿主的原文说明文件 [@x380kkm 2026-09-24] ////
    def manages_instruction_file(self) -> bool:
        return self.rule_file == self.instruction_file

    # //// 判断 Hook 定义是否内嵌在主配置文件 [@x380kkm 2026-09-24] ////
    def hooks_in_config(self) -> bool:
        return self.hook_file == self.config_file


CODEX = HostProfile(
    id="codex",
    config_subdir=".codex",
    home_variable="CODEX_HOME",
    instruction_file="AGENTS.md",
    rule_file="AGENTS.override.md",
    config_file="config.toml",
    hook_file="hooks.json",
    hook_state_name="hook-state.toml",
    skills_in_config=True,
    reads_native_rules=True,
    source_names=("AGENTS.md", "AGENTS.override.md", "config.toml", "hooks.json"),
    root_names=frozenset({"AGENTS.md", "AGENTS.override.md"}),
    target_names=frozenset({"AGENTS.override.md", "config.toml", "hooks.json", "hook-state.toml"}),
)

CLAUDE = HostProfile(
    id="claude",
    config_subdir=".claude",
    home_variable="CLAUDE_CONFIG_DIR",
    instruction_file="CLAUDE.md",
    rule_file="CLAUDE.md",
    config_file="settings.json",
    hook_file="settings.json",
    hook_state_name="",
    skills_in_config=False,
    reads_native_rules=False,
    source_names=("CLAUDE.md", "settings.json"),
    root_names=frozenset({"CLAUDE.md"}),
    target_names=frozenset({"CLAUDE.md", "settings.json"}),
)

PROFILES = {CODEX.id: CODEX, CLAUDE.id: CLAUDE}


# //// 解析宿主在本机的根目录 [@x380kkm 2026-09-24] ////
def host_root(profile: HostProfile, user_root: Path) -> Path:
    user_root = Path(user_root).expanduser().absolute()
    configured = os.environ.get(profile.home_variable) if user_root.resolve() == Path.home().resolve() else None
    return (Path(configured) if configured else user_root / profile.config_subdir).expanduser().absolute()


# //// 按宿主身份取得档案 [@x380kkm 2026-09-24] ////
def host_profile(host: str = CODEX.id) -> HostProfile:
    profile = PROFILES.get(host)
    if profile is None:
        raise KeyError(f"未登记的宿主身份: {host}.")
    return profile
