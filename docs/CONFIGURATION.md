# 本机配置

## 运行路径

公开模板：`项目实现/config/runtime.example.json`

本机文件：`项目实现/config/runtime.local.json`

主要字段：

| 字段 | 作用 |
|---|---|
| `carsim_root` | CarSim 安装根目录 |
| `runtime_root` | CarSim 临时求解目录，必须是纯英文绝对路径 |
| `data_root` | 用户 DBC/BLF 目录 |
| `output_root` | Agent 报告和历史输出目录 |
| `converter_path` | 仓库内 VS/VSB 转换器 |
| `model_template_path` | 用户车辆 `Run_all.par` |
| `formal_result_path` | 本车正式基线或公开演示基线 |
| `install_root` | Python 虚拟环境安装根目录 |

路径可以是绝对路径，也可以相对 `项目实现`。本机配置被 Git 忽略。

## LLM API

将 `llm_api.example.json` 复制为 `llm_api.local.json`，填写：

```json
{
  "api_key": "你的密钥",
  "base_url": "https://provider.example/v1",
  "model": "模型名称",
  "timeout_s": 120
}
```

要求接口兼容 OpenAI Chat Completions 风格请求。`base_url` 填写到 `/v1`，不要追加 `/chat/completions`。
密钥只由本机后端读取，不返回浏览器，也不写入优化日志。

## 临时使用另一份配置

开发和验收可以设置：

```powershell
$env:VEHICLE_AGENT_RUNTIME_CONFIG="D:\path\runtime.test.json"
```

这样无需修改默认本机配置即可模拟另一台电脑。
