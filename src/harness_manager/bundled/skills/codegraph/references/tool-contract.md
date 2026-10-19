<!-- audience: internal -->
# CodeGraph tool contract

将 `harness-manager toolkit list` 返回的发行根记为 `D`, 独立 CodeGraph CLI 或包根记为 `C`, 当前代码项目根记为 `R`. Skill 位于 `D/skills/codegraph`.

## 解析独立安装

`D/tools/codegraph/invoke.ps1` 按以下顺序解析 CodeGraph:

1. `-CodeGraphPath C` 指向 CLI 文件或包根. `-CodeGraphRoot` 是同一参数的别名.
2. `CODEGRAPH_ROOT` 指向 CLI 文件或包根.
3. `D/managed/clean-vibing.json` 中登记的 CodeGraph 来源.
4. 外部 Manager 状态中登记的 CodeGraph 来源.
5. PATH 中的 `codegraph` 命令.

包根可以直接包含 `dist/bin/codegraph.js`, 包含 `codegraph/dist/bin/codegraph.js` 的工作根, 或包含 `node_modules/@colbymchenry/codegraph/dist/bin/codegraph.js` 的宿主项目. Windows 可执行文件和命令 shim 也可以作为显式路径.

```powershell
& "$D/tools/codegraph/invoke.ps1" -CodeGraphPath $C resolve
& "$D/tools/codegraph/invoke.ps1" -CodeGraphPath $C version
```

`resolve` 返回包含 `launcher`, `path` 和 `source` 的 JSON. `source` 取值为 `explicit`, `CODEGRAPH_ROOT`, `manager-state` 或 `PATH`.

## 查询入口

runner 公开 `version`, `status`, `query`, `context`, `explore`, `node`, `callers`, `callees`, `impact`, `files` 和 `affected`. 参数, stdout, stderr 与退出码直接来自当前 CodeGraph 安装.

```powershell
& "$D/tools/codegraph/invoke.ps1" status $R --json
& "$D/tools/codegraph/invoke.ps1" query '<symbol-or-domain>' --path $R --json
& "$D/tools/codegraph/invoke.ps1" context '<code-task>' --path $R --format json
& "$D/tools/codegraph/invoke.ps1" node '<symbol>' --path $R
& "$D/tools/codegraph/invoke.ps1" callers '<symbol>' --path $R --json
& "$D/tools/codegraph/invoke.ps1" callees '<symbol>' --path $R --json
& "$D/tools/codegraph/invoke.ps1" impact '<symbol>' --path $R --depth 2 --json
& "$D/tools/codegraph/invoke.ps1" files --path $R --json
```

`status`, `query`, `callers`, `callees`, `impact` 和 `files` 使用 `--json`. `context` 使用 `--format json`. 上游输出直接到达 stdout, 所以调用方可以直接解析 JSON.

## 调用方的结果选择

根据当前 `query --help` 选择搜索词, `--kind` 与 `--limit`. `context --help` 提供 `--max-nodes` 与 `--no-code`. 默认参数满足任务时直接采用.

调用方先保存退出码, 成功后再解析 JSON. 定位结果保留符号身份与源码范围, 其余字段按问题选择:

```powershell
$ErrorActionPreference = 'Stop'
$raw = & "$D/tools/codegraph/invoke.ps1" query '<symbol>' --path $R --json
$queryExit = $LASTEXITCODE
if ($queryExit -ne 0) { throw "CodeGraph query failed ($queryExit)" }
$raw | ConvertFrom-Json | ForEach-Object {
    $_.node | Select-Object id, name, kind, filePath, startLine, endLine
}
```

需要决定行为的条件或调用关系时, 按返回的符号读取 `node` 或 `context`, 保留该实现的完整相关单元. 索引存在待同步内容时, 源码锚点回到当前文件核对. 大量日志与原生命令回执使用 clean-tools 的任务侧入口, 安装 runner 保持原生流.

## 索引与更新边界

读取代码事实前检查 `status --json`. 相关文件存在 pending change, worktree mismatch, partial index 或待处理引用时, 结论同时引用当前源码, clean-tools 或实际运行证据.

```powershell
& "$D/tools/codegraph/invoke.ps1" upgrade-check
```

`upgrade-check` 映射到上游 `upgrade --check`. runner 的稳定接口覆盖只读查询; Manager 或上游安装器承担初始化, 索引同步和实际升级, 并在执行前取得对应授权.
