"""轻量版本化迁移框架（纯标准库）。

目录结构：web/migrations/NNNN_<name>.sql（up）+ NNNN_<name>.down.sql（down）
- ensure_version_table()：schema_migrations(version, name, applied_at)
- migrate(conn, target=None)：按版本升序应用未执行的 up
- rollback(conn, steps=1)：按倒序执行 down
- status(conn)：已应用/未应用
- CLI：python -m web.migrations up|down|status|dry-run [--target NNNN] [--db PATH]
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _parse_migration(filename):
    """'0001_add_users.sql' -> ('0001', 'add_users', up)。'0001_x.py' 同理。'...down' 为 down。"""
    name = filename
    is_down = name.endswith(".down.sql") or name.endswith(".down.py")
    if is_down:
        if name.endswith(".down.sql"):
            name = name[: -len(".down.sql")]
        else:
            name = name[: -len(".down.py")]
    else:
        for suffix in (".sql", ".py"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
    if "_" not in name:
        return None
    version, label = name.split("_", 1)
    if not version.isdigit():
        return None
    return (version, label, is_down)


def list_migrations():
    """返回 [{version, name, up, down}]，按版本升序。
    up/down 可为 .sql 文件路径或 .py 模块（含 migrate(conn)/rollback(conn) 函数）。"""
    files = {}
    for p in sorted(MIGRATIONS_DIR.glob("*")):
        if p.suffix not in (".sql", ".py"):
            continue
        parsed = _parse_migration(p.name)
        if not parsed:
            continue
        version, label, is_down = parsed
        item = files.setdefault(version, {"version": version, "name": label, "up": None, "down": None})
        if is_down:
            item["down"] = p
        else:
            item["up"] = p
    return sorted(files.values(), key=lambda m: m["version"])


def _apply_file(conn, path, direction):
    """执行迁移文件（.sql 或 .py）。direction: 'migrate' | 'rollback'"""
    if path.suffix == ".sql":
        conn.executescript(path.read_text(encoding="utf-8"))
    else:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_mig_" + path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, direction, None)
        if not fn:
            raise RuntimeError("%s 缺少 %s() 函数" % (path.name, direction))
        fn(conn)


def ensure_version_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, name TEXT, applied_at TEXT)"
    )
    conn.commit()


def applied_versions(conn):
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def migrate(conn, target=None):
    """应用所有未执行迁移（事务包裹）。target 用于 --target 精确版本（含该版本）。"""
    ensure_version_table(conn)
    applied = applied_versions(conn)
    applied_now = []
    for m in list_migrations():
        if m["version"] in applied:
            continue
        if target is not None and m["version"] > target:
            break
        if not m["up"]:
            raise RuntimeError("迁移 %s 缺少 up 文件" % m["version"])
        conn.execute("BEGIN")
        try:
            _apply_file(conn, m["up"], "migrate")
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
                (m["version"], m["name"], time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
            applied_now.append(m["version"])
        except Exception:
            conn.rollback()
            raise
    return applied_now


def rollback(conn, steps=1):
    """按倒序回滚指定步数。"""
    ensure_version_table(conn)
    applied = sorted(applied_versions(conn), reverse=True)
    rolled_back = []
    for version in applied[:steps]:
        m = next((x for x in list_migrations() if x["version"] == version), None)
        if not m or not m["down"]:
            raise RuntimeError("迁移 %s 缺少 down 文件，禁止空回滚" % version)
        conn.execute("BEGIN")
        try:
            _apply_file(conn, m["down"], "rollback")
            conn.execute("DELETE FROM schema_migrations WHERE version=?", (version,))
            conn.commit()
            rolled_back.append(version)
        except Exception:
            conn.rollback()
            raise
    return rolled_back


def status(conn):
    ensure_version_table(conn)
    applied = applied_versions(conn)
    return [(m["version"], m["name"], m["version"] in applied) for m in list_migrations()]


def _dry_run_sql():
    """输出将要执行的 up SQL（不落库）。"""
    lines = []
    for m in list_migrations():
        if m["up"]:
            lines.append("-- [%s] %s" % (m["version"], m["name"]))
            lines.append(m["up"].read_text(encoding="utf-8").strip())
    return "\n".join(lines)


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 预处理 --db / --target（argparse 子命令的全局参数兼容）
    db_path = None
    target = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--db" and i + 1 < len(argv):
            db_path = argv[i + 1]
            i += 2
        elif argv[i] == "--target" and i + 1 < len(argv):
            target = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1

    parser = argparse.ArgumentParser(description="CareerPilot 数据库迁移 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up")
    sub.add_parser("down")
    sub.add_parser("status")
    sub.add_parser("dry-run")
    args = parser.parse_args(rest)

    if db_path is None:
        try:
            import server
            db_path = str(server.DB_FILE)
        except Exception:
            db_path = str(Path(__file__).resolve().parents[2] / "web" / "data" / "careerpilot.db")

    if args.cmd == "dry-run":
        print(_dry_run_sql())
        print("\n[dry-run] 未执行任何迁移。")
        return 0

    conn = _connect(db_path)
    try:
        if args.cmd == "up":
            done = migrate(conn, target=target)
            print("已应用：%s" % (", ".join(done) if done else "无"))
        elif args.cmd == "down":
            n = 1
            done = rollback(conn, steps=n)
            print("已回滚：%s" % (", ".join(done) if done else "无"))
        elif args.cmd == "status":
            for version, name, applied in status(conn):
                mark = "[x]" if applied else "[ ]"
                print("%s %s %s" % (mark, version, name))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
