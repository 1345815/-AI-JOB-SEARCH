# 002 回滚


def rollback(conn):
    conn.execute("DROP TABLE IF EXISTS login_attempts")
    conn.execute("DROP TABLE IF EXISTS cache_entries")
