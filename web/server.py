#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CareerPilot Web - 多用户本地/云端服务器。

仅使用 Python 标准库（http.server + sqlite3 + urllib）。
密码哈希使用 PBKDF2-HMAC-SHA256（标准库实现，不依赖 bcrypt）。
"""

import argparse
import base64
import email
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from profile_merger import apply_paths, build_merge_plan
from job_extractor import extract_job_from_url, search_jobs, search_company_jobs, decorate_search_results, BUILTIN_ATS_ADAPTERS
from llm_client import llm_available
from form_extractor import extract_form
from form_filler import build_fill_plan
from resume_extractor import extract_profile_from_resume
from resume_parser import extract_resume_text
from db_backup import start_backup_scheduler

try:
    from tasks import create_task, get_task, list_tasks, retry_task, VALID_TASK_TYPES
    from worker import WorkerPool
except ImportError:  # python -m web.server 包模式：web 目录不在 sys.path
    from web.tasks import create_task, get_task, list_tasks, retry_task, VALID_TASK_TYPES
    from web.worker import WorkerPool

_worker_pool = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
JOBS_SEED = DATA_DIR / "jobs_seed.json"
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE", str(DATA_DIR / "settings.json")))
DB_FILE = Path(os.environ.get("DB_PATH", str(DATA_DIR / "careerpilot.db")))
RESUME_DIR = DATA_DIR / "resumes"

SESSION_MAX_AGE_DAYS = int(os.environ.get("SESSION_MAX_AGE_DAYS", "30"))
SESSION_COOKIE = "careerpilot_session"
MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", str(1024 * 1024)))
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_WINDOW_SECONDS", "900"))

_DB_LOCK = threading.RLock()
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES = {}
_LLM_LAST_ERROR = ""
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "35"))

PROFILE_FIELDS = {
    "name", "email", "phone", "city", "school", "major", "graduation_date",
    "target_roles", "target_cities", "salary_expectation", "education",
    "experiences", "projects", "skills", "languages", "awards", "summary",
    "portfolio", "github", "linkedin", "status", "location_preference",
    "career_goals", "notes", "highest_degree", "english_level", "target_role",
    "target_sectors", "target_city", "available_date",
    "filter_keywords", "filter_exclude_keywords", "accept_internship", "min_match_score", "onboarding_completed",
}
PROFILE_TEXT_LIMITS = {
    "name": 80, "email": 160, "phone": 40, "city": 80, "school": 160,
    "major": 160, "graduation_date": 40, "salary_expectation": 80,
    "summary": 3000, "portfolio": 500, "github": 500, "linkedin": 500,
}


def _safe_json(value, fallback):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def normalize_profile(raw):
    profile = dict(raw) if isinstance(raw, dict) else {}
    for key in PROFILE_TEXT_LIMITS:
        if key in profile and isinstance(profile[key], str):
            profile[key] = profile[key].strip()
    for key in ("target_roles", "target_cities"):
        if key in profile and isinstance(profile[key], str):
            profile[key] = [x.strip() for x in profile[key].replace("，", ",").split(",") if x.strip()]
    for key in ("education", "experiences", "projects", "languages", "awards"):
        if key in profile and not isinstance(profile[key], list):
            profile[key] = []
    if isinstance(profile.get("skills"), str):
        profile["skills"] = {"strong": profile["skills"], "moderate": [], "weak": []}
    if not isinstance(profile.get("skills"), dict):
        profile["skills"] = {"strong": [], "moderate": [], "weak": []}
    for key in ("strong", "moderate", "weak"):
        value = profile["skills"].get(key, [])
        profile["skills"][key] = [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
    for key in ("filter_keywords", "filter_exclude_keywords"):
        value = profile.get(key, [])
        if isinstance(value, str):
            value = value.replace("，", ",").replace("、", ",").replace("\n", ",")
            profile[key] = [x.strip() for x in value.split(",") if x.strip()]
        elif not isinstance(value, list):
            profile[key] = []
    if "accept_internship" not in profile:
        profile["accept_internship"] = True
    try:
        profile["min_match_score"] = int(profile.get("min_match_score", 0) or 0)
    except (TypeError, ValueError):
        profile["min_match_score"] = 0
    return profile


def validate_profile_patch(body):
    if not isinstance(body, dict):
        raise ValueError("档案数据必须是对象")
    unknown = set(body) - PROFILE_FIELDS
    if unknown:
        raise ValueError("档案包含不支持的字段: " + ", ".join(sorted(unknown)))
    for key, limit in PROFILE_TEXT_LIMITS.items():
        if key in body and not isinstance(body[key], str):
            raise ValueError(f"字段 {key} 必须是文本")
        if isinstance(body.get(key), str) and len(body[key]) > limit:
            raise ValueError(f"字段 {key} 超过 {limit} 个字符")
    for key in ("education", "experiences", "projects", "languages", "awards"):
        if key in body and (not isinstance(body[key], list) or len(body[key]) > 100):
            raise ValueError(f"字段 {key} 必须是最多 100 项的数组")
    return normalize_profile(body)


def _utf8(path):
    return path.read_text(encoding="utf-8")


def _write_utf8(path, text):
    path.write_text(text, encoding="utf-8")


class _UploadedFile:
    """兼容 cgi.FieldStorage 文件字段的最小对象（filename + file.read()）。"""

    def __init__(self, filename, data):
        self.filename = filename
        self.file = io.BytesIO(data)


def _parse_multipart(content_type, raw):
    """用标准库 email 解析 multipart/form-data，返回 {字段名: [part, ...]}。

    替代已移除的 cgi.FieldStorage（Python 3.13 起不可用）。
    解析失败时返回空 dict，由调用方按“缺少 file 字段”处理。
    """
    if "multipart/form-data" not in content_type.lower():
        return {}
    try:
        header = "Content-Type: {}\r\nMIME-Version: 1.0\r\n\r\n".format(content_type)
        msg = email.message_from_bytes(header.encode("utf-8") + raw, policy=policy.default)
        parts = msg.iter_parts() if msg.is_multipart() else [msg]
    except Exception:
        return {}
    fields = {}
    for part in parts:
        name = part.get_param("name", header="content-disposition")
        if name:
            fields.setdefault(name, []).append(part)
    return fields


def load_settings():
    settings = {
        "provider": "custom",
        "base_url": os.environ.get("LLM_API_BASE", ""),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", ""),
        "enabled": os.environ.get("LLM_ENABLED", "") == "1",
        "search": {"provider": "custom", "api_key": "", "max_results": 20},
    }
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(_utf8(SETTINGS_FILE))
            for key in ("provider", "base_url", "api_key", "model", "enabled", "search"):
                # 环境变量优先：LLM_API_KEY 等已设置时不覆盖
                if key in stored and not os.environ.get({
                    "provider": "LLM_PROVIDER", "base_url": "LLM_API_BASE", "api_key": "LLM_API_KEY",
                    "model": "LLM_MODEL", "enabled": "LLM_ENABLED",
                }.get(key, "")):
                    settings[key] = stored[key]
        except Exception:
            pass
    return settings


def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _write_utf8(SETTINGS_FILE, json.dumps(settings, ensure_ascii=False, indent=2))


def normalize_llm_base_url(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if host == "platform.deepseek.com":
            return "https://api.deepseek.com/v1"
        path = parsed.path.rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path or "/v1", "", "")).rstrip("/")
    except ValueError:
        return raw


def load_seed_jobs():
    return json.loads(_utf8(JOBS_SEED))


def db():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if DB_FILE.exists() and not os.access(DB_FILE, os.W_OK):
        raise RuntimeError(f"数据库文件不可写：{DB_FILE}")
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    with _DB_LOCK:
        conn = db()
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT,
                profile_json TEXT DEFAULT '{}',
                role TEXT DEFAULT 'guest',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                city TEXT DEFAULT '',
                posting_type TEXT DEFAULT '校招',
                work_type TEXT DEFAULT '全职',
                experience TEXT DEFAULT '应届生',
                tags TEXT DEFAULT '[]',
                salary TEXT DEFAULT '',
                deadline TEXT DEFAULT '',
                source TEXT DEFAULT '',
                url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                requirements TEXT DEFAULT '[]',
                created_at TEXT DEFAULT '',
                is_demo INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT NOT NULL,
                overall INTEGER,
                verdict TEXT,
                dimensions TEXT,
                gates TEXT,
                strengths TEXT,
                gaps TEXT,
                summary TEXT,
                created_at TEXT,
                UNIQUE(user_id, job_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT,
                company TEXT,
                title TEXT,
                city TEXT,
                stage TEXT DEFAULT '已收藏',
                source TEXT DEFAULT '',
                url TEXT DEFAULT '',
                deadline TEXT DEFAULT '',
                salary TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                contact TEXT DEFAULT '',
                follow_up_at TEXT DEFAULT '',
                attachment_name TEXT DEFAULT '',
                UNIQUE(user_id, job_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT,
                kind TEXT,
                content TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS interview_preps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT,
                content TEXT,
                created_at TEXT,
                UNIQUE(user_id, job_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT,
                content TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS resume_import_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                merge_plan_json TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS help_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT DEFAULT '',
                record_type TEXT DEFAULT '求职笔记',
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                record_date TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                input_json TEXT NOT NULL,
                result_json TEXT DEFAULT '',
                error TEXT DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                worker_id TEXT DEFAULT '',
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_id, created_at);
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                worker_id TEXT DEFAULT '',
                run_status TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL,
                error TEXT DEFAULT '',
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT DEFAULT '',
                link TEXT DEFAULT '',
                read INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, read);
            CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_user_id INTEGER,
                detail TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                owner_user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT DEFAULT 'member',
                joined_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(team_id, user_id),
                FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id);
            """
        )
        conn.commit()
        # SQLite migrations for installations created before application follow-up fields.
        app_columns = {row["name"] for row in cur.execute("PRAGMA table_info(applications)").fetchall()}
        for name, definition in (
            ("contact", "TEXT DEFAULT ''"),
            ("follow_up_at", "TEXT DEFAULT ''"),
            ("attachment_name", "TEXT DEFAULT ''"),
        ):
            if name not in app_columns:
                cur.execute("ALTER TABLE applications ADD COLUMN " + name + " " + definition)
        # Enterprise admin: users.disabled for account governance.
        user_columns = {row["name"] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
        if "disabled" not in user_columns:
            cur.execute("ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0")
        conn.commit()
        cur.execute("SELECT COUNT(*) AS n FROM jobs")
        if cur.fetchone()["n"] == 0:
            now = time.strftime("%Y-%m-%d")
            for job in load_seed_jobs():
                cur.execute(
                    """INSERT OR IGNORE INTO jobs
                       (id, title, company, city, posting_type, work_type, experience,
                        tags, salary, deadline, source, url, description, requirements,
                        created_at, is_demo)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                    (
                        job["id"],
                        job["title"],
                        job["company"],
                        job.get("city", ""),
                        job.get("posting_type", "校招"),
                        job.get("work_type", "全职"),
                        job.get("experience", "应届生"),
                        json.dumps(job.get("tags", []), ensure_ascii=False),
                        job.get("salary", ""),
                        job.get("deadline", ""),
                        job.get("source", "示例数据"),
                        job.get("url", ""),
                        job.get("description", ""),
                        json.dumps(job.get("requirements", []), ensure_ascii=False),
                        now,
                    ),
                )
            conn.commit()
        conn.close()
        # 启动时自动应用未执行的版本化迁移（老库平滑升级，不丢数据）。
        try:
            from migrations import migrate
            _conn = db()
            migrate(_conn)
            _conn.close()
        except Exception as exc:
            print("迁移执行失败（已跳过，请人工检查）：%s" % exc)


# ---------------------------------------------------------------- 密码与会话

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return "pbkdf2$120000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password, stored):
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _rate_key_str(key):
    return "|".join(str(k) for k in key) if isinstance(key, (tuple, list)) else str(key)


def login_rate_status(key, now=None):
    now = now or time.time()
    k = _rate_key_str(key)
    with _LOGIN_LOCK:
        try:
            conn = db()
            row = conn.execute("SELECT timestamps_json FROM login_attempts WHERE key=?", (k,)).fetchone()
            conn.close()
        except Exception:
            row = None
        if not row:
            return 0
        try:
            attempts = [s for s in json.loads(row["timestamps_json"]) if now - s < LOGIN_RATE_WINDOW_SECONDS]
        except (ValueError, TypeError):
            attempts = []
        return max(1, int(LOGIN_RATE_WINDOW_SECONDS - (now - attempts[0]))) if len(attempts) >= LOGIN_RATE_LIMIT else 0


def record_login_failure(key, now=None):
    now = now or time.time()
    k = _rate_key_str(key)
    with _LOGIN_LOCK:
        try:
            conn = db()
            row = conn.execute("SELECT timestamps_json FROM login_attempts WHERE key=?", (k,)).fetchone()
            if row:
                try:
                    attempts = [s for s in json.loads(row["timestamps_json"]) if now - s < LOGIN_RATE_WINDOW_SECONDS]
                except (ValueError, TypeError):
                    attempts = []
            else:
                attempts = []
            attempts.append(now)
            conn.execute(
                "INSERT INTO login_attempts (key, timestamps_json, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET timestamps_json=excluded.timestamps_json, updated_at=excluded.updated_at",
                (k, json.dumps(attempts), now),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def clear_login_failures(key):
    k = _rate_key_str(key)
    with _LOGIN_LOCK:
        try:
            conn = db()
            conn.execute("DELETE FROM login_attempts WHERE key=?", (k,))
            conn.commit()
            conn.close()
        except Exception:
            pass


def create_session(user_id, max_age=None):
    token = secrets.token_urlsafe(32)
    expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + (max_age or SESSION_MAX_AGE_DAYS * 86400)))
    with _DB_LOCK:
        conn = db()
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now', 'localtime')")
        conn.execute("INSERT INTO sessions (user_id, token, expires_at) VALUES (?,?,?)", (user_id, token, expires))
        conn.commit()
        conn.close()
    return token


def delete_session(token):
    with _DB_LOCK:
        conn = db()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()


def get_user_by_token(token):
    if not token:
        return None
    with _DB_LOCK:
        conn = db()
        row = conn.execute(
            """SELECT u.* FROM users u
               JOIN sessions s ON s.user_id = u.id
               WHERE s.token=? AND s.expires_at > datetime('now', 'localtime')""",
            (token,),
        ).fetchone()
        conn.close()
    return dict(row) if row else None

SESSION_TOUCH_THRESHOLD_SECONDS = int(os.environ.get("SESSION_TOUCH_THRESHOLD_SECONDS", "300"))


def touch_session(token):
    if not token: return None
    with _DB_LOCK:
        conn=db(); row=conn.execute("SELECT expires_at FROM sessions WHERE token=?",(token,)).fetchone()
        if row:
            try:
                remain = time.mktime(time.strptime(row["expires_at"],"%Y-%m-%d %H:%M:%S")) - time.time()
            except (ValueError, TypeError):
                remain = 0
            # 写放大治理：仅当会话即将过期（剩余 < 阈值）或剩余不足 1 天时才刷新
            if remain < SESSION_TOUCH_THRESHOLD_SECONDS or (remain < 86400):
                conn.execute("UPDATE sessions SET expires_at=? WHERE token=?",(time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(time.time()+SESSION_MAX_AGE_DAYS*86400)),token)); conn.commit()
        row=conn.execute("SELECT expires_at FROM sessions WHERE token=?",(token,)).fetchone(); conn.close()
    return row["expires_at"] if row else None


def user_public(user):
    if not user:
        return None
    try:
        profile = _safe_json(user.get("profile_json"), {})
    except Exception:
        profile = {}
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email"),
        "role": user.get("role", "guest"),
        "disabled": bool(user.get("disabled")),
        "profile": profile,
    }


# ---------------------------------------------------------------- 管理员与运营

def is_admin(user):
    """判断用户是否为管理员角色。兼容 dict 与 sqlite3.Row。"""
    if not user:
        return False
    try:
        role = user.get("role") if hasattr(user, "get") else (user["role"] if "role" in user.keys() else None)
    except Exception:
        return False
    return role == "admin"


def get_user_by_id(user_id):
    with _DB_LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
    return row


def list_users(limit=20, offset=0, query=""):
    """分页列出用户，返回不含密码哈希的公开字段。"""
    with _DB_LOCK:
        conn = db()
        sql = "SELECT id, username, email, role, disabled, created_at, updated_at FROM users"
        args = []
        if query:
            sql += " WHERE username LIKE ? OR email LIKE ?"
            like = "%" + query + "%"
            args = [like, like]
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
    return rows


def set_user_disabled(user_id, disabled):
    with _DB_LOCK:
        conn = db()
        conn.execute(
            "UPDATE users SET disabled=?, updated_at=datetime('now', 'localtime') WHERE id=?",
            (1 if disabled else 0, user_id),
        )
        conn.commit()
        # 停用用户立即失效其全部会话。
        if disabled:
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.commit()
        conn.close()


def set_user_role(user_id, role):
    with _DB_LOCK:
        conn = db()
        conn.execute(
            "UPDATE users SET role=?, updated_at=datetime('now', 'localtime') WHERE id=?",
            (role, user_id),
        )
        conn.commit()
        conn.close()


def admin_overview():
    """运营总览：用户/活跃/岗位/申请/任务/存储/LLM 状态。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cutoff7 = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7 * 86400))
    with _DB_LOCK:
        conn = db()
        users_total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        users_active = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE created_at >= ?",
            (cutoff7,),
        ).fetchone()["n"]
        jobs_total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        apps_total = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        apps_by_stage = {
            row["stage"]: row["n"]
            for row in conn.execute("SELECT stage, COUNT(*) AS n FROM applications GROUP BY stage").fetchall()
        }
        tasks_total = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
        tasks_failed = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status='failed'").fetchone()["n"]
        tasks_succeeded = conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE status='succeeded'").fetchone()["n"]
        conn.close()
    settings = load_settings()
    return {
        "users_total": users_total,
        "users_active_7d": users_active,
        "jobs_total": jobs_total,
        "applications_total": apps_total,
        "applications_by_stage": apps_by_stage,
        "tasks_total": tasks_total,
        "tasks_succeeded": tasks_succeeded,
        "tasks_failed": tasks_failed,
        "db_size_bytes": DB_FILE.stat().st_size if DB_FILE.exists() else 0,
        "llm_enabled": bool(settings.get("enabled") and settings.get("api_key")),
        "server_time": now,
    }


def create_guest_user():
    username = "guest_" + secrets.token_hex(4)
    with _DB_LOCK:
        conn = db()
        cur = conn.execute(
            "INSERT INTO users (username, role, profile_json) VALUES (?, 'guest', '{}')",
            (username,),
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
    return user_id


def update_user_profile(user_id, profile):
    with _DB_LOCK:
        conn = db()
        conn.execute(
            "UPDATE users SET profile_json=?, updated_at=datetime('now', 'localtime') WHERE id=?",
            (json.dumps(profile, ensure_ascii=False), user_id),
        )
        # Profile changes invalidate all cached match scores for this user.
        # They must be recomputed against the new skills, experience and goals.
        conn.execute("DELETE FROM evaluations WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------- 岗位池

def job_row_to_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "company": row["company"],
        "city": row["city"],
        "posting_type": row["posting_type"],
        "work_type": row["work_type"],
        "experience": row["experience"],
        "tags": _safe_json(row["tags"], []),
        "salary": row["salary"],
        "deadline": row["deadline"],
        "source": row["source"],
        "url": row["url"],
        "description": row["description"],
        "requirements": _safe_json(row["requirements"], []),
        "created_at": row["created_at"],
        "is_demo": bool(row["is_demo"]),
    }


def get_job(job_id):
    with _DB_LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        conn.close()
        return job_row_to_dict(row) if row else None


def list_jobs(limit=None, offset=0):
    with _DB_LOCK:
        conn = db()
        sql = "SELECT * FROM jobs ORDER BY created_at DESC, id DESC"
        params = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [job_row_to_dict(r) for r in rows]


def count_jobs():
    with _DB_LOCK:
        conn = db()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        return total


def job_facets(jobs):
    return {
        "cities": sorted({job.get("city", "") for job in jobs if job.get("city")}),
        "types": sorted({value for job in jobs for value in (job.get("posting_type"), job.get("work_type")) if value}),
    }


def filter_jobs(jobs, query):
    keyword = (query.get("q", [""])[0] or "").strip().lower()
    city = (query.get("city", [""])[0] or "").strip()
    job_type = (query.get("type", [""])[0] or "").strip()
    source = (query.get("source", [""])[0] or "").strip()
    deadline_days = (query.get("deadline", [""])[0] or "").strip()
    if keyword:
        jobs = [job for job in jobs if keyword in " ".join((job.get("title", ""), job.get("company", ""), job.get("city", ""), " ".join(job.get("tags", [])), job.get("description", ""))).lower()]
    if city:
        jobs = [job for job in jobs if job.get("city") == city]
    if job_type:
        jobs = [job for job in jobs if job.get("posting_type") == job_type or job.get("work_type") == job_type]
    if source == "demo":
        jobs = [job for job in jobs if job.get("is_demo")]
    elif source == "local":
        jobs = [job for job in jobs if not job.get("is_demo") and job.get("source") == "local"]
    elif source == "llm":
        jobs = [job for job in jobs if not job.get("is_demo") and job.get("source") == "llm_suggested"]
    elif source == "web":
        jobs = [job for job in jobs if not job.get("is_demo") and job.get("source") not in ("local", "llm_suggested")]
    if deadline_days:
        try:
            cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() + max(int(deadline_days), 0) * 86400))
            jobs = [job for job in jobs if job.get("deadline") and job["deadline"] <= cutoff]
        except ValueError:
            pass
    return jobs


def search_source_health(results):
    """返回搜索来源概况：数量 + 各源真实健康状态（熔断/健康注册表）。"""
    counts = {}
    for item in results or []:
        source = item.get("source") or "unknown"
        counts[source] = counts.get(source, 0) + 1
    labels = {"freehire": "FreeHire ATS", "web_search": "网页解析", "URL解析": "岗位链接", "local": "本地岗位", "llm_suggested": "AI 建议"}
    try:
        from http_client import health_snapshot
        health = health_snapshot()
    except Exception:
        health = {}
    out = []
    for key, value in counts.items():
        entry = {"source": key, "label": labels.get(key, key), "count": value, "verified": key not in ("llm_suggested", "local")}
        src_key = {"web_search": "web", "URL解析": "web"}.get(key, key)
        if src_key in health:
            entry["health"] = health[src_key]
        out.append(entry)
    return out


def mark_saved_search_results(results):
    saved_jobs = list_jobs()
    saved_by_identity = {job_identity(job): job for job in saved_jobs}
    output = []
    for item in results:
        saved = next((job for job in saved_jobs if item.get("id") and job["id"] == item["id"]), None)
        saved = saved or saved_by_identity.get(job_identity(item))
        output.append({**item, "saved_job_id": saved["id"] if saved else None})
    return output


def add_job(job):
    job_id = job.get("id") or f"job-{int(time.time() * 1000)}"
    now = time.strftime("%Y-%m-%d")
    with _DB_LOCK:
        conn = db()
        # 保存前在服务端去重，不能只依赖前端的 saved_job_id。
        existing = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not existing and job.get("url"):
            normalized_url = normalize_job_url(job.get("url"))
            rows = conn.execute("SELECT id, url FROM jobs WHERE url IS NOT NULL AND url != ''").fetchall()
            existing = next((row for row in rows if normalize_job_url(row["url"]) == normalized_url), None)
        if not existing and job.get("title") and job.get("company"):
            existing = conn.execute(
                "SELECT id FROM jobs WHERE lower(title)=lower(?) AND lower(company)=lower(?) AND coalesce(city,'')=coalesce(?, '') LIMIT 1",
                (job.get("title"), job.get("company"), job.get("city", "")),
            ).fetchone()
        if existing:
            conn.close()
            return existing["id"]
        conn.execute(
            """INSERT OR IGNORE INTO jobs
               (id, title, company, city, posting_type, work_type, experience,
                tags, salary, deadline, source, url, description, requirements,
                created_at, is_demo)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                job_id,
                job.get("title", "未命名岗位"),
                job.get("company", "未知公司"),
                job.get("city", ""),
                job.get("posting_type", "未知"),
                job.get("work_type", "全职"),
                job.get("experience", ""),
                json.dumps(job.get("tags", []), ensure_ascii=False),
                job.get("salary", ""),
                job.get("deadline", ""),
                job.get("source", "手动录入"),
                job.get("url", ""),
                job.get("description", ""),
                json.dumps(job.get("requirements", []), ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        conn.close()
    return job_id


def normalize_job_url(url):
    """用于去重的链接归一化，不改变用户保存的原始链接。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if not k.lower().startswith(("utm_", "spm", "from"))]
        return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), ""))
    except ValueError:
        return raw.rstrip("/").lower()


def job_identity(job):
    url = normalize_job_url(job.get("url"))
    if url:
        return ("url", url)
    return ("text", _norm("|".join((job.get("title", ""), job.get("company", ""), job.get("city", "")))))


# ---------------------------------------------------------------- 评分引擎

SKILL_KEYWORDS = {
    "ai": ["ai", "人工智能", "llm", "大模型", "agent", "prompt", "aigc", "nlu", "机器学习", "深度学习", "对话"],
    "python": ["python", "数据分析", "数据", "sql", "pandas", "matplotlib"],
    "product": ["产品", "原型", "需求", "交互", "用户", "产品经理", "pm"],
    "game": ["游戏", "策划", "系统", "数值", "玩法", "slg", "卡牌", "关卡", "npc", "世界观", "剧情"],
    "ops": ["运营", "增长", "用户运营", "活动", "商业化", "社区", "内容", "营销", "推广", "拉新", "留存", "转化"],
    "content": ["内容", "创作", "视频", "b站", "抖音", "文案", "脚本", "创作者"],
}

CAREER_AIGAME = ["ai游戏", "游戏ai", "ai玩法", "ai+游戏", "llm agent", "智能npc", "ai驱动内容"]
CAREER_AIPROD = ["ai产品", "产品运营", "商业化", "增长", "运营", "aigc", "ai方向", "产品经理", "解决方案"]

BEHAVIOR_POSITIVE = ["快节奏", "创新", "试错", "挑战", "数据驱动", "结果导向", "自驱", "快速学习", "协作", "团队", "成长", "从0到1", "迭代"]
BEHAVIOR_NEGATIVE = ["流程繁琐", "微管理", "不允许试错", "保守", "按部就班", "重复性", "加班文化", "国企文化"]


def _norm(text):
    return (text or "").lower()


def _text_of(job):
    return " ".join(
        [
            job.get("title", ""),
            job.get("company", ""),
            job.get("description", ""),
            " ".join(job.get("requirements", [])),
            " ".join(job.get("tags", [])),
        ]
    )


def _contains(text, keywords):
    text = _norm(text)
    return [k for k in keywords if k in text]


def quick_prefilter(job, profile=None):
    """BossHunter 风格的轻量预筛：先给出可解释信号，再进入完整匹配评分。"""
    profile = profile or {}
    text = _text_of(job)
    reasons, hits = [], []
    excluded = _contains(text, profile.get("filter_exclude_keywords", []))
    if excluded:
        return {"status": "reject", "label": "不建议", "reasons": ["命中一票否决词：" + "、".join(excluded[:5])], "hits": [], "excluded": excluded}
    keywords = profile.get("filter_keywords", []) or []
    if not keywords:
        keywords = ([profile.get("target_role", "")] + profile.get("target_roles", []) + profile.get("target_sectors", []) + profile.get("career_goals", []))
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    for keyword in keywords:
        if keyword.lower() in _norm(text):
            hits.append(keyword)
    if hits:
        reasons.append("命中目标方向：" + "、".join(hits[:5]))
    target_city = profile.get("target_city") or (profile.get("target_cities") or [""])[0]
    if target_city and job.get("city") and target_city not in job.get("city", "") and "全国" not in str(target_city):
        reasons.append("城市与意向不完全一致：" + str(job.get("city")))
    if not profile.get("accept_internship", True) and "实习" in text:
        reasons.append("个人设置暂不接受实习岗位")
    if job.get("is_demo") or not job.get("url"):
        reasons.append("缺少可核验的真实岗位链接")
    status = "recommend" if hits and not any("不完全一致" in r or "不接受" in r for r in reasons) else "review"
    return {"status": status, "label": "推荐" if status == "recommend" else "需要核实", "reasons": reasons or ["未设置预筛条件，建议人工核实岗位"], "hits": hits, "excluded": []}


def profile_is_empty(profile):
    if not profile:
        return True
    skills = profile.get("skills") or {}
    has_skills = any(bool(skills.get(key)) for key in ("strong", "moderate", "weak")) if isinstance(skills, dict) else bool(skills)
    has_projects = any(bool(item) for item in (profile.get("projects") or []) if isinstance(item, dict))
    has_goals = any(bool(item) for item in (profile.get("career_goals") or []))
    if has_skills or profile.get("name") or has_projects or has_goals:
        return False
    return True


def gates(job, profile=None):
    profile = profile or {}
    text = _text_of(job)
    results = []
    prefilter = quick_prefilter(job, profile)
    blocked = False

    if _contains(text, ["社招", "仅限社招"]):
        results.append({"name": "校招/社招", "status": "fail", "note": "岗位明确标注社招，请确认是否符合要求"})
        blocked = True
    elif _contains(text, ["要求3年以上", "要求5年以上", "多年经验"]):
        results.append({"name": "校招/社招", "status": "fail", "note": "岗位要求多年工作经验，疑似社招"})
        blocked = True
    elif _contains(text, ["校招", "应届生", "管培生", "秋招", "春招", "培训生", "实习"]):
        results.append({"name": "校招/社招", "status": "pass", "note": "明确面向应届生/校招/实习"})
    else:
        results.append({"name": "校招/社招", "status": "warn", "note": "招聘类型未明确标注，建议投递前确认"})

    declared = [lang["name"].lower() for lang in profile.get("languages", [])]
    required_langs = _contains(text, ["英语", "英文", "日语", "韩语", "俄语", "法语", "德语", "西语", "西班牙语"])
    hard_unknown = [lang for lang in required_langs if lang not in declared and "英" not in lang]
    english_strict = _contains(text, ["英语流利", "英语口语流利", "fluent english", "英文流利", "可作为工作语言"])
    if hard_unknown:
        results.append({"name": "语言", "status": "fail", "note": f"岗位要求未声明语言：{','.join(hard_unknown)}"})
        blocked = True
    elif english_strict and "英语" in declared:
        results.append({"name": "语言", "status": "warn", "note": "岗位对英语要求较高（流利/工作语言），请自行判断"})
    else:
        results.append({"name": "语言", "status": "pass", "note": "未发现硬性语言门槛"})

    if _contains(text, ["出国", "驻外", "海外办公"]):
        results.append({"name": "地点", "status": "warn", "note": "岗位可能涉及海外，请确认"})
    else:
        results.append({"name": "地点", "status": "pass", "note": "岗位地点可接受"})

    return {"blocked": blocked, "items": results, "prefilter": prefilter}


def _dimension_score(text, keywords):
    hits = [k for k in keywords if k in _norm(text)]
    if not hits:
        return 0, []
    base = 45 + min(30, len(hits) * 8)
    return min(98, base), hits


def _profile_skill_text(profile):
    skills = profile.get("skills", {})
    parts = list(skills.get("strong", [])) + list(skills.get("moderate", [])) + list(skills.get("weak", []))
    return " ".join(parts)


def _profile_exp_text(profile):
    parts = []
    for exp in profile.get("experiences", []):
        parts.append(exp.get("title", ""))
        parts.append(exp.get("company", ""))
        parts.extend(exp.get("points", []))
    for project in profile.get("projects", []):
        parts.append(project.get("title", ""))
        parts.extend(project.get("points", []))
    return " ".join(parts)


def score_job_ai(job, profile, local_ev):
    """AI 深度评分（第二阶段）：校准本地分并给出洞察。失败返回 None，调用方保留本地结果。"""
    job_text = _text_of(job)[:4000]
    skill_text = _profile_skill_text(profile)
    exp_text = _profile_exp_text(profile)
    goals = " ".join(profile.get("career_goals", [])) + " " + " ".join(profile.get("target_sectors", []))
    system = (
        "你是资深校招筛选专家。基于候选人档案对目标岗位做深度匹配评估。"
        "硬性规则：只基于提供的候选人事实判断，禁止编造候选人没有的经历、技能或指标；"
        "岗位硬门槛（学历届别/地点/年限）由系统本地已判断，你专注评估技能契合度、经历迁移性与投递价值。"
        "输出严格 JSON（不要 markdown 代码块）："
        '{"overall_adjust": 整数-10到10, "strengths": ["1-2条本地未提到的匹配点"], "gaps": ["1-2条本地未提到的风险点"], "advice": "30字内投递或面试建议"}'
    )
    user = (
        "【目标岗位】\n%s\n\n【候选人技能】\n%s\n【候选人经历/项目】\n%s\n【职业目标】\n%s\n\n"
        "【本地初评】综合 %s/100：%s\n请给出深度校准与洞察。" % (
            job_text, skill_text[:800], exp_text[:1500], goals[:500],
            local_ev.get("overall"), local_ev.get("summary", ""),
        )
    )
    try:
        text = llm_chat([{"role": "user", "content": user}], system=system)
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        adjust = int(data.get("overall_adjust", 0))
        adjust = max(-10, min(10, adjust))
        return {
            "overall_adjust": adjust,
            "strengths": [str(s) for s in data.get("strengths", [])[:2]],
            "gaps": [str(g) for g in data.get("gaps", [])[:2]],
            "advice": str(data.get("advice", ""))[:80],
        }
    except Exception:
        return None


def score_job(job, profile=None, deep=False):
    profile = profile or {}
    if profile_is_empty(profile):
        return {
            "job_id": job["id"],
            "overall": 0,
            "verdict": "请先完善档案",
            "needs_profile": True,
            "dimensions": {},
            "gates": gates(job, profile),
            "strengths": [],
            "gaps": ["请先在「个人资料」中填写姓名、技能、经历与职业目标，才能获得评分。"],
            "summary": "档案为空，无法评分。",
            "created_at": time.strftime("%Y-%m-%d"),
        }

    text = _text_of(job)
    gate = gates(job, profile)
    if gate["blocked"]:
        return {
            "job_id": job["id"],
            "overall": 0,
            "verdict": "不建议申请",
            "dimensions": {},
            "gates": gate,
            "strengths": [],
            "gaps": [item["note"] for item in gate["items"] if item["status"] == "fail"],
            "summary": "硬性门槛未通过，不进行评分。",
            "created_at": time.strftime("%Y-%m-%d"),
        }

    skill_text = _profile_skill_text(profile)
    skill_hits = [k for k in SKILL_KEYWORDS.keys() if k in _norm(skill_text)]
    if skill_hits:
        skill_keywords = []
        for k in skill_hits:
            skill_keywords.extend(SKILL_KEYWORDS[k])
        skill_score, skill_hits_detail = _dimension_score(text, skill_keywords)
    else:
        skill_score = 20
        skill_hits_detail = []

    exp_text = _profile_exp_text(profile)
    exp_score = 0
    exp_hits = []
    for kws in SKILL_KEYWORDS.values():
        s, h = _dimension_score(text, [k for k in kws if k in _norm(exp_text)])
        if s > exp_score:
            exp_score, exp_hits = s, h
    if not exp_hits:
        exp_score = 20

    culture_score = 55
    culture_note = []
    pos = [k for k in BEHAVIOR_POSITIVE if k in _norm(text)]
    neg = [k for k in BEHAVIOR_NEGATIVE if k in _norm(text)]
    if pos:
        culture_score = min(98, 62 + len(pos) * 7)
        culture_note.append("匹配偏好：" + "、".join(pos[:4]))
    if neg:
        culture_score = max(25, culture_score - 18 * len(neg))
        culture_note.append("注意：" + "、".join(neg[:2]))
    if not culture_note:
        culture_note.append("岗位描述未充分体现团队文化，建议面试中确认")

    goal_text = " ".join(profile.get("career_goals", [])) + " " + " ".join(profile.get("target_sectors", []))
    career_score = 0
    career_hits = []
    for pool in (CAREER_AIGAME, CAREER_AIPROD):
        s, h = _dimension_score(text, [k for k in pool if k in _norm(goal_text)])
        if s > career_score:
            career_score, career_hits = s, h
    if not career_hits:
        career_score = 30

    overall = round(skill_score * 0.30 + exp_score * 0.25 + culture_score * 0.15 + career_score * 0.30)
    min_score = int(profile.get("min_match_score", 0) or 0)
    if min_score and overall < min_score:
        gate["prefilter"]["status"] = "reject"
        gate["prefilter"]["label"] = "不建议"
        gate["prefilter"]["reasons"] = list(gate["prefilter"].get("reasons", [])) + [f"综合匹配分 {overall} 低于个人阈值 {min_score}"]
    if overall >= 75:
        verdict = "强烈建议申请"
    elif overall >= 60:
        verdict = "建议申请"
    elif overall >= 45:
        verdict = "可考虑"
    elif overall >= 30:
        verdict = "谨慎考虑"
    else:
        verdict = "不建议申请"

    strengths = []
    gaps = []
    if skill_score >= 70:
        strengths.append(f"技能匹配度高：岗位关键词（{'、'.join(skill_hits_detail[:4])}）与你的技能重叠明显")
    elif skill_score >= 50:
        strengths.append(f"技能基本匹配：可覆盖（{'、'.join(skill_hits_detail[:3])}）等方向")
    else:
        gaps.append("技能匹配偏弱，可在档案中补充相关技能")
    if exp_score >= 70:
        strengths.append(f"经历相关性强：已有（{'、'.join(exp_hits[:3])}）等经历可佐证")
    elif exp_score < 50:
        gaps.append("直接经验不足，需要用项目与经历做迁移论证")
    if career_score >= 70:
        strengths.append("职业方向契合：与你的职业目标一致")
    elif career_score < 50:
        gaps.append("岗位与目标方向偏离较远，投递性价比需自行权衡")
    if culture_score < 50:
        gaps.append("团队文化信号与偏好存在摩擦，建议面试中确认")
    if not strengths:
        strengths.append("岗位仍可作为积累面试经验的练习机会")
    if not gaps:
        gaps.append("无明显硬伤，主要看面试发挥与细节打磨")

    summary = (
        f"综合匹配 {overall}/100（技能 {skill_score}、经历 {exp_score}、文化 {culture_score}、职业 {career_score}）。"
        + verdict
        + "。"
    )

    result = {
        "job_id": job["id"],
        "overall": overall,
        "verdict": verdict,
        "dimensions": {
            "skill": {"score": skill_score, "hits": skill_hits_detail},
            "experience": {"score": exp_score, "hits": exp_hits},
            "culture": {"score": culture_score, "note": culture_note},
            "career": {"score": career_score, "hits": career_hits},
        },
        "gates": gate,
        "strengths": strengths,
        "gaps": gaps,
        "summary": summary,
        "created_at": time.strftime("%Y-%m-%d"),
    }

    # 两阶段评分：AI 深度校准（仅 deep=True 且本地达到可评估分值时触发，节省成本）
    if deep and overall >= 45 and not gate["blocked"] and llm_available():
        ai = score_job_ai(job, profile, result)
        if ai:
            adjusted = max(0, min(100, overall + ai["overall_adjust"]))
            result["overall"] = adjusted
            if adjusted >= 75:
                result["verdict"] = "强烈建议申请"
            elif adjusted >= 60:
                result["verdict"] = "建议申请"
            elif adjusted >= 45:
                result["verdict"] = "可考虑"
            else:
                result["verdict"] = "谨慎考虑"
            for s in ai["strengths"]:
                if s and s not in result["strengths"]:
                    result["strengths"].append(s)
            for g in ai["gaps"]:
                if g and g not in result["gaps"]:
                    result["gaps"].append(g)
            result["summary"] = result["summary"].split("。")[0] + "。" + (" AI 深度校准后 " + str(adjusted) + " 分。" if adjusted != overall else "")
            result["ai"] = {"used": True, "adjust": ai["overall_adjust"], "advice": ai["advice"]}
            if ai["advice"]:
                result["gaps"].append("投递建议：" + ai["advice"])

    return result


def save_evaluation(user_id, ev):
    with _DB_LOCK:
        conn = db()
        conn.execute(
            """INSERT OR REPLACE INTO evaluations
               (user_id, job_id, overall, verdict, dimensions, gates, strengths, gaps, summary, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                ev["job_id"],
                ev["overall"],
                ev["verdict"],
                json.dumps(ev.get("dimensions", {}), ensure_ascii=False),
                json.dumps(ev.get("gates", {}), ensure_ascii=False),
                json.dumps(ev.get("strengths", []), ensure_ascii=False),
                json.dumps(ev.get("gaps", []), ensure_ascii=False),
                ev.get("summary", ""),
                ev.get("created_at", time.strftime("%Y-%m-%d")),
            ),
        )
        conn.commit()
        conn.close()
    record_event(user_id, "job_scored", {"job_id": ev["job_id"], "overall": ev.get("overall")})


def record_event(user_id, event_type, payload=None):
    """记录用户关键行为事件。失败必须静默，不得影响业务主流程。"""
    try:
        with _DB_LOCK:
            conn = db()
            conn.execute(
                "INSERT INTO events (user_id, event_type, payload, created_at) VALUES (?,?,?,?)",
                (user_id, event_type, json.dumps(payload or {}, ensure_ascii=False)[:2000], time.time()),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def funnel_stats(user_id=None):
    """按事件类型统计计数；user_id=None 时统计全局。"""
    with _DB_LOCK:
        conn = db()
        if user_id is not None:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM events WHERE user_id=? GROUP BY event_type",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type",
            ).fetchall()
        conn.close()
    return {row["event_type"]: row["n"] for row in rows}


# ---------------------------------------------------------------- 审计日志

def audit(action, resource="", resource_id="", user_id=None, ip="", ua="", meta=None):
    """写入审计日志。失败静默，不抛异常。meta 中敏感字段（api_key/password/token）被剥离。"""
    try:
        meta = _scrub_meta(meta or {})
        with _DB_LOCK:
            conn = db()
            conn.execute(
                "INSERT INTO audit_log (ts, user_id, action, resource, resource_id, ip, user_agent, meta_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), user_id, action[:100], resource[:100], str(resource_id)[:100],
                 str(ip)[:64], str(ua)[:256], json.dumps(meta, ensure_ascii=False)[:2000]),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


_SENSITIVE_META_KEYS = ("api_key", "apikey", "password", "passwd", "token", "secret", "authorization")


def _scrub_meta(meta):
    if not isinstance(meta, dict):
        if isinstance(meta, str):
            low = meta.lower()
            return "<scrubbed>" if any(s in low for s in _SENSITIVE_META_KEYS) or low.startswith("sk-") else meta
        return meta
    out = {}
    for k, v in meta.items():
        k_low = k.lower()
        is_sensitive_key = any(s in k_low for s in _SENSITIVE_META_KEYS)
        if is_sensitive_key:
            out[k] = "<scrubbed>"
        elif isinstance(v, str) and (any(s in v.lower() for s in _SENSITIVE_META_KEYS) or v.lower().startswith("sk-")):
            out[k] = "<scrubbed>"
        else:
            out[k] = v
    return out


def list_audit(limit=100, action=None, user_id=None):
    with _DB_LOCK:
        conn = db()
        sql = "SELECT id, ts, user_id, action, resource, resource_id, ip, user_agent, meta_json FROM audit_log"
        where, args = [], []
        if action:
            where.append("action=?")
            args.append(action)
        if user_id is not None:
            where.append("user_id=?")
            args.append(user_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(min(max(limit, 1), 500))
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        conn.close()
    for r in rows:
        r["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
        r["meta"] = _safe_json(r.pop("meta_json"), {})
    return rows


# ---------------------------------------------------------------- 留存触达与通知

def notify(user_id, ntype, title, body="", link=""):
    """插入站内通知。失败静默，不影响业务主流程。"""
    try:
        with _DB_LOCK:
            conn = db()
            conn.execute(
                "INSERT INTO notifications (user_id, type, title, body, link, read, created_at) VALUES (?,?,?,?,?,0,?)",
                (user_id, ntype, title[:200], body[:500], link[:500], time.time()),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def _days_left(date_str):
    """YYYY-MM-DD 距今天数（按自然日）；无效或空返回 None。"""
    if not date_str:
        return None
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        today = _dt.date.today()
        return (d - today).days
    except (ValueError, TypeError):
        return None


def today_tasks(user_id):
    """聚合四类待办：跟进 / 截止 / 面试 / 待收藏处理。"""
    today = time.strftime("%Y-%m-%d")
    week_later = time.strftime("%Y-%m-%d", time.localtime(time.time() + 7 * 86400))
    with _DB_LOCK:
        conn = db()
        follow_ups = [dict(r) for r in conn.execute(
            "SELECT id, job_id, company, title, stage, follow_up_at FROM applications"
            " WHERE user_id=? AND stage IN ('已投递','面试中') AND follow_up_at<>'' AND follow_up_at<=?"
            " ORDER BY follow_up_at ASC LIMIT 10",
            (user_id, today),
        ).fetchall()]
        deadlines = [dict(r) for r in conn.execute(
            "SELECT id, title, company, city, deadline FROM jobs"
            " WHERE deadline<>'' AND deadline>=? AND deadline<=? ORDER BY deadline ASC LIMIT 10",
            (today, week_later),
        ).fetchall()]
        interviews = [dict(r) for r in conn.execute(
            "SELECT id, job_id, company, title, stage FROM applications"
            " WHERE user_id=? AND stage='面试中' ORDER BY updated_at DESC LIMIT 10",
            (user_id,),
        ).fetchall()]
        pending = [dict(r) for r in conn.execute(
            "SELECT id, job_id, company, title, stage FROM applications"
            " WHERE user_id=? AND stage='已收藏' ORDER BY updated_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()]
        conn.close()
    for item in follow_ups:
        item["days_left"] = _days_left(item.get("follow_up_at"))
    for item in deadlines:
        item["days_left"] = _days_left(item.get("deadline"))
    return {
        "follow_ups": follow_ups,
        "deadlines": deadlines,
        "interviews": interviews,
        "pending": pending,
    }


def list_notifications(user_id, limit=20):
    with _DB_LOCK:
        conn = db()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, type, title, body, link, read, created_at FROM notifications"
            " WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()]
        conn.close()
    for row in rows:
        row["time_ago"] = _time_ago(row["created_at"])
    return rows


def _time_ago(ts):
    try:
        diff = int(time.time() - ts)
        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return "%d 分钟前" % (diff // 60)
        if diff < 86400:
            return "%d 小时前" % (diff // 3600)
        return "%d 天前" % (diff // 86400)
    except (TypeError, ValueError):
        return ""


def mark_notification_read(user_id, notif_id):
    with _DB_LOCK:
        conn = db()
        conn.execute("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", (notif_id, user_id))
        conn.commit()
        conn.close()


def mark_all_notifications_read(user_id):
    with _DB_LOCK:
        conn = db()
        conn.execute("UPDATE notifications SET read=1 WHERE user_id=? AND read=0", (user_id,))
        conn.commit()
        conn.close()


def unread_count(user_id):
    with _DB_LOCK:
        conn = db()
        n = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", (user_id,)).fetchone()["n"]
        conn.close()
    return n


# ---------------------------------------------------------------- 数据主权与合规

def record_admin_action(admin_user_id, action, target_user_id=None, detail=""):
    """管理员操作审计留痕。失败静默。"""
    try:
        with _DB_LOCK:
            conn = db()
            conn.execute(
                "INSERT INTO admin_actions (admin_user_id, action, target_user_id, detail, created_at) VALUES (?,?,?,?,?)",
                (admin_user_id, action, target_user_id, str(detail)[:500], time.time()),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def export_user_data(user_id):
    """打包用户全部数据为 dict。绝不含 password_hash / api_key；简历只导出元信息。"""
    with _DB_LOCK:
        conn = db()
        user_row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user_row:
            conn.close()
            return None
        user = dict(user_row)
        user.pop("password_hash", None)
        profile = _safe_json(user.get("profile_json"), {})
        jobs = [dict(r) for r in conn.execute(
            "SELECT id, title, company, city, posting_type, work_type, experience, tags, salary, deadline, source, url, description, requirements, created_at, is_demo FROM jobs"
            " WHERE id IN (SELECT DISTINCT job_id FROM applications WHERE user_id=?)", (user_id,)).fetchall()]
        applications = [dict(r) for r in conn.execute(
            "SELECT * FROM applications WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()]
        evaluations = [dict(r) for r in conn.execute(
            "SELECT * FROM evaluations WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()]
        resumes = [dict(r) for r in conn.execute(
            "SELECT id, filename, stored_name, size, created_at FROM resumes WHERE user_id=?", (user_id,)).fetchall()]
        chat_messages = [dict(r) for r in conn.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
        documents = [dict(r) for r in conn.execute(
            "SELECT * FROM documents WHERE user_id=?", (user_id,)).fetchall()]
        interview_preps = [dict(r) for r in conn.execute(
            "SELECT * FROM interview_preps WHERE user_id=?", (user_id,)).fetchall()]
        help_records = [dict(r) for r in conn.execute(
            "SELECT * FROM help_records WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
        events = [dict(r) for r in conn.execute(
            "SELECT event_type, payload, created_at FROM events WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
        notifications = [dict(r) for r in conn.execute(
            "SELECT type, title, body, link, read, created_at FROM notifications WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
        conn.close()
    return {
        "user": user,
        "profile": profile,
        "jobs": jobs,
        "applications": applications,
        "evaluations": evaluations,
        "resumes": resumes,
        "chat_messages": chat_messages,
        "documents": documents,
        "interview_preps": interview_preps,
        "help_records": help_records,
        "events": events,
        "notifications": notifications,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def delete_user_data(user_id):
    """硬删除用户全部关联数据与账号本身。事务保证原子性。"""
    with _DB_LOCK:
        conn = db()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM task_runs WHERE task_id IN (SELECT id FROM tasks WHERE user_id=?)", (user_id,))
            for table in (
                "sessions", "evaluations", "applications", "documents", "interview_preps",
                "chat_messages", "resume_import_drafts", "resumes", "help_records",
                "tasks", "events", "notifications",
            ):
                conn.execute("DELETE FROM %s WHERE user_id=?" % table, (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------- 团队与协作

def generate_invite_code():
    return secrets.token_hex(4).upper()


def create_team(owner_user_id, name):
    code = generate_invite_code()
    with _DB_LOCK:
        conn = db()
        cur = conn.execute(
            "INSERT INTO teams (name, invite_code, owner_user_id) VALUES (?,?,?)",
            (name[:80], code, owner_user_id),
        )
        team_id = cur.lastrowid
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (?,?,?)",
            (team_id, owner_user_id, "owner"),
        )
        conn.commit()
        conn.close()
    return get_team(team_id)


def get_team(team_id):
    with _DB_LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        conn.close()
    return dict(row) if row else None


def get_team_by_code(invite_code):
    with _DB_LOCK:
        conn = db()
        row = conn.execute("SELECT * FROM teams WHERE invite_code=?", (invite_code,)).fetchone()
        conn.close()
    return dict(row) if row else None


def team_member_count(team_id):
    with _DB_LOCK:
        conn = db()
        n = conn.execute("SELECT COUNT(*) AS n FROM team_members WHERE team_id=?", (team_id,)).fetchone()["n"]
        conn.close()
    return n


def is_team_member(team_id, user_id):
    with _DB_LOCK:
        conn = db()
        row = conn.execute(
            "SELECT 1 FROM team_members WHERE team_id=? AND user_id=?",
            (team_id, user_id),
        ).fetchone()
        conn.close()
    return row is not None


def list_my_teams(user_id):
    with _DB_LOCK:
        conn = db()
        rows = conn.execute(
            """SELECT t.id, t.name, t.invite_code, t.owner_user_id, t.created_at, m.role AS my_role
               FROM teams t JOIN team_members m ON m.team_id=t.id
               WHERE m.user_id=? ORDER BY t.id DESC""",
            (user_id,),
        ).fetchall()
        conn.close()
    teams = [dict(r) for r in rows]
    for t in teams:
        t["member_count"] = team_member_count(t["id"])
    return teams


def list_team_members(team_id):
    with _DB_LOCK:
        conn = db()
        rows = conn.execute(
            """SELECT u.id, u.username, u.email, m.role, m.joined_at
               FROM team_members m JOIN users u ON u.id=m.user_id
               WHERE m.team_id=? ORDER BY m.id""",
            (team_id,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def join_team(user_id, invite_code):
    team = get_team_by_code((invite_code or "").strip().upper())
    if not team:
        return None, "邀请码无效"
    if is_team_member(team["id"], user_id):
        return None, "已在团队中"
    with _DB_LOCK:
        conn = db()
        conn.execute(
            "INSERT INTO team_members (team_id, user_id, role) VALUES (?,?,'member')",
            (team["id"], user_id),
        )
        conn.commit()
        conn.close()
    return team, None


def leave_team(user_id, team_id):
    team = get_team(team_id)
    if not team:
        return False, "团队不存在"
    if team["owner_user_id"] == user_id:
        return False, "创建者不可退出团队"
    with _DB_LOCK:
        conn = db()
        conn.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id))
        conn.commit()
        conn.close()
    return True, None


def notify_deadline_if_needed(user_id, job):
    """岗位 deadline 距今 <=3 天时生成「即将截止」通知（同一 job 只生成一次）。"""
    if not job:
        return
    dl = _days_left(job.get("deadline"))
    if dl is None or dl > 3:
        return
    link = "/jobs/" + str(job["id"])
    with _DB_LOCK:
        conn = db()
        exists = conn.execute(
            "SELECT 1 FROM notifications WHERE user_id=? AND link=? AND type='deadline' LIMIT 1",
            (user_id, link),
        ).fetchone()
        conn.close()
    if not exists:
        notify(user_id, "deadline", "岗位即将截止", "%s · %s 还有 %d 天截止" % (job.get("title", ""), job.get("company", ""), dl), link)


def get_evaluation(user_id, job_id):
    with _DB_LOCK:
        conn = db()
        row = conn.execute(
            "SELECT * FROM evaluations WHERE user_id=? AND job_id=?",
            (user_id, job_id),
        ).fetchone()
        conn.close()
    if not row:
        return None
    return {
        "job_id": row["job_id"],
        "overall": row["overall"],
        "verdict": row["verdict"],
        "dimensions": _safe_json(row["dimensions"], {}),
        "gates": _safe_json(row["gates"], {}),
        "strengths": _safe_json(row["strengths"], []),
        "gaps": _safe_json(row["gaps"], []),
        "summary": row["summary"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------- 文档生成器

def _pick_project_points(profile, job_text, limit=3):
    scored = []
    for project in profile.get("projects", []):
        points = project.get("points", [])
        hit_count = sum(
            1
            for p in points
            if any(k in _norm(p) for k in ["ai", "llm", "prompt", "产品", "数据", "游戏", "运营", "内容", "用户", "原型"])
        )
        title_hit = sum(1 for k in SKILL_KEYWORDS["ai"] + SKILL_KEYWORDS["game"] + SKILL_KEYWORDS["ops"] if k in _norm(project.get("title", "")))
        job_hit = sum(1 for k in _norm(job_text).split() if k in _norm(" ".join(points)))
        scored.append((hit_count * 2 + title_hit * 3 + job_hit, project))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def generate_resume_ai(job, profile):
    """AI 定制简历：基于 JD + 档案生成 Markdown，不编造经历。失败返回 None 由调用方回退。"""
    job_text = _text_of(job)[:6000]
    compact = {
        "name": profile.get("name", "候选人"),
        "status": profile.get("status", ""),
        "city": profile.get("city", ""),
        "phone": profile.get("phone", ""),
        "email": profile.get("email", ""),
        "summary": profile.get("notes", ""),
        "skills": profile.get("skills", {}),
        "education": profile.get("education", [])[:1],
        "experiences": [{k: e.get(k, "") for k in ("company", "title", "period", "points")} for e in profile.get("experiences", [])],
        "projects": [{k: p.get(k, "") for k in ("title", "period", "points")} for p in profile.get("projects", [])],
        "certifications": profile.get("certifications", [])[:6],
        "languages": profile.get("languages", []),
    }
    system = (
        "你是资深校招简历定制专家。基于候选人档案和目标岗位 JD 生成一份中文 Markdown 简历。"
        "规则：只使用档案中真实存在的内容，禁止编造经历、指标、公司、学历、日期；"
        "概述（核心优势）必须针对该岗位重写，突出与 JD 关键词匹配的能力；"
        "项目/经历按与岗位相关度排序，并改写要点使其对齐 JD 用词（不改变事实）；"
        "技能按 JD 优先级重排并保留未在 JD 中但真实的技能；"
        "输出标准 Markdown：一级标题为姓名+·个人简历，二级标题为 求职意向/核心优势/项目经历/实习与工作经历/教育背景/专业技能/证书与获奖。不要输出额外解释。"
    )
    user = "【目标岗位】\n%s\n\n【候选人档案】\n%s" % (job_text, json.dumps(compact, ensure_ascii=False))
    try:
        return llm_chat([{"role": "user", "content": user}], system=system)
    except Exception:
        return None


def generate_resume(job, profile=None):
    profile = profile or {}
    if profile_is_empty(profile):
        return "请先在「个人资料」中填写姓名、技能、经历与职业目标，才能生成定制简历。"
    # AI 定制优先：LLM 可用且成功生成时使用；失败/未配置回退本地模板
    if llm_available():
        ai_resume = generate_resume_ai(job, profile)
        if ai_resume and len(ai_resume.strip()) > 80:
            return ai_resume
    job_text = _text_of(job)
    top_skills = []
    for kws in SKILL_KEYWORDS.values():
        for k in kws:
            if k in _norm(job_text) and k not in top_skills:
                top_skills.append(k)
    top_skills = top_skills[:5]
    if not top_skills:
        top_skills = ["AI应用", "产品设计", "数据分析"]

    highlights = []
    for _, project in _pick_project_points(profile, job_text, limit=3):
        title = project.get("title", "")
        points = project.get("points", [])
        line = f"{title}：{points[0]}" if points else title
        if len(line) > 90:
            line = line[:90] + "…"
        highlights.append("· " + line)

    exp_lines = []
    for exp in profile.get("experiences", []):
        exp_lines.append(f"**{exp.get('title')} | {exp.get('company')}（{exp.get('period')}）**")
        for p in exp.get("points", []):
            exp_lines.append("· " + p)

    edu = profile.get("education", [{}])
    edu = edu[0] if edu else {}
    skills = profile.get("skills", {})
    langs = profile.get("languages", [])
    lang_line = "、".join(l.get("name", "") + "（" + l.get("level", "") + "）" for l in langs) if langs else "未填写"
    lines = [
        f"# {profile.get('name', '候选人')} · 个人简历",
        "",
        f"**求职意向：** {job.get('title')}（{job.get('company')}）",
        f"**状态：** {profile.get('status', '')}",
        f"**城市：** {profile.get('city', '')}",
        f"**语言：** {lang_line}",
        "",
        "## 核心优势",
        "",
        f"针对「{job.get('title')}」的定制摘要：具备{'、'.join(top_skills[:4])}的实践基础，"
        "能够把能力、数据意识与用户洞察转化为可落地的方案。",
        "",
        "## 项目经历",
        "",
    ]
    lines.extend(highlights or ["· 请先在档案中补充项目经历"])
    lines.extend(["", "## 实习/工作经历", ""])
    lines.extend(exp_lines or ["· 请先在档案中补充经历"])
    lines.extend(["", "## 教育背景", ""])
    if edu:
        lines.append(f"**{edu.get('degree', '')}** | {edu.get('school', '')}（{edu.get('period', '')}）")
        if edu.get("detail"):
            lines.append(f"· 核心课程/方向：{edu['detail']}")
    else:
        lines.append("· 请先在档案中补充教育背景")
    lines.extend(["", "## 专业技能", ""])
    lines.append(f"**主技能：** {'、'.join(skills.get('strong', []))}")
    lines.append(f"**辅助技能：** {'、'.join(skills.get('moderate', []))}")
    lines.extend(["", "## 证书与获奖", ""])
    lines.append("· " + "；".join(profile.get("certifications", [])) if profile.get("certifications") else "· 暂无")
    for award in profile.get("awards", []):
        lines.append("· " + award)
    return "\n".join(lines)


def generate_greeting_ai(job, profile):
    """AI 投递招呼语：像真人 IM，60-110 字，围绕 JD 独特点，不编造。失败返回 None。"""
    job_text = _text_of(job)[:3000]
    name = profile.get("name", "我")
    compact = {
        "姓名": name,
        "求职意向": profile.get("status", ""),
        "城市": profile.get("city", ""),
        "技能": (profile.get("skills") or {}).get("strong", [])[:5],
        "项目": [p.get("title", "") for p in profile.get("projects", [])][:3],
        "经历": [e.get("title", "") + "@" + e.get("company", "") for e in profile.get("experiences", [])][:3],
    }
    system = (
        "你是求职者，需要给目标公司的 HR 发送一条中文打招呼消息。"
        "硬性要求：像真人随手发出的 IM 消息（Boss直聘/微信风格），绝不是求职信或公文；"
        "字数 60-110，最多 3 个短句；围绕岗位 JD 中最独特、最具体的一点展开（技术栈/项目/方向均可），"
        "不复述职位名称或整段 JD；只使用我提供的档案信息，禁止编造经历、技能或头衔；"
        "语气自然、简短、有分寸，不要感叹号堆砌，不要问句轰炸。"
    )
    user = "【目标岗位】\n%s\n\n【我的真实背景】\n%s" % (job_text, json.dumps(compact, ensure_ascii=False))
    try:
        return llm_chat([{"role": "user", "content": user}], system=system)
    except Exception:
        return None


def generate_greeting(job, profile=None):
    profile = profile or {}
    if profile_is_empty(profile):
        return "请先在「个人资料」中填写姓名、技能与求职意向，才能生成投递招呼语。"
    if llm_available():
        ai = generate_greeting_ai(job, profile)
        if ai and len(ai.strip()) > 20:
            return ai.strip()
    name = profile.get("name", "我")
    return f"您好！我是{name}，看到贵司「{job.get('title', '')}」岗位很感兴趣，与我目前的实践方向比较契合，希望有机会进一步沟通，谢谢！"


# AI 服务商预设：下拉选择后自动填充 base_url 与推荐模型
AI_PROVIDER_PRESETS = {
    "custom": {"label": "自定义", "base_url": "", "model": ""},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "doubao": {"label": "豆包（火山方舟）", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-32k"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "kimi": {"label": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "zhipu": {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
}


def generate_follow_up(app, profile=None):
    """AI 生成跟进消息：像真人 IM，60-110 字。失败回退模板。"""
    profile = profile or {}
    name = profile.get("name", "我")
    if llm_available():
        compact = {
            "姓名": name,
            "岗位": app.get("title", ""),
            "公司": app.get("company", ""),
            "阶段": app.get("stage", ""),
            "已有沟通记录": (app.get("notes") or "")[:300],
        }
        system = (
            "你是求职者，需要给目标公司的 HR 发一条跟进消息（中文 IM 风格）。"
            "硬性要求：像真人随手发出的消息，不是公文；60-110 字，最多 3 个短句；"
            "围绕面试/投递进展自然询问，礼貌不催促；只使用提供的事实，禁止编造沟通内容；"
            "不要用'尊敬的'等书面称呼。"
        )
        try:
            content = llm_chat([{"role": "user", "content": "【申请记录】\n%s" % json.dumps(compact, ensure_ascii=False)}], system=system)
            if content and len(content.strip()) > 15:
                return content.strip()
        except Exception:
            pass
    return f"{name}您好，想跟进一下「{app.get('title', '')}」{app.get('stage', '')}的进展，有需要补充的材料随时告诉我，谢谢！"


def analyze_reply(reply, app=None, profile=None):
    """AI 分析 HR 回复：判断意向并给建议。失败回退关键词判断。"""
    if not reply or not reply.strip():
        return {"intent": "未知", "advice": "粘贴 HR 回复内容后分析。"}
    if llm_available():
        system = (
            "你是求职助手。分析 HR 回复判断招聘意向并给建议。输出严格 JSON（不要代码块）："
            '{"intent": "积极|中性|消极|待定", "advice": "30字内下一步建议"}。'
            "只基于回复文本判断，不臆测。"
        )
        try:
            text = llm_chat([{"role": "user", "content": "HR 回复：\n" + reply[:1500]}], system=system)
            if text:
                text = text.strip().strip("`")
                if text.startswith("json"):
                    text = text[4:]
                data = json.loads(text)
                intent = str(data.get("intent", "待定"))
                if intent not in ("积极", "中性", "消极", "待定"):
                    intent = "待定"
                return {"intent": intent, "advice": str(data.get("advice", ""))[:80]}
        except Exception:
            pass
    # 本地兜底
    pos = [k for k in ("面试", "约", "联系", "期待", "合适", "通过") if k in reply]
    neg = [k for k in ("暂不", "不合适", "已满", "停止", "抱歉") if k in reply]
    intent = "积极" if pos and not neg else "消极" if neg else "待定"
    return {"intent": intent, "advice": "结合回复内容安排下一步（面试准备/继续跟进/转向其他机会）。"}


def diagnose_system(user=None):
    """系统体检：AI 配置 / 数据库 / 档案 / 岗位库 / 看板，返回检查项列表。"""
    items = []
    # 1. AI 配置
    try:
        settings = load_settings()
        if not settings.get("enabled"):
            items.append({"name": "AI 增强", "status": "warn", "note": "未启用。本地功能不受影响；开启后可获得 AI 评分/简历定制。"})
        elif not settings.get("api_key") or not settings.get("base_url"):
            items.append({"name": "AI 增强", "status": "fail", "note": "已启用但缺少 API 地址或 Key，请在设置中补全。"})
        else:
            items.append({"name": "AI 增强", "status": "ok", "note": "已启用（%s / %s）" % (settings.get("base_url", ""), settings.get("model", ""))})
    except Exception as exc:
        items.append({"name": "AI 增强", "status": "fail", "note": "配置读取失败：" + str(exc)[:60]})
    # 2. 数据库
    try:
        with _DB_LOCK:
            conn = db()
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            conn.close()
        items.append({"name": "数据库", "status": "ok", "note": "可读写，岗位库 %d 条" % total})
    except Exception as exc:
        items.append({"name": "数据库", "status": "fail", "note": str(exc)[:60]})
    # 3. 个人档案
    try:
        if user:
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            if profile_is_empty(profile):
                items.append({"name": "个人档案", "status": "warn", "note": "档案为空，评分/定制无法生效。到简历库上传简历一键填充。"})
            else:
                missing = [k for k in ("name", "skills", "experiences") if not profile.get(k)]
                items.append({"name": "个人档案", "status": "warn" if missing else "ok", "note": ("缺：" + "、".join(missing)) if missing else "完整，评分/定制可正常使用。"})
    except Exception as exc:
        items.append({"name": "个人档案", "status": "warn", "note": "读取异常：" + str(exc)[:50]})
    # 4. 岗位来源
    try:
        with _DB_LOCK:
            conn = db()
            last = conn.execute("SELECT MAX(created_at) AS m FROM jobs").fetchone()["m"]
            conn.close()
        items.append({"name": "岗位来源", "status": "ok", "note": ("最近入库 %s，岗位库自动刷新每 24h 运行" % last) if last else "岗位库为空，去搜索或粘贴岗位链接"})
    except Exception as exc:
        items.append({"name": "岗位来源", "status": "fail", "note": str(exc)[:50]})
    # 5. 看板服务（8420）
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8420/dashboard.html", timeout=5) as resp:
            ok = resp.status == 200
        items.append({"name": "投递看板", "status": "ok" if ok else "warn", "note": "在线" if ok else "未启动（不影响主站）"})
    except Exception:
        items.append({"name": "投递看板", "status": "warn", "note": "未启动（可选组件，不影响主站）"})
    return items


def generate_cover_letter(job, profile=None):
    profile = profile or {}
    if profile_is_empty(profile):
        return "请先在「个人资料」中填写姓名、技能、经历与职业目标，才能生成定制求职信。"
    name = profile.get("name", "")
    job_text = _text_of(job)
    points = _pick_project_points(profile, job_text, limit=3)
    project_lines = []
    for _, project in points:
        title = project.get("title", "")
        pts = project.get("points", [])
        if pts:
            project_lines.append(f"- {title}：{pts[0]}")
    if len(project_lines) < 2:
        for exp in profile.get("experiences", []):
            if exp.get("points"):
                project_lines.append(f"- {exp.get('title')}（{exp.get('company')}）：{exp['points'][0]}")

    deadline = job.get("deadline", "")
    deadline_line = f"注意到贵司招聘截止时间为 {deadline}" if deadline else "看到贵司正在招聘"
    lines = [
        f"# {job.get('title')} · 求职信",
        "",
        f"**致：{job.get('company')} 招聘团队**",
        "",
        f"您好。我是{name}，正在寻找{job.get('title')}方向的机会。"
        f"{deadline_line}，结合我的实践经历，我希望申请这一岗位。",
        "",
        f"我认为自己能为「{job.get('title')}」带来以下价值：",
        "",
    ]
    lines.extend(project_lines[:3] or ["- 请先在档案中补充项目经历"])
    lines.extend(
        [
            "",
            "这些经历的共同点是：从真实需求出发，把能力、用户洞察与数据反馈组合成可验证的结果。"
            "我习惯快节奏、创新驱动的环境，乐于快速试错并持续迭代，也能用结构化表达与跨团队协作推动事情落地。",
            "",
            "如能获得面试机会，我将非常乐意进一步说明我的匹配点与成长计划。",
            "",
            "期待您的回复。",
            "",
            "此致",
            f"{name}",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 面试准备

TOUGH_QUESTIONS = [
    {
        "question": "请介绍一下你自己。",
        "answer": "建议用 1-2 分钟讲清：教育背景 → 最相关的经历 → 一项可量化成果 → 为什么想来这个岗位。控制在 3 个要点内，结尾用一句话收束到岗位。",
    },
    {
        "question": "你最大的优势是什么？",
        "answer": "结合岗位要求挑一个优势，用 STAR 讲一个具体案例，并说明它如何迁移到目标岗位。避免只报优点不给证据。",
    },
    {
        "question": "讲一个你遇到困难并解决的经历。",
        "answer": "用 STAR 结构：遇到什么问题 → 你承担了什么 → 具体做了什么 → 结果如何，并总结你学到的方法。",
    },
    {
        "question": "你为什么选择我们公司/这个岗位？",
        "answer": "提前研究公司业务、产品与团队。回答要具体：讲一个你认可的产品或方向，并把它和你自己的经历连接起来。",
    },
]

STAR_EXAMPLES = [
    {
        "name": "项目/产品经历（可替换为你的真实案例）",
        "star": "S：描述项目背景与要解决的问题。T：你负责的目标与职责。A：你采取的具体行动。R：可量化的结果（数据、反馈、奖项）。",
        "for": ["讲一个从0到1的项目", "你如何解决实际问题？", "你如何用数据优化产品？"],
    },
    {
        "name": "团队协作经历（可替换为你的真实案例）",
        "star": "S：团队与任务背景。T：你的分工。A：如何协作与推进。R：最终结果与你的角色价值。",
        "for": ["讲一个团队合作的经历", "你的领导风格是什么？", "你如何推动复杂项目？"],
    },
    {
        "name": "用户/客户沟通经历（可替换为你的真实案例）",
        "star": "S：面对的用户与需求场景。T：你要解决的问题。A：沟通方法。R：用户反馈或转化结果。",
        "for": ["讲一个你跟用户互动的经历", "你如何理解用户需求？"],
    },
]


def generate_interview_prep(job, profile=None):
    profile = profile or {}
    job_text = _text_of(job)
    role_questions = []
    if any(k in _norm(job_text) for k in ["游戏", "策划", "玩法", "系统"]):
        role_questions = [
            "请拆解一款你熟悉的游戏，说明它的核心循环与商业化设计。",
            "如果让你为这款游戏设计一个AI驱动的玩法，你会怎么做？",
            "如何用数据验证一个新系统的玩家体验与商业化效果？",
        ]
    elif any(k in _norm(job_text) for k in ["运营", "增长", "商业化", "活动"]):
        role_questions = [
            "请讲一个你策划并落地的增长或活动案例，数据结果如何？",
            "如何评估一个产品运营活动的效果？你会看哪些指标？",
            "如果用户活跃持续下滑，你的排查与应对思路是什么？",
        ]
    else:
        role_questions = [
            "请结合一个项目，说明你如何完成需求分析到上线的完整流程。",
            "你如何理解产品/运营与研发团队的协作方式？",
            "讲一个你发现用户痛点并用方案解决的案例。",
        ]

    lines = [
        f"# 面试准备 · {job.get('title')} · {job.get('company')}",
        "",
        f"**城市：** {job.get('city', '')} | **类型：** {job.get('posting_type', '')} | **薪资参考：** {job.get('salary', '未标注')}",
        "",
        "## 岗位要点",
        "",
    ]
    lines.append("· " + "；".join(job.get("requirements", [])[:5]))
    lines.extend(["", "## 高频问题与参考回答", ""])
    for i, item in enumerate(TOUGH_QUESTIONS, 1):
        lines.append(f"### {i}. {item['question']}")
        lines.append(item["answer"])
        lines.append("")
    lines.append("## 岗位相关追问")
    for i, q in enumerate(role_questions, 1):
        lines.append(f"{i}. {q}")
    lines.extend(["", "## STAR 素材库", ""])
    for i, item in enumerate(STAR_EXAMPLES, 1):
        lines.append(f"### {i}. {item['name']}")
        lines.append(item["star"])
        lines.append(f"适合回答：{'、'.join(item['for'])}")
        lines.append("")
    lines.append("## 你可以反问面试官")
    lines.extend(
        [
            "1. 这个岗位日常主要负责什么？团队如何分工？",
            "2. 入职后前6个月最重要的目标是什么？",
            "3. 团队目前面临的最大挑战是什么？",
            "4. 新人入职后的培养机制是怎样的？",
            "5. 团队使用哪些工具和技术栈？",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- LLM 增强

def llm_chat(messages, system=None):
    global _LLM_LAST_ERROR
    _LLM_LAST_ERROR = ""
    settings = load_settings()
    if not settings.get("enabled") or not settings.get("api_key") or not settings.get("base_url"):
        return None
    base = settings["base_url"].rstrip("/")
    url = base + "/chat/completions"
    payload = {
        "model": settings.get("model") or "deepseek-chat",
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "temperature": 0.6,
        "max_tokens": 1800,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + settings["api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(600).decode("utf-8", errors="replace")
        except Exception:
            pass
        # 模型名无权限/不存在是最常见的配置错误，给可执行提示
        if exc.code in (400, 401, 403) and re.search(r"model|模型", detail, re.I):
            _LLM_LAST_ERROR = ("HTTP %d：当前模型名无效或无访问权限（可用模型请点击设置里的『自动识别模型』）" % exc.code)
        else:
            _LLM_LAST_ERROR = "HTTP " + str(exc.code) + ("：" + detail[:220] if detail else "")
        return None
    except Exception as exc:
        _LLM_LAST_ERROR = str(exc)[:240]
        return None


def llm_probe():
    """真实探测 OpenAI 兼容服务，不返回密钥；只用于用户主动点击测试。"""
    settings = load_settings()
    if not settings.get("enabled") or not settings.get("api_key") or not settings.get("base_url"):
        return {"ok": False, "status": "未配置", "message": "请填写 API 地址、API Key，并开启 AI 增强。"}
    base = normalize_llm_base_url(settings.get("base_url"))
    if base != settings.get("base_url"):
        return {"ok": False, "status": "地址需要修正", "message": "当前地址像是网页或 Key 管理页面，不是模型 API。建议使用：" + base, "suggested_base_url": base}
    try:
        text = llm_chat([{"role": "user", "content": "只回复：连接成功"}], system="你是连接测试助手。")
        if text:
            return {"ok": True, "status": "已连接", "message": "模型已成功返回响应。", "model": settings.get("model") or "默认模型"}
        detail = _LLM_LAST_ERROR
        return {"ok": False, "status": "连接失败", "message": (detail + "。" if detail else "服务没有返回有效响应。") + "请检查 API 地址是否以 /v1 结尾、Key 是否有效、模型是否存在。"}
    except Exception as exc:
        return {"ok": False, "status": "连接失败", "message": str(exc)[:240]}


def llm_models():
    settings = load_settings()
    if not settings.get("enabled") or not settings.get("api_key") or not settings.get("base_url"):
        return {"ok": False, "status": "未配置", "message": "请先保存 API 地址和 Key。", "models": []}
    base = normalize_llm_base_url(settings.get("base_url"))
    if base != settings.get("base_url"):
        return {"ok": False, "status": "地址需要修正", "message": "请使用模型 API 地址，而不是网页地址。建议：" + base, "models": []}
    req = urllib.request.Request(base.rstrip("/") + "/models", headers={"Authorization": "Bearer " + settings["api_key"]}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = [str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")]
        return {"ok": True, "status": "已识别", "message": "已从服务端读取可用模型。", "models": models}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": "识别失败", "message": "模型列表请求返回 HTTP " + str(exc.code) + "，请检查 Key 和 API 地址。", "models": []}
    except Exception as exc:
        return {"ok": False, "status": "识别失败", "message": str(exc)[:240], "models": []}


def local_assistant(user_text, profile=None):
    profile = profile or {}
    text = _norm(user_text)
    name = profile.get("name") or "求职者"
    if profile_is_empty(profile):
        return (
            "欢迎使用 CareerPilot！请先到「个人资料」页填写你的姓名、技能、经历与职业目标，"
            "之后我才能为你评估岗位匹配度、生成定制简历与求职信。你也可以先浏览岗位池。"
        )
    if any(k in text for k in ["你是谁", "自我介绍", "介绍一下"]):
        return (
            f"我是 CareerPilot 求职助手，正在协助 {name} 管理求职。"
            "我可以基于你的档案完成岗位匹配评估、简历定制、求职信撰写、面试准备和进度管理。"
            "配置 OpenAI 兼容 API 后，我会切换到 AI 增强模式。"
        )
    if any(k in text for k in ["帮我找", "推荐岗位", "找岗位", "有哪些岗位"]):
        return "在「岗位搜索」页可以按匹配度浏览全部岗位；完善档案后评分会自动生效。"
    if any(k in text for k in ["怎么写简历", "简历", "定制简历"]):
        return "在「岗位搜索」打开任意岗位详情，点击“生成简历”即可得到按该岗位定制的 Markdown 简历。"
    if any(k in text for k in ["求职信", "cover"]):
        return "在岗位详情页点击“生成求职信”，系统会生成侧重“你能为对方解决什么”的中文求职信。"
    if any(k in text for k in ["面试", "准备面试"]):
        return "在「面试准备」页选择目标岗位，系统会生成高频问题、岗位追问、STAR 素材库和反问清单。"
    if any(k in text for k in ["进度", "申请状态", "管道"]):
        return "在「申请进度」页可以查看全部投递的看板状态，并随时更新为已投递、面试中、Offer 等阶段。"
    if any(k in text for k in ["hello", "你好", "hi"]):
        return f"你好，我是 {name} 的求职助手。你可以问我：帮我找岗位、如何写简历、准备面试、更新申请进度。"
    return (
        "我理解你的问题，但当前处于本地模式。你可以试试这些指令："
        "「帮我找岗位」「怎么写简历」「准备面试」「更新申请进度」。"
        "或者在设置中配置 OpenAI 兼容 API，开启 AI 增强对话。"
    )


def assistant_reply(user_text, profile=None):
    profile = profile or {}
    settings = load_settings()
    name = profile.get("name") or "用户"
    system = (
        f"你是 CareerPilot 求职助手，服务于求职者 {name}。"
        "回答使用简体中文，具体、诚实、有条理，不编造用户简历中不存在的经历。"
        "你可以推荐岗位搜索、评分解读、简历/求职信/面试准备建议，也可以回答职业规划问题。"
    )
    llm_text = llm_chat([{"role": "user", "content": user_text}], system=system) if settings.get("enabled") else None
    return {
        "content": llm_text if llm_text else local_assistant(user_text, profile),
        "mode": "llm" if llm_text else "local",
    }


# ---------------------------------------------------------------- HTTP 层

def _cookie_value(header, name):
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:].strip()
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "CareerPilotWeb/2.0"

    def log_message(self, fmt, *args):
        if os.environ.get("CAREERPILOT_QUIET") != "1":
            super().log_message(fmt, *args)

    def _request_meta(self):
        """请求级元数据：request_id + 计时起点。"""
        if not hasattr(self, "_start_ns"):
            rid = self.headers.get("X-Request-Id") or secrets.token_hex(6)
            self._request_id = rid
            self._start_ns = time.perf_counter_ns()
            self._user_id = None
        return self._request_id

    def _send(self, code, body, content_type="application/json; charset=utf-8", extra=None, set_cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        self._record_access(code)

    def _record_access(self, code):
        """请求结束：记录指标与结构化日志。"""
        try:
            start = getattr(self, "_start_ns", None)
            if start is None:
                return
            duration_ms = (time.perf_counter_ns() - start) / 1e6
            path = urllib.parse.urlparse(self.path).path
            from observability import record_request
            record_request(
                self.command or "GET",
                path,
                code,
                duration_ms,
                request_id=getattr(self, "_request_id", None),
                user_id=getattr(self, "_user_id", None),
            )
        except Exception:
            pass

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "请求体不是有效 JSON"})
            return None

    def _reject_oversized_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"ok": False, "error": "Content-Length 无效"})
            return True
        content_type = self.headers.get("Content-Type", "").lower()
        if "multipart/form-data" not in content_type and length > MAX_JSON_BODY_BYTES:
            self._send(413, {"ok": False, "error": "请求体超过 1MB 上限"})
            return True
        return False

    def _multipart_file(self):
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 10 * 1024 * 1024 + 1024:
            return None, 413, "文件超过 10MB 上限"
        fields = _parse_multipart(content_type, self.rfile.read(length))
        parts = fields.get("file") or []
        part = parts[0] if parts else None
        filename = part.get_filename() if part is not None else None
        if part is None or not filename:
            return None, 400, "缺少 file 字段"
        return _UploadedFile(filename, part.get_payload(decode=True) or b""), 200, ""

    def _multipart_files(self):
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 50 * 1024 * 1024 + 4096:
            return None, 413, "文件总大小超过 50MB 上限"
        fields = _parse_multipart(content_type, self.rfile.read(length))
        parts = fields.get("files") or fields.get("file") or []
        uploaded = [
            _UploadedFile(p.get_filename() or "", p.get_payload(decode=True) or b"")
            for p in parts
            if p.get_filename()
        ]
        if not uploaded:
            return None, 400, "缺少 file 字段"
        return uploaded, 200, ""

    def _build_import_plan(self, user, text):
        """识别简历文本并构建合并计划；同时落一条 pending 草稿。"""
        current = _safe_json(user.get("profile_json"), {})
        result = extract_profile_from_resume(text, user["id"])
        plan = build_merge_plan(current, result["extracted"], result["confidence"])
        sources = result.get("source_text", {})
        for group in ("fills", "updates"):
            for item in plan.get(group, []):
                path = item["field_path"]
                if path in sources:
                    item["source_text"] = sources[path]
        plan["unrecognized"] = result.get("unrecognized", [])
        plan["summary"] = result.get("summary", {})
        if result.get("fallback"):
            plan["fallback"] = True
        with _DB_LOCK:
            conn = db()
            conn.execute(
                "UPDATE resume_import_drafts SET status='discarded' WHERE user_id=? AND status='pending'",
                (user["id"],),
            )
            conn.execute(
                "INSERT INTO resume_import_drafts (user_id, merge_plan_json, status) VALUES (?,?, 'pending')",
                (user["id"], json.dumps(plan, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
        return plan

    def _api_resume_import(self, method, parts, user):
        current = normalize_profile(_safe_json(user.get("profile_json"), {}))

        if method == "GET" and (not parts or parts[0] == "draft"):
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM resume_import_drafts WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()
                conn.close()
            if not row:
                self._send(200, {"ok": True, "data": None})
                return
            self._send(200, {"ok": True, "data": _safe_json(row["merge_plan_json"], {})})
            return

        if method == "POST" and parts and parts[0] == "apply":
            body = self._json_body()
            paths = body.get("accepted_field_paths", [])
            if not isinstance(paths, list):
                self._send(400, {"ok": False, "error": "accepted_field_paths 必须是数组"})
                return
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM resume_import_drafts WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()
                if not row:
                    conn.close()
                    self._send(404, {"ok": False, "error": "没有待确认的导入草稿"})
                    return
                plan = _safe_json(row["merge_plan_json"], {})
                new_profile, applied = apply_paths(current, plan, paths)
                conn.execute(
                    "UPDATE users SET profile_json=?, updated_at=datetime('now', 'localtime') WHERE id=?",
                    (json.dumps(new_profile, ensure_ascii=False), user["id"]),
                )
                conn.execute("DELETE FROM evaluations WHERE user_id=?", (user["id"],))
                conn.execute(
                    "UPDATE resume_import_drafts SET status='applied', updated_at=datetime('now', 'localtime') WHERE id=?",
                    (row["id"],),
                )
                conn.commit()
                conn.close()
            self._send(200, {"ok": True, "data": {"profile": new_profile, "applied": applied}})
            return

        if method == "POST" and (not parts or parts[0] == "upload"):
            field, code, msg = self._multipart_file()
            if code != 200:
                self._send(code, {"ok": False, "error": msg})
                return
            filename = getattr(field, "filename", "") or ""
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".pdf", ".docx", ".txt", ".md"):
                self._send(400, {"ok": False, "error": "不支持的文件类型，仅支持 PDF/DOCX/TXT/MD"})
                return
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="resume_")
            try:
                data = field.file.read(10 * 1024 * 1024 + 1)
                if len(data) > 10 * 1024 * 1024:
                    self._send(413, {"ok": False, "error": "文件超过 10MB 上限"})
                    return
                tmp.write(data)
                tmp.close()
                parsed = extract_resume_text(tmp.name)
                if parsed.get("warning"):
                    self._send(400, {"ok": False, "error": parsed["warning"]})
                    return
                plan = self._build_import_plan(user, parsed["text"])
                self._send(200, {"ok": True, "data": plan})
            except ValueError as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send(502, {"ok": False, "error": "简历解析失败：" + str(exc)})
            finally:
                try:
                    tmp.close()
                except Exception:
                    pass
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
            return

        if method == "POST" and parts and parts[0] == "text":
            body = self._json_body()
            text = (body.get("text") or "").strip()
            if len(text) < 50:
                self._send(400, {"ok": False, "error": "粘贴的文本过短，请粘贴完整简历内容（至少 50 字）"})
                return
            if len(text) > 30000:
                self._send(413, {"ok": False, "error": "文本超过 3 万字符上限"})
                return
            plan = self._build_import_plan(user, text)
            # 同时把粘贴文本存为简历文件，让它在"我的简历"里可管理/删除/下载
            try:
                user_dir = RESUME_DIR / str(user["id"])
                user_dir.mkdir(parents=True, exist_ok=True)
                stored_name = "paste_%s_%d.txt" % (time.strftime("%Y%m%d_%H%M%S"), user["id"])
                (user_dir / stored_name).write_text(text, encoding="utf-8")
                with _DB_LOCK:
                    conn = db()
                    cur = conn.execute(
                        "INSERT INTO resumes (user_id, filename, stored_name, size) VALUES (?,?,?,?)",
                        (user["id"], "粘贴简历_%s.txt" % time.strftime("%Y-%m-%d"), stored_name, len(text.encode("utf-8"))),
                    )
                    conn.commit()
                    conn.close()
                plan["resume_id"] = cur.lastrowid
            except Exception:
                pass
            self._send(200, {"ok": True, "data": plan})
            return

        self._send(404, {"ok": False, "error": "not found"})

    def _api_resumes(self, method, parts, user):
        if method == "GET" and not parts:
            with _DB_LOCK:
                conn = db()
                rows = conn.execute(
                    "SELECT id, filename, size, created_at FROM resumes WHERE user_id=? ORDER BY id DESC",
                    (user["id"],),
                ).fetchall()
                conn.close()
            self._send(200, {"ok": True, "data": [dict(r) for r in rows]})
            return

        if method == "POST" and parts and parts[0] == "upload":
            fields, code, msg = self._multipart_files()
            if code != 200:
                self._send(code, {"ok": False, "error": msg})
                return
            current = normalize_profile(_safe_json(user.get("profile_json"), {}))
            items = []
            plans = []
            errors = []
            for field in fields:
                filename = getattr(field, "filename", "") or ""
                ext = os.path.splitext(filename)[1].lower()
                if ext not in (".pdf", ".docx", ".txt", ".md"):
                    errors.append({"filename": filename, "error": "不支持的文件类型，仅支持 PDF/DOCX/TXT/MD"})
                    continue
                data = field.file.read(10 * 1024 * 1024 + 1)
                if len(data) > 10 * 1024 * 1024:
                    errors.append({"filename": filename, "error": "文件超过 10MB 上限"})
                    continue
                user_dir = RESUME_DIR / str(user["id"])
                user_dir.mkdir(parents=True, exist_ok=True)
                stored_name = secrets.token_hex(8) + ext
                target = user_dir / stored_name
                target.write_bytes(data)
                saved = False
                try:
                    parsed = extract_resume_text(target)
                    if parsed.get("warning"):
                        raise ValueError(parsed["warning"])
                    result = extract_profile_from_resume(parsed["text"], user["id"])
                    plan = build_merge_plan(current, result["extracted"], result["confidence"])
                    sources = result.get("source_text", {})
                    for group in ("fills", "updates"):
                        for item in plan.get(group, []):
                            path = item["field_path"]
                            if path in sources:
                                item["source_text"] = sources[path]
                    plan["unrecognized"] = result.get("unrecognized", [])
                    plan["summary"] = result.get("summary", {})
                    with _DB_LOCK:
                        conn = db()
                        cur = conn.execute(
                            "INSERT INTO resumes (user_id, filename, stored_name, size) VALUES (?,?,?,?)",
                            (user["id"], filename, stored_name, len(data)),
                        )
                        conn.commit()
                        conn.close()
                    items.append({"id": cur.lastrowid, "filename": filename, "size": len(data)})
                    plans.append(plan)
                    saved = True
                except ValueError as exc:
                    errors.append({"filename": filename, "error": str(exc)})
                except Exception as exc:
                    errors.append({"filename": filename, "error": "简历解析失败：" + str(exc)})
                finally:
                    if not saved:
                        try:
                            if target.exists():
                                target.unlink()
                        except Exception:
                            pass
            if not items:
                err = errors[0]["error"] if errors else "没有可导入的简历"
                self._send(400, {"ok": False, "error": err})
                return
            plan = plans[-1] if plans else None
            if plan is not None:
                with _DB_LOCK:
                    conn = db()
                    conn.execute(
                        "UPDATE resume_import_drafts SET status='discarded' WHERE user_id=? AND status='pending'",
                        (user["id"],),
                    )
                    conn.execute(
                        "INSERT INTO resume_import_drafts (user_id, merge_plan_json, status) VALUES (?,?, 'pending')",
                        (user["id"], json.dumps(plan, ensure_ascii=False)),
                    )
                    conn.commit()
                    conn.close()
            with _DB_LOCK:
                conn = db()
                rows = conn.execute(
                    "SELECT id, filename, size, created_at FROM resumes WHERE user_id=? ORDER BY id DESC",
                    (user["id"],),
                ).fetchall()
                conn.close()
            self._send(200, {
                "ok": True,
                "data": {
                    "items": items,
                    "plan": plan,
                    "resumes": [dict(r) for r in rows],
                    "errors": errors,
                },
            })
            return

        if method == "DELETE" and len(parts) == 1 and parts[0].isdigit():
            resume_id = int(parts[0])
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM resumes WHERE id=? AND user_id=?", (resume_id, user["id"])
                ).fetchone()
                if row:
                    conn.execute("DELETE FROM resumes WHERE id=?", (resume_id,))
                    conn.commit()
                conn.close()
            if not row:
                self._send(404, {"ok": False, "error": "简历不存在"})
                return
            try:
                (RESUME_DIR / str(user["id"]) / row["stored_name"]).unlink(missing_ok=True)
            except Exception:
                pass
            self._send(200, {"ok": True})
            return

        if method == "GET" and len(parts) == 2 and parts[0].isdigit() and parts[1] == "download":
            resume_id = int(parts[0])
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM resumes WHERE id=? AND user_id=?", (resume_id, user["id"])
                ).fetchone()
                conn.close()
            if not row:
                self._send(404, {"ok": False, "error": "简历不存在"})
                return
            target = RESUME_DIR / str(user["id"]) / row["stored_name"]
            if not target.exists():
                self._send(404, {"ok": False, "error": "文件已被删除"})
                return
            quoted = urllib.parse.quote(row["filename"])
            self._send(
                200,
                target.read_bytes(),
                content_type="application/octet-stream",
                extra={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
            )
            return

        self._send(404, {"ok": False, "error": "not found"})

    def _session_token(self):
        return _cookie_value(self.headers.get("Cookie"), SESSION_COOKIE)

    def _current_user(self):
        token=self._session_token(); user=get_user_by_token(token)
        if user:
            self._session_expires_at=touch_session(token)
            self._user_id = user["id"]
        return user

    def _session_cookie(self, token, remember=True):
        prefix=self.headers.get("X-Forwarded-Prefix", "/").rstrip("/") or "/"; same_site=os.environ.get("CAREERPILOT_COOKIE_SAMESITE","Lax"); secure=same_site.lower()=="none" or self.headers.get("X-Forwarded-Proto","").lower()=="https"
        return (
            f"{SESSION_COOKIE}={token}; Path={prefix}; HttpOnly; SameSite={same_site}{'; Secure' if secure else ''}{'; Max-Age=2592000' if remember else ''}"
        )

    def _clear_cookie(self):
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

    def _serve_static(self, path):
        rel = urllib.parse.unquote(path.lstrip("/"))
        if not rel:
            rel = "index.html"
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._send(403, {"ok": False, "error": "forbidden"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            self._send(404, {"ok": False, "error": "not found"})
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), content_type=content_type, extra={"Cache-Control": "no-cache"})

    def _api(self, method, parts):
        if not parts:
            self._send(404, {"ok": False, "error": "not found"})
            return
        head = parts[0]

        if head == "health":
            self._send(200, {"ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            return

        if head == "auth":
            return self._api_auth(method, parts[1:])

        user = self._current_user()
        if not user:
            self._send(401, {"ok": False, "error": "未登录或会话已过期"})
            return

        if head == "profile" and method == "GET":
            self._send(200, _safe_json(user.get("profile_json"), {}))
            return
        if head == "profile" and method == "PUT":
            body = self._json_body()
            if body is None:
                return
            try:
                patch = validate_profile_patch(body)
            except ValueError as exc:
                self._send(400, {"ok": False, "error": str(exc)})
                return
            current = normalize_profile(_safe_json(user.get("profile_json"), {}))
            current.update(patch)
            update_user_profile(user["id"], current)
            user = get_user_by_token(self._session_token())
            self._send(200, _safe_json(user.get("profile_json"), {}))
            return

        if head == "daily-recommendations" and method == "GET":
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            keywords = " ".join([profile.get("target_role", "")] + profile.get("target_sectors", []) + profile.get("target_roles", [])) .strip()
            if not keywords:
                self._send(200, {"ok": True, "data": [], "message": "先完成求职偏好问答"}); return
            results = search_jobs({"keywords": keywords, "city": profile.get("target_city", ""), "limit": 20}, load_settings())
            scored = []
            for item in results:
                if item.get("source") == "llm_suggested": continue
                try:
                    job = get_job(add_job(item)); ev = score_job(job, profile); save_evaluation(user["id"], ev)
                    scored.append({**job, "evaluation": ev})
                except Exception: continue
            scored.sort(key=lambda x: (x.get("evaluation") or {}).get("overall", 0), reverse=True)
            self._send(200, {"ok": True, "data": scored[:2], "generated_at": time.strftime("%Y-%m-%d")}); return

        if head == "admin":
            if not is_admin(user):
                self._send(403, {"ok": False, "error": "需要管理员权限"})
                return
            if len(parts) == 1:
                self._send(400, {"ok": False, "error": "缺少管理子路径"})
                return
            sub = parts[1]
            if sub == "overview" and method == "GET":
                self._send(200, {"ok": True, "data": admin_overview()})
                return
            if sub == "funnel" and method == "GET":
                self._send(200, {"ok": True, "funnel": funnel_stats()})
                return
            if sub == "audit" and method == "GET":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = int((qs.get("limit") or ["100"])[0])
                action = (qs.get("action") or [None])[0]
                rows = list_audit(limit=limit, action=action)
                audit("audit.view", user_id=user["id"], ip=self.client_address[0])
                self._send(200, {"ok": True, "data": rows})
                return
            if sub == "users" and method == "GET":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = min(max(int(qs.get("limit", ["20"])[0] or 20), 1), 100)
                offset = max(int(qs.get("offset", ["0"])[0] or 0), 0)
                query = (qs.get("q") or [""])[0][:80]
                self._send(200, {"ok": True, "data": list_users(limit, offset, query)})
                return
            if sub == "users" and len(parts) >= 3 and method == "PATCH":
                target_id = parts[2]
                target = get_user_by_id(target_id)
                if not target:
                    self._send(404, {"ok": False, "error": "用户不存在"})
                    return
                if str(target_id) == str(user["id"]):
                    self._send(400, {"ok": False, "error": "不能对自己执行该操作"})
                    return
                body = self._json_body() or {}
                action = body.get("action")
                if action == "disable":
                    set_user_disabled(target_id, True)
                elif action == "enable":
                    set_user_disabled(target_id, False)
                elif action == "set_admin":
                    set_user_role(target_id, "admin")
                elif action == "remove_admin":
                    set_user_role(target_id, "user")
                else:
                    self._send(400, {"ok": False, "error": "未知操作"})
                    return
                record_admin_action(user["id"], action, int(target_id), "target=%s" % target.get("username"))
                self._send(200, {"ok": True})
                return
            self._send(404, {"ok": False, "error": "not found"})
            return

        if head == "funnel" and method == "GET":
            self._send(200, {"ok": True, "funnel": funnel_stats(user["id"])})
            return

        if head == "sources" and len(parts) >= 2 and parts[1] == "health" and method == "GET":
            from http_client import health_snapshot
            self._send(200, {"ok": True, "sources": health_snapshot()})
            return

        if head == "teams" and method == "POST":
            body = self._json_body() or {}
            name = (body.get("name") or "").strip()
            if not name:
                self._send(400, {"ok": False, "error": "团队名称不能为空"})
                return
            team = create_team(user["id"], name)
            self._send(200, {"ok": True, "team": team})
            return

        if head == "teams" and method == "GET":
            self._send(200, {"ok": True, "teams": list_my_teams(user["id"])})
            return

        if head == "teams" and len(parts) >= 2 and parts[1] == "join" and method == "POST":
            body = self._json_body() or {}
            team, err = join_team(user["id"], body.get("invite_code") or "")
            if err:
                self._send(404 if err == "邀请码无效" else 400, {"ok": False, "error": err})
                return
            self._send(200, {"ok": True, "team": team})
            return

        if head == "teams" and len(parts) >= 3 and parts[2] == "members" and method == "GET":
            if not is_team_member(parts[1], user["id"]):
                self._send(403, {"ok": False, "error": "非团队成员无权查看"})
                return
            self._send(200, {"ok": True, "members": list_team_members(parts[1])})
            return

        if head == "teams" and len(parts) >= 3 and parts[2] == "leave" and method == "POST":
            ok, err = leave_team(user["id"], parts[1])
            if not ok:
                self._send(400, {"ok": False, "error": err})
                return
            self._send(200, {"ok": True})
            return

        if head == "export" and method == "GET":
            data = export_user_data(user["id"])
            if data is None:
                self._send(404, {"ok": False, "error": "用户不存在"})
                return
            self._send(200, {"ok": True, "data": data})
            return

        if head == "today-tasks" and method == "GET":
            self._send(200, {"ok": True, "data": today_tasks(user["id"])})
            return

        if head == "notifications" and method == "GET":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = min(max(int((qs.get("limit") or ["20"])[0]), 1), 50)
            self._send(200, {"ok": True, "data": list_notifications(user["id"], limit), "unread": unread_count(user["id"])})
            return
        if head == "notifications" and len(parts) >= 2 and parts[1] == "read-all" and method == "POST":
            mark_all_notifications_read(user["id"])
            self._send(200, {"ok": True, "unread": 0})
            return
        if head == "notifications" and len(parts) >= 3 and parts[2] == "read" and method == "POST":
            mark_notification_read(user["id"], parts[1])
            self._send(200, {"ok": True, "unread": unread_count(user["id"])})
            return

        if head == "profile" and len(parts) >= 2 and parts[1] == "resume-import":
            return self._api_resume_import(method, parts[2:], user)

        if head == "resumes":
            return self._api_resumes(method, parts[1:], user)

        if head == "forms" and len(parts)>1 and parts[1]=="extract" and method=="POST":
            body=self._json_body(); self._send(200,{"ok":True,"data":extract_form(body.get("html",""),body.get("source_url", ""))}); return
        if head == "forms" and len(parts)>1 and parts[1]=="templates" and method=="GET":
            self._send(200,{"ok":True,"data":json.loads(_utf8(ROOT / "form_field_templates.json"))}); return
        if head == "forms" and len(parts)>1 and parts[1]=="fill-plan" and method=="POST":
            body=self._json_body();
            if body is None: return
            self._send(200,{"ok":True,"data":build_fill_plan(body.get("form_id",""),body.get("fields",[]),body.get("profile") or normalize_profile(_safe_json(user.get("profile_json"), {}))) }); return

        if head == "settings" and method == "GET":
            settings = load_settings()
            settings.pop("api_key", None)
            settings["has_key"] = bool(load_settings().get("api_key"))
            settings["providers"] = AI_PROVIDER_PRESETS
            self._send(200, settings)
            return
        if head == "settings" and method == "PUT":
            body = self._json_body()
            if not isinstance(body, dict):
                self._send(400, {"ok": False, "error": "设置数据格式不正确"})
                return
            settings = load_settings()
            # api_key 语义：非空 = 替换；空 = 保留原值（前端留空不更新）
            if body.get("api_key"):
                settings["api_key"] = body["api_key"]
            settings["base_url"] = normalize_llm_base_url(body.get("base_url", settings["base_url"]))
            settings["model"] = body.get("model", settings["model"])
            settings["enabled"] = bool(body.get("enabled", settings["enabled"]))
            save_settings(settings)
            public = dict(settings)
            public.pop("api_key", None)
            public["has_key"] = bool(settings.get("api_key"))
            audit("settings.update", user_id=user["id"], ip=self.client_address[0], meta={"keys": [k for k in body.keys() if k != "api_key"]})
            self._send(200, public)
            return
        if head == "settings" and len(parts) >= 2 and parts[1] == "test" and method == "POST":
            result = llm_probe()
            self._send(200, {"ok": True, "data": result})
            return
        if head == "settings" and len(parts) >= 2 and parts[1] == "models" and method == "POST":
            self._send(200, {"ok": True, "data": llm_models()})
            return

        if head == "jobs" and len(parts) == 1 and method == "GET":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            limit = None
            offset = 0
            try:
                if "limit" in query:
                    limit = min(max(int(query["limit"][0]), 1), 100)
                offset = max(int(query.get("offset", [0])[0]), 0)
            except (TypeError, ValueError):
                self._send(400, {"ok": False, "error": "limit/offset 必须是整数"})
                return
            all_jobs = list_jobs()
            facets = job_facets(all_jobs)
            jobs = filter_jobs(all_jobs, query)
            results = []
            for job in jobs:
                ev = get_evaluation(user["id"], job["id"])
                if not ev:
                    ev = score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})))
                    save_evaluation(user["id"], ev)
                results.append({**job, "evaluation": ev})
            sort = query.get("sort", ["new"])[0]
            if sort == "deadline":
                results.sort(key=lambda job: job.get("deadline") or "9999-12-31")
            elif sort == "new":
                results.sort(key=lambda job: (job.get("created_at") or "", job.get("id") or ""), reverse=True)
            else:
                results.sort(key=lambda job: (job.get("evaluation") or {}).get("overall", 0), reverse=True)
            total = len(results)
            if limit is not None:
                results = results[offset:offset + limit]
            self._send(200, {"jobs": results, "total": total, "facets": facets} if limit is not None else results)
            return

        if head == "jobs" and len(parts) == 2 and parts[1] == "adapters" and method == "GET":
            self._send(200, {"ok": True, "data": list(BUILTIN_ATS_ADAPTERS)})
            return

        if head=="jobs" and len(parts)>=3 and parts[1]=="search" and parts[2]=="company" and method=="POST":
            body=self._json_body(); company=(body.get("company") or "").strip(); s=load_settings()
            enabled=bool(s.get("enabled") and s.get("api_key") and s.get("base_url"))
            if not company: self._send(400,{"ok":False,"error":"缺少公司名"}); return
            if not enabled: self._send(200,{"ok":True,"data":[],"skipped":[],"mode":"local","hint":"请在设置中开启 AI 模式以使用联网搜索"}); return
            city=(body.get("city") or "").strip(); limit=min(max(int(body.get("limit",8)),1),10)
            from cache import db_cache
            cache_key = "company_search:" + json.dumps({"company": company, "city": city, "limit": limit}, ensure_ascii=False, sort_keys=True)
            cached_hit = db_cache.get(cache_key)
            if cached_hit is not None:
                try:
                    result = json.loads(cached_hit)
                    result["cached"] = True
                    output = []
                    profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
                    for item in result.get("jobs", []):
                        try:
                            job = get_job(add_job(item)); ev = score_job(job, profile); save_evaluation(user["id"], ev)
                            output.append({**job, "evaluation": ev})
                        except Exception: continue
                    output = decorate_search_results(output)
                    record_event(user["id"], "job_searched", {"scope": "company", "company": company[:60]})
                    self._send(200, {"ok": True, "data": output, "skipped": result.get("skipped", []), "cached": True, "sources": search_source_health(output), "hint": "已从真实招聘页面抓取岗位；结果按信息完整度标记，投递前仍建议人工核实链接。"})
                    return
                except Exception:
                    pass
            try:
                result=search_company_jobs(company,city,limit)
            except Exception as exc:
                self._send(500,{"ok":False,"error":"联网搜索失败："+str(exc)[:160]}); return
            db_cache.set(cache_key, json.dumps(result, ensure_ascii=False), ttl_seconds=3600)
            profile=normalize_profile(_safe_json(user.get("profile_json"), {})); output=[]
            for item in result.get("jobs",[]):
                try:
                    job=get_job(add_job(item)); ev=score_job(job,profile); save_evaluation(user["id"],ev)
                    output.append({**job,"evaluation":ev})
                except Exception: continue
            output = decorate_search_results(output)
            record_event(user["id"], "job_searched", {"scope": "company", "company": company[:60]})
            self._send(200,{"ok":True,"data":output,"skipped":result.get("skipped",[]),"cached":bool(result.get("cached")),"sources":search_source_health(output),"hint":"已从真实招聘页面抓取岗位；结果按信息完整度标记，投递前仍建议人工核实链接。"}); return

        if head=="jobs" and len(parts)==2 and parts[1]=="search" and method=="GET":
            s=load_settings(); self._send(200,{"ok":True,"data":{"enabled":bool(s.get("enabled") and s.get("api_key") and s.get("base_url")),"provider":s.get("provider","custom"),"model":s.get("model",""),"has_key":bool(s.get("api_key"))}}); return
        if head=="jobs" and len(parts)==2 and parts[1]=="search" and method=="POST":
            query=self._json_body(); s=load_settings(); enabled=bool(s.get("enabled") and s.get("api_key") and s.get("base_url"))
            from cache import db_cache
            cache_key = "search_degrade:" + json.dumps({k: query.get(k) for k in ("keywords", "city", "limit")}, ensure_ascii=False, sort_keys=True)
            try:
                results=search_jobs(query,s)
            except Exception as exc:
                # 源故障降级：读上次成功缓存
                stale = db_cache.get(cache_key)
                if stale is not None:
                    try:
                        cached_results = json.loads(stale)
                        self._send(200, {"ok": True, "data": mark_saved_search_results(cached_results), "local_results": [], "mode": "degraded", "degraded": True, "stale": True, "hint": "外部数据源暂时不可用，已返回上次搜索结果。"})
                        return
                    except Exception:
                        pass
                self._send(502, {"ok": False, "error": "搜索服务暂时不可用：" + str(exc)[:120]})
                return
            db_cache.set(cache_key, json.dumps(results, ensure_ascii=False), ttl_seconds=3600)
            if not enabled: self._send(200,{"ok":True,"data":[],"local_results":mark_saved_search_results(results),"mode":"local","hint":"请在设置中开启 AI 模式以使用联网搜索"}); return
            profile=normalize_profile(_safe_json(user.get("profile_json"), {})); output=[]
            for item in results:
                if item.get("source")=="local": continue
                job=get_job(add_job(item)); ev=score_job(job,profile); save_evaluation(user["id"],ev); output.append({**job,"evaluation":ev})
            decorated = decorate_search_results(output)
            record_event(user["id"], "job_searched", {"scope": "keywords", "keywords": str((query or {}).get("keywords", ""))[:60]})
            self._send(200,{"ok":True,"data":mark_saved_search_results(decorated),"local_results":mark_saved_search_results([item for item in results if item.get("source")=="local"]),"mode":"llm","sources":search_source_health(results),"hint":"结果已按来源、信息完整度和发布时间标记；AI 建议岗位仍需打开原帖核实。"}); return

        if head == "jobs" and len(parts) >= 2 and parts[1] == "parse" and method == "POST":
            body = self._json_body()
            url = (body.get("url") or "").strip()
            if not url:
                self._send(400, {"ok": False, "error": "缺少岗位链接"})
                return
            try:
                parsed = extract_job_from_url(url)
                if not parsed.get("title"):
                    parsed["title"] = "已解析岗位"
                job_id = add_job({
                    "title": parsed.get("title", "已解析岗位"),
                    "company": parsed.get("company") or "未知公司",
                    "city": parsed.get("city", ""),
                    "posting_type": parsed.get("posting_type", "未知"),
                    "work_type": parsed.get("work_type", "未知"),
                    "salary": parsed.get("salary", ""),
                    "deadline": parsed.get("deadline", ""),
                    "tags": list(dict.fromkeys(["URL解析"] + (parsed.get("tags") or []))),
                    "url": url,
                    "description": parsed.get("description", ""),
                    "requirements": parsed.get("requirements", []),
                    "source": "URL解析",
                })
                job = get_job(job_id)
                ev = score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})), deep=True)
                save_evaluation(user["id"], ev)
                self._send(200, {"ok": True, "data": {**job, "evaluation": ev}})
            except ValueError as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send(502, {"ok": False, "error": "网页解析失败：" + str(exc)})
            return

        if head == "jobs" and method == "POST":
            body = self._json_body()
            if not body.get("title"):
                self._send(400, {"ok": False, "error": "缺少岗位名称"})
                return
            job_id = add_job(body)
            job = get_job(job_id)
            ev = score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})), deep=True)
            save_evaluation(user["id"], ev)
            self._send(200, {**job, "evaluation": ev})
            return

        if len(parts) >= 2 and head == "jobs":
            job_id = parts[1]
            job = get_job(job_id)
            if not job:
                self._send(404, {"ok": False, "error": "岗位不存在"})
                return
            if method == "GET":
                ev = get_evaluation(user["id"], job_id) or score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})), deep=True)
                notify_deadline_if_needed(user["id"], job)
                self._send(200, {**job, "evaluation": ev})
                return
            if method == "DELETE":
                with _DB_LOCK:
                    conn = db()
                    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                    conn.execute("DELETE FROM evaluations WHERE job_id=?", (job_id,))
                    conn.execute("DELETE FROM applications WHERE job_id=?", (job_id,))
                    conn.commit()
                    conn.close()
                self._send(200, {"ok": True})
                return

        if head == "tasks" and method == "POST" and len(parts) == 1:
            body = self._json_body() or {}
            task_type = body.get("task_type")
            if task_type not in VALID_TASK_TYPES:
                self._send(400, {"ok": False, "error": "不支持的 task_type：%s" % task_type})
                return
            payload = dict(body.get("input") or {})
            if task_type in ("resume.generate", "cover_letter.generate", "interview.generate"):
                payload.setdefault("profile", normalize_profile(_safe_json(user.get("profile_json"), {})))
            task = create_task(user["id"], task_type, payload)
            record_event(user["id"], "task_submitted", {"task_type": task_type, "task_id": task["id"]})
            self._send(200, {"ok": True, "task": task})
            return

        if head == "tasks" and method == "GET" and len(parts) == 1:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            status = (qs.get("status") or [None])[0]
            limit = int((qs.get("limit") or ["50"])[0])
            tasks = list_tasks(user_id=user["id"], status=status, limit=limit)
            self._send(200, {"ok": True, "tasks": tasks})
            return

        if head == "tasks" and len(parts) >= 2:
            task_id = parts[1]
            if method == "POST" and len(parts) >= 3 and parts[2] == "retry":
                ok = retry_task(task_id, user_id=user["id"])
                if not ok:
                    self._send(404, {"ok": False, "error": "任务不存在或不可重试"})
                    return
                self._send(200, {"ok": True, "task": get_task(task_id, user_id=user["id"])})
                return
            if method == "GET":
                task = get_task(task_id, user_id=user["id"])
                if not task:
                    self._send(404, {"ok": False, "error": "任务不存在"})
                    return
                self._send(200, {"ok": True, "task": task})
                return
            self._send(405, {"ok": False, "error": "method not allowed"})
            return

        if head == "documents" and method == "POST":
            body = self._json_body()
            job_id = body.get("job_id")
            kind = body.get("kind")
            job = get_job(job_id) if job_id else None
            if not job:
                self._send(404, {"ok": False, "error": "岗位不存在"})
                return
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            if kind == "resume":
                content = generate_resume(job, profile)
            elif kind == "cover_letter":
                content = generate_cover_letter(job, profile)
            elif kind == "greeting":
                content = generate_greeting(job, profile)
            else:
                self._send(400, {"ok": False, "error": "kind 必须是 resume、cover_letter 或 greeting"})
                return
            with _DB_LOCK:
                conn = db()
                conn.execute(
                    "INSERT INTO documents (user_id, job_id, kind, content, created_at) VALUES (?,?,?,?,?)",
                    (user["id"], job_id, kind, content, time.strftime("%Y-%m-%d %H:%M")),
                )
                conn.commit()
                doc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                conn.close()
            self._send(200, {"id": doc_id, "job_id": job_id, "kind": kind, "content": content})
            return

        if head == "documents" and len(parts) >= 3 and parts[1] in ("download", "pdf"):
            try:
                doc_id = int(parts[2])
            except ValueError:
                self._send(400, {"ok": False, "error": "bad id"})
                return
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM documents WHERE id=? AND user_id=?",
                    (doc_id, user["id"]),
                ).fetchone()
                conn.close()
            if not row:
                self._send(404, {"ok": False, "error": "文档不存在"})
                return
            job = get_job(row["job_id"]) or {}
            is_pdf = parts[1] == "pdf"
            suffix = "pdf" if is_pdf else "md"
            filename = f"{job.get('company', 'company')}_{job.get('title', 'role')}_{row['kind']}.{suffix}"
            if is_pdf:
                try:
                    from pdf_exporter import render_document_pdf
                    body = render_document_pdf(row["content"], filename[:-4])
                except ImportError:
                    self._send(503, {"ok": False, "error": "PDF 组件尚未安装，请重新安装 requirements.txt 并重启服务"})
                    return
            else:
                body = row["content"]
            self._send(
                200,
                body,
                content_type="application/pdf" if is_pdf else "text/markdown; charset=utf-8",
                extra={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
                },
            )
            return

        if head == "applications" and method == "GET":
            with _DB_LOCK:
                conn = db()
                rows = conn.execute(
                    "SELECT * FROM applications WHERE user_id=? ORDER BY updated_at DESC",
                    (user["id"],),
                ).fetchall()
                conn.close()
            self._send(200, [dict(r) for r in rows])
            return

        if head in ("help-records", "help_records") and method == "GET":
            with _DB_LOCK:
                conn = db()
                rows = conn.execute("SELECT * FROM help_records WHERE user_id=? ORDER BY record_date DESC, updated_at DESC, id DESC", (user["id"],)).fetchall()
                conn.close()
            self._send(200, [dict(r) for r in rows])
            return

        if head == "applications" and len(parts) >= 2 and parts[1] == "follow-up" and method == "POST":
            body = self._json_body()
            app_id = (body or {}).get("app_id")
            with _DB_LOCK:
                conn = db()
                app = conn.execute("SELECT * FROM applications WHERE id=? AND user_id=?", (app_id, user["id"])).fetchone()
                conn.close()
            if not app:
                self._send(404, {"ok": False, "error": "申请记录不存在"})
                return
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            content = generate_follow_up(dict(app), profile)
            self._send(200, {"ok": True, "content": content})
            return

        if head == "applications" and len(parts) >= 2 and parts[1] == "analyze-reply" and method == "POST":
            body = self._json_body()
            app_id = (body or {}).get("app_id")
            reply = (body or {}).get("reply") or ""
            with _DB_LOCK:
                conn = db()
                app = conn.execute("SELECT * FROM applications WHERE id=? AND user_id=?", (app_id, user["id"])).fetchone()
                conn.close()
            result = analyze_reply(reply, dict(app) if app else None)
            self._send(200, {"ok": True, "data": result})
            return

        if head == "system" and len(parts) >= 1 and parts[0] == "diagnose" and method == "GET":
            items = diagnose_system(user)
            self._send(200, {"ok": True, "data": items})
            return

        if head in ("help-records", "help_records") and method == "POST":
            body = self._json_body()
            title = (body.get("title") or "").strip()
            content = (body.get("content") or "").strip()
            if not title:
                self._send(400, {"ok": False, "error": "记录标题不能为空"})
                return
            if len(title) > 120 or len(content) > 10000:
                self._send(400, {"ok": False, "error": "记录内容过长"})
                return
            job_id = (body.get("job_id") or "").strip()
            if job_id and not get_job(job_id):
                self._send(404, {"ok": False, "error": "关联岗位不存在"})
                return
            now = time.strftime("%Y-%m-%d %H:%M")
            record_date = (body.get("record_date") or time.strftime("%Y-%m-%d")).strip()
            with _DB_LOCK:
                conn = db()
                cur = conn.execute("INSERT INTO help_records (user_id, job_id, record_type, title, content, record_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)", (user["id"], job_id, (body.get("record_type") or "求职笔记").strip()[:40], title, content, record_date, now, now))
                conn.commit()
                row = conn.execute("SELECT * FROM help_records WHERE id=?", (cur.lastrowid,)).fetchone()
                conn.close()
            self._send(200, dict(row))
            return

        if head in ("help-records", "help_records") and len(parts) >= 2 and method == "DELETE":
            with _DB_LOCK:
                conn = db()
                cur = conn.execute("DELETE FROM help_records WHERE id=? AND user_id=?", (parts[1], user["id"]))
                conn.commit(); conn.close()
            if not cur.rowcount:
                self._send(404, {"ok": False, "error": "记录不存在"})
                return
            self._send(200, {"ok": True})
            return

        if head == "applications" and method == "POST":
            body = self._json_body()
            job_id = body.get("job_id")
            job = get_job(job_id) if job_id else None
            if not job:
                self._send(404, {"ok": False, "error": "岗位不存在"})
                return
            stage = body.get("stage", "已收藏")
            now = time.strftime("%Y-%m-%d %H:%M")
            with _DB_LOCK:
                conn = db()
                conn.execute(
                    """INSERT INTO applications
                       (user_id, job_id, company, title, city, stage, source, url, deadline, salary, created_at, updated_at, notes, contact, follow_up_at, attachment_name)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(user_id, job_id) DO UPDATE SET
                       stage=excluded.stage, updated_at=excluded.updated_at, notes=excluded.notes,
                       contact=excluded.contact, follow_up_at=excluded.follow_up_at, attachment_name=excluded.attachment_name""",
                    (
                        user["id"],
                        job_id,
                        job["company"],
                        job["title"],
                        job["city"],
                        stage,
                        job["source"],
                        job["url"],
                        job["deadline"],
                        job["salary"],
                        now,
                        now,
                        body.get("notes", ""),
                        body.get("contact", ""),
                        body.get("follow_up_at", ""),
                        body.get("attachment_name", ""),
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM applications WHERE user_id=? AND job_id=?",
                    (user["id"], job_id),
                ).fetchone()
                conn.close()
            record_event(user["id"], "job_saved", {"job_id": job_id, "stage": stage})
            self._send(200, dict(row))
            return

        if head == "applications" and len(parts) >= 2 and method == "PATCH":
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM applications WHERE id=? AND user_id=?",
                    (parts[1], user["id"]),
                ).fetchone()
                if not row:
                    conn.close()
                    self._send(403, {"ok": False, "error": "无权访问该记录"})
                    return
                body = self._json_body()
                stage = body.get("stage", row["stage"])
                notes = body.get("notes", row["notes"])
                contact = body.get("contact", row["contact"])
                follow_up_at = body.get("follow_up_at", row["follow_up_at"])
                attachment_name = body.get("attachment_name", row["attachment_name"])
                conn.execute(
                    "UPDATE applications SET stage=?, notes=?, contact=?, follow_up_at=?, attachment_name=?, updated_at=? WHERE id=? AND user_id=?",
                    (stage, notes, contact, follow_up_at, attachment_name, time.strftime("%Y-%m-%d %H:%M"), parts[1], user["id"]),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM applications WHERE id=? AND user_id=?", (parts[1], user["id"])).fetchone()
                conn.close()
            if stage == "面试中":
                record_event(user["id"], "interview_scheduled", {"application_id": parts[1], "job_id": row["job_id"]})
                notify(user["id"], "interview", "进入面试阶段", "%s · %s 准备起来" % (row["title"], row["company"]), "/pipeline")
            elif stage == "Offer":
                record_event(user["id"], "offer_received", {"application_id": parts[1], "job_id": row["job_id"]})
            elif stage == "已投递":
                record_event(user["id"], "applied", {"application_id": parts[1], "job_id": row["job_id"]})
            self._send(200, dict(row))
            return

        if head == "interview" and method == "POST":
            body = self._json_body()
            job = get_job(body.get("job_id", ""))
            if not job:
                self._send(404, {"ok": False, "error": "岗位不存在"})
                return
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            content = generate_interview_prep(job, profile)
            with _DB_LOCK:
                conn = db()
                conn.execute(
                    """INSERT OR REPLACE INTO interview_preps (user_id, job_id, content, created_at)
                       VALUES (?,?,?,?)""",
                    (user["id"], job["id"], content, time.strftime("%Y-%m-%d %H:%M")),
                )
                conn.commit()
                conn.close()
            self._send(200, {"job_id": job["id"], "content": content})
            return

        if head == "chat" and method == "POST":
            body = self._json_body()
            user_text = (body.get("message") or "").strip()
            if not user_text:
                self._send(400, {"ok": False, "error": "消息为空"})
                return
            now = time.strftime("%Y-%m-%d %H:%M")
            profile = normalize_profile(_safe_json(user.get("profile_json"), {}))
            reply = assistant_reply(user_text, profile)
            with _DB_LOCK:
                conn = db()
                conn.execute(
                    "INSERT INTO chat_messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
                    (user["id"], "user", user_text, now),
                )
                conn.execute(
                    "INSERT INTO chat_messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
                    (user["id"], "assistant", reply["content"], now),
                )
                conn.commit()
                conn.close()
            record_event(user["id"], "chat_sent", {"chars": len(user_text)})
            self._send(200, reply)
            return

        if head == "chat" and method == "GET":
            with _DB_LOCK:
                conn = db()
                rows = conn.execute(
                    "SELECT role, content, created_at FROM chat_messages WHERE user_id=? ORDER BY id DESC LIMIT 50",
                    (user["id"],),
                ).fetchall()
                conn.close()
            self._send(200, [dict(r) for r in reversed(rows)])
            return

        self._send(404, {"ok": False, "error": "not found"})

    def _api_auth(self, method, parts):
        action = parts[0] if parts else ""
        if action == "register" and method == "POST":
            body = self._json_body()
            username = (body.get("username") or "").strip()
            email = (body.get("email") or "").strip().lower() or None
            password = body.get("password") or ""
            if not username:
                self._send(400, {"ok": False, "error": "用户名不能为空"})
                return
            if len(password) < 6:
                self._send(400, {"ok": False, "error": "密码至少 6 位"})
                return
            with _DB_LOCK:
                conn = db()
                exists = conn.execute(
                    "SELECT id FROM users WHERE username=? COLLATE NOCASE OR (email IS NOT NULL AND email=? COLLATE NOCASE)",
                    (username, email),
                ).fetchone()
                if exists:
                    conn.close()
                    self._send(400, {"ok": False, "error": "用户名或邮箱已存在"})
                    return
                # 首个注册用户自动成为 admin；或命中 CAREERPILOT_ADMIN_USERNAME 列表。
                env_admins = {
                    u.strip().lower()
                    for u in os.environ.get("CAREERPILOT_ADMIN_USERNAME", "").split(",")
                    if u.strip()
                }
                user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
                role = "admin" if (user_count == 0 or username.lower() in env_admins) else "user"
                cur = conn.execute(
                    "INSERT INTO users (username, email, password_hash, role, profile_json) VALUES (?,?,?,?, '{}')",
                    (username, email, hash_password(password), role),
                )
                conn.commit()
                user_id = cur.lastrowid
                conn.close()
            remember=bool(body.get("remember",True)); token = create_session(user_id,2592000 if remember else None)
            user = user_public(get_user_by_token(token))
            self._send(200, {"ok": True, "user": user}, set_cookie=self._session_cookie(token,remember))
            return

        if action == "login" and method == "POST":
            body = self._json_body()
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            rate_key = (self.client_address[0], username.lower())
            retry_after = login_rate_status(rate_key)
            if retry_after:
                self._send(429, {"ok": False, "error": "登录尝试过多，请稍后再试"}, extra={"Retry-After": str(retry_after)})
                return
            with _DB_LOCK:
                conn = db()
                row = conn.execute(
                    "SELECT * FROM users WHERE username=? COLLATE NOCASE OR (email IS NOT NULL AND email=? COLLATE NOCASE)",
                    (username, username),
                ).fetchone()
                conn.close()
            if not row or not row["password_hash"] or not verify_password(password, row["password_hash"]):
                record_login_failure(rate_key)
                audit("login.failure", user_id=None, ip=self.client_address[0], ua=self.headers.get("User-Agent", ""), meta={"username": username[:64]})
                self._send(400, {"ok": False, "error": "用户名或密码错误（支持用户名或邮箱，不区分大小写）"})
                return
            if row["disabled"]:
                self._send(403, {"ok": False, "error": "账号已被停用，请联系管理员"})
                return
            clear_login_failures(rate_key)
            audit("login.success", user_id=row["id"], ip=self.client_address[0], ua=self.headers.get("User-Agent", ""))
            remember=bool(body.get("remember",True)); token = create_session(row["id"],2592000 if remember else None)
            user = user_public(get_user_by_token(token))
            self._send(200, {"ok": True, "user": user}, set_cookie=self._session_cookie(token,remember))
            return

        if action == "logout" and method == "POST":
            token = self._session_token()
            if token:
                delete_session(token)
            self._send(200, {"ok": True}, set_cookie=self._clear_cookie())
            return

        if action == "me" and method == "GET":
            user = self._current_user()
            self._send(200 if user else 401, {"ok": bool(user), "user": user_public(user) if user else None, "server_time":time.strftime("%Y-%m-%d %H:%M:%S"), "session_expires_at":getattr(self,"_session_expires_at",None)})
            return

        if action == "logout-all" and method == "POST":
            user=self._current_user()
            if not user: self._send(401,{"ok":False,"error":"未登录"}); return
            with _DB_LOCK:
                conn=db(); conn.execute("DELETE FROM sessions WHERE user_id=?",(user["id"],)); conn.commit(); conn.close()
            self._send(200,{"ok":True},set_cookie=self._clear_cookie()); return

        if action == "delete-account" and method == "POST":
            user = self._current_user()
            if not user:
                self._send(401, {"ok": False, "error": "未登录"})
                return
            if user.get("role") == "admin":
                self._send(403, {"ok": False, "error": "管理员账号不可注销，请先转移权限"})
                return
            body = self._json_body()
            if not (body or {}).get("confirm"):
                self._send(400, {"ok": False, "error": "请确认注销（confirm: true）"})
                return
            try:
                delete_user_data(user["id"])
            except Exception as exc:
                self._send(500, {"ok": False, "error": "注销失败：" + str(exc)[:160]})
                return
            self._send(200, {"ok": True}, set_cookie=self._clear_cookie())
            return

        if action == "guest" and method == "POST":
            guest_id = create_guest_user()
            token = create_session(guest_id, 2592000)
            user = user_public(get_user_by_token(token))
            self._send(200, {"ok": True, "user": user}, set_cookie=self._session_cookie(token, True))
            return

        if action == "upgrade" and method == "POST":
            user = self._current_user()
            if not user:
                self._send(401, {"ok": False, "error": "未登录"})
                return
            if user.get("role") != "guest":
                self._send(400, {"ok": False, "error": "只有游客可以转正"})
                return
            body = self._json_body()
            username = (body.get("username") or "").strip()
            email = (body.get("email") or "").strip().lower() or None
            password = body.get("password") or ""
            if not username:
                self._send(400, {"ok": False, "error": "用户名不能为空"})
                return
            if len(password) < 6:
                self._send(400, {"ok": False, "error": "密码至少 6 位"})
                return
            with _DB_LOCK:
                conn = db()
                exists = conn.execute(
                    "SELECT id FROM users WHERE id != ? AND (username=? COLLATE NOCASE OR (email IS NOT NULL AND email=? COLLATE NOCASE))",
                    (user["id"], username, email),
                ).fetchone()
                if exists:
                    conn.close()
                    self._send(400, {"ok": False, "error": "用户名或邮箱已存在"})
                    return
                conn.execute(
                    "UPDATE users SET username=?, email=?, password_hash=?, role='user', updated_at=datetime('now', 'localtime') WHERE id=?",
                    (username, email, hash_password(password), user["id"]),
                )
                conn.commit()
                conn.close()
            user = user_public(get_user_by_token(self._session_token()))
            self._send(200, {"ok": True, "user": user})
            return

        self._send(404, {"ok": False, "error": "not found"})

    def do_GET(self):
        self._request_meta()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            self._send(200, {"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            return
        if path == "/metrics":
            from observability import text_format_metrics
            self._send(200, text_format_metrics(), content_type="text/plain; version=0.0.4")
            return
        if path.startswith("/api/"):
            parts = path[len("/api/"):].strip("/").split("/")
            self._api("GET", parts)
        else:
            self._serve_static(path)

    def do_POST(self):
        if self._reject_oversized_body():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            parts = path[len("/api/"):].strip("/").split("/")
            self._api("POST", parts)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_PUT(self):
        if self._reject_oversized_body():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            parts = parsed.path[len("/api/"):].strip("/").split("/")
            self._api("PUT", parts)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_PATCH(self):
        if self._reject_oversized_body():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            parts = parsed.path[len("/api/"):].strip("/").split("/")
            self._api("PATCH", parts)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            parts = parsed.path[len("/api/"):].strip("/").split("/")
            self._api("DELETE", parts)
        else:
            self._send(404, {"ok": False, "error": "not found"})


def main():
    parser = argparse.ArgumentParser(description="CareerPilot Web 多用户服务器")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    init_db()
    global _worker_pool
    if os.environ.get("TASK_QUEUE_ENABLED", "1") == "1":
        _worker_pool = WorkerPool()
        _worker_pool.start()
    if os.environ.get("BACKUP_ENABLED", "1") == "1":
        start_backup_scheduler(
            DB_FILE,
            Path(os.environ.get("BACKUP_DIR", str(DB_FILE.parent / "backups"))),
            float(os.environ.get("BACKUP_INTERVAL_HOURS", "24")),
            int(os.environ.get("BACKUP_RETENTION", "14")),
        )
    from cache import start_cleanup_scheduler
    start_cleanup_scheduler(float(os.environ.get("CLEANUP_INTERVAL_HOURS", "1")) * 3600)
    if os.environ.get("JOB_REFRESH_ENABLED", "1") == "1":
        from job_refresh import start_job_refresh_scheduler
        start_job_refresh_scheduler()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"CareerPilot Web 已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
