# PDFTwocast 技术需求文档 (TRD)

> 版本：v2.1 | 日期：2026-06-10 | 对应产品版本：v2.1

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
| **数据库** | SQLite 3 | 用户认证与配额管理 (db.py) |
| **配置管理** | python-dotenv | 从 `.env` 文件加载环境变量 |
| **前端** | 原生 HTML/CSS/JS | 零框架，零构建 |
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
                  │ HTTP (GET /api/audio/{id})
                  │ HTTP (POST /api/auth/*)
                  │ HTTP (GET/POST /api/admin/*)
                  ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI (main.py)                      │
│                                                          │
│  ┌──────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ 路由层   │  │   配置解析     │  │   静态文件服务    │  │
│  │ /api/*   │  │  get_config() │  │  /static/*       │  │
│  │ /api/auth │  │  dotenv       │  │  /               │  │
│  │ /api/admin│  │               │  │                   │  │
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
│  │  auth_*()               用户认证逻辑               │   │
│  │  admin_*()              管理员功能                 │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │             数据持久化层 (db.py)                  │   │
│  │  ┌──────────┐    ┌──────────────┐             │   │
│  │  │  users    │    │ upgrade_log  │             │   │
│  │  │ (用户表)  │    │ (升级记录表) │             │   │
│  │  └──────────┘    └──────────────┘             │   │
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
├── main.py              # FastAPI 应用主文件
├── db.py                # SQLite 数据库层 (用户/配额管理)
├── protocols.py         # 火山引擎 WebSocket 二进制协议
├── requirements.txt     # Python 依赖
├── .env.example        # 环境变量模板
├── .env                # 环境变量配置 (不提交到 Git)
├── static/
│   └── index.html       # 前端 SPA
├── outputs/             # 生成音频输出目录
│   └── podcast_{id}.mp3
└── docs/
    ├── PRD.md           # 产品需求文档
    └── TRD.md          # 技术需求文档 (本文件)
```

---

## 3. API 设计

### 3.1 接口总览

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| `GET` | `/` | ❌ | 返回前端页面 `static/index.html` |
| `GET` | `/health` | ❌ | 健康检查 + 引擎配置状态 |
| `POST` | `/api/generate` | ❌* | 核心接口：上传 PDF 生成播客 |
| `GET` | `/api/audio/{job_id}` | ❌ | 下载/播放生成的 MP3 音频 |
| `POST` | `/api/auth/register` | ❌ | 用户注册 |
| `POST` | `/api/auth/login` | ❌ | 用户登录 |
| `GET` | `/api/auth/me` | ✅ | 获取当前登录用户信息 |
| `POST` | `/api/auth/logout` | ✅ | 退出登录 |
| `GET` | `/api/admin/users` | 🔑 | 管理员：查看所有用户 |
| `POST` | `/api/admin/upgrade` | 🔑 | 管理员：升级用户为付费会员 |
| `GET` | `/api/admin/config` | 🔑 | 管理员：获取当前配置 (脱敏) |
| `POST` | `/api/admin/config` | 🔑 | 管理员：更新配置 |

`* /api/generate`：未登录用户可使用脚本生成，但播客生成需要登录且有配额。

`🔑`：需要管理员 Token（Bearer Token 认证）

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
| `Authorization` | Bearer Token（会员功能需要） |

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
  "engine": "podcast",
  "membership": {
    "type": "paid",
    "quota": 9,
    "can_generate_podcast": true
  }
}
```

**错误响应**：

| 状态码 | 场景 |
|--------|------|
| 400 | PDF 为空 / 解析失败 / 内容不足 50 字 |
| 401 | 需要登录（播客生成） |
| 403 | 配额不足或会员等级不够 |
| 500 | LLM 调用失败 / 脚本 JSON 解析失败 |

### 3.3 认证 API

#### POST /api/auth/register

```json
// 请求
{"username": "testuser", "password": "pass1234"}

// 响应
{"ok": true, "user_id": 1, "membership_type": "regular"}
```

#### POST /api/auth/login

```json
// 请求
{"username": "admin", "password": "admin918"}

// 响应
{"ok": true, "token": "abc123...", "membership_type": "admin"}
```

#### GET /api/auth/me

```
// 请求头
Authorization: Bearer abc123...

// 响应
{
  "ok": true,
  "user": {
    "id": 1,
    "username": "admin",
    "membership_type": "admin",
    "podcast_quota": 999999,
    "total_upgrades": 0
  }
}
```

### 3.4 管理员 API

#### GET /api/admin/users

```json
// 响应
{
  "ok": true,
  "users": [
    {"id": 1, "username": "admin", "membership_type": "admin", "podcast_quota": 999999},
    {"id": 2, "username": "testuser", "membership_type": "paid", "podcast_quota": 9}
  ]
}
```

#### POST /api/admin/upgrade

```json
// 请求
{"user_id": 2}

// 响应
{"ok": true, "new_quota": 10, "membership_type": "paid"}
```

#### GET /api/admin/config

```json
// 响应
{
  "fields": [
    {"key": "HARDCODED_DEEPSEEK_KEY", "label": "DeepSeek API Key", "value": "sk-c4e...***...48f", "is_set": true},
    {"key": "HARDCODED_VOLC_APP_ID", "label": "火山引擎 App ID", "value": "7952...***...922", "is_set": true}
  ]
}
```

#### POST /api/admin/config

```json
// 请求
{"fields": {"HARDCODED_DEEPSEEK_KEY": "sk-new-key-xxx"}}

// 响应
{"ok": true, "changed": ["HARDCODED_DEEPSEEK_KEY"], "restart_required": false}
```

### 3.5 GET /health

```json
{
  "status": "ok",
  "deepseek_configured": true,
  "minimax_configured": true,
  "podcast_configured": true,
  "default_engine": "podcast",
  "version": "2.1"
}
```

---

## 4. 核心模块设计

### 4.1 配置解析 (`get_config` + `load_dotenv`)

**优先级链**：`python-dotenv` 加载 `.env` → 环境变量 → 默认值

```python
# main.py 开头
from dotenv import load_dotenv
load_dotenv()  # 从 .env 文件加载环境变量

# 配置读取
DEFAULT_DEEPSEEK_KEY = os.getenv("HARDCODED_DEEPSEEK_KEY", "")
DEFAULT_VOLC_ACCESS_KEY = os.getenv("HARDCODED_VOLC_ACCESS_KEY", "")
```

**`.env` 文件格式**：

```bash
# DeepSeek
HARDCODED_DEEPSEEK_KEY=sk-xxxxxxxxxxxxxxxx

# 火山引擎播客大模型
HARDCODED_VOLC_APP_ID=7952479922
HARDCODED_VOLC_ACCESS_KEY=9CoQO-pdxubfE-LMjY76ahHgCrU5VHx-
HARDCODED_VOLC_APP_KEY=0jfAPJ1FomHJK2-xdpD4Kw8MCnMkitOg

# MiniMax (降级)
HARDCODED_MINIMAX_KEY=your-minimax-key
HARDCODED_MINIMAX_GROUP=your-group-id
```

### 4.2 数据库设计 (`db.py`)

**依赖**：Python 内置 `sqlite3` 模块（无需额外安装）

**表结构**：

```sql
-- 用户表
CREATE TABLE users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT UNIQUE NOT NULL,
    password_hash     TEXT NOT NULL,
    membership_type   TEXT DEFAULT 'regular',  -- regular / paid / admin
    podcast_quota     INTEGER DEFAULT 0,
    total_upgrades    INTEGER DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- 升级记录表
CREATE TABLE upgrade_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    upgraded_by       INTEGER NOT NULL,
    upgraded_at       TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
```

**关键函数**：

| 函数 | 说明 |
|------|------|
| `create_user(username, password_hash)` | 注册新用户 |
| `get_user_by_username(username)` | 查找用户 |
| `update_user_membership(user_id, type, quota)` | 更新会员类型 |
| `consume_quota(user_id)` | 消耗 1 次播客配额 |
| `get_all_users()` | 管理员：获取所有用户 |
| `log_upgrade(user_id, admin_id)` | 记录升级操作 |

### 4.3 认证机制

**Token 存储**：内存字典 `active_sessions`（非持久化，重启后清空）

```python
active_sessions = {}  # token -> {"user_id": ..., "expire_at": ...}
```

**Token 生成**：

```python
import hashlib, time, secrets

def generate_token(user_id: int) -> str:
    raw = f"{user_id}_{time.time()}_{secrets.token_hex(16)}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

**Token 验证**：

```python
def _get_session(authorization: str | None) -> dict | None:
    if not authorization:
        return None
    token = authorization.replace("Bearer ", "")
    sess = active_sessions.get(token)
    if not sess:
        return None
    if sess["expire_at"] < time.time():
        del active_sessions[token]
        return None
    return sess
```

**⚠️ 安全提示**：当前为演示级实现，生产环境应使用 JWT + Redis。

### 4.4 PDF 文本提取 (`extract_pdf_text`)

```python
def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 8000) -> str:
    """
    1. fitz.open(stream=pdf_bytes) 从内存打开
    2. 逐页 get_text() 提取
    3. 超过 max_chars 截断并附加提示
    """
```

### 4.5 播客脚本生成 (`generate_podcast_script`)

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

### 4.6 播客大模型端到端生成 (`generate_via_podcast_model`)

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

### 4.7 MiniMax TTS (`tts_minimax`)

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

### 4.8 管理员配置管理 (`/api/admin/config`)

**读取配置** (`GET /api/admin/config`)：

1. 验证 Token 和管理员权限
2. 读取 `.env` 文件（如果存在）
3. 对每个配置项进行脱敏处理（`_mask_value()`）
4. 返回字段列表（含标签、占位符、是否已配置）

**脱敏算法**：

```python
def _mask_value(val: str, keep: int = 6) -> str:
    if not val:
        return ""
    if len(val) <= keep * 2:
        return "*" * len(val)
    return val[:keep] + "***" + val[-keep:]
```

**更新配置** (`POST /api/admin/config`)：

1. 验证 Token 和管理员权限
2. 校验请求体（`fields` 必须是 dict）
3. 白名单校验（只允许更新指定的 6 个键）
4. 读取现有 `.env` 文件（保留注释行）
5. 更新对应键值
6. 写回 `.env` 文件
7. 同时更新当前进程的 `os.environ`（立即生效，无需重启）

---

## 5. 数据流

### 5.1 播客大模型模式（主流程）

```
┌──────────┐   PDF    ┌──────────┐   text   ┌──────────────┐
│  Browser │ ───────→│ FastAPI  │────────→│ extract_pdf  │
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

### 5.3 会员配额消耗流程

```
用户点击「生成播客」
  ↓
GET /api/auth/me (获取会员状态)
  ↓
检查 membership_type 和 podcast_quota
  ↓
[普通会员] → 返回 403 "请先升级为付费会员"
  ↓
[付费会员 + 配额>0] → 继续生成
  ↓
生成成功 → POST /api/auth/consume_quota
  ↓
quota -= 1
  ↓
[quota == 0] → 自动降级为普通会员
```

---

## 6. 部署方案

### 6.1 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入实际密钥

# 初始化数据库
python -c "from db import init_db; init_db()"

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
   ↓
SQLite (users.db)
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

| 变量 | 说明 | 默认值 | 必填 |
|------|------|-------|------|
| `HARDCODED_DEEPSEEK_KEY` | DeepSeek API Key | - | ✅ |
| `HARDCODED_VOLC_APP_ID` | 火山引擎应用 ID | - | ❌* |
| `HARDCODED_VOLC_ACCESS_KEY` | 火山引擎 Access Token | - | ❌* |
| `HARDCODED_VOLC_APP_KEY` | 火山引擎 App Key | - | ❌* |
| `HARDCODED_MINIMAX_KEY` | MiniMax API Key | - | ❌** |
| `HARDCODED_MINIMAX_GROUP` | MiniMax Group ID | - | ❌** |

`*` 使用播客大模型时需要  
`**` 使用 MiniMax 降级时需要

---

## 7. 安全策略

### 7.1 API 密钥管理

| 级别 | 优先级 | 说明 |
|------|-------|------|
| `.env` 文件 | 1 (最高) | 通过 `python-dotenv` 加载，不提交到 Git |
| 请求头注入 | 2 | 前端用户可自定义 DeepSeek Key |
| 环境变量 | 3 | 服务器环境变量 `os.getenv()` |
| 硬编码 | ❌ | **已移除**，不再使用 |

### 7.2 认证安全

- **密码存储**：`hashlib.sha256(password.encode()).hexdigest()`
  - ⚠️ **非生产级**：建议使用 `bcrypt` 或 `argon2`
- **Token 存储**：内存字典，重启后清空
  - ⚠️ **非持久化**：建议使用 Redis
- **Token 过期**：24 小时自动过期
- **会话管理**：`active_sessions` 字典，定期清理过期 Token

### 7.3 管理员配置安全

- **访问控制**：仅管理员 Token 可访问 `/api/admin/*`
- **脱敏显示**：已配置的密钥只显示前 6 位 + `***` + 后 6 位
- **白名单校验**：只允许更新预定义的 6 个配置键
- **热更新**：保存后即刻更新 `os.environ`，无需重启

### 7.4 注意事项

- `.env` 文件已在 `.gitignore` 中，不会被提交到 GitHub
- CORS 当前为 `*`，生产环境需限制为实际域名
- 上传文件无病毒扫描，建议增加文件大小限制（当前前端限制 20MB）
- SQLite 适合低并发场景，高并发建议使用 PostgreSQL

---

## 8. 关键依赖

| 包名 | 版本要求 | 用途 | 是否新增 |
|------|---------|------|---------|
| `fastapi` | ≥ 0.110.0 | Web 框架 | - |
| `uvicorn` | ≥ 0.29.0 | ASGI 服务器 | - |
| `httpx` | ≥ 0.27.0 | 异步 HTTP 客户端 | - |
| `PyMuPDF` | ≥ 1.24.0 | PDF 文本提取 | - |
| `websockets` | ≥ 12.0 | WebSocket 客户端 | - |
| `python-multipart` | ≥ 0.0.9 | 文件上传解析 | - |
| `python-dotenv` | ≥ 1.0.0 | 从 `.env` 加载环境变量 | 🆕 |
| `sqlite3` | 内置 | 数据库（无需安装） | 🆕 |

---

## 9. 已知问题 & 技术债务

| 问题 | 影响 | 优先级 | 状态 |
|------|------|-------|------|
| 密码使用 SHA256 而非 bcrypt | 安全性不足 | 🔴 高 | 📋 待修复 |
| Token 存储在内存，重启丢失 | 用户体验下降 | 🟡 中 | 📋 待修复 |
| CORS `*` | 安全风险 | 🔴 高 | 📋 待修复 |
| 无文件病毒扫描 | 安全风险 | 🟡 中 | 📋 规划中 |
| 无并发控制 | 多用户同时生成可能超时 | 🟡 中 | 📋 规划中 |
| MP3 合并为字节拼接 | 可能有兼容性问题 | 🟢 低 | ✅ 可接受 |
| 前端无构建工具 | 不便维护扩展 | 🟢 低 | 📋 规划中 |
| 无历史记录/日志 | 无法追溯 | 🟢 低 | 📋 规划中 |
| SQLite 高并发性能 | 多用户场景受限 | 🟡 中 | 📋 规划中 |

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
| 配置保存生效 | ≤ 1s | 无需重启 |

---

## 11. 前端架构

### 11.1 页面结构

```
static/index.html
├── <style> ... </style>        # 所有 CSS（内联）
├── <div class="container">      # 主容器
│   ├── 标题区域
│   ├── 上传区域（拖拽 + 点击）
│   ├── 文件信息显示
│   ├── 生成按钮
│   ├── 进度显示（4 步骤）
│   ├── 脚本展示
│   ├── 音频播放器
│   └── 会员状态栏
├── <div id="loginOverlay">     # 登录弹窗
├── <div id="adminOverlay">     # 管理面板弹窗
├── <div id="configOverlay">    # 系统配置弹窗 🆕
└── <script> ... </script>      # 所有 JS（内联）
```

### 11.2 关键 JS 函数

| 函数 | 说明 |
|------|------|
| `handleFile(file)` | 处理文件选择/拖拽 |
| `generateBtn.addEventListener()` | 生成按钮点击事件 |
| `api(path, opts)` | 封装 fetch，自动附加 Token |
| `refreshUser()` | 刷新用户状态（登录/会员信息） |
| `showAdmin()` / `hideAdmin()` | 管理员面板显示/隐藏 |
| `showConfig()` / `hideConfig()` | 系统配置弹窗显示/隐藏 🆕 |
| `saveConfig()` | 保存系统配置 🆕 |
| `showToast(msg)` | 显示提示消息 |
| `setStep(n, state, desc)` | 更新进度步骤显示 |

### 11.3 管理员功能入口

```
管理员登录成功
  ↓
显示「管理面板」按钮 (id="btnAdmin")
显示「系统配置」按钮 (id="btnConfig")  🆕
  ↓
点击「管理面板」→ 显示 adminOverlay
  - 查看所有用户
  - 升级普通会员为付费会员
  ↓
点击「系统配置」→ 显示 configOverlay  🆕
  - 显示 6 个配置项（脱敏）
  - 可填入新值
  - 可勾选「显示」查看明文
  - 保存后即刻生效
```
