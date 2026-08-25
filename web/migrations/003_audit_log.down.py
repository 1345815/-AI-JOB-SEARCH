# 003 回滚


def rollback(conn):
    conn.execute("DROP TABLE IF EXISTS audit_log")
