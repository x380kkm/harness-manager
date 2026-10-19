# audience: internal
# # agent-connection
"""连接描述固定当前 Python 启动环境与来源范围. 输出由用户或外部客户端纳入宿主配置."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .json_codec import encode_json


# //// 生成明确目录和来源权限的 stdio 连接描述 [@x380kkm 2026-09-08] ////
def connection_config(name: str, *, workspace: Path | None = None, user_root: Path | None = None,
                      read_roots: list[Path] | None = None, compact: bool = True) -> dict:
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
        raise ValueError("连接名称需要英文字母, 数字, 连字符或下划线.")
    arguments = ["-m", "harness_manager.cli"]
    for flag, value in (("--workspace", workspace), ("--user-root", user_root)):
        if value is not None:
            arguments.extend([flag, str(value.expanduser().resolve())])
    for root in read_roots or []:
        arguments.extend(["--read-root", str(root.expanduser().resolve())])
    arguments.append("mcp")
    if compact:
        arguments.append("--compact")
    return {"mcp_servers": {name: {"command": sys.executable, "args": arguments, "cwd": str(Path.cwd())}}}


# //// 将连接字段输出为 Codex 使用的 TOML 片段 [@x380kkm 2026-09-08] ////
def connection_toml(configuration: dict) -> str:
    name, fields = next(iter(configuration["mcp_servers"].items()))
    return "\n".join([f"[mcp_servers.{name}]", *(key + " = " + encode_json(value) for key, value in fields.items())])
