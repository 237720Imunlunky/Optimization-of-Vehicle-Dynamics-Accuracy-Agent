# GitHub 开源部署指南

## 用户应该下载什么

必须下载整个仓库。推荐在 GitHub 仓库首页选择 `Code > Download ZIP`，解压后保留原目录结构。

仓库已经包含：

- Agent 后端、前端和评价代码；
- 参数、工况和安全协议；
- VS/VSB 转换器；
- Python 依赖清单；
- 安装、启动、体检和部署验收脚本；
- 不含真实车辆信息的演示基线。

用户不得从其他开发者电脑复制：API Key、CarSim 许可证文件、未授权车辆模型或真实试验数据。

## 系统要求

- Windows 10/11 x64；
- Python 3.14 x64，并加入 PATH；
- 现代浏览器；
- 至少 10 GB 可用空间；完整优化建议预留 30 GB；
- 项目路径可以包含中文，但 CarSim Runtime 必须使用纯英文 ASCII 路径。

Ubuntu 22.04/24.04 x86_64 可运行演示、干运行、数据解码、准入审查、评价和本地界面；需要
`python3`、`python3-venv`、`python3-pip` 和现代浏览器。Ubuntu 原生不支持 CarSim 2023.2
Windows CLI/DLL，完整闭环必须使用 Windows CarSim，或后续配置远程 Windows 求解节点。

## 第一次安装

双击：

`项目实现/部署包/install_agent.cmd`

安装器优先把环境放在 F 盘；没有 F 盘时使用当前用户的本机应用数据目录。它不会安装 CarSim。
GitHub Release 的可复制压缩包附带 `部署包/wheelhouse/` 时，Python 依赖可以离线安装；源码 ZIP
没有 wheelhouse 时需要访问 PyPI。

命令行可指定不同路径：

```powershell
powershell -ExecutionPolicy Bypass -File ".\项目实现\部署包\install.ps1" `
  -InstallRoot "D:\VehicleDynamicsAgent" `
  -RuntimeRoot "D:\VehicleDynamicsAgent\Runtime" `
  -CarSimRoot "D:\Engineering\CarSim2023.2\install"
```

也可以提前设置：

```powershell
$env:CARSIM_ROOT="D:\Engineering\CarSim2023.2\install"
```

## 分级验收

双击 `项目实现/部署包/verify_installation.cmd`。验收结果保存到：

`项目实现/输出/部署验收/<时间>/`

三级结论：

- `demo_and_dry_run`：下载即用，不需要 CarSim、实车数据或 API；
- `data_workflow`：DBC/BLF 已就绪，可以解码和准入；
- `full_optimization`：CarSim、车辆模板、本车基线、数据和 API 全部就绪。

双击 `项目实现/部署包/start_agent.cmd` 启动网页。脚本会先探测 `/api/job`，确认服务已真正启动后再打开浏览器；
默认端口 `8765` 被占用时会自动选择后续可用端口并显示实际地址。

## 完整优化准备顺序

1. 安装并激活合法授权的 CarSim 2023.2。
2. 将本车 `Run_all.par` 放入 `项目实现/local_assets/vehicle_template/`。
3. 将 DBC/BLF 放入 `项目实现/local_assets/data/`。
4. 运行解码、数据准入和正式基线计算。
5. 将正式基线配置为 `local_assets/formal_baseline/formal_acceptance.json`。
6. 填写 `Agent交互界面/config/llm_api.local.json`。
7. 重新运行部署验收，确认级别变为 `full_optimization`。

## GitHub 发布前检查

```powershell
git status --short
git check-ignore "项目实现/config/runtime.local.json"
git check-ignore "项目实现/Agent交互界面/config/llm_api.local.json"
python -m pytest -q
```

确认 `实车数据/`、`项目实现/输出/`、`local_assets` 私有内容和所有本机配置没有进入提交。

## Ubuntu/Bash 安装

```bash
chmod +x 项目实现/部署包/install_ubuntu.sh 项目实现/部署包/start_ubuntu.sh 项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/install_ubuntu.sh
./项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/start_ubuntu.sh
```

脚本会自动创建虚拟环境、安装依赖、生成本机路径配置并运行分级体检。API 配置仍放在
`项目实现/Agent交互界面/config/llm_api.local.json`，可直接编辑或使用 `xdg-open` 打开。
