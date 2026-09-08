# audience: internal
# # desktop-launcher
# 启动入口在自身目录构建并打开桌面应用, 运行环境使用 PowerShell 7.
$ErrorActionPreference = 'Stop'

# //// 从桌面目录准备并打开管理窗口 [@x380kkm 2026-09-06] ////
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:npm_config_script_shell = 'pwsh'
    $env:ELECTRON_RUN_AS_NODE = $null
    $package = Get-Content -LiteralPath 'package.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    $dependencies = @($package.dependencies.PSObject.Properties.Name) + @($package.devDependencies.PSObject.Properties.Name)
    $missing = $dependencies | Where-Object { -not (Test-Path -LiteralPath (Join-Path node_modules $_)) }
    if ($missing) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw '桌面依赖安装失败.' }
    }
    npm start
    if ($LASTEXITCODE -ne 0) { throw '桌面应用启动失败.' }
} finally {
    Pop-Location
}
# //// /从桌面目录准备并打开管理窗口 ////
