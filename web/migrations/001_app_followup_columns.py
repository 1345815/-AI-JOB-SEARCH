# 001: 固化 applications 表 follow-up 字段（原 init_db 手写 ALTER 逻辑）
# Python 迁移：PRAGMA 检查列存在性，兼容 SQLite 各版本。


def _ensure_column(conn, name, definition):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(applications)").fetchall()}
    if name not in cols:
        conn.execute("ALTER TABLE applications ADD COLUMN " + name + " " + definition)


def migrate(conn):
    _ensure_column(conn, "contact", "TEXT DEFAULT ''")
    _ensure_column(conn, "follow_up_at", "TEXT DEFAULT ''")
    _ensure_column(conn, "attachment_name", "TEXT DEFAULT ''")


def rollback(conn):
    # SQLite 3.35+ 支持 DROP COLUMN；早期版本需重建表。
    # 为保持兼容与数据安全，回滚仅移除版本记录并提示（列保留）。
    pass
