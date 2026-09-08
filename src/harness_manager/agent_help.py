# audience: internal
# # agent-help
"""外部客户端按主题读取管理流程. 方法输入从运行中的公开处理器签名生成."""

TOPICS = {
    "overview": {
        "title": "选择管理入口",
        "text": "默认 scope=user. 用 agent.capabilities 查询方法, 指定 method 读取完整输入与副作用. "
                "manager_query 仅接受标为 readOnly 的方法; manager_action 提供写入入口, 调用方应先取得具体操作的授权. "
                "先用 codex.groups 或 catalog.list 搜索摘要, 再按身份读取单项. "
                "编辑沿用读取基线, 预览后确认差异, 提交后检查 hostSync 和 diagnostics. "
                "管理器写出静态文件, 宿主直接使用这些文件."
    },
    "editing": {
        "title": "读取, 预览和保存",
        "text": "document.read 返回 document 与 baseline. 修改 document 后, 将原 baseline 一起交给 document.preview. "
                "确认 changes 后将完整 plan 交给 document.apply. 新声明使用 baseline=null. "
                "预览 diagnostics 的 scope 标明当前编辑及已选项目中新增影响的范围, 保存前逐项确认. "
                "删除使用 document.preview_remove, 再 document.apply. "
                "card.describe 的 configBaseline 用于 card.configure, sharingBaseline 用于 card.set_shared. "
                "card.configure 的 state 使用 enabled, disabled 或 inherit. "
                "card.configure 与关系快捷操作直接保存; 调用前需要用户的修改授权. "
                "接管开启时, 保存接口会尝试应用宿主文件. 直接编辑目录或来源文件后, 使用 host.preview 和 host.apply 应用变更."
    },
    "modules": {
        "title": "组合独立内容",
        "text": "module.describe 省略 id 返回成员候选; 编辑时传模块身份并保留 baseline. "
                "module.preview 接收 name, description 和有序 members; 每项使用 itemId, 可含 role. "
                "role 生成供 Agent 参考的条件提示; 任务路由与 Hook 触发条件使用各自的配置. "
                "module.preview 的 diagnostics 提供各范围的引用与冲突原因, 确认后由 module.apply 保存完整 plan. 成员引用固定来源版本, 模块创建与使用绑定分别保存. "
                "用 usage.describe 读取模块的成员和 settingsSchema, 再 usage.preview 与 document.apply 设置范围及启用. "
                "settings.acceptance=current 时, members 应包含用户实际确认的成员. "
                "规则, Skill, Hook 和 Tool 各自保有来源; 工具与 Hook 采用明确接口引用."
    },
    "rules": {
        "title": "迁移与修改规则",
        "text": "规则应用以有效原文为底稿, 优先使用原 AGENTS.override.md, 其次为 AGENTS.md; 项目还可按配置选择备用说明文件. "
                "在 manager.card/source 扩展的 payload 中, path 与 sections=[{line,endLine},...] 或 line/endLine 定位原文片段. "
                "originalText 用于验证初次映射; 未提供时使用规则正文 text 验证. "
                "也可将从原文完整复制的片段放入该 payload 的 fragments 数组, 由 Agent 明确修正对应关系. "
                "text 表示完整规则正文. 单片段直接以 text 替换; 多片段各自保留在原文位置, 标题和空行可作为显示包装. "
                "正文中的表格, 适用条件等内容也需要有对应来源片段. "
                "rule.describe(id, ref, scope) 返回内联规则的声明与 baseline, 以及 texts 编辑字段和 parts 完整正文顺序. "
                "parts 的 text 项保留原文, fragment 项引用 texts 的下标; 修改 texts 后据此组装 payload.text. "
                "mapped=true 时同时保存 payload.fragmentTexts=texts, 再经 document.preview 与 document.apply 提交. "
                "unmappedText 保留与当前片段不一致的已保存正文, 调整片段并确认完整正文后保存. "
                "编辑多片段规则时, 在贡献 payload.fragmentTexts 中按来源声明顺序逐项填写替换正文, 空字符串删除该片段. "
                "text 须等于修改前的完整规则正文逐段替换后的结果; 初次提交逐段编辑时用来源 originalText 提供修改前的完整规则正文. "
                "来源定位未变时继续使用已确认片段; 明确修改 fragments, sections 或其他定位字段会重新核对映射. "
                "外部原文改变后, 先重读原文, 再填写新的完整 fragments; 旧归属记录也可用 fragments 修正. "
                "片段重叠, 无法唯一匹配或预览后来源变化会使 hostSync.status=blocked, 宿主文件保持原样; 原因见 hostSync.diagnostics. "
                "未受管部分的修改可在重新预览后保留. "
                "未映射的新规则追加到原文之后并保留已有内容. 通过 inherit 移除绑定会恢复对应原文; 最后一个绑定移除后恢复原覆盖文件或由宿主读取 AGENTS 候选文件. "
                "迁移时用 codex.read 读取原文, 用 document.read 取得声明与 baseline, 据原文填写或修正上述 fragments, "
                "再 document.preview, 确认差异后 document.apply. 行号应依据实际原文核对, 普通来源文件按进程的读取授权访问."
    },
    "host": {
        "title": "宿主应用和备份恢复",
        "text": "host.status 返回 enabled, backups 与 baseline. 接管默认开启. "
                "host.inspect 只读比较保存配置与宿主文件, 返回 pending, unchanged, blocked, disabled 或 uninitialized, 保留已有预览令牌. "
                "host.initialize 一次存档首次使用前的 AGENTS.md, AGENTS.override.md, config.toml 和 hooks.json, 包括空文件与不存在状态, 宿主文件保持原样. "
                "后续保存保留这份原始恢复点. "
                "host.preview 返回文件摘要与 planId; 在同一 MCP 或 RPC 进程将 planId 作为 plan_id 传给 host.apply. "
                "恢复用 host.preview_restore, id 取自 backups, 然后 host.apply. "
                "恢复前保存当前文件和字段归属, 即受管内容及其原值, 保护副本只保留最近一份. "
                "同名保护副本在恢复成功后替换; 失败或中断的所选目标通过 restore-target 备份保留, 可按 host.status 中的身份重新预览. "
                "原始恢复点恢复接管前配置, 保护副本和异常事务恢复各自的归属; 恢复成功后关闭接管. "
                "恢复未完成操作前, 当前文件另存为 interrupted, 保留最近一份且保持已有 before-restore. "
                "backups 中 restorable=false 的记录保留文件字节, restoreError 说明无法确定归属的原因, 应选择可恢复的记录. "
                "host.set_enabled(enabled=false) 保留宿主当前文件; enabled=true 会尝试应用已保存设置. "
                "用户自动保存或重新开启接管时, 同步当前已选定, 已初始化且开启接管的项目, hostSync.scopes 分别报告各范围结果. "
                "显式 host.apply 只写预览确认的范围. "
                "hostSync.status=blocked 表示声明已保存但宿主应用受阻, 应读取 hostSync.diagnostics 并处理原因."
    },
    "content": {
        "title": "按需读取内容",
        "text": "catalog.discover 按目标与范围返回有界摘要, candidate.read 给出下一次读取调用. "
                "content.read 读取一个完整单元; content.open 固定选定 Skill 及适用配套内容. "
                "readiness=needs-content 时携带 continuation 调用 content.continue, 增加 budget 可容纳较大完整单元. "
                "可选资源通过 resource 或 resources 明确请求. 读取快照与统计写入用户 observations 目录; "
                "content.preview 的快照独立于使用次数. 原生宿主使用次数以 unavailable 表示未采集."
    },
    "scope": {
        "title": "范围, 共享和来源权限",
        "text": "scope=user 使用用户默认. 显式 --workspace 后, project 读取用户与项目共享层, project-local 再包含个人覆盖. "
                "个人项目修改使用 project-local; 共享写入进入项目 .harness/catalog.json, Git 提交由用户决定. "
                "关系的两端均共享时关系与 Adapter 才进入共享文件. "
                "--read-root 额外授予来源读取路径, 不改变声明写入位置; scope 与 context 只筛选内容. "
                "Git 元数据链接, 对象存储和配置中声明的附加文件均需要读取授权, 缓存续读也核对当前 Git 存储. "
                "项目文件能表达的规则范围由宿主编译检查. Hook 执行仍需满足 Codex 的信任要求."
    },
    "relocation": {
        "title": "项目搬移后的配置关联",
        "text": "项目已经移动或改名后, 以 --workspace 选择当前位置, 用 project.relocate_preview(old_workspace=原绝对路径) 预览关联变化. "
                "sourceCandidates 列出可重定位的用户声明, 默认选中当前项目或旧私人覆盖所引用的项目内来源; source_ids 可明确筛选这些 documentId. "
                "确认 changes, externalLocations 和 warnings 后, 将完整 plan 原样交给 project.relocate_apply. 计划可跨 CLI 进程提交. "
                "操作更新选定来源及项目内部范围, 复制旧私人覆盖和宿主备份归属到当前位置; 旧记录保留. "
                "同名冲突或未完成宿主事务会阻止关联, 来源和目标变化需要重新预览. 宿主备份部分只返回基线摘要, 原文字节保留在本机. "
                "计划的其他部分包含选中用户及私人声明的正文, 应在本机或已授权的 Agent 中处理. "
                "操作保持实体项目与宿主文件原样. 完成后按 hostApplyRequired 列出的作用域分别 host.preview 和 host.apply, 核对应用结果. "
                "面板入口位于项目配置的配置接管与备份, 填写项目原位置后预览重新关联. 新协作者使用项目共享文件; 作者搬移项目时还需重连自己的私人覆盖."
    },
    "cli": {
        "title": "CLI 与进程内预览",
        "text": "call METHOD --params - 从 stdin 接收 JSON 参数, 输出包含 result 或 error 的 JSON. "
                "声明 plan 含完整基线, 可跨 CLI 调用提交. host planId 只由生成它的进程持有. "
                "使用 host apply 或 host restore BACKUP_ID 在同一命令中预览并输入确认词. "
                "自动化客户端使用 rpc 的逐行 JSON 或保持 MCP 连接, 阅读预览后再提交. "
                "rpc 请求为 {id, method, params}; 每行返回相同 id 的 result 或 error."
    },
    "connection": {
        "title": "生成外部连接配置",
        "text": "connection 命令输出 Codex mcp_servers 的 TOML 片段, 不写宿主配置. "
                "默认连接 compact MCP, 提供查询, 写入和按需帮助入口. --full 输出完整工具模式. "
                "连接使用当前 Python 环境及明确的工作目录; 迁移安装后重新生成. "
                "可在启动命令前添加 --workspace, --user-root 与重复的 --read-root. "
                "Codex 配置参考: https://learn.chatgpt.com/docs/config-file/config-reference"
    },
    "errors": {
        "title": "处理冲突与应用缺口",
        "text": "catalog-conflict 表示读取基线已变化, 保留草稿, 重新读取与合并后预览. "
                "catalog-busy 可在当前写入完成后重试. 来源越界需用户明确扩大启动时的 --read-root. "
                "hostSync.status=blocked 时读取 hostSync.diagnostics 处理原因; 声明保存结果和宿主应用结果分别检查. "
                "宿主 planId 过期需在当前连接重新预览. 恢复只使用 host.status 返回的备份身份."
    },
}


# //// 返回单个帮助主题与按需目录 [@x380kkm 2026-09-08] ////
def read_help(topic: str = "overview") -> dict:
    if not isinstance(topic, str) or topic not in TOPICS:
        raise ValueError("帮助主题不存在, 请使用 overview 读取主题目录.")
    result = {"topic": topic, **TOPICS[topic], "uri": "harness://agent/" + topic}
    if topic == "overview":
        result["topics"] = [{"id": key, "title": value["title"], "uri": "harness://agent/" + key} for key, value in TOPICS.items()]
    return result
