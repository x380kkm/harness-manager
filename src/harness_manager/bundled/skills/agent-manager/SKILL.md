---
name: agent-manager
description: 安装或迁移 Harness 环境, 管理规则, Skills 和组件的来源与更新, 协助用户采用默认配置或调整宿主偏好.
---

# Agent Manager

Harness Manager 的 toolkit 接口读取组件清单, 把来源, 运行时, 能力, 健康命令, 更新检查和依赖关系整理成机器可读结果. Skill 正文通过 Manager 的 catalog 发现和读取.

## 默认配置与用户选择

用户要求配置 Harness 环境时, 先确认实际宿主, 用户或项目范围以及现有安装. 未配置项采用发行默认: 规则模块启用, 执行偏好留空, Hooks 关闭. 已有规则选择, 执行偏好与 Hooks 状态沿用用户选择, 重置按用户明确要求执行. 只在目标宿主无法判断或存在冲突时询问会影响结果的选择.

安装使用 `toolkit.plan` 与 `toolkit.install`, 核对下载来源和目标后在授权范围内执行. 安装登记内容, 应用规则通过对应 `host` 的 `host.preview` 与 `host.apply` 完成. 需要加入 MCP 连接时, 使用当前安装的 `connection` 输出和宿主实际提供的登记入口, 在已有配置中合并. 报告实际连接状态.

CLI 默认预览和确认使用 `harness-manager host apply`. Claude 使用 `harness-manager host --host claude apply`. 用户希望自动接管继续关闭时, 在 `apply` 后使用 `--once`. MCP 同一连接先预览再使用返回的 `planId`, 两次调用均选择相同宿主. 应用后检查 `hostSync` 和 `host.inspect`.

向用户简要说明可选项: 规则成员, 组件, 模型与推理偏好, 会话记忆 Hooks. 用户省略个性化设置时继续使用默认方案. 更新和迁移保留既有选择; 缺少某个专用分析组件时, 先用项目现有工具处理可完成的任务.

## 规则模块

`agent-rules` 组件提供 9 组文件规则. `plugin:toolkit/<scope>/agent-rules@local` 引用各组独立 Plugin. Codex 与 Claude 各有一份绑定, ID 为 `binding:<scope>/plugin:toolkit/<scope>/agent-rules/<host>`.

通过 `usage.describe` 读取模块, 将目标绑定的原始对象作为 `baseline` 交给 `usage.preview`, 用 `settings.id` 指定同一绑定, 通过 `settings.members.<成员 ID>` 选择 `include` 或 `exclude`. 保留原有成员选择及选项, 核对差异后通过 `document.apply` 保存. 单独绑定规则或组合其他模块时, 保持每份内容在同一宿主的选择一致.

正文通过 `catalog.discover` 返回的 `content.read` 入口读取. 默认规则来源位于工具包的 `rules` 目录, 更新沿用受管文件的冲突保护. 用户需要独立正文时, 用 `document.preview` 创建自己的规则声明并在默认模块中排除对应成员, 再按当前用户或项目绑定. 项目级规则与项目一起管理.

迁移已有全局规则时, 对照当前正文和候选规则确认替换范围, 保留用户自定义部分. 避免将同一要求作为旧规则与新模块重复启用. 来源映射和宿主差异使用 `rules` 帮助及 `host.preview` 核对.

## 观察入口

```powershell
$ErrorActionPreference = 'Stop'
harness-manager toolkit list
harness-manager toolkit doctor
harness-manager toolkit check
harness-manager toolkit graph
```

`list` 返回当前范围的发行根和组件. `doctor` 调用组件声明的健康命令. `check` 查询上游更新候选, 可能访问组件自己的发布源. `graph` 生成可交给 Archify 的组件关系.

全局 `--user-root` 定位用户目录, `--workspace` 定位项目目录. toolkit 子命令的 `--scope user` 使用用户工具包, `--scope project` 使用项目工具包. `--manifest` 读取定制组件清单. 调用前读取当前命令帮助或 MCP `agent_capabilities` 确认参数.

## 安装与更新

```powershell
$ErrorActionPreference = 'Stop'
harness-manager toolkit install
harness-manager toolkit update codegraph archify
```

`install` 默认预览来源获取, 工具安装与 Skill 登记. `update` 默认预览选定组件的上游更新. 加上 `--apply` 才写入对应范围的工具包与登记信息. 保存前核对候选来源, 目标位置和已有文件差异.

组件目录位于当前范围的 `.harness/toolkit`, 包含 `rules`, `skills`, `tools`, `sources` 和 `managed`. 工具安装通过 Manager catalog 提供内容, 宿主原生配置沿用独立的配置管理入口.

source-aware 组件在干净 Git 来源上使用 fast-forward, 并运行 `install.commands` 声明的构建入口. 已安装发行版使用组件自己的 updater. CodeGraph 的源码 checkout 更新后需要构建, Archify 的源码 checkout 直接跟随上游提交.

更新完成后运行 `doctor`, 按实际版本, 健康结果和依赖判断组件是否可用. 项目定制存放在项目自己的 modules, adapter Skills 或脚手架中.

迁移保存 `.harness/catalog.json`, `.harness/toolkit.json` 和所引用的定制来源. 安装生成的 `.harness/.gitignore` 排除工具副本, 下载, 本机备份, 接管状态和读取记录. 宿主自己的会话文件与凭据沿用本机私有存放方式.

## 宿主执行偏好

安装本组件会登记 `plugin:toolkit/<scope>/execution-codex@local` 和 `plugin:toolkit/<scope>/execution-claude@local` 两份可选配置. 使用 `usage.describe` 读取声明, 选项 schema 和绑定基线, 用户确认后通过 `usage.preview` 与 `document.apply` 保存. 宿主应用沿用 `host.preview` 和 `host.apply`, 两次调用均使用对应的 `host`. 接管关闭时, 明确的一次应用使用 `host.apply` 的 `once=true`, 自动接管保持关闭.

绑定的 `options.defaults` 保存宿主默认偏好, `options.tasks.<任务名>` 保存任务覆盖. 两者均可设置 `workflow`, `model` 和 `audit_round_limit`; Claude 使用 `effort`, Codex 使用 `model_reasoning_effort`. 字段省略时沿用宿主选择. 工作流名称, 模型和推理档位依据当前宿主能力与用户选择填写. 偏好只作用于支持相关参数的任务调用.

两份绑定分别限定自己的 `host`, 初始关闭. 保存的选项随 `.harness/catalog.json` 迁移. 查询生效偏好时, `catalog.discover` 使用实际宿主及 `point=preference.x380kkm/method`, `detail=full` 返回合成后的选项.

## 与其他 Skills 协作

- 代码事实由 `codegraph` 或 `clean-tools` 提供.
- 算法, 运行观察, 业务流程, Git 整理和 Skill 结构由 `archify` 提供.
- `graph` 结果可以作为 Archify 的 architecture 输入, 代码影响分析使用 CodeGraph.
