param(
    [string]$InstallRoot = "",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $PSScriptRoot
$runtimeConfig = Join-Path $packageRoot "config\runtime.local.json"
if (-not $InstallRoot -and (Test-Path -LiteralPath $runtimeConfig)) {
    $configText = [System.IO.File]::ReadAllText($runtimeConfig, [System.Text.Encoding]::UTF8)
    $config = $configText | ConvertFrom-Json
    $InstallRoot = [string]$config.install_root
}
if (-not $InstallRoot) {
    $InstallRoot = if (Test-Path -LiteralPath "F:\") { "F:\VehicleDynamicsAgent" } else { Join-Path $env:LOCALAPPDATA "VehicleDynamicsAgent" }
}
$python = Join-Path $InstallRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到F盘虚拟环境，请先双击install_agent.cmd，或运行install_f_drive.ps1"
}
Write-Host "Agent正在启动：http://127.0.0.1:$Port"
Write-Host "关闭本窗口即可停止Agent。"
& $python (Join-Path $packageRoot "Agent交互界面\server.py") --port $Port
