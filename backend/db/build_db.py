"""Builds the local HR/payroll reference database from schema.sql and seed_data.sql."""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent
DB_PATH = DB_DIR / "reference.db"
SCHEMA_PATH = DB_DIR / "schema.sql"
SEED_DATA_PATH = DB_DIR / "seed_data.sql"


def build_database() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(SCHEMA_PATH.read_text())
        connection.executescript(SEED_DATA_PATH.read_text())
        connection.commit()
    finally:
        connection.close()

    print(f"Built reference database at {DB_PATH}")


if __name__ == "__main__":
    build_database()
