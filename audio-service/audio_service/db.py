"""The service's own SQLite database: assets, rights, links to catalogue versions, moderation."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- one stored audio file, identified by its content
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    format TEXT,                 -- mp3, flac, m4a ...
    content_type TEXT,
    duration REAL,               -- seconds
    bitrate INTEGER,             -- bits per second
    sample_rate INTEGER,
    original_name TEXT,          -- the file name it came with (never a path)
    fingerprint TEXT,            -- Chromaprint (stage 4)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- under which rights an asset may be played / downloaded (rights.py decides)
CREATE TABLE IF NOT EXISTS rights (
    asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
    status TEXT NOT NULL DEFAULT 'unknown',   -- unknown, private, owner_permission, licensed, public_domain, promo
    license TEXT,                             -- e.g. "CC BY 4.0"
    allows_download INTEGER NOT NULL DEFAULT 0,
    evidence TEXT,                            -- where the permission is documented
    decided_by TEXT,
    decided_at TEXT
);

-- which catalogue version an asset is (the catalogue keeps the authoritative copy
-- of this link and moves it when songs are merged)
CREATE TABLE IF NOT EXISTS asset_versions (
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    version_id INTEGER NOT NULL,              -- versions.id of the tracker's catalogue
    confidence REAL,                          -- 1.0 = confirmed by a person
    linked_by TEXT,
    PRIMARY KEY (asset_id, version_id)
);

-- moderation: every state change is kept (audit log)
CREATE TABLE IF NOT EXISTS moderation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id),
    state TEXT NOT NULL,                      -- pending, approved, rejected, taken_down
    note TEXT,
    actor TEXT,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS asset_versions_by_version ON asset_versions(version_id);
CREATE INDEX IF NOT EXISTS moderation_by_asset ON moderation(asset_id, id);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    with connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


def moderation_state(connection: sqlite3.Connection, asset_id: int) -> str:
    """The latest moderation state of an asset ('pending' when none was recorded)."""
    row = connection.execute("SELECT state FROM moderation WHERE asset_id = ? ORDER BY id DESC LIMIT 1",
                             (asset_id,)).fetchone()
    return row["state"] if row else "pending"
