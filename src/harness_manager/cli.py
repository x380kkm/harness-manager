# audience: internal
# # harness-cli
"""命令行使用用户级默认目录, 显式项目目录参与局部配置合成. 来源读取授权由进程入口单独提供."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .rpc import error_record, handle_request, serve
from .agent_connection import connection_config, connection_toml
from .json_codec import decode_json, encode_json
from .service import Manager
from .storage_errors import StorageError


# //// 定义管理入口及进程级读取范围 [@x380kkm 2026-09-06] ////
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理本地 Harness 声明与内容引用")
    parser.add_argument("--workspace", type=Path, help="显式加入项目局部配置")
    parser.add_argument("--user-root", type=Path, help="用户级声明目录所在的根目录")
    parser.add_argument("--read-root", type=Path, action="append", default=[], help="额外允许读取的来源目录, 可重复指定")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rpc", help="通过逐行 JSON 处理本地管理调用")
    mcp = commands.add_parser("mcp", help="启动外部 Agent 使用的 stdio MCP 接口")
    mcp.add_argument("--compact", action="store_true", help="使用四个发现和调用入口, 包含只读查询与写入操作")
    api = commands.add_parser("api", help="查询方法目录或单个方法的输入和副作用")
    api.add_argument("method", nargs="?", help="方法名; 省略时列出方法摘要")
    api.add_argument("--group", default="", help="按方法组筛选目录, 如 module 或 host")
    api.add_argument("--limit", type=int, default=20, help="每页方法数, 1 至 100, 默认 20")
    api.add_argument("--cursor", type=int, default=0, help="上一页返回的 nextCursor, 默认从首项开始")
    guide = commands.add_parser("guide", help="按主题读取外部管理说明")
    guide.add_argument("topic", nargs="?", default="overview", help="帮助主题, 默认 overview 返回主题目录")
    connection = commands.add_parser("connection", help="输出 Codex MCP 配置片段, 由用户选择保存位置")
    connection.add_argument("--name", default="harness_manager", help="MCP 连接名称, 默认 harness_manager")
    connection.add_argument("--format", choices=["toml", "json"], default="toml", help="输出格式, 默认 toml")
    connection.add_argument("--full", action="store_true", help="使用完整 MCP 工具列表")
    host = commands.add_parser("host", help="在同一进程中预览并确认宿主文件变化")
    host.add_argument("--scope", choices=["user", "project", "project-local"], default="user", help="宿主配置范围; 项目范围同时需要 --workspace")
    host_commands = host.add_subparsers(dest="host_action", required=True)
    host_commands.add_parser("apply", help="预览当前配置, 输入 apply 后应用")
    restore = host_commands.add_parser("restore", help="预览所选备份, 输入 restore 后恢复")
    restore.add_argument("backup_id", help="host.status 返回的备份身份")
    call = commands.add_parser("call", help="执行一个管理方法")
    call.add_argument("method", help="agent.capabilities 列出的管理方法")
    call.add_argument("--params", default="{}", help="JSON 对象; - 表示从 stdin 读取")
    return parser


# //// 在单个 CLI 会话中保存用户确认的宿主预览 [@x380kkm 2026-09-08] ////
def confirm_host(manager: Manager, action: str, scope: str, backup_id: str | None = None) -> int:
    method = "host.preview_restore" if action == "restore" else "host.preview"
    params = {"scope": scope, **({"id": backup_id} if action == "restore" else {})}
    response = handle_request(manager, {"id": "preview", "method": method, "params": params})
    print(encode_json(response), flush=True)
    if "error" in response or response["result"].get("planId") is None:
        return 2
    print(f"确认上述文件变化后输入 {action}, 其他输入取消: ", end="", file=sys.stderr, flush=True)
    if sys.stdin.readline(32).strip() != action:
        print(encode_json({"id": "apply", "result": {"status": "cancelled"}}))
        return 0
    applied = handle_request(manager, {"id": "apply", "method": "host.apply", "params": {"plan_id": response["result"]["planId"]}})
    print(encode_json(applied))
    return 2 if "error" in applied else 0


# //// 启动选定的管理传输入口 [@x380kkm 2026-09-06] ////
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.command == "connection":
            config = connection_config(args.name, workspace=args.workspace, user_root=args.user_root,
                                       read_roots=args.read_root, compact=not args.full)
            print(connection_toml(config) if args.format == "toml" else encode_json(config, indent=2))
            return 0
        manager = Manager(args.workspace, args.read_root, user_root=args.user_root)
        if args.command == "rpc":
            serve(manager, sys.stdin, sys.stdout)
            return 0
        if args.command == "mcp":
            from .mcp_server import create_server
            create_server(manager, compact=args.compact).run()
            return 0
        if args.command == "api":
            print(encode_json(manager.agent.capabilities(args.method, args.group, args.limit, args.cursor), indent=2))
            return 0
        if args.command == "guide":
            print(encode_json(manager.agent.help(args.topic), indent=2))
            return 0
        if args.command == "host":
            return confirm_host(manager, args.host_action, args.scope, getattr(args, "backup_id", None))
        if args.method == "host.apply":
            raise ValueError("host planId 需要原进程. 使用 host apply, host restore 或保持 MCP/RPC 连接.")
        raw = sys.stdin.read() if args.params == "-" else args.params
        response = handle_request(manager, {"id": "cli", "method": args.method, "params": decode_json(raw)})
        if args.method in {"host.preview", "host.preview_restore"} and "result" in response:
            response["cli"] = {"planLifetime": "process", "applyWith": "host apply 或 host restore 在同一命令中预览和确认."}
        print(encode_json(response, indent=2))
        return 2 if "error" in response else 0
    except (StorageError, ValueError, OSError) as error:
        print(encode_json({"error": error_record(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
