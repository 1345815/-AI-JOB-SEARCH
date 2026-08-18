import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import server  # noqa: E402


def test_existing_application_database_gains_follow_up_columns(tmp_path):
    database = tmp_path / "careerpilot.db"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY, user_id INTEGER, job_id TEXT)")
    conn.commit()
    conn.close()

    original_database = server.DB_FILE
    server.DB_FILE = database
    try:
        server.init_db()
    finally:
        server.DB_FILE = original_database

    conn = sqlite3.connect(database)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(applications)")}
    conn.close()
    assert {"contact", "follow_up_at", "attachment_name"} <= columns
