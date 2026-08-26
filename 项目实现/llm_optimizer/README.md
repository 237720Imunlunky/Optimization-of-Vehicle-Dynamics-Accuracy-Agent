# LLM参数优化Agent

本模块是CarSim纵向动力学参数优化的安全控制层。LLM只负责分析偏差和提出候选，程序负责参数边界检查、CarSim求解、正式评价、接受或回退。

## 当前阶段

当前完成候选协议、参数注册表、锁定参数、单轮变化限制、标定/验证数据划分、配置驱动保护线、API客户端和状态回退机制。

`candidate_executor.py`已接入候选模型生成和批量求解。执行器将运行12条加速Trace和1条共享滑行仿真，形成18组实车比较，并按标定集、独立验证集和全部数据三层判定接受或回退。

## 文件作用

- `config/parameter_registry.json`：可调参数、物理边界、单步上限、锁定参数和机器可执行CarSim绑定。
- `../config.json`：项目唯一运行配置，集中管理评价阈值、迭代次数、候选数量、数据划分和判停条件。
- `schemas/proposal.schema.json`：LLM响应JSON协议。
- `parameter_space.py`：候选安全校验。
- `objective.py`：目标压缩、保护线和候选接受逻辑。
- `prompt_builder.py`：构造LLM输入。
- `llm_client.py`：OpenAI兼容API客户端，密钥只从环境变量读取。
- `state_store.py`：最优基线保留与迭代状态。
- `run_agent.py`：候选生成阶段入口。
- `model_patcher.py`：把合规参数写入独立CarSim候选模型。
- `candidate_executor.py`：执行13次CarSim求解并完成接受或回退。
- `test_agent_foundation.py`：安全机制测试。

## 干运行

在`项目实现`目录执行：

```powershell
python -m llm_optimizer.run_agent --dry-run
python -m pytest -q llm_optimizer/test_agent_foundation.py
python -m llm_optimizer.candidate_executor
```

后续迭代必须通过`--state`读取上一轮最优点，例如（路径以当前输出目录中的实际状态文件为准）：

```powershell
python -m llm_optimizer.run_agent --use-api `
  --state "输出/LLM参数优化Agent/ui_时间戳_iter_01_C1_carsim_eval/agent_state.json" `
  --output "输出/LLM参数优化Agent/manual_api_run"
```

## API配置

真实调用前设置以下环境变量，程序不会把密钥写入任何输出文件：

```powershell
$env:CARSIM_LLM_API_KEY="你的API密钥"
$env:CARSIM_LLM_BASE_URL="OpenAI兼容接口地址，例如https://api.example.com/v1"
$env:CARSIM_LLM_MODEL="模型名称"
python -m llm_optimizer.run_agent --use-api --output "输出/LLM参数优化Agent/manual_api_run"
```

所有生成结果统一保存到`项目实现/输出/LLM参数优化Agent/`下的独立迭代目录，脚本拒绝覆盖历史输出。
