# 一键部署包

## 普通用户

1. 下载并解压整个 GitHub 仓库。
2. 安装 Python 3.14 x64 并加入 PATH。
3. 双击 `install_agent.cmd`。
4. 双击 `verify_installation.cmd` 查看当前可用级别。
5. 双击 `start_agent.cmd`，脚本会等待服务就绪并自动打开浏览器。默认地址为 `http://127.0.0.1:8765`；如果端口被占用，会自动切换并在窗口显示实际地址。

安装器优先使用 F 盘；电脑没有 F 盘时使用本机应用数据目录。CarSim Runtime 可以位于任意盘，
但必须是纯英文 ASCII 绝对路径。

## 自定义 CarSim 路径

```powershell
powershell -ExecutionPolicy Bypass -File ".\install.ps1" `
  -InstallRoot "D:\VehicleDynamicsAgent" `
  -RuntimeRoot "D:\VehicleDynamicsAgent\Runtime" `
  -CarSimRoot "D:\Engineering\CarSim2023.2\install"
```

安装器按显式参数、`CARSIM_ROOT` 环境变量和常见盘符顺序发现 CarSim。未发现 CarSim 时仍可安装演示和干运行模式。

## 分级环境

- 演示和干运行：Windows 10/11 x64、Python 3.14 x64、现代浏览器；
- 数据工作流：额外需要用户自己的 DBC/BLF；
- 完整优化：额外需要合法授权 CarSim 2023.2、车辆模板、本车正式基线和 OpenAI 兼容 API。

## 文件作用

- `install_agent.cmd` / `install.ps1`：可迁移安装入口；
- `install_f_drive.ps1`：兼容旧的 F 盘调用方式；
- `start_agent.cmd` / `start_agent.ps1`：启动本地界面；
- `verify_installation.cmd` / `verify_installation.ps1`：环境体检加干运行验收；
- `health_check.py`：输出三级环境结论；
- `requirements.txt`：固定 Python 依赖。
- `wheelhouse/`：压缩发布包可附带的Python 3.14 Windows x64离线依赖；存在时安装器自动优先使用。

体检和验收结果统一保存到 `项目实现/输出/部署体检/` 与 `项目实现/输出/部署验收/`。
详细说明见仓库根目录 `docs/OPEN_SOURCE_DEPLOYMENT.md`。

## Ubuntu/Bash

Ubuntu 用户不能运行 `.cmd` 和 `.ps1`，请在仓库根目录执行：

```bash
chmod +x 项目实现/部署包/install_ubuntu.sh 项目实现/部署包/start_ubuntu.sh 项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/install_ubuntu.sh
./项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/start_ubuntu.sh
```

默认虚拟环境在 `项目实现/.venv`，CarSim 临时目录在 `/tmp/VehicleDynamicsAgent/Runtime`。
Ubuntu 版支持演示、干运行、BLF解码、数据准入、结果评价和界面；CarSim 2023.2 的 Windows CLI/DLL
不能在原生 Ubuntu 直接运行，完整闭环需 Windows CarSim 或远程 Windows 求解服务。
