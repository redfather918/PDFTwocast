# PDFTwocast 技术需求文档 (TRD)

> 版本：v1.0 | 日期：2026-06-10 | 对应产品版本：v2.0

---

## 1. 技术栈总览

| 层级 | 技术选型 | 版本 / 说明 |
|------|---------|------------|
| **后端框架** | FastAPI | Python 异步 Web 框架 |
| **ASGI Server** | Uvicorn | 生产级 ASGI 服务器 |
| **PDF 解析** | PyMuPDF (fitz) | C 扩展，高性能 PDF 文本提取 |
| **AI 脚本** | DeepSeek Chat API | DeepSeek-V3 / deepseek-chat |
| **播客大模型** | 火山引擎 SAMI Podcast TTS | WebSocket 二进制协议 |
| **降级 TTS** | MiniMax TTS (speech-02-hd) | REST API, MP3 输出 |
| **WebSocket** | websockets 库 | 播客大模型二进制协议通信 |
| **HTTP 客户端** | httpx | 异步 HTTP 请求 (DeepSeek, MiniMax) |
| **前端** | 原生 HTML/CSS/JS | 零框架，零构建，663 行 |
| **运行环境** | Python 3.13.12 (managed) | 隔离环境，不污染系统 |

---

## 2. 系统架构

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────┐
│                      Client Browser                       │
│                   (static/index.html)                     │
└─────────────────┬────────────────────────────────────────┘
                  │ HTTP (POST /api/generate)
                  │ HTTP (GET  /api/audio/{id})
                  │ HTTP (GET  /health)
                  ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI (main.py)                      │
│                                                          │
│  ┌──────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ 路由层   │  │   配置解析     │  │   静态文件服务    │  │
│  │ /api/*   │  │ get_config()  │  │ /static/*        │  │
│  └────┬─────┘  └───────────────┘  └──────────────────┘  │
│       │                                                  │
│  ┌────▼─────────────────────────────────────────────┐   │
│  │                  业务逻辑层                        │   │
│  │                                                  │   │
│  │  extract_pdf_text()     PDF 文本提取              │   │
│  │  generate_podcast_script()  DeepSeek 脚本生成     │   │
│  │  generate_via_podcast_model() 播客大模型端到端    │   │
│  │  tts_minimax()          MiniMax 单句合成          │   │
│  │  merge_mp3_files()      MP3 字节拼接             │   │
│  └──────────────────────────────────────────────────┘   │
└──────────┬───────────────┬───────────────┬──────────────┘
           │               │               │
           ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
│  PyMuPDF     │ │  DeepSeek    │ │  火山引擎播客      │
│  (本地)      │ │  Chat API    │ │  大模型 (WSS)     │
│  PDF → Text  │ │  REST HTTPS  │ │  WebSocket Binary  │
└──────────────┘ └──────────────┘ └──────────────────┘
                          │
                          ▼
                 ┌──────────────┐
                 │  MiniMax TTS │
                 │  REST HTTPS  │
                 └──────────────┘
```

### 2.2 目录结构

```
pdftwocast/
├── main.py              # FastAPI 应用主文件 (524 行)
├── protocols.py         # 火山引擎 WebSocket 二进制协议 (274 行)
├── requirements.txt     # Python 依赖
├── static/
│   └── index.html       # 前端 SPA (663 行)
├── outputs/             # 生成音频输出目录
│   └── podcast_{id}.mp3 # 生成的播客音频
└── docs/
    ├── PRD.md           # 产品需求文档
    └── TRD.md           # 技术需求文档 (本文件)
```

---

## 3. API 设计

### 3.1 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 返回前端页面 `static/index.html` |
| `GET` | `/health` | 健康检查 + 引擎配置状态 |
| `POST` | `/api/generate` | **核心接口**：上传 PDF 生成播客 |
| `GET` | `/api/audio/{job_id}` | 下载/播放生成的 MP3 音频 |

### 3.2 POST /api/generate

**请求**：`multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pdf_file` | file | ✅ | PDF 文件 |
| `language` | string | ❌ | `zh`（默认）或 `en` |

**请求头**（可选覆盖默认配置）：

| Header | 说明 |
|--------|------|
| `X-DeepSeek-Key` | 自定义 DeepSeek API Key |
| `X-MiniMax-Key` | 自定义 MiniMax API Key |
| `X-MiniMax-Group` | 自定义 MiniMax Group ID |
| `X-TTS-Engine` | 强制指定引擎：`podcast` / `minimax` |

**成功响应** (200)：

```json
{
  "job_id": "podcast_a1b2c3d4",
  "script": [
    {"speaker": "Alex", "text": "欢迎收听今天的播客..."},
    {"speaker": "小米", "text": "是的，今天我们要聊..."}
  ],
  "audio_url": "/api/audio/podcast_a1b2c3d4",
  "text_preview": "PDF 前 300 字符预览...",
  "tts_available": true,
  "engine": "podcast"
}
```

**错误响应**：

| 状态码 | 场景 |
|--------|------|
| 400 | PDF 为空 / 解析失败 / 内容不足 50 字 / DeepSeek Key 未配置 |
| 500 | LLM 调用失败 / 脚本 JSON 解析失败 |

### 3.3 GET /api/audio/{job_id}

- **成功**：返回 `audio/mpeg` 二进制流 + `Content-Disposition: attachment`
- **失败**：404 `{"detail": "音频文件不存在"}`

### 3.4 GET /health

```json
{
  "status": "ok",
  "deepseek_configured": true,
  "minimax_configured": true,
  "podcast_configured": true,
  "default_engine": "podcast",
  "version": "2.0"
}
```

---

## 4. 核心模块设计

### 4.1 配置解析 (`get_config`)

**优先级链**：请求头 > 环境变量 > 硬编码

```python
def get_config(request: Request) -> dict:
    """
    配置取值逻辑：
    - DeepSeek Key: X-DeepSeek-Key > DEEPSEEK_API_KEY > _HARDCODED_DEEPSEEK_KEY
    - MiniMax: 强制使用硬编码（安全考虑）
    - TTS Engine: X-TTS-Engine > 播客大模型可用 > MiniMax
    """
```

### 4.2 PDF 文本提取 (`extract_pdf_text`)

```python
def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 8000) -> str:
    """
    1. fitz.open(stream=pdf_bytes) 从内存打开
    2. 逐页 get_text() 提取
    3. 超过 max_chars 截断并附加提示
    """
```

### 4.3 播客脚本生成 (`generate_podcast_script`)

```
输入：纯文本 + 语言标识
  ↓
DeepSeek Chat API (model: deepseek-chat)
  - system prompt: 播客脚本编写专家角色设定
  - user prompt: 待转换的文本内容
  - temperature: 0.7, max_tokens: 2000
  ↓
提取 JSON（处理 markdown 代码块包裹）
  ↓
json.loads → [{"speaker": "Alex", "text": "..."}, ...]
```

### 4.4 播客大模型端到端生成 (`generate_via_podcast_model`)

**协议**：WebSocket 二进制协议（见 `protocols.py`）

**流程**：

```
1. WebSocket 连接 ← wss://openspeech.bytedance.com/api/v3/sami/podcasttts
   Headers: X-Api-App-Id, X-Api-App-Key, X-Api-Access-Key, X-Api-Resource-Id
2. start_connection() → 建立连接
3. start_session(payload) → 提交生成请求
   - input_text: PDF 提取的全文
   - audio_config: mp3 / 24000 Hz
   - use_head_music / use_tail_music: True
4. finish_session() → 触发生成
5. 循环接收消息流：
   ├── PodcastRoundStart   → 解析对话文本 (speaker + text)
   ├── PodcastRoundResponse → 累积音频字节
   ├── UsageResponse       → Token 用量
   └── SessionFinished     → 完成
6. finish_connection() → 关闭连接
7. 保存 output_dir/{job_id}.mp3
```

**二进制消息格式** (protocols.py)：

```
Header (4-16 bytes):
  [Version(4b) | HeaderSize(4b)]  [MsgType(4b) | Flag(4b)]  [Serialization(4b) | Compression(4b)]  [padding...]

Body (variable):
  [EventType: int32]  [SessionID: length + utf8]  [Sequence: int32]  [Payload: length + data]
```

**事件类型映射**：

| EventType | 值 | 含义 |
|-----------|----|------|
| StartConnection | 1 | 客户端请求建立连接 |
| ConnectionStarted | 50 | 服务端确认连接 |
| StartSession | 100 | 提交播客生成请求 |
| SessionStarted | 150 | 会话创建成功 |
| FinishSession | 102 | 结束会话请求 |
| PodcastRoundStart | 360 | 一轮对话开始 (含 speaker + text) |
| PodcastRoundResponse | 361 | 该轮音频数据 |
| PodcastRoundEnd | 362 | 该轮结束 (含 duration) |
| PodcastEnd | 363 | 全部对话结束 |
| UsageResponse | 154 | Token 用量 |
| SessionFinished | 152 | 会话已完成 |
| SessionFailed | 153 | 会话失败 |

### 4.5 MiniMax TTS (`tts_minimax`)

```
POST https://api.minimax.chat/v1/t2a_v2?GroupId={group}
  Authorization: Bearer {key}
  Body: {
    model: "speech-02-hd",
    text: "...",
    voice_setting: { voice_id: "...", speed: 1.0, vol: 1.0, pitch: 0 },
    audio_setting: { sample_rate: 32000, bitrate: 128000, format: "mp3", channel: 1 }
  }
  ↓
Response: { data: { audio: "hex_string" } }
  ↓
bytes.fromhex(audio_hex) → mp3_bytes
```

---

## 5. 数据流

### 5.1 播客大模型模式（主流程）

```
┌──────────┐   PDF    ┌──────────┐   text   ┌──────────────┐
│  Browser │ ────────→│ FastAPI  │────────→│ extract_pdf  │
│          │          │ /generate│         │ _text()      │
└──────────┘          └────┬─────┘         └──────────────┘
                           │ text
                           ▼
                    ┌──────────────┐
                    │generate_via  │
                    │_podcast_model│
                    └──────┬───────┘
                           │ WebSocket Binary
                           ▼
              ┌───────────────────────┐
              │ 火山引擎播客大模型      │
              │ podcasttts (WSS)      │
              └───────────┬───────────┘
                          │ audio + script
                          ▼
┌──────────┐  JSON+URL  ┌──────────────┐
│  Browser │←───────────│  FastAPI     │
│ 播放+下载│            │  保存 MP3    │
└──────────┘            └──────────────┘
```

### 5.2 降级模式（DeepSeek + MiniMax）

```
┌──────────┐   PDF    ┌───────────┐   text   ┌──────────────┐
│  Browser │────────→│ FastAPI   │────────→│ extract_pdf  │
└──────────┘         └─────┬─────┘         │ _text()      │
                           │ text           └──────────────┘
                           ▼
              ┌──────────────────────┐
              │generate_podcast_script│
              │→ DeepSeek Chat API   │
              └──────────┬───────────┘
                         │ [{"speaker":"Alex","text":"..."},...]
                         ▼
              ┌──────────────────────┐
              │  for each segment:   │
              │  tts_minimax(text,   │
              │    speaker)          │
              │  → MiniMax TTS API   │
              └──────────┬───────────┘
                         │ [seg_000.mp3, seg_001.mp3, ...]
                         ▼
              ┌──────────────────────┐
              │  merge_mp3_files()   │
              │  字节拼接            │
              └──────────┬───────────┘
                         │ podcast_{id}.mp3
                         ▼
┌──────────┐  JSON+URL  ┌──────────────┐
│  Browser │←───────────│  FastAPI     │
└──────────┘            └──────────────┘
```

---

## 6. 部署方案

### 6.1 本地开发

```bash
# 安装依赖
pip install fastapi uvicorn httpx PyMuPDF websockets python-multipart

# 启动服务
python main.py
# 默认监听: http://0.0.0.0:7861
```

### 6.2 生产部署建议

```
Nginx (TLS termination, static cache)
   ↓ reverse proxy
Uvicorn (multiple workers)
   ↓ ASGI
FastAPI (main.py)
```

**Docker 方案**（推荐）：

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7861"]
```

### 6.3 环境变量

| 变量 | 说明 | 默认值 |
|------|------|-------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | 硬编码值 |
| `MINIMAX_API_KEY` | MiniMax API Key | 硬编码值 |
| `MINIMAX_GROUP_ID` | MiniMax Group ID | 硬编码值 |
| `VOLC_APP_ID` | 火山引擎应用 ID | 硬编码值 |
| `VOLC_ACCESS_KEY` | 火山引擎 Access Token | 硬编码值 |

---

## 7. 安全策略

### 7.1 API 密钥管理

| 级别 | 优先级 | 说明 |
|------|-------|------|
| 请求头注入 | 1 (最高) | 前端用户可自定义 DeepSeek Key |
| 环境变量 | 2 | 服务器环境变量，不暴露给前端 |
| 硬编码 | 3 (最低) | 仅开发/演示用，生产需移除 |

### 7.2 注意事项

- **当前硬编码了所有 API Key**，生产环境必须改为仅环境变量
- CORS 当前为 `*`，需限制为实际域名
- 上传文件无病毒扫描，建议增加文件大小限制
- 无用户认证，多用户场景需增加鉴权

---

## 8. 关键依赖

| 包名 | 版本要求 | 用途 |
|------|---------|------|
| `fastapi` | ≥ 0.100 | Web 框架 |
| `uvicorn` | ≥ 0.20 | ASGI 服务器 |
| `httpx` | ≥ 0.24 | 异步 HTTP 客户端 |
| `PyMuPDF` | ≥ 1.22 | PDF 文本提取 |
| `websockets` | ≥ 12.0 | WebSocket 客户端 |
| `python-multipart` | ≥ 0.0.5 | 文件上传解析 |

---

## 9. 已知问题 & 技术债务

| 问题 | 影响 | 优先级 |
|------|------|-------|
| API Key 硬编码 | 安全隐患 | 🔴 高 |
| CORS `*` | 安全风险 | 🔴 高 |
| 无文件大小限制 | 大 PDF 可能 OOM | 🟡 中 |
| 无并发控制 | 多用户同时生成可能超时 | 🟡 中 |
| MP3 合并为字节拼接 | 可能有兼容性问题 | 🟢 低 |
| 前端无构建工具 | 不便维护扩展 | 🟢 低 |
| 无历史记录/日志 | 无法追溯 | 🟢 低 |
| `import` 语句写在函数后 | 代码风格不统一 | 🟢 低 |

---

## 10. 性能基准

基于本地开发环境（Intel i7, 16GB RAM, 100Mbps 网络）：

| 操作 | 耗时 | 说明 |
|------|------|------|
| PDF 解析 8,000 字 | 0.5s | PyMuPDF C 扩展 |
| 播客大模型端到端 | 15-25s | WebSocket 全流程 |
| DeepSeek 脚本生成 | 5-15s | API 网络延迟为主 |
| MiniMax 单句 TTS | 2-5s | 取决于文本长度 |
| 8 句并发 MiniMax | 15-30s | 串行 + 0.3s 间隔 |
