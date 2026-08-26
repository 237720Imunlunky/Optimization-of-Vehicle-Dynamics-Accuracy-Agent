# 实车数据参数一致性对比

## 当前正式结论

严格数据策略下，当前最优纵向综合精度为94.16%，超过80%门槛。加速工况按每条实车
控制Trace独立仿真；滑行工况经原始CAN核验为舒适模式N挡、零油门、零制动、零回收，
因此使用统一50->30 km/h零驱动扭矩仿真。第3次因中途制动排除，第6次因缺少严格50 km/h起点排除。
控制证据见`输出/滑行工况审计/当前配置审计/`。

综合精度通过不代表所有单项阈值均通过，0-100峰值加速度和部分车速拟合指标仍需后续精修。

## LLM参数优化Agent

第一阶段安全控制层已落地到`llm_optimizer/`，包括参数注册表、物理边界、锁定参数、
标定/验证数据划分、结构化候选协议、OpenAI兼容API客户端和失败回退机制。

当前最优状态与本轮候选证据位于`输出/LLM参数优化Agent/`下最新的时间戳目录。候选参数文件生成和CarSim批量评价执行器也已接入，
当前同口径正式基线为91.65%，Agent最优结果为94.16%。滑行验证集目前只有第5次一个样本，
其结果可以用于阶段验收，但统计稳定性仍有限，后续应补采N挡验证数据和独立D挡回收数据。

本目录是根据《研究方向、目标.md》落地的第一版可运行实现，当前覆盖纵向动力学：

- BLF + DBC 解码为统一 CSV；
- 滑行、0-100 km/h、60-100 km/h 三类实车数据自动识别；
- 车速曲线 R²、NRMSE、峰值纵向加速度、达到目标车速时间等指标；
- 加速权重 0.5833、滑行权重 0.4167 的纵向总分；
- 正式验收阈值固定为纵向动力学输出数据精度 ≥80%；
- 可将 Carsim 输出 CSV 作为 `--sim-root` 输入，完成仿真与实车的同口径对比。

## 运行环境

建议使用 Python 3.10+。项目复用上级目录 `_tools/blf_parser` 中已提供的 `can`、`cantools`，不需要重新下载依赖。

## 运行方式

### 图形交互界面（推荐）

在本目录执行：

```powershell
python "Agent交互界面\server.py" --port 8765
```

浏览器访问 `http://127.0.0.1:8765`，即可使用优化看板、参数空间、迭代历史、
干运行和真实 API 优化等功能。详细说明见 `Agent交互界面/README.md`。

需要使用真实 LLM 优化时，请手动填写：

`Agent交互界面/config/llm_api.local.json`

需要填写 `api_key`、`base_url` 和 `model`。密钥仅由本机后端在启动真实任务时读取，
不会返回给前端，也不会被提交到 Git。未填写时仍可使用看板和干运行功能。

真实 API 优化按钮会启动多轮闭环：每轮按“LLM 生成候选 → 参数安全校验 → 依次评价C1/C2/C3等全部合格候选 → 选出本轮最优 → 读取最新最优状态”执行。
默认最多12轮，连续3轮没有提升时自动停止；每轮输出按 `ui_时间_iter_编号_llm_proposal` 和
`ui_时间_iter_编号_carsim_eval` 分类保存。

当前Agent已加入多候选经验记忆：每轮额外生成`ui_时间_iter_编号_round_memory_carsim_eval`，
保存全部候选的参数变化、标定/验证/全量指标改善与退化、保护线失败原因和本轮胜者。
下一轮按`C1=沿最优点利用、C2=融合已验证非胜者收益、C3=避开失败方向探索`生成候选。
若整轮全部回退，失败经验仍会进入下一轮提示词；任何融合结果都必须重新通过CarSim。

项目运行配置现在统一为`config.json`。请直接修改该文件中的`metric_thresholds`、
`formal_acceptance_threshold_pct`、`longitudinal_weights`或`agent`区，前端看板、候选评价器、
LLM提示词和多轮循环会在下次启动/任务时读取新值。

### 命令行方式

在本目录执行：

```powershell
python run_project.py --decode
python run_project.py --evaluate --sim-root "F:\\path\\to\\carsim_csv"
python prepare_coast_condition.py
python run_parameter_sensitivity.py --tstop 90
```

首次运行建议先只执行 `--decode`。单位修正版解码结果位于 `输出/解码CSV_单位修正`，评价结果位于 `输出/评价报告`。

`prepare_coast_condition.py` 会从 6 份真实滑行数据中提取 50->30 km/h 的时间边界，输出到 `输出/滑行工况/coast_condition_manifest.json`，供 Carsim 工况生成使用。

`run_parameter_sensitivity.py` 会在 `输出/参数敏感性` 下建立独立迭代目录，对 `M_SU`、`H_CG_SU`、`IYY_SU` 做正负单步 Trace 扫描。每个试验目录都保存实际 `Run_all.par`、Carsim 原始结果、统一 CSV、指标 JSON 和 README。该扫描用于定位敏感参数，不会覆盖正式 `输出/评价报告`。

当前扫描结论：在允许单步范围内，`M_SU` 的影响最大，但平均 0-100 精度仍仅约 19.50%–19.82%；`H_CG_SU` 和 `IYY_SU` 几乎不改变结果。已将确认生效的电机表缩放参数 `MOTOR_TORQUE_SCALE` 接入参数更新闭环；下一阶段需继续定位电驱功率管理/电池约束，不能无限放大扭矩表。

## 输入约定

Carsim CSV 每个工况一个文件，文件名包含 `condition_01`、`condition_02`、`coast` 或 `滑行` 即可被自动识别。至少应包含 `time_s`、`vxdot`、`ax` 三列；实车解码 CSV 会同时保留油门、制动、方向盘和四轮速度等驾驶员输入信号。
