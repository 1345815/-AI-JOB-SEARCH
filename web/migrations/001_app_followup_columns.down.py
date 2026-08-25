# 001 回滚：SQLite 3.35+ 支持 DROP COLUMN，早期版本需重建表。
# 为保持兼容与数据安全，此处仅移除版本记录（列保留，无数据损失风险）。
# 若确需删除列，请手动：ALTER TABLE applications DROP COLUMN <col>;


def rollback(conn):
    conn.execute("DELETE FROM schema_migrations WHERE version='001'")
