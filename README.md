# Harness Manager

Harness Manager 管理用户和项目的静态配置. 桌面面板, CLI 与 stdio MCP 使用同一管理核心. 模块组合独立的规则, Skills, Hooks 和 Tools, 支持静态输出的成员可应用到宿主配置文件.

## 安装与启动

准备 Git, uv 和 Python 3.12 或更高版本. 以下命令使用 PowerShell 7:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
git clone https://github.com/x380kkm/harness-manager.git
Set-Location harness-manager
uv sync
```

CLI 和 MCP 使用仓库中的 Python 环境. 桌面面板还需要 Node.js 和 npm; Windows 用户在仓库根目录运行:

```powershell
$ErrorActionPreference = 'Stop'
./desktop/启动管理器.ps1
```

启动入口会准备缺少的桌面依赖并构建页面. 使用 CLI 或给 Agent 查询接口时, 在仓库根目录运行:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager api
uv run harness-manager api module.preview
uv run harness-manager guide
```

`api` 返回分页方法目录. 指定方法后返回根据实际处理器签名生成的 `inputSchema`, 持久化副作用和授权边界. `--group` 筛选方法组, `--limit` 与 `--cursor` 控制返回量. `guide` 按主题说明编辑, 模块, 宿主恢复和内容读取.

迁移或修改已有规则时, 运行 `uv run harness-manager guide rules` 查看原文映射, 正文修改与解除管理的用法. MCP 可读取同一主题 `harness://agent/rules`.

桌面规则编辑按来源片段提供输入框, 完整预览保留片段之间的标题和说明. Agent 可通过 `rule.describe` 读取同一组编辑字段和声明基线, 具体参数与保存方式见 `uv run harness-manager api rule.describe` 和规则帮助.

规则跨多个原文位置时, 每个片段保持原位. 修改正文需要在贡献的 `payload.fragmentTexts` 中按来源声明顺序给出替换文本, 完整 `text` 与替换结果一致. 空字符串删除对应片段. 初次逐段编辑通过来源 `originalText` 提供修改前的完整规则正文. 表格和适用条件也应纳入来源映射; 有未映射正文时, 应用会报告原因并保留当前宿主文件.

默认使用用户级配置. 启动时传入 `--workspace` 才加入项目, `--read-root` 可重复指定允许读取的额外来源目录. `scope=user` 使用用户默认, `scope=project` 包含项目共享配置, `scope=project-local` 再包含个人覆盖. 来源读取权限与配置生效范围分别维护.

## 接管本机配置

接管把已登记且选中启用的内容写入宿主文件. 卡片的启用绑定决定使用哪些内容, 接管开关决定保存后是否自动应用. `host.status.enabled=true` 是默认控制状态; 实际应用结果由绑定, `lastApplied` 和宿主差异共同确认.

`host.*` 方法的 `host` 参数选择写入哪个宿主, 默认 `codex`. 当前登记 `codex` 与 `claude` 两个宿主, 各自的存档按作用域身份分目录保存, 一个宿主的接管与恢复不影响另一个.

| 宿主 | 管理器写入的说明 | 主配置 | Hook 定义 | Hook 开关 |
| --- | --- | --- | --- | --- |
| `codex` | `AGENTS.override.md` | `config.toml` | `hooks.json` | `config.toml` 的 `[hooks.state]` |
| `claude` | `CLAUDE.md` | `settings.json` | `settings.json` | 写入与否 |

Codex 保留用户手写的 `AGENTS.md` 作为原文来源, 管理器只写 `AGENTS.override.md`. Claude Code 没有对应的覆盖层, 管理器直接管理 `CLAUDE.md`: 首次接管把原文存入恢复点, 之后的写入更新受管正文, 撤销接管时写回原文.

Hook 定义与主配置同文件时, 管理器只改写 `hooks` 键, 主配置的其余字段逐字段保留. 这类宿主没有独立的逐处理器开关, 启停以写入与否表达: 未取得启用请求的事件组不写入, 主配置保持原样. 因此新接管的 Hook 在本机默认关闭, 需要显式启用请求才写出.

宿主根目录为 `~/.codex` 与 `~/.claude`. 用户目录是当前主目录时, `CODEX_HOME` 与 `CLAUDE_CONFIG_DIR` 分别覆盖对应位置.

宿主缺少某类载体时, 该成员以 `severity=warning` 的 `host_capability_unsupported` 跳过, 其余成员照常写出; 阻断只由 `severity=error` 的诊断产生, 此时整份输出为空. `claude` 宿主的 Skill 由目录放置决定而非配置行, 因此按跳过处理; 该宿主的项目范围需要能够读取其全局规则的适配器, 仍按阻断处理.

保存声明后的自动同步当前只覆盖 `codex`, `claude` 使用 `host.preview` 与 `host.apply` 显式应用.

绑定的 `target.selector.host` 决定内容投向哪个宿主. 未限定 `host` 的绑定对所有宿主生效; 同一内容需要分别投向两个宿主时, 各自使用限定 `host` 的独立绑定, 避免同范围的重复绑定产生 `binding_conflict`.

先读取本机状态和首次接管说明:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager call host.status
uv run harness-manager guide takeover
```

批量迁移已有配置时, 先保全当前宿主文件及管理记录, 再关闭自动接管以准备声明和启用绑定. 规则的完整正文应覆盖原文中的表格, 条件和所有选定片段. `host.preview` 可在接管关闭时编译候选, 用于核对将写入的文件, 成员和差异; `host.inspect` 在关闭状态下只报告状态.

预览符合预期后, 在面板的配置接管与备份中开启接管, 或由 Agent 调用 `host.set_enabled` 并传入最新控制基线. 开启操作会立即尝试应用保存的配置. 检查返回的 `enabled` 与 `hostSync`; 再用 `host.inspect` 确认差异. MCP 接入让 Agent 调用管理接口, 宿主接管负责写出配置, 两者分别设置.

## 连接外部 Agent

生成 Codex 使用的连接片段:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager connection
```

输出包含当前 Python 环境的 `command`, 启动 `args` 和 `cwd`. 将片段合并到所选 Codex `config.toml` 的 `mcp_servers` 配置, 然后在客户端重新启动该 MCP 服务. 服务器由 MCP 客户端启动, 与桌面面板共享本机管理目录. Codex 的配置位置和客户端入口见 [官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp). 指定项目和额外来源时可运行:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager --workspace C:/work/project --read-root C:/work/skills connection
```

路径替换为实际项目和来源. `connection --format json` 输出结构化配置; `connection --full` 生成完整工具列表的连接. 源码更新后运行 `uv sync` 并重启 MCP 服务; 桌面依赖通过 `desktop` 目录中的 `npm ci` 更新. 安装目录或 Python 环境迁移后重新生成连接. Codex 的配置字段见 [官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference).

`connection` 只生成连接片段. 在本机配置中保存片段属于单独的配置修改; 用 `codex mcp list` 查看登记的服务器, 在客户端的 MCP 页面确认连接状态. 生成的绝对路径用于当前机器, 各机器分别生成自己的连接配置.

Claude Code 使用自己的 MCP 登记方式, 不读取上面的 TOML 片段. 在任意目录运行以下命令登记同一服务, `--scope user` 让全部项目共用:

```powershell
$ErrorActionPreference = 'Stop'
claude mcp add --scope user harness-manager -- <仓库>/.venv/Scripts/python.exe -m harness_manager.cli mcp --compact
```

`<仓库>` 替换为本仓库的绝对路径. 用 `claude mcp list` 确认连接状态. 包以可编辑方式安装, 该命令在任意工作目录都能启动服务.

默认连接使用 `mcp --compact`, 提供五个入口:

| 工具 | 用途 |
| --- | --- |
| `agent_capabilities` | 分页发现方法, 按方法读取输入 Schema 和副作用 |
| `agent_help` | 按主题读取流程与边界 |
| `skill_list` | 列出当前范围可用的 Skill 和读取入口 |
| `manager_query` | 执行明确标为只读的方法 |
| `manager_action` | 执行已授权的数据保存或宿主修改 |

`manager_query` 只允许能力目录中标为 `readOnly` 的方法, 服务端会拒绝持久化写入. `manager_action` 提供写入入口, 调用方应先取得用户对具体操作的授权; `method` 和 `params` 对应能力目录中的方法与输入. Compact 模式控制工具数量, 同时保留查询和写入能力. 完整模式 `mcp` 还提供 `document_read`, `module_preview` 等专用工具.

服务初始说明只给出简短路由. 详细帮助从 `harness://agent/overview` 发现, 单个主题从 `harness://agent/{topic}` 读取. 声明结构位于 `harness://protocol/schema`. 常规查询使用 `codex.groups` 或 `catalog.list` 的摘要, 再按身份读取正文.

### Agent 调用顺序

连接后先调用 `agent_help` 读取 `overview` 或 `toolkit`, 再调用 `skill.list` 查看当前范围的 Skill 名称, 摘要, 版本和读取入口. 首次接管用 `takeover`, 规则编辑用 `rules`, 宿主应用和恢复用 `host`. 通过 `agent_capabilities` 的 `method` 参数读取所需方法的 `inputSchema`, `readOnly`, `writes` 和授权边界.

compact MCP 只保留少量发现和路由工具, 这不代表能力只有这些入口. 完整方法目录仍可通过 `agent_capabilities` 分页读取, 写入统一经 `manager_action` 执行. `toolkit` 帮助主题按用途列出内容读取, 声明编辑, 模块关系, 宿主恢复, 项目搬移和诊断入口.

例如, 给 `manager_query` 传入以下参数可只读检查用户宿主状态:

```json
{"method":"host.status","params":{}}
```

编辑时, 用查询入口取得原始对象和基线, 按相应方法预览修改, 确认后将完整计划交给 `manager_action`. 保留读取时的基线, 让管理器检测并发修改. `card.configure` 等直接保存入口也会尝试宿主同步, 调用前先读取方法的副作用并取得修改授权.

`content.read` 读取完整内容单元. 需要固定 Skill 及配套内容时使用 `content.open`, 后续按返回的 `continuation` 调用 `content.continue`. 快照型读取会保存本机观察记录, 按能力目录中的 `readOnly` 选择查询或动作入口.

保存后分别检查声明结果和 `hostSync`, 用 `host.inspect` 核对当前宿主差异. `hostSync.status=blocked` 需要处理其诊断; 接管开关的实际状态以返回的 `enabled` 或最新 `host.status` 为准. 发生基线冲突时保留草稿, 重新读取, 合并和预览.

## 导入, 预览与保存

外部 Agent 使用以下调用关系:

| 用例 | 读取和基线 | 预览 | 保存 |
| --- | --- | --- | --- |
| 编辑声明 | `document.read` 的 `document` 和 `baseline` | `document.preview` | `document.apply` |
| 导入来源 | `document.import` 返回草稿 | `document.preview` | `document.apply` |
| 移除声明 | `document.read` 的 `baseline` | `document.preview_remove` | `document.apply` |
| 编辑模块 | `module.describe` 的 `settings` 和 `baseline` | `module.preview` | `module.apply` |
| 修改使用范围 | `usage.describe` 的成员, 绑定和 `settingsSchema` | `usage.preview` | `document.apply` |
| 配套说明 | `context.describe` 的正文与绑定基线 | `context.preview` | `context.apply` |

预览返回完整 `plan`, 保存时原样传回. 基线变化会返回 `catalog-conflict`, 应保留草稿, 重新读取并合并后预览. 模块成员使用 `itemId` 和可选的 `role`, 按列表顺序保存固定版本引用. 创建模块与启用模块分别提交; 启用时确认 `usage.describe` 返回的成员.

声明和模块预览的 `diagnostics.scope` 标明诊断范围, 包括当前编辑对已选项目新增的影响. 相同发布在多个配置层中保持相同正文; 需要独立正文时使用独立发布身份. 保存前确认这些诊断, 保存后检查 `hostSync` 的实际应用结果.

以下 PowerShell 示例导入一个真实 Skill, 显示差异后由操作者确认保存:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$skillPath = 'C:/work/skills/report/SKILL.md'
$sourceRoot = Split-Path $skillPath
$draft = @{ path = $skillPath } | ConvertTo-Json -Compress |
    uv run harness-manager --read-root $sourceRoot call document.import --params - |
    ConvertFrom-Json
$preview = @{ document = $draft.result.document } | ConvertTo-Json -Depth 30 -Compress |
    uv run harness-manager call document.preview --params - |
    ConvertFrom-Json
$preview.result.plan.changes | Format-List
if ((Read-Host '输入 apply 确认保存') -eq 'apply') {
    @{ plan = $preview.result.plan } | ConvertTo-Json -Depth 30 -Compress |
        uv run harness-manager call document.apply --params -
}
```

编辑已有声明时, 把 `document.read` 返回的原 `baseline` 与修改后的 `document` 一起传入预览. `card.configure`, `card.set_relation` 和 `card.set_shared` 是直接保存入口, 调用前读取相应基线并取得修改授权.

接管开启时, 通过上述保存接口提交声明会尝试应用宿主文件. 返回的 `hostSync.status=blocked` 表示声明已保存, 宿主应用需要处理 `hostSync.diagnostics` 中的问题. 直接编辑目录文件或来源文件后, 使用 `host.preview` 与 `host.apply` 将变更应用到宿主. `scope=project-local` 的日常修改保存在个人项目目录; 项目共享设置与关系由明确的共享操作写入 Git 工作区, 提交由用户决定.

## 宿主预览与恢复

`host.status` 返回接管状态, 备份摘要和控制基线. 接管默认开启. `host.initialize` 一次存档首次使用前该宿主的全部说明与配置文件, 包括空文件与原本不存在的状态, 宿主文件保持原样. Codex 存档 `AGENTS.md`, `AGENTS.override.md`, `config.toml` 和 `hooks.json`; Claude Code 存档 `CLAUDE.md` 和 `settings.json`. 后续保存保留这份原始恢复点.

`host.set_enabled` 使用 `host.status` 返回的 `baseline` 作为控制基线. 关闭接管时保留宿主当前文件, 开启时立即尝试应用保存的配置. 开关变化后重新取得状态和预览. 管理器退出后, 宿主继续读取已经写出的文件.

用户保存或重新开启接管时, 用户范围返回 `applied` 或 `unchanged` 后, 再同步当前已选定, 已初始化且开启接管的项目. `hostSync.scopes` 仅在进一步处理项目范围时出现, 记录各范围结果; 项目应用受阻时总体状态为 `blocked`. `host.inspect` 只读比较文件与配置, 项目页面据此提示待应用内容; 实际写入通过宿主预览确认.

`host.preview` 与 `host.preview_restore` 返回当前服务进程持有的 `planId`. MCP 或逐行 RPC 客户端保持连接, 阅读预览后将该值作为 `plan_id` 传给 `host.apply`. 普通配置应用要求接管已开启; 恢复计划可在接管关闭时应用. 声明的完整 `plan` 可跨 CLI 调用保存; 宿主 `planId` 随进程结束失效.

CLI 提供在同一进程中预览和确认的命令:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager host apply
uv run harness-manager call host.status
```

`host apply` 先输出预览, 输入 `apply` 后提交, 其他输入取消. 从 `host.status` 返回的 `backups` 选择身份后, 使用 `host restore <备份身份>`, 阅读预览并输入 `restore`. 恢复前保存当前文件与字段归属, 即管理器控制哪些内容及其原值; 恢复成功后关闭接管. 原始恢复点恢复接管前的配置, 保护副本与异常事务恢复各自保存的归属. 保护副本只保留最近一份. 项目宿主操作使用 `--workspace` 和 `host --scope project-local`.

`initial` 保存创建恢复点时的整份配置, 可能早于后来的本机修改. 恢复它会影响备份列出的说明文件, 主配置和 Hooks. 接管前, 将当前宿主文件, 当前配置层的声明文件与 `host.status.backupRoot` 的管理记录另存到独立的私有备份目录, 并记录原本缺失的文件. 恢复前逐项核对预览差异. 恢复成功后, 已登记声明和绑定继续保留, 可另行决定是否再次接管.

规则文件的覆盖顺序和项目配置的加载由对应宿主决定. 写入 Codex 的 Hook 仍需满足其信任要求才能执行.

Hook 卡片读取 Codex 用户配置 `hooks.state` 中的逐处理器开关. 在 Codex 修改开关后, 刷新卡片即可读取相同选择; `nativeState=mixed` 表示同组处理器的开关不同. `card.configure` 沿用完整 `configBaseline`, 接管开启时把明确启停写入原生状态, 接管关闭时保存待应用选择. 普通同步保留原生选择, 两端冲突时读取诊断并重新确认. 启停保留事件组位置; 编辑后的命令按 Codex 的信任要求重新审阅. `nativeEnabled` 表示配置开关, 执行结果由 Codex 确认.

项目 Hook 的个人开关同样保存在用户配置. 项目恢复只合并该项目 Hook 的状态行, 其他用户设置沿用当前文件. 来自用户配置的 Hook 在用户页面调整; 项目页面读取其实际原生状态.

恢复未完成操作前, 当前文件另存为 `interrupted`, 保留最近一份, 原有 `before-restore` 保持不变. 备份摘要的 `restorable=false` 表示文件与管理归属无法对应, `restoreError` 提供原因. 这些字节继续保存在本机, 面板只允许选择可恢复的记录. 仅管理归属变化时, 面板也提供确认入口.

恢复失败或中断时, 所选目标保留为 `restore-target` 备份, 后续普通应用保留该恢复目标. 从 `host.status` 读取可选身份后重新预览恢复. 同名 `before-restore` 保护副本在恢复成功后替换.

自动化客户端可以运行 `rpc`, 每行发送一个 `{id, method, params}` JSON 对象, 读取对应的 `{id, result}` 或 `{id, error}`. 保持同一进程即可在读取宿主预览后决定是否提交. `call host.apply` 会提示使用这种持续连接或上述确认命令.

## 项目移动或改名

项目搬移后, 在项目配置的配置接管与备份中填写原位置, 选择"预览重新关联". 查看受影响的私人覆盖, 用户来源和宿主备份记录, 确认后保存关联. 查看项目页面保持配置文件原样; 该范围的原始恢复点在首次应用前保存.

CLI 和 MCP 使用相同方法. 用 `--workspace` 选择当前位置, 调用 `project.relocate_preview`, 参数 `old_workspace` 为原绝对路径. `sourceCandidates` 提供可重定位的用户声明; 如需调整选择, 将其 `documentId` 数组作为 `source_ids` 再次预览. 确认后调用 `project.relocate_apply`, 原样传入完整 `plan`. `uv run harness-manager guide relocation` 和 `harness://agent/relocation` 提供同一帮助.

重新关联会复制私人覆盖和宿主存档, 保留旧记录, 并更新明确位于旧项目内的来源路径与范围. 宿主备份部分只返回基线摘要, 原文字节留在本机. 计划的其他部分包含选中用户及私人声明的正文, 应在本机或已授权的 Agent 中处理. 新位置已有冲突记录或存在未完成事务时需要先处理, 外部位置通过 `externalLocations` 与 `warnings` 显示.

关联保存后, 宿主文件仍保持原样. 按结果中的 `hostApplyRequired` 分别预览并应用用户或项目设置. 新协作者读取项目共享配置; 作者自己的私人覆盖通过上述入口关联.

## 按需读取与统计

`catalog.discover` 默认返回用于选择内容的摘要, `read` 提供下一次调用. `point` 在分页前筛选内容类型, Skill 使用 `skill.x380kkm/deployment`; `next_cursor` 是下一页的游标. 使用 `query` 搜索所需内容, 维护来源和绑定时设置 `detail=full` 取得选项, 来源和引用链.

`skill.list` 默认使用 `host=harness-manager`. `context` 中的任务等字段补充默认上下文, 显式 `host` 则选择对应宿主. 使用候选返回的 `read` 参数读取正文, 保持同一范围与版本.

`read` 给出所选内容的下一次调用. `content.read` 读取完整单元; `content.open` 固定选定 Skill 和当前范围的完整配套内容. 收到 `readiness=needs-content` 时使用 `continuation` 调用 `content.continue`, 可增加 `budget` 以容纳更大的完整单元. 可选资料通过 `resource` 或 `resources` 明确请求.

由 Codex 全局加载的规则可在绑定的 `target.selector` 中限定 `host=codex`. 对已经加载这些规则的 Agent, 将个人 Skill 的读取绑定限定为 `host=harness-manager`, 查询时传入相同的 `context.host`. 这时 Skill 读取携带选中正文和适用的任务配套, 全局规则仍由 Codex 注入. 单独核对规则时, 使用 `context.host=codex` 和规则的 `point` 查询, 再按返回的引用读取. 通用客户端继续按照自身范围提供完整上下文.

`content.open`, `content.preview` 和 `content.continue` 会保存读取快照或观察记录, 能力目录明确列出这些副作用. `content.preview` 的快照独立于使用次数. `statistics.reads` 统计通过 Manager 完成的读取, 原生宿主直接使用以 `unavailable` 表示未采集.

## 开发与验证

宿主写入验证使用临时 `user_root` 和项目目录. 按改动选择相关用例; 需要运行整套检查时, 在仓库根目录执行:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
uv run python -m unittest discover -s tests
node --test (Get-ChildItem tests/test_*.mjs).FullName
```

界面检查和构建使用 `desktop` 的 npm 依赖. Python 依赖与运行要求在 `pyproject.toml` 中声明, 桌面命令由 `desktop/package.json` 定义. 将用户级连接配置和恢复备份保存在私有位置.
