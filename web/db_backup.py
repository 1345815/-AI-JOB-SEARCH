#!/usr/bin/env python3
"""Verified SQLite backup, retention, and restore utilities."""

import argparse
import os
import sqlite3
import threading
import time
from pathlib import Path


def verify_database(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在：{path}")
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{result}")
        return {"path": str(path), "size": path.stat().st_size, "integrity": result}
    finally:
        conn.close()


def create_backup(database, backup_dir, retention=14):
    database, backup_dir = Path(database), Path(backup_dir)
    if not database.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"careerpilot-{stamp}.db"
    source_conn = sqlite3.connect(database)
    target_conn = sqlite3.connect(target)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    verify_database(target)
    backups = sorted(backup_dir.glob("careerpilot-*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in backups[max(int(retention), 1):]:
        old.unlink()
    return target


def restore_backup(backup, database):
    backup, database = Path(backup), Path(database)
    verify_database(backup)
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        safety = database.with_name(database.name + ".pre-restore-" + time.strftime("%Y%m%d-%H%M%S"))
        os.replace(database, safety)
    else:
        safety = None
    temp = database.with_name(database.name + ".restoring")
    if temp.exists():
        temp.unlink()
    source_conn = sqlite3.connect(backup)
    target_conn = sqlite3.connect(temp)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    verify_database(temp)
    os.replace(temp, database)
    return database, safety


def start_backup_scheduler(database, backup_dir, interval_hours=24, retention=14):
    interval = max(float(interval_hours), 1.0) * 3600

    def run():
        while True:
            try:
                backup_dir_path = Path(backup_dir)
                today = time.strftime("%Y%m%d")
                if not any(backup_dir_path.glob(f"careerpilot-{today}-*.db")):
                    create_backup(database, backup_dir, retention)
            except Exception as exc:
                print(f"数据库自动备份失败：{exc}")
            time.sleep(interval)

    thread = threading.Thread(target=run, name="careerpilot-db-backup", daemon=True)
    thread.start()
    return thread


def main():
    parser = argparse.ArgumentParser(description="CareerPilot SQLite 备份与恢复")
    sub = parser.add_subparsers(dest="command", required=True)
    backup_cmd = sub.add_parser("backup")
    backup_cmd.add_argument("--database", required=True)
    backup_cmd.add_argument("--output", required=True)
    backup_cmd.add_argument("--retention", type=int, default=14)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("backup")
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("backup")
    restore_cmd.add_argument("--database", required=True)
    args = parser.parse_args()
    if args.command == "backup":
        target = create_backup(args.database, args.output, args.retention)
        print(target or "数据库尚不存在，未创建备份")
    elif args.command == "verify":
        print(verify_database(args.backup))
    else:
        target, safety = restore_backup(args.backup, args.database)
        print(f"已恢复到 {target}；原数据库备份：{safety or '无'}")


if __name__ == "__main__":
    main()
