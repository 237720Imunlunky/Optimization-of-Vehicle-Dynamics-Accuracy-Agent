param(
    [switch]$FullTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeConfig = Join-Path $projectRoot "config\runtime.local.json"
if (-not (Test-Path -LiteralPath $runtimeConfig)) {
    throw "尚未生成runtime.local.json，请先运行install_agent.cmd。"
}
$configText = [System.IO.File]::ReadAllText($runtimeConfig, [System.Text.Encoding]::UTF8)
$config = $configText | ConvertFrom-Json
$python = Join-Path ([string]$config.install_root) "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "未找到安装环境：$python" }

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$output = Join-Path $projectRoot "输出\部署验收\$timestamp"
New-Item -ItemType Directory -Force -Path $output | Out-Null

& $python (Join-Path $PSScriptRoot "health_check.py")
if ($LASTEXITCODE -ne 0) { throw "环境体检执行失败" }
$healthPath = Join-Path $projectRoot "输出\部署体检\当前机器\health_check.json"
$healthText = [System.IO.File]::ReadAllText($healthPath, [System.Text.Encoding]::UTF8)
$health = $healthText | ConvertFrom-Json

$dryOutput = Join-Path $output "dry_run"
Push-Location $projectRoot
try {
    & $python -m llm_optimizer.run_agent --dry-run --output $dryOutput
    if ($LASTEXITCODE -ne 0) { throw "干运行失败" }
    if ($FullTests) {
        & $python -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "单元测试失败" }
    }
} finally {
    Pop-Location
}

$summary = [ordered]@{
    verified_at = (Get-Date -Format o)
    demo_and_dry_run_ready = [bool]$health.demo_and_dry_run_ready
    data_workflow_ready = [bool]$health.data_workflow_ready
    full_optimization_files_ready = [bool]$health.full_optimization_files_ready
    active_level = [string]$health.active_level
    dry_run_passed = $true
    full_tests_run = [bool]$FullTests
}
$summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output "verification_summary.json") -Encoding utf8
@"
# 部署验收

- 演示与干运行：$($summary.demo_and_dry_run_ready)
- 数据工作流：$($summary.data_workflow_ready)
- 完整CarSim闭环文件：$($summary.full_optimization_files_ready)
- 当前可用级别：$($summary.active_level)
- 干运行：通过
- 完整单元测试：$($summary.full_tests_run)

`verification_summary.json`保存机器可读结论，`dry_run/`保存候选协议和安全校验证据。
"@ | Set-Content -LiteralPath (Join-Path $output "README.md") -Encoding utf8
Write-Host "部署验收完成：$output"
