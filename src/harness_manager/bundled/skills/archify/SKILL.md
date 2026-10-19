---
name: archify
description: 使用独立 Archify 把复杂关系整理成可验证的小型 IR, 并在算法推演, 运行观察, 业务流程, Git 整理, Skill 设计或人机边界包含分支, 状态, 反馈或多方协作时持续修正模型. 直接查询, 单步操作和纯代码定位由原工具处理.
---

# Archify

Archify 是 Agent 的结构化思考介质. 它在工作过程中承载关系, 约束, 观察和未知项. typed JSON IR (中间表示) 是可持续编辑的载体, HTML, SVG 和其他可视化产物在交付或共同评审需要时生成.

## 何时建立 IR

关系包含分支, 循环, 状态迁移, 职责边界, 并行路径, 基线对照或多种证据来源时, 维护一份只覆盖当前问题的小型 Archify IR.

一个事实, 一条直接命令, 一处明确修改或可以用短段落说清的线性过程直接完成. 图形数量和固定步骤只服务于判断.

IR 只保留会改变下一步决策的实体和关系. 新证据改变模型时更新对应节点或边, 已失去决策价值的分支随即移除.

## 与可信事实层协作

涉及代码时, 优先用 clean-tools 或 CodeGraph 获取符号, 调用关系, 影响范围和 `path:line` 锚点. Archify 把这些可信事实与用户目标, 运行观察和业务约束组织成可检查的关系模型.

代码关系沿用来源给出的置信度. `unique`, `ambiguous`, `unresolved`, 实际运行结果和用户确认分别保留, 推断保持为推断, 未知保持为未知.

代码工作以符号和源码锚点为事实载体. 流程, 蓝图, 连连看式关系, 角色协作和人机边界以 Archify IR 为结构载体. 两类载体通过稳定 ID 和明确来源关联.

cleanread trace 保存定位查询与可信锚点的短期记忆. Archify IR 保存当前任务的关系模型. 读取历史事实时回到 trace 或原始证据, 继续推演时读取 IR.

## 思考与执行

先写清这份 IR 要回答的一个问题. 类型不明确时调用 `guide`; 类型明确时进入 author 操作.

author 表示 Agent 读取当前 Archify 安装的 schema, common schema 和一个同类型 example, 然后编写新的 typed JSON IR. 当前 CLI 的稳定接口以 `help` 输出为准, author 由 skill 内的编写动作完成.

IR 在工作中持续参与决策:

- 算法推演用节点表达变换, 判断和不变量, 用边表达控制或数据依赖, 再把实际运行轨迹映射回模型.
- 运行观察把预期路径, 实际事件, 重试和终止状态分开, 每条观察保留时间或原始输出锚点.
- 业务流程把责任, 输入, 决策门和交付结果分开, 人类授权与 Agent 自主执行拥有明确边界.
- Git 整理先从 Git 和代码事实构造基线, 再用 Archify 表达模块关系, 变更路径或交接流程.
- Skill 制作和使用把触发条件, 路由, 依赖, 权限门, fallback, 停止条件和证据回传表达为可检查关系.

按当前问题选择最短闭环. 已写入 JSON 的 IR 使用 `validate` 检查结构. 需要交付可读产物时使用 `deliver`, 需要在浏览器中收集交付证据时再使用 `visual-check`. 需要比较两个已作者化架构基线时使用 `compare architecture`; Git 状态和运行影响继续来自各自的事实工具.

详细模式选择和反馈循环见 [references/reasoning-patterns.md](references/reasoning-patterns.md). 独立工具解析和稳定命令见 [references/tool-contract.md](references/tool-contract.md).

## 模块边界

Archify 包, schema, renderer 和 examples 保持在独立安装或上游仓库中. `harness-manager toolkit list` 返回当前范围的发行根, 其中的 `tools/archify/invoke.ps1` 解析当前安装并原样转发命令.

Manager 通过更新独立 Archify 安装完成升级. PATH 或 `ARCHIFY_ROOT` 指向更新后的安装时, 本 skill 自动使用新的 CLI 与 schema.

项目客制化内容保存在项目自己的 IR, adapter skill 或脚手架中. 通用 skill 保持项目, 引擎和版本无关.
