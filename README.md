# Harness Manager

Harness Manager 管理用户和项目的静态配置. 桌面面板, CLI 与 stdio MCP 使用同一管理核心. 模块组合独立的规则, Skills, Hooks 和 Tools, 支持静态输出的成员可应用到宿主配置文件.

## 安装工具包

准备 Git, uv, PowerShell 7 和 Node.js. CodeGraph 的 Node.js 版本范围由其上游 `package.json` 声明, Node.js 24 可运行本工具包. uv 准备 Python 3.12 或更高版本.

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
uv tool install git+https://github.com/x380kkm/harness-manager.git
harness-manager toolkit install
harness-manager toolkit install --apply
harness-manager host apply
harness-manager connection
```

`uv tool install` 安装 CLI 和随包资源. `toolkit install` 显示来源, 构建命令和安装目标; `--apply` 下载来源, 构建工具并登记规则与 Skill. `host apply` 预览 Codex 规则, 输入 `apply` 后应用. Claude 用户将该命令换成 `harness-manager host --host claude apply`. 自动接管已关闭且希望保持时, 在 `apply` 后添加 `--once`. 最后一个命令生成当前机器的 MCP 连接配置, 按下文的连接说明接入所用客户端. CLI 尚未出现在 PATH 时, 运行 `uv tool update-shell` 并重新打开终端.

也可以把下面的请求交给已经能访问本仓库或 Harness Manager 的 Agent:

> 请根据 Harness Manager README 安装并配置当前宿主的用户级环境. 保留已有设置, 未配置项使用默认规则与默认组件, 模型和推理选项沿用宿主, 新登记的 Hooks 保持关闭. 核对来源和宿主差异后完成安装, 规则应用与 MCP 连接, 告诉我实际生效结果及可选的个性化入口.

执行默认方案即可使用. Agent 通过 `agent-manager` Skill 协助调整规则成员, 组件和宿主偏好, 个性化字段均可省略.

默认安装内容由 [组件清单](src/harness_manager/bundled/manager/components.json) 管理:

| 内容 | 获取和更新方式 |
| --- | --- |
| agent-rules | 随 Harness Manager 分发 9 组规则, 分别登记并组合成默认模块 |
| CodeGraph | 从 [CodeGraph 仓库](https://github.com/colbymchenry/codegraph) 获取, 安装依赖并构建 |
| Archify | 从 [Archify 仓库](https://github.com/tt-a1i/archify) 获取 CLI, schema 和 renderer |
| agent-manager, codegraph, archify, clean-tools Skills | 随 Harness Manager 分发 |
| cleanread, cleanaudit, 工具调用脚本与配套资源 | 随 Harness Manager 分发, 来源为 clean-vibing 的可分发内容 |
| session-memory | 随 Harness Manager 分发, 记录用户请求并注入会话回顾, 两个宿主的 Hook 初始关闭 |
| clean-writer-cn | 可选, 从 [独立仓库](https://github.com/x380kkm/clean-writer-cn) 获取 |
| agent-handoff | 可选, 随 Harness Manager 分发跨 Agent 交接规则, 工作树与宿主路径解析, 会话摘要脚本 |

仅安装选定组件时, 在 `install` 后填写名称, 管理器同时处理其必需依赖. 例如 `harness-manager toolkit install clean-writer-cn --apply`. 各组件保持独立来源; `CODEGRAPH_ROOT`, `ARCHIFY_ROOT` 和 `CLEAN_WRITER_CN_ROOT` 可指向用户已有安装.

交替使用多个 Agent 时可安装 `harness-manager toolkit install agent-handoff --apply`. 它按 Git 工作树与 `CODEX_HOME`, `CLAUDE_CONFIG_DIR` 解析本机位置, 也接受明确的路径覆盖. 私人交接和会话记录保留在用户环境, Skill 本身随 Manager 更新.

安装位置为所选用户目录的 `.harness/toolkit`. `rules`, `skills` 和 `tools` 保存受管副本, `sources` 保存独立 Git checkout, `managed` 保存本机安装记录. Skill 自动绑定到 `host=harness-manager`, MCP 客户端可通过 `skill_list` 发现并读取完整正文和配套资源. 安装目录的读取范围由 Manager 提供. 宿主原生 Skill 加载与规则配置使用独立的 `usage` 和 `host` 入口.

### 规则选择

安装 `agent-manager` 同时安装 `agent-rules`. [规则目录](src/harness_manager/bundled/rules/catalog.json) 声明命令环境, 代码设计, 注释, 文档, 代码证据, 组件管理, 协作, 验证和 Manager 使用方式. 每份正文都有独立 Plugin, 默认模块 `plugin:toolkit/<scope>/agent-rules@local` 通过引用组合它们. 两个宿主分别保存绑定, 可通过 `usage.describe` 和 `usage.preview` 调整成员, 再应用对应宿主. 正文从文件读取, 规则的选择和组合保持独立.

默认规则遵循项目语言, 工具链和宿主能力. PowerShell 条款在使用 PowerShell 时适用, 专用代码检索和结构工具按已安装能力选用. 用户规则通过独立声明或项目配置保存. 已有全局规则迁移时由 Agent 对照原文处理重复要求, 宿主预览显示最终差异.

### 更新与迁移

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
uv tool upgrade harness-manager
harness-manager toolkit update
harness-manager toolkit update --apply
harness-manager toolkit doctor
```

`uv tool upgrade` 更新 Manager 与预置内容. `toolkit update` 查询组件自己的 Git 上游并显示安装差异; `--apply` 快进来源, 完成所需构建, 再同步受管副本. Git 工作树或安装副本含有用户修改时, 管理器报告冲突并保留正文. 保存并合并这些修改后重新运行对应命令. 构建失败时保留已下载来源, 再次 `install --apply` 会重试构建.

`.harness/toolkit.json` 保存组件选择及可选定制清单的位置. 将它与 `.harness/catalog.json` 和引用的定制清单纳入自己的配置仓库, 在目标用户目录恢复后运行 `toolkit install --apply`. 工具包声明的相对来源随用户或项目目录搬移; 其他外部来源按其实际位置重新关联. `.harness/toolkit` 中的下载, 缓存和安装状态由目标机器重新生成. 每台机器重新运行 `connection` 生成其 MCP 连接, 安装或更新 Manager 后重启该 MCP 服务.

安装生成 `.harness/.gitignore`, 排除工具副本, 备份, 接管状态, 检查点和读取记录. 共享配置时一并纳入这份忽略文件. 会话记忆目录和宿主凭据保存在本机私有位置.

项目环境使用独立范围:

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
harness-manager --workspace C:/work/project toolkit install --scope project
harness-manager --workspace C:/work/project toolkit install --scope project --apply
harness-manager --workspace C:/work/project connection
```

项目内的 `.harness/toolkit.json` 和 `.harness/catalog.json` 随项目共享, 工具来源位于该项目的 `.harness/toolkit`. 已有项目规则若忽略整个 `.harness`, 共享时仅纳入这两份配置及所需自定义声明. `--user-root` 可把个人环境指向另一个已存在的用户目录.

定制来源时复制组件清单到自己的配置仓库, 编辑所需的 Git URL, 分支, 构建命令, 依赖和安装目标, 再通过 `toolkit install --manifest <清单路径>` 预览. 清单处于当前项目以外时, 通过全局 `--read-root` 授权该目录. 预置资源的目标来源相对发行包解析, 独立来源的目标使用 `source_root=component`. 定制清单由它所在的仓库维护, 默认清单随 Manager 更新.

### 执行偏好与会话记录

`agent-manager` 登记 Codex 与 Claude 各自的可选执行偏好. 使用 `usage.describe` 读取 `plugin:toolkit/<scope>/execution-codex@local` 或 `plugin:toolkit/<scope>/execution-claude@local`, 在绑定的 `options.defaults` 和 `options.tasks.<任务名>` 中设置宿主默认值和任务覆盖. 保存使用 `usage.preview` 与 `document.apply`, 宿主输出使用现有的 `host.preview` 与 `host.apply`, 两次调用均选择对应的 `host`.

| 选项 | Codex | Claude |
| --- | --- | --- |
| 模型 | `model` | `model` |
| 推理强度 | `model_reasoning_effort` | `effort` |
| 可用工作流 | `workflow` | `workflow` |
| 自动审计修复轮数上限 | `audit_round_limit` | `audit_round_limit` |

绑定初始关闭, 所有字段可省略. Agent 按当前任务和实际宿主能力使用所选值, 省略的字段沿用宿主当前选择. 选项作为任务调用的偏好写入对应宿主说明; 主会话设置由宿主配置入口管理. `.harness/catalog.json` 保存声明与用户选项, 搬移和更新保留已有选择.

cleanread 的探索, 锚点共享, scout/worker 配合和索引维护说明随 `clean-tools` Skill 加载. 宿主的通用规则只需保留该 Skill 的发现入口.

`session-memory` 在 `UserPromptSubmit` 记录请求并注入历史, 在 `Stop` 保存已输出的助手答复. 会话记录位于各宿主配置目录的 `session-memory` 下, 保留最近 12 条请求和有界答复, 回顾标明原执行目录. 这些记录是历史资料, 项目状态仍以源码和运行结果为准. Hook 使用宿主输入的 `last_assistant_message`, 转写读取作为兼容路径. Codex 保存答复需要 `thread_source=user` 或转写元数据确认用户主会话; 来源未知时保留已有回顾. 记录按会话隔离, 读取或保存失败时放行.

启用会话记录需要用户明确选择对应宿主的两个 Hook. 默认安装只登记关闭的绑定. 源码安装与配置迁移后再次安装该组件会刷新命令路径. 其他人获取工具包时沿用自己的宿主配置与启停选择.

`toolkit.list`, `toolkit.plan` 和 `toolkit.graph` 提供 MCP 查询入口. `toolkit.doctor` 与 `toolkit.check` 执行清单中的组件命令, 可先查看 `toolkit.list` 返回的 `checks`, 取得执行授权后使用 `manager_action`. `toolkit.install` 与 `toolkit.update` 是取得用户授权后调用的写入入口. 图形输出是 Archify architecture IR, `toolkit graph` 的 CLI 回执中由 `result` 字段承载.

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

Codex 保留用户手写的 `AGENTS.md` 作为原文来源, 管理器只写 `AGENTS.override.md`. 管理器直接管理 Claude Code 的 `CLAUDE.md`: 首次接管存档并保留原文, 之后按来源归属更新受管正文, 撤销接管时写回原文.

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
claude mcp add --scope user harness-manager -- <仓库>/.venv/Scripts/python.exe -m harness_manager.cli --read-root <来源目录> mcp --compact
```

`<仓库>` 替换为本仓库的绝对路径. 用 `claude mcp list` 确认连接状态. 包以可编辑方式安装, 该命令在任意工作目录都能启动服务.

Skill 正文位于各自登记来源的目录中, 读取这些目录需要 `--read-root` 授权, 该参数可重复给出. 缺少授权时 `skill.list` 仍然返回候选, 按候选调用 `content.open` 才报来源超出批准范围; 登记多处来源时逐一给出对应目录.

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

`host.preview` 与 `host.preview_restore` 返回当前服务进程持有的 `planId`. MCP 或逐行 RPC 客户端保持连接, 阅读预览后将该值作为 `plan_id` 传给 `host.apply`. 普通配置应用要求接管已开启; `once=true` 一次应用该预览并保持当前接管开关, 接管关闭时后续保存继续只更新声明. 恢复计划可在接管关闭时应用. 声明的完整 `plan` 可跨 CLI 调用保存; 宿主 `planId` 随进程结束失效.

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

`card.inventory`, `card.describe` 与 `card.configure` 接受可选的 `host`. Hook 卡片省略该参数时采用唯一原生宿主绑定, 其余情况采用 Codex. 同一声明供多个宿主使用时传入 `host=codex` 或 `host=claude`. Claude 卡片读取 `settings.json` 中的事件组与 `disableAllHooks`, 显式启停保存到 Claude 的管理记录, 通过 `host.preview` 与 `host.apply` 应用. 恢复继承后重新操作该宿主时继续传入 `host`.

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
