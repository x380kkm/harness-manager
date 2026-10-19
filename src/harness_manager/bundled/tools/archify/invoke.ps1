# audience: internal
# # archify-invoke
# 此命令解析独立管理的 Archify 包并原样转发 CLI 参数与退出码.
# 运行环境提供 Node.js, Archify 命令或 Archify 包根.

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter()]
    [string]$ArchifyRoot,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArchifyArguments
)

$ErrorActionPreference = 'Stop'

# //// 解析包根中的 Archify 入口 [@x380kkm 2026-09-03] ////
function Resolve-ArchifyEntrypoint {
    param(
        [Parameter(Mandatory)]
        [string]$CandidateRoot
    )

    if ([string]::IsNullOrWhiteSpace($CandidateRoot)) {
        return $null
    }

    $resolvedRoot = Resolve-Path -LiteralPath $CandidateRoot -ErrorAction SilentlyContinue
    if ($null -eq $resolvedRoot) {
        return $null
    }

    $entrypoints = @(
        Join-Path $resolvedRoot.Path 'bin\archify.mjs'
        Join-Path $resolvedRoot.Path 'archify\bin\archify.mjs'
    )

    return $entrypoints |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}

# //// 转发命令到 Archify 包入口 [@x380kkm 2026-09-03] ////
function Invoke-ArchifyEntrypoint {
    param(
        [Parameter(Mandatory)]
        [string]$Entrypoint,

        [Parameter()]
        [string[]]$Arguments
    )

    & node $Entrypoint @Arguments
    exit $LASTEXITCODE
}

# //// 从 manager 自有状态读取 Archify 来源 [@x380kkm 2026-09-03] ////
function Get-ArchifyManagedRoot {
    $toolkitRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $localStatePath = Join-Path $toolkitRoot 'managed/clean-vibing.json'
    if (Test-Path -LiteralPath $localStatePath -PathType Leaf) {
        $localState = Get-Content -LiteralPath $localStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $localSource = $localState.sources.archify
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
        return $state.sources.archify
    } catch {
        return $null
    }
}

# //// 选择显式配置的独立 Archify 安装 [@x380kkm 2026-09-03] ////
if (-not [string]::IsNullOrWhiteSpace($ArchifyRoot)) {
    $entrypoint = Resolve-ArchifyEntrypoint -CandidateRoot $ArchifyRoot
    if ($null -eq $entrypoint) {
        throw "Archify 包根无有效入口: $ArchifyRoot"
    }
    Invoke-ArchifyEntrypoint -Entrypoint $entrypoint -Arguments $ArchifyArguments
}

if (-not [string]::IsNullOrWhiteSpace($env:ARCHIFY_ROOT)) {
    $entrypoint = Resolve-ArchifyEntrypoint -CandidateRoot $env:ARCHIFY_ROOT
    if ($null -eq $entrypoint) {
        throw "ARCHIFY_ROOT 无有效入口: $env:ARCHIFY_ROOT"
    }
    Invoke-ArchifyEntrypoint -Entrypoint $entrypoint -Arguments $ArchifyArguments
}

$managedSource = Get-ArchifyManagedRoot
if (-not [string]::IsNullOrWhiteSpace($managedSource)) {
    $entrypoint = Resolve-ArchifyEntrypoint -CandidateRoot $managedSource
    if ($null -ne $entrypoint) {
        Invoke-ArchifyEntrypoint -Entrypoint $entrypoint -Arguments $ArchifyArguments
    }
}

$pathCommand = Get-Command 'archify' -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $pathCommand) {
    & $pathCommand.Source @ArchifyArguments
    exit $LASTEXITCODE
}

$projectRoot = (Get-Location).Path
$managedRoots = @(
    $(if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'tools\archify' })
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE '.codex\tools\archify' })
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE '.codex\skills\archify' })
    $(if ($env:USERPROFILE) { Join-Path $env:USERPROFILE '.agents\skills\archify' })
    (Join-Path $projectRoot '.codex\skills\archify')
    (Join-Path $projectRoot '.agents\skills\archify')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

foreach ($managedRoot in $managedRoots) {
    $entrypoint = Resolve-ArchifyEntrypoint -CandidateRoot $managedRoot
    if ($null -ne $entrypoint) {
        Invoke-ArchifyEntrypoint -Entrypoint $entrypoint -Arguments $ArchifyArguments
    }
}

throw 'Archify 未找到. 请安装 archify 命令, 设置 ARCHIFY_ROOT, 或传入 -ArchifyRoot <包根或上游仓库根>.'
