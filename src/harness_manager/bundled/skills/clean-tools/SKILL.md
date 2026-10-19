---
name: clean-tools
description: 查询源码, 追踪依赖或读取构建与命令日志时使用. 提供有界结果, 源码锚点, 日志续读和可见的退出状态, 支持 cleanread 与独立 CodeGraph 的任务侧调用.
---

# clean-tools

Python 入口通过 `uv run --python 3.12 <脚本>` 运行, PowerShell 入口使用 PowerShell 7. `harness-manager toolkit list` 返回当前范围的发行根, 其 `tools` 是工具根; Skill 文件位于同发行根的 `skills` 中. 全局 `--workspace` 和 `--user-root` 定位项目与用户目录, 子命令 `--scope project` 选择项目工具包. 下文的 `R` 是当前代码项目, `S` 是已确认的 cleanread store, `T` 是工具根.

## 项目范围与调用契约

从当前工作树, 项目绑定或调用者明确给出的路径确定 `R`. 会话记录中的 `file:` URI 转换为本机路径后使用. 同一回合复用适用的路径与工具帮助; 工作树, 绑定或连接改变后重新解析.

存在 cleanread 配置或 `source.db` 时, 使用 `T/cleanread/trace.py preflight --root R 词1 词2` 查询已有证据. 该命令会创建缺少的 trace 目录和表. 检索词是位置参数 `terms`, 显式存储位置使用 `--store S`. 自动发现选择当前工作树内最近的 store; 项目解析错误列出 store 的所属根与请求根时, 按当前工作树或明确绑定选择对应 store. 多个候选保留各自路径和歧义.

CodeGraph 的 `status R --json` 提供索引覆盖, `pendingChanges` 和 `worktreeMismatch`. 可用索引支撑当前问题时使用其查询能力. 两种索引都缺少适用证据时, 用限定目录和条数的 `rg` 定位, 再按文件和行范围读取当前源码. 建立或刷新索引由当前问题和组件授权决定.

首次使用方法时读取当前 MCP schema 或 CLI 帮助. 方法, 连接, 版本与范围均适用时复用已取得的契约. `limit`, `budget`, `cursor` 等参数以该方法实际提供的字段为准, 默认值满足任务时直接采用.

## cleanread 探索与协作

使用 cleanread 调查代码时, 先定位与问题相关的 `path:line` 或符号跨度, 再读取对应正文. 复用 trace 中仍适用于当前工作树的定位查询, 新查询保留默认 trace 记录.

多个任务共享同一探索范围, 且宿主支持子代理时, 可安排只读 scout 输出锚点和未覆盖范围. 下游按锚点精读并返回带来源的结论. 单点问题由当前 Agent 直接处理. Workflow 名称和调用参数以当前宿主实际提供的接口及用户配置为准.

依赖追踪持续扩大或索引缺少相关材料时, 将已有锚点和覆盖缺口交给普通 worker, 使用限定范围的搜索和读取继续调查. 文件修改, 命令执行与未索引材料由具有对应权限的 worker 处理. 各 worker 共享新增证据, 文件编辑使用明确的归属范围.

## 代码结果

CodeGraph 的 `query --json` 定位符号, `context --format json` 提取任务上下文, `node` 读取单个符号, `callers` 与 `callees` 分别提供调用方和被调用方. runner 原样返回上游参数, JSON 与退出码; 字段选择在调用方完成.

cleanread 的 `T/cleanread/query.py --root R "<SQL>"` 执行查询并记录 trace. 用 SQL 的列, `WHERE` 与 `LIMIT` 选择任务证据. 返回的 `output` 是 JSON 文本, `returnedRange` 给出字符范围, `truncated` 表明是否有剩余内容. 大结果保存到当前 store 的 `short/results`; 使用实际返回的 `continuation` 参数调用同一入口读取后续页. 续读复用已保存结果. 需要解析完整 JSON 时, 按顺序连接各页 `output`.

定位时选择 `files.path`, `symbols.name`, `start_line`, `end_line`, `start_byte`, `end_byte`. 正文读取保留路径与跨度, 用 `slice_bytes(content, start_byte, end_byte-start_byte)` 按符号的字节范围提取. `substr` 适用于按字符定位的文本锚点. 条件, 调用方向和必要上下文随目标实现一起返回.

库表包括 `files`, `symbols`, `edges` 和 `files_fts`. `files_fts` 支持子串搜索; `edges.relation` 区分 `calls`, `imports`, `uses_type`, `contains`, `extends`, `implements`. 数量只采用查询实际计算的值.

cleanscan 的 `impact <符号> --store S` 返回依赖影响, `interface --target-root <相对目录> --store S` 返回边界接口, `seam <符号> --store S` 返回拆分位置. 入口为 `T/cleanread/cleanscan/scan.py`. `unique`, `ambiguous`, `unresolved` 分别保持唯一命中, 多义和未解析的原有含义.

## 命令日志与退出状态

高输出构建或需要保留命令回执时使用 `T/cleanread/invoke-command.ps1`. `-Command` 指定可执行入口, `-ArgumentList` 逐项传参, `-WorkingDirectory` 选择执行目录, `-LogDirectory` 指向任务日志目录. 具体参数通过当前 PowerShell 帮助读取. stdout 与 stderr 流式落盘, 回执包含实际退出码, 错误上下文和日志位置. 普通失败抛出错误并停止依赖该结果的后续操作.

已有日志使用同一入口的 `-ReadLog` 参数读取; 已知命令退出码时传入 `-ExitCode`. 未知退出码保持未知. 按回执中的日志路径, 行号与字符范围取得其余原文. 返回诊断遮蔽常见凭据字段, 完整日志沿用任务目录的访问和保留规则.

普通短命令直接使用 PowerShell 7. 脚本开头设置 `$ErrorActionPreference = 'Stop'`, 每个原生命令结束后立即保存并检查 `$LASTEXITCODE`. `rg` 的 `0` 表示匹配, `1` 表示无匹配, `2` 表示错误. 独立操作分别保留状态, PowerShell cmdlet 的错误遵循 `ErrorActionPreference`.

## Manager 内容读取

个人 Skill 的发现范围为 `host=harness-manager`, 全局规则的 `context.host` 选择实际宿主. 使用候选实际返回的 `read.method` 和 `read.params`. `content.open` 接收 `context` 与多项 `resources`; `content.read` 使用单项 `resource` 和版本校对参数 `expected_revision`.

同一连接与回合复用适用的候选, schema 和完整 Skill 内容. 连接, 内容版本或目标范围改变, 或上下文已缺少完整正文时重新读取对应项. 选中的 Skill 正文及必需资源完整读取; `needs-content` 沿 `continuation` 调用 `content.continue` 直至完成.

组合调用只向模型返回当前判断需要的字段. 同一 MCP 结果选择 `structuredContent` 或对应文本之一. 普通结果按范围裁剪, Skill 完整阅读遵循上面的内容契约.

## 索引维护与组件归属

cleanaudit 对改动文件使用临时解析. `T/cleanaudit/cleanaudit.py changeset --root R` 定位改动符号, `context --root R --dim comment` 读取相应上下文, `plan` 选择与内容相关的检查维度.

当前问题需要新覆盖范围时使用 `index_build.py --init --root R --langs <语言>` 和 `enrich_treesitter.py --root R`. 已有索引需要刷新时, `index_build.py --incremental --root R` 返回变动路径, `enrich_treesitter.py --root R --files <逗号分隔的相对路径>` 更新对应符号和关系. 正式跨文件索引属于交付物或后续工作将读取它时, 执行完整构建和 enrichment. `ref.py` 管理阅读片段, `rules.py` 管理经预览确认的解析规则, `trace.py` 管理项目查询记忆.

独立 CodeGraph 维护代码图语义. Archify 维护流程, 角色和组件关系, 通过源码锚点关联本 Skill 的证据. 组件管理器从登记的发行源安装 `skills/clean-tools`, `tools/cleanread` 与 `tools/cleanaudit`.
