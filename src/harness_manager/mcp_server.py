# audience: internal
# # harness-mcp
"""stdio MCP 使用进程指定的目录与读取范围. 配置写入与内容观察分别声明副作用, 详细帮助按主题读取."""
from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .rpc import handle_request
from .service import Manager


# //// 注册与桌面共用的管理用例 [@x380kkm 2026-09-06] ////
def create_server(manager: Manager, *, compact: bool = False) -> MCPServer:
    server = MCPServer("Harness Manager", instructions=(
        "这是 Harness Manager 的通用本地工具集, 覆盖 Skill 和内容读取, 规则与声明编辑, 模块与关系, 宿主接管备份恢复, 项目搬移和诊断. "
        "compact MCP 只暴露发现和路由入口: skill_list, agent_capabilities, agent_help, manager_query, manager_action. "
        "先用 agent_help('toolkit') 或 skill_list 了解可用能力, 再用 agent_capabilities 读取具体输入与副作用. "
        "先查询摘要再读取单项, 编辑携带基线并确认预览. 首次接管读取 takeover. "
        "写入可能应用宿主文件, 需要用户修改授权; host planId 只在当前连接有效."))
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    observation = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    # //// 保留核心的结构化错误与读取边界 [@x380kkm 2026-09-06] ////
    def invoke(method: str, params: dict | None = None) -> Any:
        response = handle_request(manager, {"id": "mcp", "method": method, "params": params})
        if "error" in response:
            raise ToolError(json.dumps(response["error"], ensure_ascii=False))
        return response["result"]

    # //// 按需发现方法的真实输入与持久化边界 [@x380kkm 2026-09-08] ////
    @server.tool(annotations=read_only)
    def agent_capabilities(method: str | None = None, group: str = "", limit: int = 20, cursor: int = 0) -> dict[str, Any]:
        """分页查询方法; 指定 method 返回实际输入 Schema, 副作用和下一步帮助."""
        return invoke("agent.capabilities", {"method": method, "group": group, "limit": limit, "cursor": cursor})

    # //// 读取一个管理主题的操作说明 [@x380kkm 2026-09-08] ////
    @server.tool(annotations=read_only)
    def agent_help(topic: str = "overview") -> dict[str, Any]:
        """按需读取 overview 返回的单个帮助主题, 包括 takeover, rules, modules, host 和 content."""
        return invoke("agent.help", {"topic": topic})

    # //// 将查询限定到明确的只读管理方法 [@x380kkm 2026-09-08] ////
    @server.tool(annotations=read_only)
    def manager_query(method: str, params: dict | None = None) -> dict[str, Any]:
        """执行 agent_capabilities 标为 readOnly 的方法, params 遵循该方法的 inputSchema."""
        try:
            manager.agent.require_query(method)
        except ValueError as error:
            raise ToolError(str(error)) from error
        return invoke(method, params)

    # //// 调用已经获得授权的数据保存或宿主修改用例 [@x380kkm 2026-09-08] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def manager_action(method: str, params: dict | None = None) -> dict[str, Any]:
        """执行含持久化副作用的方法. 先读取 agent_capabilities 的输入与授权边界, 再传完整基线或已确认计划."""
        return invoke(method, params)

    # //// 直接列出当前范围可使用的 Skill [@x380kkm 2026-09-09] ////
    @server.tool(annotations=read_only)
    def skill_list(context: dict | None = None, query: str = "", limit: int = 20, cursor: int = 0,
                   scope: str = "user") -> dict[str, Any]:
        """返回 Skill 名称, 摘要, 版本和下一步 content.open 入口."""
        return invoke("skill.list", {"context": context or {"host": "harness-manager"}, "query": query,
                                      "limit": limit, "cursor": cursor, "scope": scope})

    # //// 公开帮助目录和按需主题资源 [@x380kkm 2026-09-08] ////
    @server.resource("harness://agent/overview", mime_type="application/json")
    def agent_overview() -> str:
        """外部 Agent 的管理入口与帮助目录."""
        return json.dumps(invoke("agent.help"), ensure_ascii=False)

    # //// 按资源主题读取管理说明 [@x380kkm 2026-09-08] ////
    @server.resource("harness://agent/{topic}", mime_type="application/json")
    def agent_topic(topic: str) -> str:
        """读取 overview 目录中的一个帮助主题."""
        return json.dumps(invoke("agent.help", {"topic": topic}), ensure_ascii=False)

    # //// 提供可供编辑器和外部工具读取的 Schema [@x380kkm 2026-09-06] ////
    @server.resource("harness://protocol/schema", mime_type="application/json")
    def protocol_schema() -> str:
        """返回管理声明的 JSON Schema."""
        return json.dumps(invoke("protocol.schema"), ensure_ascii=False)

    if compact:
        return server

    # //// 读取静态宿主管理状态及初始备份摘要 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def host_status(scope: str = "user") -> dict[str, Any]:
        """查询接管开关和固定原始恢复点, 正文保存在本机备份中."""
        return invoke("host.status", {"scope": scope})

    # //// 只读比较已接管范围中的配置与宿主文件 [@x380kkm 2026-09-08] ////
    @server.tool(annotations=read_only)
    def host_inspect(scope: str = "user") -> dict[str, Any]:
        """返回 pending, unchanged 或具体阻断状态和差异, 应用通过独立的宿主预览确认."""
        return invoke("host.inspect", {"scope": scope})

    # //// 固定首次使用前的原始宿主配置 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def host_initialize(scope: str = "user") -> dict[str, Any]:
        """一次性保存原配置, 再次调用保持原始恢复点."""
        return invoke("host.initialize", {"scope": scope})

    # //// 调整后续管理写入并保留当前宿主文件 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def host_set_enabled(enabled: bool, baseline: dict, scope: str = "user") -> dict[str, Any]:
        """使用 host_status 的 baseline 调整接管. 开启应用配置, 关闭保留文件; 使用由宿主直接完成."""
        return invoke("host.set_enabled", {"enabled": enabled, "baseline": baseline, "scope": scope})

    # //// 预览当前静态配置的宿主文件变化 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def host_preview(scope: str = "user") -> dict[str, Any]:
        """返回静态文件差异和本次服务内有效的 planId, 配置机密字段保留在本机."""
        return invoke("host.preview", {"scope": scope})

    # //// 预览选中原始配置或恢复保护点 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def host_preview_restore(id: str, scope: str = "user") -> dict[str, Any]:
        """用 host_status 返回的备份身份预览恢复. 原始配置与恢复前保护副本分开保存."""
        return invoke("host.preview_restore", {"id": id, "scope": scope})

    # //// 应用既有预览并保存文件恢复信息 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def host_apply(plan_id: str) -> dict[str, Any]:
        """应用 host_preview 或 host_preview_restore 的 planId. 恢复原配置后关闭接管."""
        return invoke("host.apply", {"plan_id": plan_id})

    # //// 读取模块组合和独立成员候选 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def module_describe(id: str | None = None, scope: str = "user") -> dict[str, Any]:
        """读取模块表单与基线, 省略 id 返回新建模块的候选内容."""
        return invoke("module.describe", {"id": id, "scope": scope})

    # //// 为模块组成准备原子保存计划 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def module_preview(name: str, description: str, members: list[dict], scope: str = "user", id: str | None = None, baseline: dict | None = None) -> dict[str, Any]:
        """members 使用 itemId 和 role, 保存独立来源版本引用; 使用开关另由绑定控制."""
        return invoke("module.preview", {"name": name, "description": description, "members": members, "scope": scope, "id": id, "baseline": baseline})

    # //// 原子保存模块及采用的独立来源 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def module_apply(plan: dict) -> dict[str, Any]:
        """提交 module_preview 的 plan, 来源变化时保留当前文件并要求重新预览."""
        return invoke("module.apply", {"plan": plan})

    # //// 提供有界本机内容摘要 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def codex_list(query: str = "", kind: str = "", limit: int = 20, cursor: int = 0) -> dict[str, Any]:
        """只读盘点本机 Codex 来源, 返回摘要与原文身份. kind 使用 skill, rule, hook, instruction, mcp, plugin, config, component 或 source."""
        return invoke("codex.list", {"query": query, "kind": kind, "limit": limit, "cursor": cursor})

    # //// 提供默认个人范围的聚合内容查询 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def codex_groups(query: str = "", origin: str = "user", limit: int = 20, cursor: int = 0) -> dict[str, Any]:
        """按来源聚合本机内容, 默认查询个人组. origin=official 读取官方组, itemIds 可交给 codex_read."""
        return invoke("codex.groups", {"query": query, "origin": origin, "limit": limit, "cursor": cursor})

    # //// 读取本机内容与来源关系 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def codex_read(id: str) -> dict[str, Any]:
        """读取 codex_list 返回的观察身份, 正文限于 Skill 和说明载体, 配置返回安全摘要."""
        return invoke("codex.read", {"id": id})

    # //// 准备本机来源的用户级登记草稿 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def codex_import(id: str) -> dict[str, Any]:
        """将完整 Skill 或说明准备为独立声明, 草稿经 document_preview 和 document_apply 写入用户库."""
        return invoke("codex.import", {"id": id})

    # //// 提供有界目录摘要 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def card_describe(id: str, scope: str = "user") -> dict[str, Any]:
        """读取 Skill, 规则或 Hook 卡片的启用与共享基线. project-local 合成用户, 共享项目和个人覆盖."""
        return invoke("card.describe", {"id": id, "scope": scope})

    # //// 直接调整卡片在明确配置层的启用状态 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def card_configure(id: str, state: str, scope: str = "user", baseline: dict | None = None) -> dict[str, Any]:
        """按 card_describe 的 configBaseline 设置 enabled, disabled 或 inherit, 原文保持原位."""
        return invoke("card.configure", {"id": id, "state": state, "scope": scope, "baseline": baseline})

    # //// 查询同一有向关系的两侧与适配正文 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def card_relations(id: str, scope: str = "user") -> dict[str, Any]:
        """返回 outgoing, incoming 和候选工具. 同一关系保留一份 Adapter 文本."""
        return invoke("card.relations", {"id": id, "scope": scope})

    # //// 保存有向参考关系及适配提示 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def card_set_relation(from_id: str, to_id: str, text: str | None = None, scope: str = "user", baseline: dict | None = None, enabled: bool = True) -> dict[str, Any]:
        """更新一条参考关系; 省略text使用默认提示, 项目关系随两端共享状态选择存储位置."""
        return invoke("card.set_relation", {"from_id": from_id, "to_id": to_id, "text": text, "scope": scope, "baseline": baseline, "enabled": enabled})

    # //// 移出当前层的参考关系并恢复继承 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def card_remove_relation(from_id: str, to_id: str, scope: str = "user", baseline: dict | None = None) -> dict[str, Any]:
        """根据关系基线移出本层 Adapter, 用户层内容保持独立."""
        return invoke("card.remove_relation", {"from_id": from_id, "to_id": to_id, "scope": scope, "baseline": baseline})

    # //// 发布或收回项目共享卡片并协调相关关系 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def card_set_shared(id: str, shared: bool, baseline: dict | None = None) -> dict[str, Any]:
        """使用 card_describe 的 sharingBaseline 调整项目共享, 双端均共享的关系及正文写入项目文件."""
        return invoke("card.set_shared", {"id": id, "shared": shared, "baseline": baseline})

    # //// 读取指定天数内可确认的完整读取统计 [@x380kkm 2026-09-07] ////
    @server.tool(annotations=read_only)
    def statistics_reads(days: int = 7) -> dict[str, Any]:
        """按原始Skill统计Manager完整读取, 每快照只计一次; 原生Codex直接使用列为未采集."""
        return invoke("statistics.reads", {"days": days})

    # //// 提供有界目录摘要 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def catalog_list(query: str = "", kind: str = "", limit: int = 20, cursor: int = 0, scope: str = "user") -> dict[str, Any]:
        """按名称和种类查询声明摘要, 正文通过 document_read 读取."""
        result = invoke("catalog.list", {"query": query, "kind": kind, "scope": scope, "limit": limit, "cursor": cursor})
        return {**result, "next_cursor": result["nextCursor"]}

    # //// 读取单个声明及其编辑基线 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def document_read(id: str, scope: str = "user") -> dict[str, Any]:
        """读取存储身份对应的完整声明与 baseline."""
        return invoke("document.read", {"id": id, "scope": scope})

    # //// 准备新增或编辑的差异计划 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def document_preview(document: dict, baseline: dict | None = None, scope: str = "user") -> dict[str, Any]:
        """校验声明并预览差异, 编辑时携带 document_read 返回的 baseline."""
        return invoke("document.preview", {"document": document, "baseline": baseline, "scope": scope})

    # //// 预览登记移除 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def document_preview_remove(id: str, baseline: dict, scope: str = "user") -> dict[str, Any]:
        """预览移出登记的差异, 来源内容保留原位."""
        return invoke("document.preview_remove", {"id": id, "baseline": baseline, "scope": scope})

    # //// 提交当前基线仍然匹配的声明变化 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def document_apply(plan: dict) -> dict[str, Any]:
        """重新校验并应用预览计划, 写入计划固定的用户或项目登记目录."""
        return invoke("document.apply", {"plan": plan})

    # //// 从批准的真实来源准备导入声明 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def document_import(path: str, kind: str | None = None, scope: str = "user") -> dict[str, Any]:
        """把批准路径中的 Skill, 说明或 JSON 准备为声明草稿, 之后使用预览与应用入口."""
        return invoke("document.import", {"path": path, "kind": kind, "scope": scope})

    # //// 查询当前范围内的使用候选 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def catalog_discover(context: dict | None = None, query: str = "", limit: int = 20, cursor: int = 0,
                         scope: str | None = None, point: str = "", detail: Literal["summary", "full"] = "summary") -> dict[str, Any]:
        """按作用范围和 point 筛选后分页返回摘要与读取入口; detail=full 同时提供来源, 绑定和引用链."""
        return invoke("catalog.discover", {"context": context, "query": query, "limit": limit, "cursor": cursor,
                                          "scope": scope, "point": point, "detail": detail})

    # //// 按引用读取完整内容单元 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def content_read(ref: str, version: str | None = None, resource: str | None = None, budget: int = 65536,
                     expected_revision: str | None = None, scope: str | None = None) -> dict[str, Any]:
        """读取一个完整单元, unit_status 表示单元传输状态. expected_revision 比较同一来源入口的当前正文."""
        return invoke("content.read", {"ref": ref, "version": version, "resource": resource, "budget": budget,
                                       "expected_revision": expected_revision, "scope": scope})

    # //// 读取所选方法和当前适用的完整上下文 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=observation)
    def content_open(ref: str, version: str | None = None, context: dict | None = None,
                     budget: int = 65536, resources: list[str] | None = None, scope: str | None = None) -> dict[str, Any]:
        """固定配置和正文, 保存读取快照与完成统计. readiness 表示所选 Skill 与配套内容的齐备性."""
        return invoke("content.open", {"ref": ref, "version": version, "context": context, "budget": budget,
                                       "resources": resources, "scope": scope})

    # //// 描述来源发布与当前范围的使用设置 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def usage_describe(plugin: str, scope: str = "user", context: dict | None = None) -> dict[str, Any]:
        """使用具体 Plugin 登记 ID 查询成员, 本层绑定和当前候选."""
        return invoke("usage.describe", {"plugin": plugin, "scope": scope, "context": context})

    # //// 预览独立绑定的启停与成员设置 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def usage_preview(plugin: str, settings: dict, baseline: dict | None = None, scope: str = "user") -> dict[str, Any]:
        """settings 遵循 usage_describe 返回的 settingsSchema, 预览后通过 document_apply 保存计划."""
        return invoke("usage.preview", {"plugin": plugin, "settings": settings, "baseline": baseline, "scope": scope})

    # //// 列出所选方法的独立配套正文 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def context_describe(plugin: str, scope: str = "user") -> dict[str, Any]:
        """列出选定 Plugin 的 Skill 与配套说明, 返回独立正文和绑定的读取基线."""
        return invoke("context.describe", {"plugin": plugin, "scope": scope})

    # //// 预览独立正文与使用设置的联合变化 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def context_preview(plugin: str, settings: dict, baseline: dict | None = None, scope: str = "user") -> dict[str, Any]:
        """settings 使用 context_describe 的 settingsSchema, 配套说明按 skill 引用与 selector 范围参与读取."""
        return invoke("context.preview", {"plugin": plugin, "settings": settings, "baseline": baseline, "scope": scope})

    # //// 预览正文与其本层使用绑定的移除 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def context_preview_remove(plugin: str, baseline: dict, scope: str = "user") -> dict[str, Any]:
        """根据原始读取基线预览移出配套说明, 上游 Skill 保持原位."""
        return invoke("context.preview_remove", {"plugin": plugin, "baseline": baseline, "scope": scope})

    # //// 在固定目录原子保存配套正文与使用设置 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=ToolAnnotations(open_world_hint=False))
    def context_apply(plan: dict) -> dict[str, Any]:
        """保存 context_preview 或 context_preview_remove 返回的计划, 任一基线变化时整组提交拒绝."""
        return invoke("context.apply", {"plan": plan})

    # //// 延续同一快照的完整内容读取 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=observation)
    def content_continue(continuation: str, budget: int = 65536) -> dict[str, Any]:
        """使用 continuation 延续读取快照并保存观察, 每次重新核对来源授权."""
        return invoke("content.continue", {"continuation": continuation, "budget": budget})

    # //// 查看读取快照的配置输入与来源归属 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def content_snapshot(id: str) -> dict[str, Any]:
        """读取 ContentReadResult.snapshot 对应的固定配置和完整单元描述."""
        return invoke("content.snapshot", {"id": id})

    # //// 提供声明关系的静态图数据 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def catalog_graph(scope: str = "user") -> dict[str, Any]:
        """返回声明与引用关系, 未登记引用通过诊断保留."""
        return invoke("catalog.graph", {"scope": scope})

    # //// 读取合成声明及各项输入归属 [@x380kkm 2026-09-06] ////
    @server.tool(annotations=read_only)
    def catalog_effective() -> dict[str, Any]:
        """读取用户默认与显式项目声明的组合, 包含冲突诊断和原始输入位置."""
        return invoke("catalog.effective")

    return server
