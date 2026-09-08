# Harness Manager

Harness Manager 管理用户和项目的静态配置. 桌面面板, CLI 与 stdio MCP 使用同一管理核心. 模块组合独立的规则, Skills, Hooks 和 Tools, 支持静态输出的成员可应用到宿主配置文件.

## 启动与接口发现

在本目录中运行:

```powershell
$ErrorActionPreference = 'Stop'
uv sync
uv run harness-manager api
uv run harness-manager api module.preview
uv run harness-manager guide overview
```

`api` 返回分页方法目录. 指定方法后返回根据实际处理器签名生成的 `inputSchema`, 持久化副作用和授权边界. `--group` 筛选方法组, `--limit` 与 `--cursor` 控制返回量. `guide` 按主题说明编辑, 模块, 宿主恢复和内容读取.

迁移或修改已有规则时, 运行 `uv run harness-manager guide rules` 查看原文映射, 正文修改与解除管理的用法. MCP 可读取同一主题 `harness://agent/rules`.

桌面规则编辑按来源片段提供输入框, 完整预览保留片段之间的标题和说明. Agent 可通过 `rule.describe` 读取同一组编辑字段和声明基线, 具体参数与保存方式见 `uv run harness-manager api rule.describe` 和规则帮助.

规则跨多个原文位置时, 每个片段保持原位. 修改正文需要在贡献的 `payload.fragmentTexts` 中按来源声明顺序给出替换文本, 完整 `text` 与替换结果一致. 空字符串删除对应片段. 初次逐段编辑通过来源 `originalText` 提供修改前的完整规则正文. 表格和适用条件也应纳入来源映射; 有未映射正文时, 应用会报告原因并保留当前宿主文件.

默认使用用户级配置. 启动时传入 `--workspace` 才加入项目, `--read-root` 可重复指定允许读取的额外来源目录. `scope=user` 使用用户默认, `scope=project` 包含项目共享配置, `scope=project-local` 再包含个人覆盖. 来源读取权限与配置生效范围分别维护.

## 连接外部 Agent

生成 Codex 使用的连接片段:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager connection
```

输出包含当前 Python 环境的 `command`, 启动 `args` 和 `cwd`. 由用户将片段加入所选 Codex 配置, 命令自身只输出文本. 指定项目和额外来源时可运行:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager --workspace C:/work/project --read-root C:/work/skills connection
```

路径替换为实际项目和来源. `connection --format json` 输出结构化配置; `connection --full` 生成完整工具列表的连接. 安装或 Python 环境迁移后重新生成连接. Codex 的配置字段见 [官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference).

默认连接使用 `mcp --compact`, 提供四个入口:

| 工具 | 用途 |
| --- | --- |
| `agent_capabilities` | 分页发现方法, 按方法读取输入 Schema 和副作用 |
| `agent_help` | 按主题读取流程与边界 |
| `manager_query` | 执行明确标为只读的方法 |
| `manager_action` | 执行已授权的数据保存或宿主修改 |

`manager_query` 只允许能力目录中标为 `readOnly` 的方法, 服务端会拒绝持久化写入. `manager_action` 提供写入入口, 调用方应先取得用户对具体操作的授权; `method` 和 `params` 对应能力目录中的方法与输入. Compact 模式控制工具数量, 同时保留查询和写入能力. 完整模式 `mcp` 还提供 `document_read`, `module_preview` 等专用工具.

服务初始说明只给出简短路由. 详细帮助从 `harness://agent/overview` 发现, 单个主题从 `harness://agent/{topic}` 读取. 声明结构位于 `harness://protocol/schema`. 常规查询使用 `codex.groups` 或 `catalog.list` 的摘要, 再按身份读取正文.

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

`host.status` 返回接管状态, 备份摘要和控制基线. 接管默认开启. `host.initialize` 一次存档首次使用前的 `AGENTS.md`, `AGENTS.override.md`, `config.toml` 和 `hooks.json`, 包括空文件与原本不存在的状态, 宿主文件保持原样. 后续保存保留这份原始恢复点.

`host.set_enabled` 关闭接管时保留宿主当前文件, 开启时尝试应用保存的配置. 管理器退出后, 宿主继续读取已经写出的文件.

用户自动保存或重新开启接管时, 同步当前已选定, 已初始化且开启接管的项目. `hostSync.scopes` 提供各范围结果, 相关项目应用受阻时总体状态为 `blocked`. `host.inspect` 只读比较文件与配置, 项目页面据此提示待应用内容; 实际写入通过宿主预览确认.

`host.preview` 与 `host.preview_restore` 返回当前服务进程持有的 `planId`. MCP 或逐行 RPC 客户端保持连接, 阅读预览后将该值作为 `plan_id` 传给 `host.apply`. 声明的完整 `plan` 可跨 CLI 调用保存; 宿主 `planId` 随进程结束失效.

CLI 提供在同一进程中预览和确认的命令:

```powershell
$ErrorActionPreference = 'Stop'
uv run harness-manager host apply
uv run harness-manager call host.status
```

`host apply` 先输出预览, 输入 `apply` 后提交, 其他输入取消. 从 `host.status` 返回的 `backups` 选择身份后, 使用 `host restore <备份身份>`, 阅读预览并输入 `restore`. 恢复前保存当前文件与字段归属, 即管理器控制哪些内容及其原值; 恢复成功后关闭接管. 原始恢复点恢复接管前的配置, 保护副本与异常事务恢复各自保存的归属. 保护副本只保留最近一份. 项目宿主操作使用 `--workspace` 和 `host --scope project-local`.

规则文件的覆盖顺序和项目配置的加载由 Codex 决定. Hook 写入后仍需满足 Codex 的信任要求才能执行.

恢复未完成操作前, 当前文件另存为 `interrupted`, 保留最近一份, 原有 `before-restore` 保持不变. 备份摘要的 `restorable=false` 表示文件与管理归属无法对应, `restoreError` 提供原因. 这些字节继续保存在本机, 面板只允许选择可恢复的记录. 仅管理归属变化时, 面板也提供确认入口.

恢复失败或中断时, 所选目标保留为 `restore-target` 备份, 后续普通应用保留该恢复目标. 从 `host.status` 读取可选身份后重新预览恢复. 同名 `before-restore` 保护副本在恢复成功后替换.

自动化客户端可以运行 `rpc`, 每行发送一个 `{id, method, params}` JSON 对象, 读取对应的 `{id, result}` 或 `{id, error}`. 保持同一进程即可在读取宿主预览后决定是否提交. `call host.apply` 会提示使用这种持续连接或上述确认命令.

## 项目移动或改名

项目搬移后, 在项目配置的配置接管与备份中填写原位置, 选择"预览重新关联". 查看受影响的私人覆盖, 用户来源和宿主备份记录, 确认后保存关联. 查看项目页面保持配置文件原样; 该范围的原始恢复点在首次应用前保存.

CLI 和 MCP 使用相同方法. 用 `--workspace` 选择当前位置, 调用 `project.relocate_preview`, 参数 `old_workspace` 为原绝对路径. `sourceCandidates` 提供可重定位的用户声明; 如需调整选择, 将其 `documentId` 数组作为 `source_ids` 再次预览. 确认后调用 `project.relocate_apply`, 原样传入完整 `plan`. `uv run harness-manager guide relocation` 和 `harness://agent/relocation` 提供同一帮助.

重新关联会复制私人覆盖和宿主存档, 保留旧记录, 并更新明确位于旧项目内的来源路径与范围. 宿主备份部分只返回基线摘要, 原文字节留在本机. 计划的其他部分包含选中用户及私人声明的正文, 应在本机或已授权的 Agent 中处理. 新位置已有冲突记录或存在未完成事务时需要先处理, 外部位置通过 `externalLocations` 与 `warnings` 显示.

关联保存后, 宿主文件仍保持原样. 按结果中的 `hostApplyRequired` 分别预览并应用用户或项目设置. 新协作者读取项目共享配置; 作者自己的私人覆盖通过上述入口关联.

## 按需读取与统计

`catalog.discover` 按目标和范围返回有界候选, 每项的 `read` 字段给出下一次调用. `content.read` 读取完整单元; `content.open` 固定选定 Skill 和适用配套内容. 收到 `readiness=needs-content` 时使用 `continuation` 调用 `content.continue`, 可增加 `budget` 以容纳更大的完整单元. 可选资料通过 `resource` 或 `resources` 明确请求.

`content.open`, `content.preview` 和 `content.continue` 会保存读取快照或观察记录, 能力目录明确列出这些副作用. `content.preview` 的快照独立于使用次数. `statistics.reads` 统计通过 Manager 完成的读取, 原生宿主直接使用以 `unavailable` 表示未采集.
