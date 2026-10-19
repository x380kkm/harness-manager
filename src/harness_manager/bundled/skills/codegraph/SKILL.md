---
name: codegraph
description: 使用独立 CodeGraph 索引定位代码事实, 提取源码上下文, 追踪调用关系并核对变更影响. 适用于代码搜索, 实现前定位, 运行轨迹对应源码和修改影响分析; 查询记忆由 clean-tools 承载, 流程与非代码关系由 Archify 承载.
---

# CodeGraph

CodeGraph 是任务级代码事实 skill. 独立上游安装维护索引, 解析器和 CLI; 本 skill 保存使用判断与稳定查询入口. 项目或引擎客制化内容进入各自的 adapter skill 或脚手架.

## 代码事实工作

开始代码探索时先读取 `status --json`. `initialized`, `pendingChanges`, `worktreeMismatch` 和 `index` 决定当前索引能否支撑结论.

- 用 `query --json` 从名称, 类型或领域词定位符号.
- 用 `context --format json` 为一个代码任务提取相关符号, 关系和源码块.
- 用 `explore` 获取聚合源码, 调用路径和影响摘要.
- 用 `node` 精读一个符号或带行号的索引文件.
- 用 `callers --json` 和 `callees --json` 核对调用方向.
- 用 `impact --json` 核对修改可能传播到的代码范围.
- 用 `files --json` 核对索引覆盖范围.

查询结果中的源码和 `path:line` 是索引事实. 动态分派, 反射, 生成代码, 专业资源和未支持语言会留下覆盖边界. 索引状态显示相关文件有待同步时, 当前源码或 clean-tools 提供本次判断的证据.

## 与其他 task skill 协作

cleanread trace 保存定位查询, 文件状态和可信锚点的短期记忆. CodeGraph 提供预构建符号图和跨文件关系. 用 trace 复用已经定位的证据, 用 CodeGraph 扩展符号, 调用和影响关系, 并保留两者给出的歧义或未解析状态.

Archify 保存当前任务的结构模型. 算法分支, 运行轨迹, 业务流程, 蓝图关系, 人机边界和 skill 路由进入 Archify IR. 其中涉及代码的节点通过 CodeGraph 符号 ID 或 clean-tools 源码锚点连接.

实际运行工具证明事件和结果. CodeGraph 定位事件对应的实现与静态调用路径. Archify 组织预期路径, 实际轨迹和首次分歧点.

## 独立工具边界

`harness-manager toolkit list` 返回当前范围的发行根, 使用其中的 `tools/codegraph/invoke.ps1` 调用独立安装. runner 依次从显式路径, `CODEGRAPH_ROOT`, 同发行根的 Manager 状态, 外部 Manager 状态或 PATH 解析 CLI, 并原样转发查询输出与退出码. JSON 由上游 CodeGraph 生成并保持原样.

`upgrade-check` 只读取可用版本. 实际安装, 索引同步和升级通过 Manager 或上游 CLI 在用户明确授权后执行.

命令映射, 安装解析和 JSON 调用方式见 [references/tool-contract.md](references/tool-contract.md).
