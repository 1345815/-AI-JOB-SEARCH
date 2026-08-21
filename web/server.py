#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CareerPilot Web - 多用户本地/云端服务器。

仅使用 Python 标准库（http.server + sqlite3 + urllib）。
密码哈希使用 PBKDF2-HMAC-SHA256（标准库实现，不依赖 bcrypt）。
"""

import argparse
import base64
import cgi
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from profile_merger import apply_paths, build_merge_plan
from job_extractor import extract_job_from_url, search_jobs, search_company_jobs
from form_extractor import extract_form
from form_filler import build_fill_plan
from resume_extractor import extract_profile_from_resume
from resume_parser import extract_resume_text
from db_backup import start_backup_scheduler

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
JOBS_SEED = DATA_DIR / "jobs_seed.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = Path(os.environ.get("DB_PATH", str(DATA_DIR / "careerpilot.db")))

SESSION_MAX_AGE_DAYS = int(os.environ.get("SESSION_MAX_AGE_DAYS", "7"))
SESSION_COOKIE = "careerpilot_session"
MAX_JSON_BODY_BYTES = int(os.environ.get("MAX_JSON_BODY_BYTES", str(1024 * 1024)))
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_WINDOW_SECONDS", "900"))

_DB_LOCK = threading.RLock()
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES = {}

PROFILE_FIELDS = {
    "name", "email", "phone", "city", "school", "major", "graduation_date",
    "target_roles", "target_cities", "salary_expectation", "education",
    "experiences", "projects", "skills", "languages", "awards", "summary",
    "portfolio", "github", "linkedin", "status", "location_preference",
    "career_goals", "notes", "highest_degree", "english_level", "target_role",
    "target_sectors", "target_city", "available_date",
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
                if key in stored:
                    settings[key] = stored[key]
        except Exception:
            pass
    return settings


def save_settings(settings):
    _write_utf8(SETTINGS_FILE, json.dumps(settings, ensure_ascii=False, indent=2))


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


def login_rate_status(key, now=None):
    now = now or time.time()
    with _LOGIN_LOCK:
        attempts = [stamp for stamp in _LOGIN_FAILURES.get(key, []) if now - stamp < LOGIN_RATE_WINDOW_SECONDS]
        if attempts:
            _LOGIN_FAILURES[key] = attempts
        else:
            _LOGIN_FAILURES.pop(key, None)
        return max(1, int(LOGIN_RATE_WINDOW_SECONDS - (now - attempts[0]))) if len(attempts) >= LOGIN_RATE_LIMIT else 0


def record_login_failure(key, now=None):
    now = now or time.time()
    with _LOGIN_LOCK:
        attempts = [stamp for stamp in _LOGIN_FAILURES.get(key, []) if now - stamp < LOGIN_RATE_WINDOW_SECONDS]
        attempts.append(now)
        _LOGIN_FAILURES[key] = attempts


def clear_login_failures(key):
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(key, None)


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

def touch_session(token):
    if not token: return None
    with _DB_LOCK:
        conn=db(); row=conn.execute("SELECT expires_at FROM sessions WHERE token=?",(token,)).fetchone()
        if row and time.mktime(time.strptime(row["expires_at"],"%Y-%m-%d %H:%M:%S"))-time.time()<86400:
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
        "profile": profile,
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


def mark_saved_search_results(results):
    saved_jobs = list_jobs()
    output = []
    for item in results:
        saved = next((job for job in saved_jobs if (item.get("id") and job["id"] == item["id"]) or (item.get("url") and job.get("url") == item["url"]) or (job.get("title") == item.get("title") and job.get("company") == item.get("company"))), None)
        output.append({**item, "saved_job_id": saved["id"] if saved else None})
    return output


def add_job(job):
    job_id = job.get("id") or f"job-{int(time.time() * 1000)}"
    now = time.strftime("%Y-%m-%d")
    with _DB_LOCK:
        conn = db()
        conn.execute(
            """INSERT OR REPLACE INTO jobs
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


def profile_is_empty(profile):
    if not profile:
        return True
    if profile.get("skills") or profile.get("name") or profile.get("projects") or profile.get("career_goals"):
        return False
    return True


def gates(job, profile=None):
    profile = profile or {}
    text = _text_of(job)
    results = []
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

    return {"blocked": blocked, "items": results}


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


def score_job(job, profile=None):
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

    return {
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


def generate_resume(job, profile=None):
    profile = profile or {}
    if profile_is_empty(profile):
        return "请先在「个人资料」中填写姓名、技能、经历与职业目标，才能生成定制简历。"
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


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
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
        field = form["file"] if "file" in form else None
        if isinstance(field, list):
            field = field[0] if field else None
        if field is None or not getattr(field, "filename", None):
            return None, 400, "缺少 file 字段"
        return field, 200, ""

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
                self._send(200, {"ok": True, "data": plan})
            except ValueError as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
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

        self._send(404, {"ok": False, "error": "not found"})

    def _session_token(self):
        return _cookie_value(self.headers.get("Cookie"), SESSION_COOKIE)

    def _current_user(self):
        token=self._session_token(); user=get_user_by_token(token)
        if user: self._session_expires_at=touch_session(token)
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

        if head == "profile" and len(parts) >= 2 and parts[1] == "resume-import":
            return self._api_resume_import(method, parts[2:], user)

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
            self._send(200, settings)
            return
        if head == "settings" and method == "PUT":
            body = self._json_body()
            settings = load_settings()
            if body.get("api_key") and body["api_key"] != "******":
                settings["api_key"] = body["api_key"]
            elif "api_key" in body and not body.get("api_key"):
                settings["api_key"] = ""
            settings["base_url"] = body.get("base_url", settings["base_url"])
            settings["model"] = body.get("model", settings["model"])
            settings["enabled"] = bool(body.get("enabled", settings["enabled"]))
            save_settings(settings)
            public = dict(settings)
            public.pop("api_key", None)
            public["has_key"] = bool(settings.get("api_key"))
            self._send(200, public)
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

        if head=="jobs" and len(parts)>=3 and parts[1]=="search" and parts[2]=="company" and method=="POST":
            body=self._json_body(); company=(body.get("company") or "").strip(); s=load_settings()
            enabled=bool(s.get("enabled") and s.get("api_key") and s.get("base_url"))
            if not company: self._send(400,{"ok":False,"error":"缺少公司名"}); return
            if not enabled: self._send(200,{"ok":True,"data":[],"skipped":[],"mode":"local","hint":"请在设置中开启 AI 模式以使用联网搜索"}); return
            city=(body.get("city") or "").strip(); limit=min(max(int(body.get("limit",8)),1),10)
            try:
                result=search_company_jobs(company,city,limit)
            except Exception as exc:
                self._send(500,{"ok":False,"error":"联网搜索失败："+str(exc)[:160]}); return
            profile=normalize_profile(_safe_json(user.get("profile_json"), {})); output=[]
            for item in result.get("jobs",[]):
                try:
                    job=get_job(add_job(item)); ev=score_job(job,profile); save_evaluation(user["id"],ev)
                    output.append({**job,"evaluation":ev})
                except Exception: continue
            self._send(200,{"ok":True,"data":output,"skipped":result.get("skipped",[]),"cached":bool(result.get("cached")),"hint":"已从真实招聘页面抓取岗位，投递前仍建议人工核实链接。"}); return

        if head=="jobs" and len(parts)==2 and parts[1]=="search" and method=="GET":
            s=load_settings(); self._send(200,{"ok":True,"data":{"enabled":bool(s.get("enabled") and s.get("api_key") and s.get("base_url")),"provider":s.get("provider","custom"),"model":s.get("model",""),"has_key":bool(s.get("api_key"))}}); return
        if head=="jobs" and len(parts)==2 and parts[1]=="search" and method=="POST":
            query=self._json_body(); s=load_settings(); enabled=bool(s.get("enabled") and s.get("api_key") and s.get("base_url")); results=search_jobs(query,s)
            if not enabled: self._send(200,{"ok":True,"data":[],"local_results":mark_saved_search_results(results),"mode":"local","hint":"请在设置中开启 AI 模式以使用联网搜索"}); return
            profile=normalize_profile(_safe_json(user.get("profile_json"), {})); output=[]
            for item in results:
                if item.get("source")=="local": continue
                job=get_job(add_job(item)); ev=score_job(job,profile); save_evaluation(user["id"],ev); output.append({**job,"evaluation":ev})
            self._send(200,{"ok":True,"data":mark_saved_search_results(output),"local_results":mark_saved_search_results([item for item in results if item.get("source")=="local"]),"mode":"llm","hint":"以下为 LLM 建议，投递前请人工核实链接。"}); return

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
                    "city": "",
                    "posting_type": "未知",
                    "salary": "",
                    "deadline": "",
                    "tags": ["URL解析"],
                    "url": url,
                    "description": parsed.get("description", ""),
                    "requirements": parsed.get("requirements", []),
                    "source": "URL解析",
                })
                job = get_job(job_id)
                ev = score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})))
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
            ev = score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})))
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
                ev = get_evaluation(user["id"], job_id) or score_job(job, normalize_profile(_safe_json(user.get("profile_json"), {})))
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
            else:
                self._send(400, {"ok": False, "error": "kind 必须是 resume 或 cover_letter"})
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
            email = (body.get("email") or "").strip() or None
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
                    "SELECT id FROM users WHERE username=? OR (email IS NOT NULL AND email=?)",
                    (username, email),
                ).fetchone()
                if exists:
                    conn.close()
                    self._send(400, {"ok": False, "error": "用户名或邮箱已存在"})
                    return
                cur = conn.execute(
                    "INSERT INTO users (username, email, password_hash, role, profile_json) VALUES (?,?,?, 'user', '{}')",
                    (username, email, hash_password(password)),
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
                row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                conn.close()
            if not row or not row["password_hash"] or not verify_password(password, row["password_hash"]):
                record_login_failure(rate_key)
                self._send(400, {"ok": False, "error": "用户名或密码错误"})
                return
            clear_login_failures(rate_key)
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

        if action == "guest" and method == "POST":
            guest_id = create_guest_user()
            token = create_session(guest_id)
            user = user_public(get_user_by_token(token))
            self._send(200, {"ok": True, "user": user}, set_cookie=self._session_cookie(token))
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
            email = (body.get("email") or "").strip() or None
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
                    "SELECT id FROM users WHERE id != ? AND (username=? OR (email IS NOT NULL AND email=?))",
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
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
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
    if os.environ.get("BACKUP_ENABLED", "1") == "1":
        start_backup_scheduler(
            DB_FILE,
            Path(os.environ.get("BACKUP_DIR", str(DB_FILE.parent / "backups"))),
            float(os.environ.get("BACKUP_INTERVAL_HOURS", "24")),
            int(os.environ.get("BACKUP_RETENTION", "14")),
        )
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
