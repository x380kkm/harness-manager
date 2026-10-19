# audience: internal
# # command-log
# invoke-command.ps1 加载此文件以生成 UTF8 日志诊断和原文续读范围.
$ErrorActionPreference = 'Stop'
$beforeErrorLines = 3
$afterErrorLines = 3
$tailLines = 4

$bearerMasker = [System.Text.RegularExpressions.MatchEvaluator]{
    param([System.Text.RegularExpressions.Match]$Match)
    return $Match.Groups[1].Value + ('*' * $Match.Groups[2].Value.Length)
}
$assignmentMasker = [System.Text.RegularExpressions.MatchEvaluator]{
    param([System.Text.RegularExpressions.Match]$Match)
    return $Match.Groups[1].Value + $Match.Groups[2].Value + ('*' * $Match.Groups[3].Value.Length)
}

# //// 裁剪回执文本中的常见凭据值, 日志文件保持进程原始输出 [@x380kkm 2026-09-14] ////
function Protect-DiagnosticText {
    param(
        [AllowEmptyString()]
        [string]$Text
    )

    $masked = [regex]::Replace($Text, '(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]+)', $bearerMasker)
    return [regex]::Replace(
        $masked,
        '(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[:=]\s*)([^\s,;]+)',
        $assignmentMasker
    )
}
# //// /裁剪凭据值 ////


# //// 单遍扫描一个日志, 保留首个错误块和末尾上下文 [@x380kkm 2026-09-14] ////
function Get-LogScan {
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [Parameter(Mandatory)]
        [string]$Stream
    )

    $before = [System.Collections.Generic.List[object]]::new()
    $errorBlock = [System.Collections.Generic.List[object]]::new()
    $tail = [System.Collections.Generic.List[object]]::new()
    $firstError = $null
    $afterCount = 0
    $lineNumber = 0
    $reader = [System.IO.StreamReader]::new($Path, [System.Text.Encoding]::UTF8, $true)

    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            $lineNumber++
            $record = [pscustomobject]@{ line = $lineNumber; text = $line }
            if ($null -eq $firstError -and $line -match '(?i)(?:^|\b)(?:error|fatal|exception|failed|failure)(?:\b|:)') {
                foreach ($prior in $before) {
                    $errorBlock.Add($prior)
                }
                $errorBlock.Add($record)
                $firstError = $record
                $afterCount = $afterErrorLines
            } elseif ($null -ne $firstError -and $afterCount -gt 0) {
                $errorBlock.Add($record)
                $afterCount--
            } elseif ($null -eq $firstError) {
                $before.Add($record)
                if ($before.Count -gt $beforeErrorLines) {
                    $before.RemoveAt(0)
                }
            }

            $tail.Add($record)
            if ($tail.Count -gt $tailLines) {
                $tail.RemoveAt(0)
            }
        }
    } finally {
        $reader.Dispose()
    }

    return [pscustomobject]@{
        stream = $Stream
        logPath = $Path
        lineCount = $lineNumber
        byteCount = (Get-Item -LiteralPath $Path -ErrorAction Stop).Length
        firstError = $firstError
        errorBlock = $errorBlock.ToArray()
        tail = $tail.ToArray()
    }
}
# //// /扫描日志 ////


# //// 生成带字符续读锚点的单行回执 [@x380kkm 2026-09-14] ////
function New-ReceiptLine {
    param(
        [Parameter(Mandatory)]
        [pscustomobject]$Record,

        [AllowNull()]
        [string]$LogPath,

        [Parameter(Mandatory)]
        [int]$Limit
    )

    if ($Limit -le 0) {
        return $null
    }

    $text = Protect-DiagnosticText -Text $Record.text
    $shownLength = [Math]::Min($text.Length, $Limit)
    $line = [ordered]@{
        line = $Record.line
        text = $text.Substring(0, $shownLength)
    }
    if ($shownLength -lt $text.Length) {
        $line.continuation = [ordered]@{
            logPath = $LogPath
            line = $Record.line
            characterStart = $shownLength + 1
            characterEnd = $text.Length
            totalCharacters = $text.Length
        }
    }
    return [pscustomobject]$line
}
# //// /生成单行回执 ////


# //// 合并已返回行并计算未返回行区间 [@x380kkm 2026-09-14] ////
function Get-LineCoverage {
    param(
        [Parameter(Mandatory)]
        [int]$LineCount,

        [Parameter()]
        [int[]]$Lines = @()
    )

    $numbers = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($line in $Lines) {
        if ($line -ge 1 -and $line -le $LineCount) {
            $null = $numbers.Add($line)
        }
    }
    $orderedLines = @($numbers | Sort-Object)
    $returnedRanges = [System.Collections.Generic.List[object]]::new()
    if ($orderedLines.Count -gt 0) {
        $start = $orderedLines[0]
        $end = $start
        foreach ($line in $orderedLines | Select-Object -Skip 1) {
            if ($line -eq $end + 1) {
                $end = $line
            } else {
                $returnedRanges.Add([ordered]@{ start = $start; end = $end })
                $start = $line
                $end = $line
            }
        }
        $returnedRanges.Add([ordered]@{ start = $start; end = $end })
    }

    $omittedRanges = [System.Collections.Generic.List[object]]::new()
    $nextLine = 1
    foreach ($range in $returnedRanges) {
        if ($nextLine -lt $range.start) {
            $omittedRanges.Add([ordered]@{ start = $nextLine; end = $range.start - 1 })
        }
        $nextLine = $range.end + 1
    }
    if ($nextLine -le $LineCount) {
        $omittedRanges.Add([ordered]@{ start = $nextLine; end = $LineCount })
    }

    return [pscustomobject]@{
        returnedRanges = $returnedRanges.ToArray()
        omittedRanges = $omittedRanges.ToArray()
    }
}
# //// /计算行区间 ////


# //// 按字符预算从日志扫描结果生成失败诊断 [@x380kkm 2026-09-14] ////
function New-LogDiagnostic {
    param(
        [Parameter()]
        [object[]]$Scans = @(),

        [Parameter(Mandatory)]
        [int]$CharacterBudget,

        [string]$FallbackMessage
    )

    $remaining = $CharacterBudget
    $tailReserve = [Math]::Min($remaining, [Math]::Max(128, [int]($CharacterBudget / 4)))
    $firstScan = $Scans | Where-Object { $null -ne $_.firstError } | Select-Object -First 1
    $firstError = $null
    $selectedLines = @{}
    $longLineTruncated = $false
    foreach ($scan in $Scans) {
        $selectedLines[$scan.logPath] = [System.Collections.Generic.HashSet[int]]::new()
    }
    if ($null -ne $firstScan) {
        $firstLimit = [Math]::Min(512, [Math]::Max(1, $remaining - $tailReserve))
        $line = New-ReceiptLine -Record $firstScan.firstError -LogPath $firstScan.logPath -Limit $firstLimit
        $remaining -= $line.text.Length
        $null = $selectedLines[$firstScan.logPath].Add($line.line)
        $firstError = [ordered]@{
            stream = $firstScan.stream
            logPath = $firstScan.logPath
            line = $line.line
            text = $line.text
        }
        if ($null -ne $line.continuation) {
            $firstError.continuation = $line.continuation
            $longLineTruncated = $true
        }
    } elseif (-not [string]::IsNullOrWhiteSpace($FallbackMessage)) {
        $firstLimit = [Math]::Min(512, [Math]::Max(1, $remaining - $tailReserve))
        $line = New-ReceiptLine -Record ([pscustomobject]@{ line = 0; text = $FallbackMessage }) -LogPath $null -Limit $firstLimit
        $remaining -= $line.text.Length
        $firstError = [ordered]@{ stream = 'runner'; logPath = $null; line = 0; text = $line.text }
        if ($null -ne $line.continuation) {
            $firstError.continuation = $line.continuation
            $longLineTruncated = $true
        }
    }

    $errorBudget = $remaining - $tailReserve
    $errorBlocks = [System.Collections.Generic.List[object]]::new()
    foreach ($scan in $Scans) {
        $lines = [System.Collections.Generic.List[object]]::new()
        foreach ($record in @($scan.errorBlock)) {
            $line = New-ReceiptLine -Record $record -LogPath $scan.logPath -Limit ([Math]::Min(1024, $errorBudget))
            if ($null -eq $line) {
                break
            }
            $lines.Add($line)
            $null = $selectedLines[$scan.logPath].Add($line.line)
            $errorBudget -= $line.text.Length
            $remaining -= $line.text.Length
            if ($null -ne $line.continuation) {
                $longLineTruncated = $true
            }
        }
        if ($lines.Count -gt 0) {
            $errorBlocks.Add([ordered]@{ stream = $scan.stream; logPath = $scan.logPath; lines = $lines.ToArray() })
        }
    }

    $tailBlocks = [System.Collections.Generic.List[object]]::new()
    foreach ($scan in $Scans) {
        $lines = [System.Collections.Generic.List[object]]::new()
        for ($index = $scan.tail.Count - 1; $index -ge 0 -and $remaining -gt 0; $index--) {
            $line = New-ReceiptLine -Record $scan.tail[$index] -LogPath $scan.logPath -Limit ([Math]::Min(1024, $remaining))
            if ($null -ne $line) {
                $lines.Insert(0, $line)
                $null = $selectedLines[$scan.logPath].Add($line.line)
                $remaining -= $line.text.Length
                if ($null -ne $line.continuation) {
                    $longLineTruncated = $true
                }
            }
        }
        $tailBlocks.Add([ordered]@{ stream = $scan.stream; logPath = $scan.logPath; lines = $lines.ToArray() })
    }

    $logReceipts = [System.Collections.Generic.List[object]]::new()
    $hasOmittedRanges = $false
    foreach ($scan in $Scans) {
        [int[]]$lineNumbers = @($selectedLines[$scan.logPath] | ForEach-Object { $_ })
        $coverage = Get-LineCoverage -LineCount $scan.lineCount -Lines $lineNumbers
        if (@($coverage.omittedRanges).Count -gt 0) {
            $hasOmittedRanges = $true
            $firstGap = $coverage.omittedRanges[0]
            $rereadStart = [int]$firstGap.start
            $rereadCount = [Math]::Min(32, [int]$firstGap.end - $rereadStart + 1)
        } else {
            $rereadStart = 1
            $rereadCount = [Math]::Min(16, [Math]::Max(1, [int]$scan.lineCount))
        }
        $quotedPath = $scan.logPath.Replace("'", "''")
        $logReceipts.Add([ordered]@{
            stream = $scan.stream
            logPath = $scan.logPath
            lineCount = $scan.lineCount
            byteCount = $scan.byteCount
            returnedRanges = $coverage.returnedRanges
            omittedRanges = $coverage.omittedRanges
            reread = [ordered]@{
                range = [ordered]@{ start = $rereadStart; end = $rereadStart + $rereadCount - 1 }
                command = "Get-Content -LiteralPath '$quotedPath' -Encoding UTF8 | Select-Object -Skip $($rereadStart - 1) -First $rereadCount"
            }
        })
    }

    return [ordered]@{
        characterBudget = $CharacterBudget
        truncated = $longLineTruncated -or $hasOmittedRanges
        firstError = $firstError
        errorBlocks = $errorBlocks.ToArray()
        tail = $tailBlocks.ToArray()
        logs = $logReceipts.ToArray()
    }
}
# //// /生成失败诊断 ////
