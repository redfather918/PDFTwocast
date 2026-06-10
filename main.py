"""
PDFTwocast - 将 PDF 转换为双人播客
技术栈: FastAPI + PyMuPDF + DeepSeek LLM + MiniMax TTS / 火山引擎播客大模型
版本: v2.0 - 集成火山引擎播客大模型（Podcast TTS）
"""

import os
import json
import httpx
import asyncio
import uuid
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import websockets
from protocols import (
    start_connection, finish_connection,
    start_session, finish_session,
    receive_message,
    EventType, MsgType,
)
from db import (
    authenticate, create_user, get_user_by_id, get_all_users,
    upgrade_user, consume_quota, init_db,
)

app = FastAPI(title="PDFTwocast", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "X-DeepSeek-Key", "X-Minimax-Key", "X-Minimax-Group", "X-TTS-Engine"],
)

# 静态文件
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 输出目录
output_dir = Path(__file__).parent / "outputs"
output_dir.mkdir(exist_ok=True)

# ─── 会话管理（内存 Token）──────────────────────────────────
_sessions: dict[str, dict] = {}   # token → {user_id, username, membership_type, expires_at}
SESSION_TTL = 86400  # 24 小时


def _get_session(authorization: str | None) -> dict | None:
    """从 Bearer Token 获取会话，过期自动清除"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    sess = _sessions.get(token)
    if not sess:
        return None
    if time.time() > sess["expires_at"]:
        del _sessions[token]
        return None
    return sess


def _require_auth(authorization: str | None) -> dict:
    """强制鉴权：返回 session 或抛出 401"""
    sess = _get_session(authorization)
    if not sess:
        raise HTTPException(status_code=401, detail="请先登录")
    return sess


def _require_admin(authorization: str | None) -> dict:
    """强制 admin：返回 session 或抛出 403"""
    sess = _require_auth(authorization)
    if sess["membership_type"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    return sess


# ─── 认证 API ─────────────────────────────────────────────

@app.post("/api/auth/register")
async def auth_register(data: Request):
    body = await data.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if len(username) < 2 or len(username) > 32:
        raise HTTPException(status_code=400, detail="用户名长度 2-32 字符")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码至少 4 位")

    user = create_user(username, password)
    if not user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    token = uuid.uuid4().hex
    _sessions[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "membership_type": user["membership_type"],
        "podcast_quota": user["podcast_quota"],
        "expires_at": time.time() + SESSION_TTL,
    }
    return {"token": token, "user": user}


@app.post("/api/auth/login")
async def auth_login(data: Request):
    body = await data.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()

    user = authenticate(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = uuid.uuid4().hex
    _sessions[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "membership_type": user["membership_type"],
        "podcast_quota": user["podcast_quota"],
        "expires_at": time.time() + SESSION_TTL,
    }
    return {"token": token, "user": user}


@app.get("/api/auth/me")
async def auth_me(authorization: str | None = Header(None)):
    sess = _get_session(authorization)
    if not sess:
        return {"logged_in": False}

    # 刷新数据库状态
    user = get_user_by_id(sess["user_id"])
    if not user:
        return {"logged_in": False}

    # 同步 quota
    sess["membership_type"] = user["membership_type"]
    sess["podcast_quota"] = user["podcast_quota"]

    return {
        "logged_in": True,
        "user_id": user["id"],
        "username": user["username"],
        "membership_type": user["membership_type"],
        "podcast_quota": user["podcast_quota"],
        "total_upgrades": user["total_upgrades"],
    }


@app.post("/api/auth/logout")
async def auth_logout(authorization: str | None = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        _sessions.pop(token, None)
    return {"ok": True}


# ─── 管理员 API ───────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_users(authorization: str | None = Header(None)):
    _require_admin(authorization)
    users = get_all_users()
    return {"users": users}


@app.post("/api/admin/upgrade")
async def admin_upgrade(data: Request, authorization: str | None = Header(None)):
    admin_sess = _require_admin(authorization)
    body = await data.json()
    target_user_id = body.get("user_id")
    if not target_user_id:
        raise HTTPException(status_code=400, detail="缺少 user_id")

    updated = upgrade_user(admin_sess["user_id"], target_user_id)
    if not updated:
        raise HTTPException(status_code=400, detail="升级失败：用户不存在或已是付费/管理员")

    return {"ok": True, "user": updated}


# ─── 默认配置（从环境变量读取）──────────────────────────────
DEFAULT_DEEPSEEK_KEY    = os.getenv("DEEPSEEK_API_KEY", "")
DEFAULT_MINIMAX_KEY     = os.getenv("MINIMAX_API_KEY", "")
DEFAULT_MINIMAX_GROUP   = os.getenv("MINIMAX_GROUP_ID", "")
DEFAULT_VOLC_APP_ID     = os.getenv("VOLC_APP_ID", "")
DEFAULT_VOLC_ACCESS_KEY  = os.getenv("VOLC_ACCESS_KEY", "")

# ─── MiniMax 音色映射 ─────────────────────────────────────
MINIMAX_VOICES = {
    "Alex": "male-qn-jingying",
    "小米": "female-shaonv",
    "Sam":  "male-qn-badao",
}

# ─── 硬编码密钥（避免环境变量/请求头传递问题）──────────────
_HARDCODED_DEEPSEEK_KEY   = os.getenv("HARDCODED_DEEPSEEK_KEY", "")
_HARDCODED_MINIMAX_KEY    = os.getenv("HARDCODED_MINIMAX_KEY", "")
_HARDCODED_MINIMAX_GROUP  = os.getenv("HARDCODED_MINIMAX_GROUP", "")

# ─── 火山引擎 硬编码配置（选填，留空则不使用）──────────────
_HARDCODED_VOLC_APP_ID     = os.getenv("HARDCODED_VOLC_APP_ID", "")
_HARDCODED_VOLC_ACCESS_KEY = os.getenv("HARDCODED_VOLC_ACCESS_KEY", "")
_HARDCODED_VOLC_APP_KEY    = os.getenv("HARDCODED_VOLC_APP_KEY", "")
_HARDCODED_VOLC_RESOURCE   = "volc.service_type.10050"


def get_config(request: Request) -> dict:
    """DeepSeek 允许自定义；MiniMax 强制硬编码；火山引擎按配置自动启用。"""
    headers = request.headers

    def _val(header_key: str, env_val: str, hard_val: str) -> str:
        v = headers.get(header_key)
        if v and v not in ("undefined", "null", "None"):
            return v
        if env_val:
            return env_val
        return hard_val

    # TTS 引擎选择：请求头 > 默认（podcast 优先，volcengine 次之，minimax 兜底）
    tts_engine = headers.get("x-tts-engine", "").strip()
    if tts_engine not in ("podcast", "volcengine", "minimax"):
        podcast_ok = bool(
            DEFAULT_VOLC_ACCESS_KEY or _HARDCODED_VOLC_ACCESS_KEY
        )
        tts_engine = "podcast" if podcast_ok else "minimax"

    return {
        "deepseek_key":     _val("x-deepseek-key",  DEFAULT_DEEPSEEK_KEY,  _HARDCODED_DEEPSEEK_KEY),
        "minimax_key":      _HARDCODED_MINIMAX_KEY,
        "minimax_group":    _HARDCODED_MINIMAX_GROUP,
        "tts_engine":       tts_engine,
        "volc_app_id":      DEFAULT_VOLC_APP_ID     or _HARDCODED_VOLC_APP_ID,
        "volc_access_key":  DEFAULT_VOLC_ACCESS_KEY or _HARDCODED_VOLC_ACCESS_KEY,
        "volc_app_key":     _HARDCODED_VOLC_APP_KEY,
        "volc_resource":    _HARDCODED_VOLC_RESOURCE,
    }


# ─── PDF 提取 ────────────────────────────────────────────
def extract_pdf_text(pdf_bytes: bytes, max_chars: int = 8000) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    full_text = "\n".join(texts).strip()
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n[...内容已截断，基于以上内容生成播客...]"
    return full_text


# ─── LLM 生成对话脚本 ─────────────────────────────────────
async def generate_podcast_script(text: str, language: str, cfg: dict) -> list:
    if language == "zh":
        system_prompt = """你是一个播客脚本编写专家。请根据给定的文章内容，生成一段自然流畅的双人播客对话。

角色设定：
- 主播A（Alex）：资深行业观察者，善于深度分析，语气沉稳专业
- 主播B（小米）：好奇心旺盛的新人，喜欢追问细节，语气轻松活泼

要求：
1. 对话要自然，有来有往，包含真实讨论的感觉
2. 每段对话 1-3 句话，不要太长
3. 加入适当的过渡语（"你说得对"、"这让我想到"等）
4. 总共 8-12 轮对话（每人各 4-6 次发言）
5. 开头要有引入，结尾要有总结

请严格按照以下 JSON 格式输出，不要有任何额外文字：
[
  {"speaker": "Alex", "text": "..."},
  {"speaker": "小米", "text": "..."}
]"""
        user_prompt = f"请根据以下内容生成播客对话：\n\n{text}"
    else:
        system_prompt = """You are a podcast script writer. Generate a natural two-host podcast dialogue based on the given content.

Characters:
- Host A (Alex): Senior industry analyst, deep insights, calm and professional tone
- Host B (Sam): Curious newcomer, likes to ask questions, casual and lively

Requirements:
1. Natural dialogue with back-and-forth discussion
2. 1-3 sentences per turn, not too long
3. Include transition phrases ("That's a good point", "This reminds me of", etc.)
4. 8-12 total exchanges (4-6 turns each)
5. Proper intro and conclusion

Output ONLY in this exact JSON format, no extra text:
[
  {"speaker": "Alex", "text": "..."},
  {"speaker": "Sam", "text": "..."}
]"""
        user_prompt = f"Generate a podcast dialogue based on this content:\n\n{text}"

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['deepseek_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 2000,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"LLM 调用失败 ({resp.status_code}): {resp.text[:400]}")

    content = resp.json()["choices"][0]["message"]["content"].strip()

    # 提取 JSON（去掉 markdown 代码块）
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"脚本解析失败: {e}\n原始: {content[:400]}")


# ─── MiniMax TTS ─────────────────────────────────────────
async def tts_minimax(text: str, speaker: str, output_path: Path, cfg: dict) -> bool:
    voice_id = MINIMAX_VOICES.get(speaker, "male-qn-jingying")
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={cfg['minimax_group']}"

    payload = {
        "model": "speech-02-hd",
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg['minimax_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        print(f"[TTS Error] {resp.status_code}: {resp.text[:300]}")
        return False

    data = resp.json()
    if "data" in data and "audio" in data["data"]:
        audio_hex = data["data"]["audio"]
        audio_bytes = bytes.fromhex(audio_hex)
        output_path.write_bytes(audio_bytes)
        return True

    print(f"[TTS] 未找到音频: {json.dumps(data)[:300]}")
    return False


# ─── 火山引擎 播客大模型（WebSocket 端到端生成）────────────
from websockets.exceptions import ConnectionClosed

async def generate_via_podcast_model(text: str, job_id: str, job_dir: Path, cfg: dict) -> dict:
    """
    调用火山引擎播客大模型：
    输入文本 → 自动生成双人对话 → 合成音频 → 返回 script + audio_path
    使用 WebSocket 二进制协议
    """
    app_id = cfg.get("volc_app_id", "")
    access_token = cfg.get("volc_access_key", "")
    app_key = cfg.get("volc_app_key", "")
    resource_id = cfg.get("volc_resource", "volc.service_type.10050")

    if not app_id or not access_token:
        return {"error": "火山引擎凭证未配置"}

    headers = {
        "X-Api-App-Id": app_id,
        "X-Api-App-Key": app_key,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }

    req_params = {
        "input_id": f"pdftwocast_{job_id}",
        "input_text": text,
        "nlp_texts": None,
        "prompt_text": "",
        "action": 0,
        "use_head_music": True,
        "use_tail_music": True,
        "input_info": {"input_url": "", "return_audio_url": False, "only_nlp_text": False},
        "speaker_info": {"random_order": False},
        "audio_config": {"format": "mp3", "sample_rate": 24000, "speech_rate": 0},
    }

    session_id = uuid.uuid4().hex

    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                "wss://openspeech.bytedance.com/api/v3/sami/podcasttts",
                additional_headers=headers,
            ),
            timeout=15.0,
        )
    except Exception as e:
        print(f"[Podcast] WebSocket 连接失败: {e}")
        return {"error": f"WebSocket 连接失败: {e}"}

    try:
        # 1. 建立连接
        await start_connection(ws)
        print("[Podcast] 连接已建立")

        # 2. 开启会话
        await start_session(ws, json.dumps(req_params).encode(), session_id)
        print("[Podcast] 会话已开启")

        # 3. 结束会话请求
        await finish_session(ws, session_id)

        # 4. 接收数据流
        audio_all = bytearray()
        script = []
        usage_info = {}

        while True:
            msg = await asyncio.wait_for(receive_message(ws), timeout=120.0)

            if msg.event == EventType.PodcastRoundStart:
                data = json.loads(msg.payload.decode())
                round_id = data.get("round_id", -1)
                speaker = data.get("speaker", "")
                round_text = data.get("text", "")

                if round_id >= 0:
                    # 映射 speaker 到角色名
                    role = "Alex" if "male" in speaker else "小米"
                    if round_text.strip():  # 过滤空文本（片尾等）
                        script.append({"speaker": role, "text": round_text})
                        print(f"[Podcast] Round {round_id}: {role}: {round_text[:50]}...")

            elif msg.event == EventType.PodcastRoundResponse:
                audio_all.extend(msg.payload)

            elif msg.event == EventType.UsageResponse:
                usage_info = json.loads(msg.payload.decode())
                print(f"[Podcast] Usage: {usage_info}")

            elif msg.event == EventType.SessionFinished:
                print("[Podcast] 会话已完成")
                break

            elif msg.event == EventType.SessionFailed:
                print(f"[Podcast] 会话失败: {msg.payload.decode()}")
                return {"error": f"播客生成失败: {msg.payload.decode()}"}

        # 5. 结束连接
        await finish_connection(ws)
        try:
            await ws.close()
        except Exception:
            pass

        # 6. 保存音频
        if audio_all:
            final_path = output_dir / f"{job_id}.mp3"
            final_path.write_bytes(bytes(audio_all))
            print(f"[Podcast] 生成成功: {len(audio_all)} bytes, {len(script)} 段对话")
            return {
                "script": script,
                "audio_path": str(final_path),
                "usage": usage_info,
            }
        else:
            return {"error": "未收到音频数据"}

    except ConnectionClosed as e:
        print(f"[Podcast] WebSocket 连接关闭: {e}")
        return {"error": f"连接中断: {e}"}
    except asyncio.TimeoutError:
        print("[Podcast] 超时")
        return {"error": "播客生成超时"}
    except Exception as e:
        print(f"[Podcast] 异常: {e}")
        return {"error": str(e)}


async def merge_mp3_files(audio_files: list, output_path: Path):
    """简单字节拼接 MP3"""
    combined = b""
    for f in audio_files:
        if Path(f).exists():
            combined += Path(f).read_bytes()
    output_path.write_bytes(combined)


# ─── 主接口 ──────────────────────────────────────────────
@app.post("/api/generate")
async def generate_podcast(
    request: Request,
    pdf_file: UploadFile = File(...),
    language: str = Form(default="zh"),
    authorization: str | None = Header(None),
):
    cfg = get_config(request)
    sess = _get_session(authorization)
    user_membership = sess["membership_type"] if sess else "guest"

    # 1. 读取 & 解析 PDF
    pdf_bytes = await pdf_file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="PDF 文件为空")

    try:
        text = extract_pdf_text(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF 解析失败: {e}")

    if len(text.strip()) < 50:
        raise HTTPException(status_code=400, detail="PDF 内容太少，无法生成播客")

    job_id = f"podcast_{os.urandom(4).hex()}"
    job_dir = output_dir / job_id
    job_dir.mkdir(exist_ok=True)
    audio_url = None
    tts_available = False
    script = []

    # ── 权限判断 ──────────────────────────────────────
    can_generate_podcast = user_membership in ("paid", "admin")
    quota_info = {"quota": 0, "consumed": False}

    if user_membership == "paid":
        quota_info["quota"] = sess.get("podcast_quota", 0)
        if quota_info["quota"] <= 0:
            can_generate_podcast = False

    # ── 模式 1：播客大模型（付费用户 + 配额充足）─────────
    if can_generate_podcast and cfg.get("tts_engine") == "podcast":
        print(f"[Podcast] 使用火山引擎播客大模型生成...")
        result = await generate_via_podcast_model(text, job_id, job_dir, cfg)
        if "script" in result:
            script = result["script"]
            final_path = Path(result["audio_path"])
            if final_path.exists():
                audio_url = f"/api/audio/{job_id}"
                tts_available = True
                # 扣减配额
                if user_membership == "paid":
                    updated = consume_quota(sess["user_id"], 1)
                    if updated:
                        sess["podcast_quota"] = updated["podcast_quota"]
                        quota_info["quota"] = updated["podcast_quota"]
                        quota_info["consumed"] = True
                print(f"[Podcast] 端到端生成完成: {len(script)} 段对话, {final_path.stat().st_size} bytes, 剩余配额: {quota_info['quota']}")
                try:
                    job_dir.rmdir()
                except Exception:
                    pass
            else:
                print("[Podcast] 音频未生成")
        else:
            print(f"[Podcast] 播客大模型失败: {result.get('error', '未知错误')}")

    # ── 模式 2：DeepSeek 脚本 + MiniMax TTS（降级，付费）──
    if not tts_available and can_generate_podcast:
        print("[TTS] 使用 DeepSeek + MiniMax 降级流程...")

        if not cfg["deepseek_key"]:
            raise HTTPException(status_code=400, detail="请配置 DeepSeek API Key")

        script = await generate_podcast_script(text, language, cfg)
        audio_files = []

        if cfg.get("minimax_key") and cfg.get("minimax_group"):
            print("[TTS] 尝试 MiniMax 合成...")
            for i, line in enumerate(script):
                seg_path = job_dir / f"seg_{i:03d}.mp3"
                ok = await tts_minimax(line["text"], line["speaker"], seg_path, cfg)
                if ok:
                    audio_files.append(seg_path)
                await asyncio.sleep(0.3)
            if len(audio_files) >= len(script) * 0.5:
                tts_available = True
                print(f"[TTS] MiniMax 合成完成，{len(audio_files)}/{len(script)} 段成功")
                # 扣减配额
                if user_membership == "paid":
                    updated = consume_quota(sess["user_id"], 1)
                    if updated:
                        sess["podcast_quota"] = updated["podcast_quota"]
                        quota_info["quota"] = updated["podcast_quota"]
                        quota_info["consumed"] = True
            else:
                print(f"[TTS] MiniMax 合成失败（仅 {len(audio_files)}/{len(script)} 段）")
                audio_files = []

        if tts_available and audio_files:
            final_path = output_dir / f"{job_id}.mp3"
            await merge_mp3_files(audio_files, final_path)
            for f in audio_files:
                try:
                    Path(f).unlink(missing_ok=True)
                except Exception:
                    pass
            audio_url = f"/api/audio/{job_id}"

        try:
            for f in list(job_dir.iterdir()):
                try:
                    f.unlink()
                except Exception:
                    pass
            job_dir.rmdir()
        except Exception:
            pass

    # ── 模式 3：仅生成脚本（普通会员 / 游客）────────
    if not script:
        print("[ScriptOnly] 仅生成对话脚本...")
        if not cfg["deepseek_key"]:
            raise HTTPException(status_code=400, detail="请配置 DeepSeek API Key")
        script = await generate_podcast_script(text, language, cfg)
        try:
            for f in list(job_dir.iterdir()):
                try:
                    f.unlink()
                except Exception:
                    pass
            job_dir.rmdir()
        except Exception:
            pass

    return JSONResponse({
        "job_id": job_id,
        "script": script,
        "audio_url": audio_url,
        "text_preview": text[:300],
        "tts_available": tts_available,
        "engine": cfg.get("tts_engine", "podcast") if tts_available else "script_only",
        "membership": {
            "type": user_membership,
            "can_generate_podcast": can_generate_podcast,
            "quota_remaining": quota_info["quota"],
            "quota_consumed": quota_info["consumed"],
        },
    })


@app.get("/api/audio/{job_id}")
async def get_audio(job_id: str):
    audio_path = output_dir / f"{job_id}.mp3"
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(str(audio_path), media_type="audio/mpeg", filename=f"{job_id}.mp3")


@app.get("/health")
async def health():
    podcast_configured = bool(
        DEFAULT_VOLC_ACCESS_KEY or _HARDCODED_VOLC_ACCESS_KEY
    )
    return {
        "status": "ok",
        "deepseek_configured": bool(DEFAULT_DEEPSEEK_KEY or _HARDCODED_DEEPSEEK_KEY),
        "minimax_configured": bool(
            (DEFAULT_MINIMAX_KEY or _HARDCODED_MINIMAX_KEY)
            and (DEFAULT_MINIMAX_GROUP or _HARDCODED_MINIMAX_GROUP)
        ),
        "podcast_configured": podcast_configured,
        "default_engine": "podcast" if podcast_configured else "minimax",
        "version": "2.1",
    }


# ── 管理员配置管理 ─────────────────────────────────────
_ENV_FILE = Path(__file__).parent / ".env"

def _mask_value(val: str, keep: int = 6) -> str:
    """脱敏显示：只保留前 keep 位和后 keep 位，中间用 *** 代替"""
    if not val:
        return ""
    if len(val) <= keep * 2:
        return "*" * len(val)
    return val[:keep] + "***" + val[-keep:]


def _read_env_file() -> dict:
    """读取 .env 文件，返回键值对"""
    result = {}
    if not _ENV_FILE.exists():
        return result
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env_file(data: dict):
    """写回 .env 文件，保留注释行"""
    if not _ENV_FILE.exists():
        _ENV_FILE.write_text("", encoding="utf-8")
    lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines = []
    written_keys = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            k, _, _ = stripped.partition("=")
            k = k.strip()
            if k in data:
                new_lines.append(f"{k}={data[k]}")
                written_keys.add(k)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    # 新增文件中没有的键
    for k, v in data.items():
        if k not in written_keys:
            new_lines.append(f"{k}={v}")
    _ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@app.get("/api/admin/config")
async def get_admin_config(authorization: str | None = Header(None)):
    """管理员读取当前配置（脱敏）"""
    sess = _get_session(authorization)
    if not sess or sess["membership_type"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    env = _read_env_file()
    # 定义所有可配置的项及其说明
    fields = [
        {"key": "HARDCODED_DEEPSEEK_KEY",  "label": "DeepSeek API Key",     "placeholder": "sk-..."},
        {"key": "HARDCODED_VOLC_APP_ID",      "label": "火山引擎 App ID",       "placeholder": "数字ID"},
        {"key": "HARDCODED_VOLC_ACCESS_KEY",  "label": "火山引擎 Access Key",   "placeholder": "Access Token"},
        {"key": "HARDCODED_VOLC_APP_KEY",     "label": "火山引擎 App Key",      "placeholder": "App Key"},
        {"key": "HARDCODED_MINIMAX_KEY",      "label": "MiniMax API Key",      "placeholder": "MiniMax Key"},
        {"key": "HARDCODED_MINIMAX_GROUP",    "label": "MiniMax Group ID",     "placeholder": "MiniMax Group"},
    ]
    result = []
    for f in fields:
        val = env.get(f["key"], "")
        result.append({
            **f,
            "value": _mask_value(val) if val else "",
            "is_set": bool(val),
        })
    return {"fields": result}


@app.post("/api/admin/config")
async def update_admin_config(request: Request, authorization: str | None = Header(None)):
    """管理员更新配置"""
    sess = _get_session(authorization)
    if not sess or sess["membership_type"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    body = await request.json()
    updates: dict = body.get("fields", {})
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="fields 必须是对象")
    # 只允许更新白名单中的键
    allowed = {
        "HARDCODED_DEEPSEEK_KEY",
        "HARDCODED_VOLC_APP_ID",
        "HARDCODED_VOLC_ACCESS_KEY",
        "HARDCODED_VOLC_APP_KEY",
        "HARDCODED_MINIMAX_KEY",
        "HARDCODED_MINIMAX_GROUP",
    }
    env = _read_env_file()
    changed = []
    for k, v in updates.items():
        if k not in allowed:
            continue
        v = str(v).strip()
        old = env.get(k, "")
        if v and v != old:
            env[k] = v
            changed.append(k)
        elif v == "" and old:
            env[k] = ""
            changed.append(k)
    if changed:
        _write_env_file(env)
        # 同时更新当前进程的环境变量，使配置立即生效（无需重启）
        for k in changed:
            os.environ[k] = env.get(k, "")
        print(f"[Config] 管理员更新了配置: {changed}")
    return {"ok": True, "changed": changed, "restart_required": False}


@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7861, reload=False)
