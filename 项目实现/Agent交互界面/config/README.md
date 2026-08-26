# API配置文件

手动编辑`llm_api.local.json`，填写OpenAI兼容接口信息：

```json
{
  "api_key": "填写API密钥",
  "base_url": "填写接口根地址，例如https://api.example.com/v1",
  "model": "填写模型名称",
  "timeout_s": 120
}
```

注意事项：

- `base_url`填写到`/v1`这一层，不要附加`/chat/completions`；
- 密钥只保存在本机该文件中；
- 不要把此文件发送给其他人或提交到版本库；
- 修改后在Agent界面点击“重新检查”。

