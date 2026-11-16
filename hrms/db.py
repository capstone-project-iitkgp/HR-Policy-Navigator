"""SQLite connection helper. The DB file is generated, never committed."""

import os
import sqlite3
from datetime import date
from pathlib import Path

HRMS_DIR = Path(__file__).resolve().parent
DEFAULT_TODAY = "2026-11-16"
# Bump when schema.sql changes. A DB built with another version is rebuilt from the seed files
# the next time it is opened (it only ever holds demo data).
SCHEMA_VERSION = 2


def db_path() -> Path:
    """Where the SQLite file lives (HRMS_DB_PATH overrides the default)."""
    return Path(os.environ.get("HRMS_DB_PATH") or HRMS_DIR / "hrms.db")


def today() -> date:
    """The 'current date' used for notice, back-dating and accrual rules.

    Fixed (HRMS_TODAY) so demos, tests and the eval set give the same answers.
    """
    return date.fromisoformat(os.environ.get("HRMS_TODAY") or DEFAULT_TODAY)


def _version(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def get_conn() -> sqlite3.Connection:
    """Open the DB, building and seeding it first if it does not exist yet."""
    path = db_path()
    if not path.exists() or path.stat().st_size == 0 or _version(path) != SCHEMA_VERSION:
        from . import seed  # imported here to avoid a circular import

        seed.build(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
