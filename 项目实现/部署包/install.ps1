param(
    [string]$InstallRoot = "",
    [string]$RuntimeRoot = "",
    [string]$CarSimRoot = "",
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Assert-ExternalCommandSucceeded {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step 失败，进程退出码：$LASTEXITCODE" }
}

function Get-PythonVersion {
    param([string]$Command)
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "未找到Python命令：$Command。请安装Python 3.14 x64并加入PATH。"
    }
    $details = & $Command -c "import platform; print(platform.python_version()); print(platform.architecture()[0])"
    Assert-ExternalCommandSucceeded -Step "读取Python版本"
    if ($details.Count -lt 2 -or -not $details[0].StartsWith("3.14") -or $details[1] -ne "64bit") {
        throw "需要Python 3.14 x64，当前为Python $($details[0]) $($details[1])。"
    }
    return $details
}

function Find-CarSimRoot {
    param([string]$ExplicitRoot)
    if ($ExplicitRoot) { return [System.IO.Path]::GetFullPath($ExplicitRoot) }
    if ($env:CARSIM_ROOT) { return [System.IO.Path]::GetFullPath($env:CARSIM_ROOT) }
    $candidates = @()
    foreach ($drive in @("C", "D", "E", "F")) {
        $candidates += "$drive`:\Carsim\Carsim2023\Carsim2023.2\install"
        $candidates += "$drive`:\CarSim\CarSim2023.2\install"
        $candidates += "$drive`:\Program Files\Mechanical Simulation\CarSim 2023.2"
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (Test-Path -LiteralPath (Join-Path $candidate "Programs\VS_SolverWrapper_CLI_64.exe")) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return ""
}

function Default-InstallRoot {
    if (Test-Path -LiteralPath "F:\") { return "F:\VehicleDynamicsAgent" }
    return (Join-Path $env:LOCALAPPDATA "VehicleDynamicsAgent")
}

function Default-RuntimeRoot {
    if (Test-Path -LiteralPath "F:\") { return "F:\VehicleDynamicsAgent\Runtime" }
    return (Join-Path $env:LOCALAPPDATA "VehicleDynamicsAgent\Runtime")
}

if (-not $InstallRoot) { $InstallRoot = Default-InstallRoot }
if (-not $RuntimeRoot) { $RuntimeRoot = Default-RuntimeRoot }
$resolvedInstall = [System.IO.Path]::GetFullPath($InstallRoot)
$resolvedRuntime = [System.IO.Path]::GetFullPath($RuntimeRoot)
if (-not [System.Text.Encoding]::ASCII.GetString([System.Text.Encoding]::ASCII.GetBytes($resolvedRuntime)).Equals($resolvedRuntime)) {
    throw "CarSim Runtime必须使用纯英文ASCII路径，请通过-RuntimeRoot指定，例如C:\VehicleDynamicsAgent\Runtime。"
}

$pythonVersion = Get-PythonVersion -Command $PythonCommand
Write-Host "Python检查通过：$($pythonVersion[0]) $($pythonVersion[1])"
New-Item -ItemType Directory -Force -Path $resolvedInstall, $resolvedRuntime | Out-Null
$venvRoot = Join-Path $resolvedInstall "venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonCommand -m venv $venvRoot
    Assert-ExternalCommandSucceeded -Step "创建Python虚拟环境"
} else {
    & $venvPython --version
    Assert-ExternalCommandSucceeded -Step "检查现有Python虚拟环境"
}
& $venvPython -m pip install --upgrade pip
Assert-ExternalCommandSucceeded -Step "升级pip"
$wheelhouse = Join-Path $PSScriptRoot "wheelhouse"
$offlineWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "*.whl" -ErrorAction SilentlyContinue)
if ($offlineWheels.Count -gt 0) {
    Write-Host "检测到离线依赖包，使用部署包内wheelhouse安装。"
    & $venvPython -m pip install --no-index --find-links $wheelhouse -r (Join-Path $PSScriptRoot "requirements.txt")
} else {
    Write-Host "未发现离线依赖包，从PyPI安装。"
    & $venvPython -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}
Assert-ExternalCommandSucceeded -Step "安装Python依赖"

$detectedCarSim = Find-CarSimRoot -ExplicitRoot $CarSimRoot
$carSimAvailable = $detectedCarSim -and (Test-Path -LiteralPath (Join-Path $detectedCarSim "Programs\VS_SolverWrapper_CLI_64.exe"))
$configuredCarSim = if ($detectedCarSim) { $detectedCarSim } else { "C:/path/to/CarSim2023.2/install" }
$localConfig = Join-Path $projectRoot "config\runtime.local.json"
if (-not (Test-Path -LiteralPath $localConfig)) {
    $configuration = [ordered]@{
        carsim_root = ($configuredCarSim -replace '\\', '/')
        runtime_root = ($resolvedRuntime -replace '\\', '/')
        data_root = "local_assets/data"
        output_root = "输出"
        converter_path = "tools/convert_carsim_vsb.py"
        blf_dependencies = "tools"
        model_template_path = "local_assets/vehicle_template/Run_all.par"
        formal_result_path = "demo_assets/formal_acceptance.demo.json"
        install_root = ($resolvedInstall -replace '\\', '/')
    }
    $configuration | ConvertTo-Json | Set-Content -LiteralPath $localConfig -Encoding utf8
}

$apiExample = Join-Path $projectRoot "Agent交互界面\config\llm_api.example.json"
$apiLocal = Join-Path $projectRoot "Agent交互界面\config\llm_api.local.json"
if (-not (Test-Path -LiteralPath $apiLocal)) { Copy-Item -LiteralPath $apiExample -Destination $apiLocal }

& $venvPython (Join-Path $PSScriptRoot "health_check.py")
Assert-ExternalCommandSucceeded -Step "环境体检"
Write-Host "安装完成。CarSim：$(if($carSimAvailable){$detectedCarSim}else{'未发现，当前可使用演示和干运行模式'})"
Write-Host "运行 start_agent.cmd 启动界面，运行 verify_installation.cmd 执行安装验收。"
