param(
    [string]$InstallRoot = "",
    [int]$Port = 8765,
    [switch]$NoBrowser
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
    throw "未找到Python虚拟环境：$python。请先双击install_agent.cmd。"
}

function Find-FreePort {
    param([int]$StartPort)
    foreach ($candidate in $StartPort..($StartPort + 20)) {
        if (-not (Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue)) { return $candidate }
    }
    throw "从端口$StartPort开始连续21个端口均被占用，请关闭冲突程序后重试。"
}

function Wait-AgentReady {
    param([int]$ReadyPort, [System.Diagnostics.Process]$Process)
    $url = "http://127.0.0.1:$ReadyPort/api/job"
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if ($Process.HasExited) { throw "Agent进程提前退出，退出码：$($Process.ExitCode)" }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $url }
        } catch { Start-Sleep -Milliseconds 300 }
    }
    throw "Agent启动超时，30秒内未能访问：$url"
}

$actualPort = Find-FreePort -StartPort $Port
$serverScript = Join-Path $packageRoot "Agent交互界面\server.py"
$process = Start-Process -FilePath $python -ArgumentList @($serverScript, "--port", $actualPort) -WorkingDirectory $packageRoot -PassThru -NoNewWindow
try {
    $url = Wait-AgentReady -ReadyPort $actualPort -Process $process
    Write-Host "Agent已启动：$url"
    if ($actualPort -ne $Port) { Write-Host "默认端口$Port已占用，已自动切换到$actualPort。" }
    if (-not $NoBrowser) { Start-Process $url | Out-Null }
    Write-Host "关闭本窗口即可停止Agent。"
    Wait-Process -Id $process.Id
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
}
