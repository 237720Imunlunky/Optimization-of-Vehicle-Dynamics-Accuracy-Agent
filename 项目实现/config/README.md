# 本机运行配置

- `runtime.example.json`：公开路径配置示例，不包含密钥。
- `runtime.local.json`：本机实际路径。首次部署时由安装脚本根据F盘目录生成，该文件不应提交到版本库。

未创建本机配置时，程序自动发现常见 CarSim 位置，优先使用F盘纯英文Runtime；没有F盘时使用本机应用数据目录。
公开仓库的转换器位于`tools/`，演示基线位于`demo_assets/`，真实数据和车辆模板放在`local_assets/`。
