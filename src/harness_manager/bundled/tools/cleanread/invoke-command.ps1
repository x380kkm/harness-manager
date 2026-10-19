# audience: internal
# # cleanread-invoke-command
# 此入口为任务调用方执行一个原生命令, 分流保存标准输出和标准错误, 并返回可定位的诊断回执.
# 日志行号和字符范围均使用从 1 开始的索引.

[CmdletBinding(DefaultParameterSetName = 'Command')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Command')]
    [ValidateNotNullOrEmpty()]
    [string]$Command,

    [Parameter(ParameterSetName = 'Command')]
    [string[]]$ArgumentList = @(),

    [Parameter(ParameterSetName = 'Command')]
    [string]$WorkingDirectory = (Get-Location).Path,

    [Parameter(Mandatory, ParameterSetName = 'Command')]
    [ValidateNotNullOrEmpty()]
    [string]$LogDirectory,

    [Parameter(Mandatory, ParameterSetName = 'ReadLog')]
    [ValidateNotNullOrEmpty()]
    [string]$ReadLog,

    [Parameter(ParameterSetName = 'ReadLog')]
    [int]$ExitCode,

    [Parameter(ParameterSetName = 'Command')]
    [Parameter(ParameterSetName = 'ReadLog')]
    [string]$Operation,

    [Parameter(ParameterSetName = 'Command')]
    [Parameter(ParameterSetName = 'ReadLog')]
    [ValidateRange(512, 65536)]
    [int]$Budget = 12000
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)

# //// 为进程执行加载标准流复制类型 [@x380kkm 2026-09-14] ////
function Initialize-CommandStream {
    if ($null -ne ('CleanreadCommandStream' -as [type])) {
        return
    }

    Add-Type -TypeDefinition @'
using System.IO;
using System.Threading.Tasks;

public static class CleanreadCommandStream
{
    public static async Task CopyAsync(TextReader reader, TextWriter writer)
    {
        var buffer = new char[8192];
        int count;
        while ((count = await reader.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) > 0)
        {
            await writer.WriteAsync(buffer, 0, count).ConfigureAwait(false);
            await writer.FlushAsync().ConfigureAwait(false);
        }
    }
}
'@
}
# //// /加载标准流复制类型 ////


. (Join-Path $PSScriptRoot 'lib/command-log.ps1')


# //// 用 ArgumentList 启动进程并异步保存完整标准流 [@x380kkm 2026-09-14] ////
function Invoke-LoggedProcess {
    param(
        [Parameter(Mandatory)]
        [string]$FileName,

        [Parameter()]
        [string[]]$Arguments = @(),

        [Parameter(Mandatory)]
        [string]$Directory,

        [Parameter(Mandatory)]
        [string]$StdoutPath,

        [Parameter(Mandatory)]
        [string]$StderrPath
    )

    $workingDirectory = (Resolve-Path -LiteralPath $Directory -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $workingDirectory -PathType Container)) {
        throw "工作目录不是目录: $Directory"
    }

    Initialize-CommandStream
    $stdoutWriter = [System.IO.StreamWriter]::new($StdoutPath, $false, $utf8)
    $stderrWriter = [System.IO.StreamWriter]::new($StderrPath, $false, $utf8)
    $process = $null
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $FileName
        $startInfo.WorkingDirectory = $workingDirectory
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.StandardOutputEncoding = $utf8
        $startInfo.StandardErrorEncoding = $utf8
        foreach ($argument in $Arguments) {
            $null = $startInfo.ArgumentList.Add([string]$argument)
        }

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "进程没有启动: $FileName"
        }
        $stdoutTask = [CleanreadCommandStream]::CopyAsync($process.StandardOutput, $stdoutWriter)
        $stderrTask = [CleanreadCommandStream]::CopyAsync($process.StandardError, $stderrWriter)
        $exitTask = $process.WaitForExitAsync()
        $pendingTasks = [System.Collections.Generic.List[System.Threading.Tasks.Task]]::new()
        $pendingTasks.Add($exitTask)
        $pendingTasks.Add($stdoutTask)
        $pendingTasks.Add($stderrTask)
        while ($pendingTasks.Count -gt 0) {
            $completedTask = [System.Threading.Tasks.Task]::WhenAny($pendingTasks.ToArray()).GetAwaiter().GetResult()
            $null = $pendingTasks.Remove($completedTask)
            if ($completedTask.IsFaulted -or $completedTask.IsCanceled) {
                if (-not $process.HasExited) {
                    $process.Kill($true)
                }
                try {
                    $exitTask.GetAwaiter().GetResult()
                } catch {
                }
                try {
                    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask))
                } catch {
                }
                $completedTask.GetAwaiter().GetResult()
            }
        }
        $actualExitCode = $process.ExitCode
        return $actualExitCode
    } finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
        $stdoutWriter.Dispose()
        $stderrWriter.Dispose()
    }
}
# //// /启动进程 ////


# //// 只读现有日志并返回诊断, 不执行原生命令 [@x380kkm 2026-09-14] ////
if ($PSCmdlet.ParameterSetName -eq 'ReadLog') {
    $suppliedExitCode = if ($PSBoundParameters.ContainsKey('ExitCode')) { $ExitCode } else { $null }
    $readOperation = if ([string]::IsNullOrWhiteSpace($Operation)) { [System.IO.Path]::GetFileName($ReadLog) } else { $Operation }
    $readFailure = $null
    try {
        $logPath = (Resolve-Path -LiteralPath $ReadLog -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
            throw "日志路径不是文件: $ReadLog"
        }
        $receipt = [ordered]@{
            status = 'read-log'
            command = $null
            operation = $readOperation
            actualExitCode = $suppliedExitCode
            logs = [ordered]@{ log = $logPath }
            diagnostic = New-LogDiagnostic -Scans @(Get-LogScan -Path $logPath -Stream 'log') -CharacterBudget $Budget
        }
    } catch {
        $readFailure = $_
        $receipt = [ordered]@{
            status = 'read-error'
            command = $null
            operation = $readOperation
            actualExitCode = $suppliedExitCode
            logs = [ordered]@{ log = $ReadLog }
            diagnostic = New-LogDiagnostic -CharacterBudget $Budget -FallbackMessage $_.Exception.Message
        }
    }
    Write-Output ($receipt | ConvertTo-Json -Depth 12 -Compress)
    if ($null -ne $readFailure) {
        throw $readFailure
    }
    return
}
# //// /只读现有日志 ////


# //// 执行命令, 失败后输出回执并抛出以停止调用方依赖操作 [@x380kkm 2026-09-14] ////
$resolvedCommand = $Command
if ([string]::IsNullOrWhiteSpace([System.IO.Path]::GetExtension($Command))) {
    $commandInfo = Get-Command $Command -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $commandInfo -and -not [string]::IsNullOrWhiteSpace($commandInfo.Source)) {
        $resolvedCommand = $commandInfo.Source
    }
}
$commandName = [System.IO.Path]::GetFileName($resolvedCommand)
if ([string]::IsNullOrWhiteSpace($commandName)) {
    $commandName = $Command
}
$operationName = if ([string]::IsNullOrWhiteSpace($Operation)) { $commandName } else { $Operation }
$safeOperation = [regex]::Replace($operationName, '[^A-Za-z0-9._-]+', '-').Trim([char[]]@('-', '.'))
if ([string]::IsNullOrWhiteSpace($safeOperation)) {
    $safeOperation = 'command'
}
$logPaths = $null
$receiptWritten = $false
try {
    $absoluteLogDirectory = [System.IO.Path]::GetFullPath($LogDirectory)
    $null = New-Item -ItemType Directory -Path $absoluteLogDirectory -Force
    $absoluteLogDirectory = (Resolve-Path -LiteralPath $absoluteLogDirectory -ErrorAction Stop).Path
    $runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N')
    $logPaths = [ordered]@{
        stdout = Join-Path $absoluteLogDirectory "$safeOperation-$runId.stdout.log"
        stderr = Join-Path $absoluteLogDirectory "$safeOperation-$runId.stderr.log"
    }

    $extension = [System.IO.Path]::GetExtension($resolvedCommand).ToLowerInvariant()
    $isCommandShell = [System.IO.Path]::GetFileName($resolvedCommand) -match '^(?i:cmd)(?:\.exe)?$'
    if ($extension -in @('.cmd', '.bat') -or $isCommandShell) {
        [System.IO.File]::WriteAllText($logPaths.stdout, '', $utf8)
        [System.IO.File]::WriteAllText($logPaths.stderr, '', $utf8)
        $receipt = [ordered]@{
            status = 'unsupported-command'
            command = $commandName
            operation = $operationName
            actualExitCode = $null
            logs = $logPaths
            diagnostic = New-LogDiagnostic -CharacterBudget $Budget -FallbackMessage '批处理文件和 cmd.exe 需要 cmd.exe 解释. 此入口只使用 PowerShell 7 和无 shell 的进程启动, 因此不执行它们.'
        }
        Write-Output ($receipt | ConvertTo-Json -Depth 12 -Compress)
        $receiptWritten = $true
        throw 'cmd.exe 和批处理文件不受此入口支持.'
    }

    if ($extension -eq '.ps1') {
        if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion.Major -lt 7) {
            throw '此入口需要 PowerShell 7 或更高版本.'
        }
        $pwsh = Join-Path $PSHOME 'pwsh.exe'
        if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) {
            $pwsh = (Get-Command 'pwsh' -CommandType Application -ErrorAction Stop).Source
        }
        $fileName = $pwsh
        $arguments = @('-NoProfile', '-File', $resolvedCommand) + @($ArgumentList)
    } else {
        $fileName = $resolvedCommand
        $arguments = @($ArgumentList)
    }

    $actualExitCode = Invoke-LoggedProcess -FileName $fileName -Arguments $arguments -Directory $WorkingDirectory -StdoutPath $logPaths.stdout -StderrPath $logPaths.stderr
    $isNoMatch = $commandName -match '^(?i:rg)(?:\.exe)?$' -and $actualExitCode -eq 1
    $status = if ($actualExitCode -eq 0) { 'success' } elseif ($isNoMatch) { 'no-match' } else { 'failed' }
    $receipt = [ordered]@{
        status = $status
        command = $commandName
        operation = $operationName
        actualExitCode = $actualExitCode
        logs = $logPaths
    }
    if ($status -eq 'failed') {
        $scans = @(
            Get-LogScan -Path $logPaths.stderr -Stream 'stderr'
            Get-LogScan -Path $logPaths.stdout -Stream 'stdout'
        )
        $receipt.diagnostic = New-LogDiagnostic -Scans $scans -CharacterBudget $Budget -FallbackMessage "进程以退出码 $actualExitCode 结束."
    }
    Write-Output ($receipt | ConvertTo-Json -Depth 12 -Compress)
    $receiptWritten = $true
    if ($status -eq 'failed') {
        throw "命令 $commandName 以退出码 $actualExitCode 结束."
    }
} catch {
    if (-not $receiptWritten) {
        $receipt = [ordered]@{
            status = 'runner-error'
            command = $commandName
            operation = $operationName
            actualExitCode = $null
            logs = if ($null -eq $logPaths) { [ordered]@{} } else { $logPaths }
            diagnostic = New-LogDiagnostic -CharacterBudget $Budget -FallbackMessage $_.Exception.Message
        }
        Write-Output ($receipt | ConvertTo-Json -Depth 12 -Compress)
    }
    throw
}
# //// /执行命令 ////
