# Harness Manager 内容与配置

连接 Harness Manager 或配置其工具包时应用本节.

- 首次连接通过 `agent_help` 读取 `overview`, 具体方法通过 `agent_capabilities` 查询输入与副作用.
- 发现 Skill 使用 `manager_query` 的 `catalog.discover`, `context.host` 为 `harness-manager`, `point` 为 `skill.x380kkm/deployment`, 按用户或项目选择 `scope`. 按返回的 `read.method` 和 `read.params` 读取正文, 遇到 `needs-content` 沿 `continuation` 读取到完整.
- 核对规则使用实际宿主的 `context.host` 和 `point=context.x380kkm/instruction`. 规则选择, 来源和宿主投放通过 Manager 的模块与绑定管理.
- 同一范围和版本复用已经取得的候选, 方法 schema 和完整正文, 普通查询只返回任务需要的字段.
- 用户要求安装或配置时, 读取 agent-manager Skill, 检查已有选择并按授权预览和应用. 未配置项采用默认规则, 模型与推理选项沿用宿主设置, Hooks 初始关闭. 已有规则选择, 执行偏好与 Hooks 状态沿用用户选择.
- 个性化选项按用户需求调整. 未提供选项时使用默认方案继续任务, 已有明确选择在更新与迁移时保留.
- 配置保存沿用读取基线, 核对预览后提交并检查 `hostSync` 与对应宿主的 `host.inspect`.
- MCP 连接不可用时说明受影响的能力, 使用现有项目工具完成可独立推进的工作. 读取授权由实际工具接口控制.
