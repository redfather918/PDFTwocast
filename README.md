# PDFTwocast

将 PDF 文档一键转换为双人播客音频。

## 功能

- **PDF 解析** — 自动提取文本内容
- **播客大模型** — 火山引擎豆包播客大模型端到端生成（输入文本 → 双人对话 + 音频，约 20 秒）
- **降级链路** — 播客大模型不可用时自动降级到 DeepSeek 脚本 + MiniMax TTS
- **双人对话** — 自动生成 Alex（男声）+ 小米（女声）自然对话
- **片头片尾音乐** — 播客大模型模式自带背景音乐

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制 .env.example 为 .env 并填入密钥）
cp .env.example .env

# 启动
python main.py
# 访问 http://localhost:7861
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `HARDCODED_VOLC_APP_ID` | 火山引擎 App ID |
| `HARDCODED_VOLC_ACCESS_KEY` | 火山引擎 Access Token |
| `HARDCODED_VOLC_APP_KEY` | 火山引擎 App Key |
| `HARDCODED_DEEPSEEK_KEY` | DeepSeek API Key（降级用） |
| `HARDCODED_MINIMAX_KEY` | MiniMax API Key（降级用） |
| `HARDCODED_MINIMAX_GROUP` | MiniMax Group ID（降级用） |

## 技术栈

- FastAPI
- PyMuPDF（PDF 解析）
- 火山引擎豆包播客大模型（WebSocket 二进制协议）
- DeepSeek + MiniMax TTS（降级链路）

## 文档

- [产品需求文档 (PRD)](docs/PRD.md)
- [技术需求文档 (TRD)](docs/TRD.md)
