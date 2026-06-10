"""
PDFTwocast 会员系统 — SQLite 数据库层
"""
import sqlite3
import hashlib
import os
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "pdftwocast.db"


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表，插入默认 admin 用户"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            membership_type TEXT NOT NULL DEFAULT 'regular',   -- regular | paid | admin
            podcast_quota   INTEGER NOT NULL DEFAULT 0,        -- 剩余播客生成次数
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            upgraded_at     TEXT,
            total_upgrades  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS upgrade_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            action      TEXT NOT NULL,    -- 'upgrade'
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (admin_id) REFERENCES users(id),
            FOREIGN KEY (user_id)  REFERENCES users(id)
        );
    """)
    conn.commit()

    # 确保 admin 用户存在
    admin = conn.execute(
        "SELECT id FROM users WHERE username = ?", ("admin",)
    ).fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users (username, password_hash, membership_type, podcast_quota) VALUES (?, ?, 'admin', 999999)",
            ("admin", _hash("admin918")),
        )
        conn.commit()
        print("[DB] 已创建默认 admin 用户（密码: admin918）")
    conn.close()


# ─── 用户 CRUD ──────────────────────────────────────────

def create_user(username: str, password: str) -> dict | None:
    """创建普通会员，返回用户信息或 None（用户名已存在）"""
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, membership_type, podcast_quota) VALUES (?, ?, 'regular', 0)",
            (username, _hash(password)),
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, username, membership_type, podcast_quota, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        return dict(user)
    except sqlite3.IntegrityError:
        conn.close()
        return None


def authenticate(username: str, password: str) -> dict | None:
    """验证登录，返回用户信息或 None"""
    conn = _get_conn()
    user = conn.execute(
        "SELECT id, username, membership_type, podcast_quota, total_upgrades, created_at, upgraded_at FROM users WHERE username = ? AND password_hash = ?",
        (username, _hash(password)),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = _get_conn()
    user = conn.execute(
        "SELECT id, username, membership_type, podcast_quota, total_upgrades, created_at, upgraded_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(user) if user else None


def get_all_users() -> list[dict]:
    """admin 查看所有用户"""
    conn = _get_conn()
    users = conn.execute(
        "SELECT id, username, membership_type, podcast_quota, total_upgrades, created_at, upgraded_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(u) for u in users]


# ─── 升级 & 扣减 ────────────────────────────────────────

def upgrade_user(admin_id: int, user_id: int) -> dict | None:
    """
    admin 将普通用户升级为付费会员：
    - membership_type → 'paid'
    - podcast_quota → 10（重置）
    - total_upgrades += 1
    - 记录 upgrade_log
    返回更新后的用户信息，或 None（用户不存在或不是 regular）
    """
    conn = _get_conn()
    user = conn.execute("SELECT id, membership_type FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or user["membership_type"] != "regular":
        conn.close()
        return None

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE users SET membership_type = 'paid', podcast_quota = 10, total_upgrades = total_upgrades + 1, upgraded_at = ? WHERE id = ?",
        (now, user_id),
    )
    conn.execute(
        "INSERT INTO upgrade_log (admin_id, user_id, action, created_at) VALUES (?, ?, 'upgrade', ?)",
        (admin_id, user_id, now),
    )
    conn.commit()

    updated = conn.execute(
        "SELECT id, username, membership_type, podcast_quota, total_upgrades, upgraded_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(updated)


def consume_quota(user_id: int, amount: int = 1) -> dict | None:
    """
    扣减播客配额，返回更新后的用户信息。
    扣减成功返回用户信息，quota 不足返回 None。
    """
    conn = _get_conn()
    user = conn.execute(
        "SELECT id, membership_type, podcast_quota FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user or user["membership_type"] != "paid" or user["podcast_quota"] < amount:
        conn.close()
        return None

    conn.execute(
        "UPDATE users SET podcast_quota = podcast_quota - ? WHERE id = ?",
        (amount, user_id),
    )
    conn.commit()

    updated = conn.execute(
        "SELECT id, username, membership_type, podcast_quota, total_upgrades FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return dict(updated)


# ─── 启动时初始化 ──────────────────────────────────────
init_db()
