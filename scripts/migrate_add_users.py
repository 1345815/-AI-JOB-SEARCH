#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多用户迁移脚本（幂等）。

为已有数据库补齐多用户结构：
- 创建 users / sessions 表
- 为 applications / documents / interview_preps / chat_messages 添加 user_id
- 创建 admin 用户，并把已有私有数据关联到 admin
- 可重复执行
"""

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_FILE = Path(os.environ.get("DB_PATH", str(ROOT / "web" / "data" / "careerpilot.db")))


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return "pbkdf2$120000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def column_names(conn, table):
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r["name"] for r in rows]
    except Exception:
        return []


def ensure_user_tables(conn):
    conn.executescript(
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
        """
    )


def ensure_user_column(conn, table):
    if "user_id" not in column_names(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0"
        )
        return True
    return False


def main():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    ensure_user_tables(conn)

    tables = ["applications", "documents", "interview_preps", "chat_messages"]
    changed = []
    for table in tables:
        if ensure_user_column(conn, table):
            changed.append(table)

    admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
    if not admin:
        password = secrets.token_urlsafe(12)
        admin_hash = hash_password(password)
        cur = conn.execute(
            "INSERT INTO users (username, role, password_hash, profile_json) VALUES ('admin', 'admin', ?, '{}')",
            (admin_hash,),
        )
        admin_id = cur.lastrowid
        print(f"已创建 admin 用户，用户名: admin  密码: {password}")
    else:
        admin_id = admin["id"]
        print(f"使用已有 admin 用户 id={admin_id}")

    for table in tables:
        conn.execute(
            f"UPDATE {table} SET user_id=? WHERE user_id=0 OR user_id IS NULL",
            (admin_id,),
        )

    conn.commit()
    conn.close()

    print("迁移完成。")
    print(f"数据库: {DB_FILE}")
    print(f"新增/修改的表: users, sessions, {', '.join(changed) if changed else '(无新列，已幂等)'}")


if __name__ == "__main__":
    main()
