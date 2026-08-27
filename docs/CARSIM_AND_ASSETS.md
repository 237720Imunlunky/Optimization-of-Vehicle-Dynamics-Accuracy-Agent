# CarSim 与车辆资产准备

## CarSim 安装路径

用户的 CarSim 不需要安装在固定盘符。查找顺序为：

1. 安装命令的 `-CarSimRoot`；
2. 环境变量 `CARSIM_ROOT`；
3. Windows 常见 C/D/E/F 安装目录；
4. Python 运行时补充查询 Windows 卸载注册表。

有效根目录必须包含：

```text
Programs/VS_SolverWrapper_CLI_64.exe
Programs/solvers/carsim_64.dll
Resources/
```

找不到时，编辑 `项目实现/config/runtime.local.json` 的 `carsim_root`。这里只填写安装根目录，不填写 exe 路径。

## CarSim 授权

文件存在不等于许可证有效。首次完整验收必须执行一个最小求解。仓库不提供许可证，也不会绕过授权检查。

## 车辆模板

完整优化需要一个用户有权使用的展开模型：

`项目实现/local_assets/vehicle_template/Run_all.par`

模板必须包含 `llm_optimizer/config/parameter_registry.json` 声明的字段。Agent 不会猜测不存在的 CarSim 关键字。

## 实车数据

将 DBC 放在 `local_assets/data/` 根目录；BLF 子目录应匹配 `conditions/condition_registry.json` 的
`source_subdirectory`。原始 BLF 始终只读，准入流程只创建分类副本和校验摘要。

## 正式基线

仓库内 `demo_assets/formal_acceptance.demo.json` 仅用于演示。完整优化必须使用本车数据和本车模板计算正式基线，
保存到 `local_assets/formal_baseline/formal_acceptance.json`，再更新 `runtime.local.json`。

## 不能公开的文件

- CarSim 安装目录内容；
- 商业车辆模型和许可证；
- 厂商 DBC；
- 原始 BLF 和人工复核记录；
- 真实 API Key。
