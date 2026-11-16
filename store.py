"""Store every rate con we receive, keeping every version of every load."""
import json
import sqlite3

DB_PATH = "ratecons.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_cons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    load_number   TEXT NOT NULL,      -- broker's load number: how we match versions
    version       INTEGER NOT NULL,   -- 1 for the first one we see, 2 for the next...
    received_date TEXT NOT NULL,      -- when the rate con arrived (YYYY-MM-DD)
    source_file   TEXT,               -- which file it came from
    doc_hash      TEXT NOT NULL,      -- fingerprint of the original document
    data          TEXT,               -- Claude's extraction, stored as JSON text
    errors        TEXT NOT NULL,      -- validation errors and conflicts, JSON list
    changes       TEXT NOT NULL,      -- what changed vs the previous version, JSON list
    status        TEXT NOT NULL,      -- ok | needs_review | revision | duplicate
    UNIQUE (load_number, version)
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    """Open the database file (creating it if needed) and make sure the table exists."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # lets us read columns by name: row["status"]
    conn.execute(SCHEMA)
    return conn


def latest_version(conn: sqlite3.Connection, load_number: str):
    """Return the newest stored version of this load as a dict, or None if we've never seen it."""
    row = conn.execute(
        """
        SELECT * FROM rate_cons
        WHERE load_number = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (load_number,),
    ).fetchone()

    if row is None:
        return None

    version = dict(row)
    version["data"] = json.loads(version["data"]) if version["data"] else None
    version["errors"] = json.loads(version["errors"])
    version["changes"] = json.loads(version["changes"])
    return version


def save(conn, load_number, received_date, source_file, doc_hash, data, errors, changes, status) -> int:
    """Store a new version of a load and return its version number."""
    previous = latest_version(conn, load_number)
    version = 1 if previous is None else previous["version"] + 1

    conn.execute(
        """
        INSERT INTO rate_cons
            (load_number, version, received_date, source_file, doc_hash,
             data, errors, changes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            load_number, version, received_date, source_file, doc_hash,
            json.dumps(data, default=str), json.dumps(errors), json.dumps(changes), status,
        ),
    )
    conn.commit()
    return version


def review_queue(conn: sqlite3.Connection) -> list:
    """The newest version of every load whose status needs a person (anything but ok)."""
    rows = conn.execute(
        """
        SELECT load_number, version, status, changes, errors
        FROM rate_cons AS r
        WHERE version = (SELECT MAX(version) FROM rate_cons WHERE load_number = r.load_number)
          AND status != 'ok'
        ORDER BY received_date
        """
    ).fetchall()
    return [dict(row) for row in rows]
