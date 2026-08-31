# Vehicle Dynamics Parameter Agent

这是一个使用 LLM 提出参数候选、由 CarSim 执行物理仿真、再用实车数据评价并决定接受或回退的车辆动力学参数优化 Agent。
当前公开实现覆盖 0–100 km/h、60–100 km/h 和 50–30 km/h 滑行三类纵向工况。

## 下载后能做什么

| 模式 | 需要 CarSim | 需要实车数据 | 需要 LLM API |
|---|---:|---:|---:|
| 演示看板和干运行 | 否 | 否 | 否 |
| BLF 解码与数据准入 | 否 | 是 | 否 |
| 完整参数优化闭环 | 是 | 是 | 是 |

## 五分钟开始

1. 下载整个 GitHub 仓库 ZIP 并解压，不能只下载 `部署包` 文件夹。
2. 安装 Windows 10/11 x64 和 Python 3.14 x64。
3. 双击根目录 `1_安装Agent.cmd`。
4. 双击根目录 `2_验收Agent.cmd`。
5. 双击根目录 `3_启动Agent.cmd`。服务就绪后会自动打开浏览器。

首次安装没有 CarSim、DBC 或 BLF 也可以通过演示和干运行验收。完整闭环还需要用户自行准备合法授权的
CarSim 2023.2、车辆模板、DBC/BLF、本车正式基线和 OpenAI 兼容模型 API。

## 仓库不包含

- CarSim 安装程序、许可证或求解器动态库；
- 未获授权再分发的 CarSim 车辆模型；
- 真实车辆 BLF、DBC 和试验记录；
- API Key；
- 开发者电脑上的历史输出和 Python 环境。

详细安装与资产清单见 [开源部署指南](docs/OPEN_SOURCE_DEPLOYMENT.md)。

Ubuntu 用户请使用 Bash 入口：

```bash
chmod +x 项目实现/部署包/install_ubuntu.sh 项目实现/部署包/start_ubuntu.sh 项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/install_ubuntu.sh
./项目实现/部署包/verify_ubuntu.sh
./项目实现/部署包/start_ubuntu.sh
```

Ubuntu 支持演示、干运行、数据解码、准入审查、评价和本地界面；CarSim 2023.2 的 Windows 求解器需在 Windows 或远程 Windows 节点运行。

## 文档

- [开源部署指南](docs/OPEN_SOURCE_DEPLOYMENT.md)
- [CarSim 与车辆资产准备](docs/CARSIM_AND_ASSETS.md)
- [本机配置说明](docs/CONFIGURATION.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [正式发布检查清单](docs/PUBLICATION_CHECKLIST.md)
- [项目实现说明](项目实现/README.md)

## 安全边界

LLM 只提出候选，不直接修改正式最优状态。所有候选必须经过参数边界检查、CarSim 求解、标定集、验证集和全量数据保护线。
API Key 只保存在被 Git 忽略的本机配置中。

## 许可证

项目代码使用 MIT License。CarSim、用户车辆模型、DBC/BLF 和其他第三方资产遵循各自许可证，MIT License 不授予这些资产的再分发权。
