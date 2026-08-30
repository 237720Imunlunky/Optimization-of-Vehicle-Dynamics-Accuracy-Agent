# 故障排查

## PowerShell 找不到部署脚本

相对路径依赖当前目录。最简单的方式是直接双击 `install_agent.cmd`，或使用带双引号的完整路径。

## Python 版本不正确

运行 `py -0p` 查看所有 Python。安装器当前验收 Python 3.14 x64，可通过 `-PythonCommand` 指定解释器完整路径。

## CarSim 未发现

运行安装器时传入 `-CarSimRoot`，或者设置 `CARSIM_ROOT`。根目录下必须有 CLI Solver 和 `carsim_64.dll`。

## ASCII 编码错误

CarSim Runtime 不能包含中文。项目、数据和输出目录可以包含中文；只需把 `runtime_root` 改为例如
`D:/VehicleDynamicsAgent/Runtime`。

## 只有演示模式

这是正常分级状态。依次补齐 DBC/BLF、车辆模板、本车正式基线、CarSim 和 LLM API，再重新运行部署验收。

## 完整优化按钮不可用

检查界面中的 LLM API、CarSim 求解器和数据准入三项，并打开
`输出/部署体检/当前机器/health_check.json` 查看缺失文件的准确路径。

## CarSim 文件存在但求解失败

文件检查不能证明许可证有效。先在 CarSim 本体中确认许可证和相同车辆模型可运行，再检查模板绑定字段和 Runtime 权限。

## Ubuntu 上无法运行 CarSim

这是平台限制，不是 Bash 脚本错误。CarSim 2023.2 的 CLI Solver 和 DLL 是 Windows 组件。
Ubuntu 上使用 `verify_ubuntu.sh` 验收数据和干运行；完整优化请在 Windows 安装 CarSim，或实现远程 Windows 求解服务。
