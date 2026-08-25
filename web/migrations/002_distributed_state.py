# 002: 分布式状态表（登录限流 + 缓存），跨 worker 共享


def migrate(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS login_attempts (
            key TEXT PRIMARY KEY,
            timestamps_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache_entries (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            expires_at REAL NOT NULL
        )"""
    )


def rollback(conn):
    conn.execute("DROP TABLE IF EXISTS login_attempts")
    conn.execute("DROP TABLE IF EXISTS cache_entries")
