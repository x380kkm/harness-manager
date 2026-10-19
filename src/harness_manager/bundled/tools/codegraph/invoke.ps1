# audience: internal
# # codegraph-invoke
# 此命令解析独立管理的 CodeGraph CLI, 并为代码事实查询公开稳定的只读入口.
# 运行环境提供 CodeGraph 命令或包根, Node.js 负责执行 JavaScript 入口.

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter()]
    [Alias('CodeGraphRoot')]
    [string]$CodeGraphPath,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CodeGraphArguments
)

$ErrorActionPreference = 'Stop'

# //// 描述一个可执行的 CodeGraph 入口 [@x380kkm 2026-09-03] ////
function New-CodeGraphEntrypoint {
    param(
        [Parameter(Mandatory)]
        [ValidateSet('direct', 'node')]
        [string]$Launcher,

        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Source
    )

    return [pscustomobject]@{
        launcher = $Launcher
        path = $Path
        source = $Source
    }
}

# //// 解析文件或包根中的 CodeGraph 入口 [@x380kkm 2026-09-03] ////
function Resolve-CodeGraphCandidate {
    param(
        [Parameter(Mandatory)]
        [string]$Candidate,

        [Parameter(Mandatory)]
        [string]$Source
    )

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $null
    }

    $resolved = Resolve-Path -LiteralPath $Candidate -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        return $null
    }

    $item = Get-Item -LiteralPath $resolved.Path
    if (-not $item.PSIsContainer) {
        $extension = [System.IO.Path]::GetExtension($item.FullName).ToLowerInvariant()
        $launcher = if ($extension -in @('.js', '.cjs', '.mjs')) { 'node' } else { 'direct' }
        return New-CodeGraphEntrypoint -Launcher $launcher -Path $item.FullName -Source $Source
    }

    $entrypoints = @(
        @{ launcher = 'node'; path = Join-Path $item.FullName 'dist\bin\codegraph.js' }
        @{ launcher = 'node'; path = Join-Path $item.FullName 'codegraph\dist\bin\codegraph.js' }
        @{ launcher = 'node'; path = Join-Path $item.FullName 'node_modules\@colbymchenry\codegraph\dist\bin\codegraph.js' }
        @{ launcher = 'node'; path = Join-Path $item.FullName 'current\dist\bin\codegraph.js' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'codegraph.exe' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'codegraph.cmd' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'codegraph.ps1' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'bin\codegraph.exe' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'bin\codegraph.cmd' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'bin\codegraph.ps1' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'current\bin\codegraph.exe' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'current\bin\codegraph.cmd' }
        @{ launcher = 'direct'; path = Join-Path $item.FullName 'current\bin\codegraph.ps1' }
    )

    foreach ($entrypoint in $entrypoints) {
        if (Test-Path -LiteralPath $entrypoint.path -PathType Leaf) {
            $entrypointPath = (Resolve-Path -LiteralPath $entrypoint.path).Path
            return New-CodeGraphEntrypoint -Launcher $entrypoint.launcher -Path $entrypointPath -Source $Source
        }
    }

    return $null
}

# //// 按配置优先级选择独立 CodeGraph 安装 [@x380kkm 2026-09-03] ////
function Resolve-CodeGraphEntrypoint {
    if (-not [string]::IsNullOrWhiteSpace($CodeGraphPath)) {
        $entrypoint = Resolve-CodeGraphCandidate -Candidate $CodeGraphPath -Source 'explicit'
        if ($null -eq $entrypoint) {
            throw "CodeGraph 显式路径无有效入口: $CodeGraphPath"
        }
        return $entrypoint
    }

    if (-not [string]::IsNullOrWhiteSpace($env:CODEGRAPH_ROOT)) {
        $entrypoint = Resolve-CodeGraphCandidate -Candidate $env:CODEGRAPH_ROOT -Source 'CODEGRAPH_ROOT'
        if ($null -eq $entrypoint) {
            throw "CODEGRAPH_ROOT 无有效入口: $env:CODEGRAPH_ROOT"
        }
        return $entrypoint
    }

    $managedSource = Get-CodeGraphManagedRoot
    if (-not [string]::IsNullOrWhiteSpace($managedSource)) {
        $entrypoint = Resolve-CodeGraphCandidate -Candidate $managedSource -Source 'manager-state'
        if ($null -ne $entrypoint) {
            return $entrypoint
        }
    }

    $pathCommand = Get-Command 'codegraph' -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $pathCommand) {
        return New-CodeGraphEntrypoint -Launcher 'direct' -Path $pathCommand.Source -Source 'PATH'
    }

    throw 'CodeGraph 未找到. 请安装 codegraph 命令, 设置 CODEGRAPH_ROOT, 或传入 -CodeGraphPath <CLI 文件或包根>.'
}

# //// 从 manager 自有状态读取 CodeGraph 来源 [@x380kkm 2026-09-03] ////
function Get-CodeGraphManagedRoot {
    $toolkitRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $localStatePath = Join-Path $toolkitRoot 'managed/clean-vibing.json'
    if (Test-Path -LiteralPath $localStatePath -PathType Leaf) {
        $localState = Get-Content -LiteralPath $localStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $localSource = $localState.sources.codegraph
        if (-not [string]::IsNullOrWhiteSpace($localSource)) {
            if (-not [System.IO.Path]::IsPathRooted($localSource)) {
                $localSource = Join-Path $toolkitRoot $localSource
            }
            return $localSource
        }
    }

    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } elseif ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE '.codex'
    }
    if ([string]::IsNullOrWhiteSpace($codexRoot)) {
        return $null
    }
    $statePath = Join-Path $codexRoot 'managed\clean-vibing.json'
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $null
    }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        return $state.sources.codegraph
    } catch {
        return $null
    }
}

# //// 原样转发参数, 输出和退出码 [@x380kkm 2026-09-03] ////
function Invoke-CodeGraphEntrypoint {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Entrypoint,

        [Parameter()]
        [string[]]$Arguments
    )

    if ($Entrypoint.launcher -eq 'node') {
        & node $Entrypoint.path @Arguments
    } else {
        & $Entrypoint.path @Arguments
    }

    exit $LASTEXITCODE
}

# //// 展示 runner 的稳定查询接口 [@x380kkm 2026-09-03] ////
function Show-CodeGraphRunnerHelp {
    @'
Usage:
  invoke.ps1 [-CodeGraphPath <CLI file or package root>] <command> [arguments]

Commands:
  version        Print the installed CodeGraph version.
  status         Read index health and pending changes.
  query          Search indexed symbols.
  context        Build task context from symbols and relationships.
  explore        Read relevant source, call paths, and blast radius.
  node           Read one symbol or indexed file.
  callers        Read callers of a symbol.
  callees        Read callees of a symbol.
  impact         Read the impact radius of a symbol.
  files          Read the indexed file structure.
  affected       Read tests affected by source files.
  upgrade-check  Check the upstream release without installing it.
  resolve        Print the resolved CLI entrypoint as JSON.
  help           Show this help.
'@
}

# //// 将 runner 命令映射到只读上游命令 [@x380kkm 2026-09-03] ////
if ($null -eq $CodeGraphArguments -or $CodeGraphArguments.Count -eq 0) {
    Show-CodeGraphRunnerHelp
    exit 0
}

$operation = $CodeGraphArguments[0].ToLowerInvariant()
$remainingArguments = @($CodeGraphArguments | Select-Object -Skip 1)

if ($operation -in @('help', '--help', '-h')) {
    Show-CodeGraphRunnerHelp
    exit 0
}

if ($operation -in @('--version', '-v')) {
    $operation = 'version'
}

$entrypoint = Resolve-CodeGraphEntrypoint

if ($operation -eq 'resolve') {
    $entrypoint | ConvertTo-Json -Compress
    exit 0
}

$readCommands = @(
    'version',
    'status',
    'query',
    'context',
    'explore',
    'node',
    'callers',
    'callees',
    'impact',
    'files',
    'affected'
)

if ($operation -eq 'upgrade-check') {
    $upstreamArguments = @('upgrade') + $remainingArguments + @('--check')
    Invoke-CodeGraphEntrypoint -Entrypoint $entrypoint -Arguments $upstreamArguments
}

if ($operation -notin $readCommands) {
    throw "CodeGraph runner 只公开查询入口: $operation. 实际安装, 索引同步和升级由 Manager 或上游 CLI 在明确授权后执行."
}

$upstreamArguments = @($operation) + $remainingArguments
Invoke-CodeGraphEntrypoint -Entrypoint $entrypoint -Arguments $upstreamArguments
