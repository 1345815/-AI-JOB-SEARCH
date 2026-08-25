# 003: 审计日志表


def migrate(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            resource TEXT DEFAULT '',
            resource_id TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            meta_json TEXT DEFAULT '{}'
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")


def rollback(conn):
    conn.execute("DROP TABLE IF EXISTS audit_log")
