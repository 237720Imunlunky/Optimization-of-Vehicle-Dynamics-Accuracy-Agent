param(
    [string]$InstallRoot = "F:\VehicleDynamicsAgent",
    [string]$RuntimeRoot = "F:\VehicleDynamicsAgent\Runtime",
    [string]$CarSimRoot = "",
    [string]$PythonCommand = "python"
)

# 兼容旧入口；GitHub用户推荐使用install_agent.cmd或install.ps1。
& (Join-Path $PSScriptRoot "install.ps1") `
    -InstallRoot $InstallRoot -RuntimeRoot $RuntimeRoot `
    -CarSimRoot $CarSimRoot -PythonCommand $PythonCommand
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
