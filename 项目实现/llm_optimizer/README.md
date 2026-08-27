# LLM参数优化Agent

本模块是CarSim纵向动力学参数优化的安全控制层。LLM只负责分析偏差和提出候选，程序负责参数边界检查、CarSim求解、正式评价、接受或回退。

## 当前阶段

当前完成候选协议、参数注册表、锁定参数、单轮变化限制、标定/验证数据划分、配置驱动保护线、API客户端和状态回退机制。

`candidate_executor.py`已接入候选模型生成和批量求解。执行器将运行12条加速Trace和1条共享滑行仿真，形成18组实车比较，并按标定集、独立验证集和全部数据三层判定接受或回退。

执行器现在只读取最新数据准入清单中的合格样本，不再依赖固定文件数。运行真实候选前必须先在项目根目录执行
`python data_admission.py run`，并对缺少挡位、驾驶模式或路面证据的记录完成人工复核；不合格和待复核样本不会进入标定或验证。

## 文件作用

- `config/parameter_registry.json`：可调参数、物理边界、单步上限、锁定参数和机器可执行CarSim绑定。
- `../config.json`：项目唯一运行配置，集中管理评价阈值、迭代次数、候选数量、LLM响应重试、数据划分和判停条件。
- `schemas/proposal.schema.json`：LLM响应JSON协议。
- `parameter_space.py`：候选安全校验。
- `objective.py`：目标压缩、保护线和候选接受逻辑。
- `prompt_builder.py`：构造LLM输入。
- `llm_client.py`：OpenAI兼容API客户端，密钥只从环境变量读取；兼容纯JSON、Markdown代码块和分段content，并对HTTP临时错误或正文格式错误自动重试。
- `state_store.py`：最优基线保留与迭代状态。
- `experience_memory.py`：验证经验去重、相关性裁剪、提示词预算和连续失败降级。
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

首次运行可以不传`--state`，Agent会从当前正式基线自动创建初始状态。已有CarSim评价结果后，后续迭代通过`--state`读取上一轮最优点，例如（路径以当前输出目录中的实际状态文件为准）：

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

真实API任务会在本轮候选目录保存：

- `llm_request_messages.json`：本轮发送的提示词，不含API密钥；
- `llm_response_attempts.json`：每次响应是否成功解析、结束原因、正文长度以及失败时最多500字预览，不保存推理全文；
- `llm_response.json`：最终成功解析并进入安全校验的候选JSON。

`config.json.agent.llm_response_max_attempts`控制HTTP与格式异常的最大尝试次数，
`config.json.agent.llm_max_output_tokens`控制候选响应长度上限。修改这两项不会改变车辆评价口径或历史最优分数。
`config.json.agent.llm_thinking_mode`用于控制支持该参数的模型思考模式；当前DeepSeek V4配置为
`disabled`，使模型直接返回候选JSON，避免思考正文耗尽输出长度。
发送给模型的经验采用“最近轮次索引 + 可融合候选摘要 + 回退方向摘要”，只保留参数变化、
标定/验证主要得失和保护线原因，避免同一候选的完整指标在提示词中重复展开。

同一次完整优化会继承本任务前序轮次的压缩经验。跨任务默认仅继承经 CarSim 验证且策略指纹一致的经验；
界面选择“全新优化”时从正式基线和空经验启动，历史文件仍然保留。未经仿真验证的模型判断不会进入可靠经验，
完整指标证据只保存在任务档案，不进入提示词。连续两轮由历史经验引导的候选全部回退时，任务会降级为近期经验或冷启动探索。

工况来源统一注册在 `../conditions/condition_registry.json`。当前只启用 0-100、60-100 和滑行三类纵向工况；
横向优化目前只有扩展框架，尚未配置真实工况、指标或 CarSim 模板。
