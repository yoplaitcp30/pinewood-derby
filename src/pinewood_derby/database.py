"""SQLite persistence and versioned schema migrations."""
from pathlib import Path
import os
import sqlite3
import sys

from .models import AdministrativeResult, Assignment, Event, Heat, LaneResult, Racer, RaceError

SCHEMA_VERSION = 3

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lanes INTEGER NOT NULL CHECK(lanes BETWEEN 2 AND 6),
    runs INTEGER NOT NULL CHECK(runs > 0),
    finalists INTEGER NOT NULL CHECK(finalists > 0),
    scoring TEXT NOT NULL,
    current_heat_id TEXT,
    status TEXT NOT NULL DEFAULT 'registration'
        CHECK(status IN ('registration','preliminary','ready_finals','finals','complete')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE racers (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    car_name TEXT NOT NULL,
    car_number TEXT NOT NULL,
    number_key TEXT NOT NULL,
    tag TEXT,
    tag_key TEXT,
    UNIQUE(event_id, number_key),
    UNIQUE(event_id, tag_key)
);
CREATE TABLE heats (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('preliminary','finals')),
    number INTEGER NOT NULL CHECK(number > 0),
    UNIQUE(event_id, stage, number)
);
CREATE TABLE assignments (
    heat_id TEXT NOT NULL REFERENCES heats(id) ON DELETE CASCADE,
    lane INTEGER NOT NULL CHECK(lane BETWEEN 1 AND 6),
    racer_id TEXT NOT NULL REFERENCES racers(id),
    PRIMARY KEY(heat_id, lane),
    UNIQUE(heat_id, racer_id)
);
CREATE TABLE results (
    heat_id TEXT NOT NULL,
    lane INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position > 0),
    elapsed_seconds REAL CHECK(elapsed_seconds > 0),
    PRIMARY KEY(heat_id, lane),
    UNIQUE(heat_id, position),
    FOREIGN KEY(heat_id, lane) REFERENCES assignments(heat_id, lane) ON DELETE CASCADE
);
CREATE INDEX heats_event ON heats(event_id);
CREATE INDEX racers_event ON racers(event_id);
PRAGMA user_version = 1;
COMMIT;
"""


def default_database_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "pinewood-derby" / "events.sqlite3"


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=10)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RaceError(f"Database version {version} is newer than this application supports.")
            if version == 0:
                self.connection.executescript(SCHEMA)
                version = 1
            if version == 1:
                self._migrate_v2()
                version = 2
            if version == 2:
                with self.connection:
                    self.connection.execute('BEGIN IMMEDIATE')
                    self.connection.execute("ALTER TABLE heats ADD COLUMN tiebreak_scope TEXT NOT NULL "
                                            "DEFAULT 'qualification' CHECK(tiebreak_scope IN ('qualification','finals'))")
                    self.connection.execute('PRAGMA user_version = 3')
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def events(self) -> list[Event]:
        return [self.event(row[0]) for row in self.connection.execute(
            "SELECT id FROM events ORDER BY created_at DESC, rowid DESC")]

    def event(self, event_id: str) -> Event:
        row = self.connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise RaceError("Event not found.")
        return Event(row["id"], row["name"], row["lanes"], row["runs"], row["finalists"],
                     row["scoring"], row["current_heat_id"], row["status"],
                     row["late_policy_kind"], row["late_policy_value"])

    def racers(self, event_id: str) -> list[Racer]:
        return [Racer(row["id"], row["event_id"], row["name"], row["car_name"],
                      row["car_number"], row["tag"], bool(row["late_entry"])) for row in self.connection.execute(
            "SELECT * FROM racers WHERE event_id=? ORDER BY number_key, id", (event_id,))]

    def heats(self, event_id: str, stage: str | None = None) -> list[Heat]:
        query = "SELECT id FROM heats WHERE event_id=?"
        params: tuple = (event_id,)
        if stage is not None:
            query += " AND stage=?"
            params += (stage,)
        query += (" ORDER BY CASE WHEN stage='preliminary' THEN 0 "
                  "WHEN stage='tiebreak' AND tiebreak_scope='qualification' THEN 1 "
                  "WHEN stage='finals' THEN 2 ELSE 3 END, number")
        return [self.heat(row[0]) for row in self.connection.execute(query, params)]

    def heat(self, heat_id: str) -> Heat:
        row = self.connection.execute("SELECT * FROM heats WHERE id=?", (heat_id,)).fetchone()
        if row is None:
            raise RaceError("Heat not found.")
        assignments = tuple(Assignment(a["lane"], a["racer_id"]) for a in self.connection.execute(
            "SELECT * FROM assignments WHERE heat_id=? ORDER BY lane", (heat_id,)))
        results = tuple(LaneResult(r["lane"], r["position"], r["elapsed_seconds"])
                        for r in self.connection.execute(
                            "SELECT * FROM results WHERE heat_id=? ORDER BY lane", (heat_id,)))
        return Heat(row["id"], row["event_id"], row["stage"], row["number"], assignments, results,
                    row["round_number"], row['tiebreak_scope'])


    def administrative_results(self, event_id: str) -> list[AdministrativeResult]:
        return [AdministrativeResult(row['id'], row['event_id'], row['racer_id'], row['kind'],
                                     row['points'], row['placement'], row['batch_id'])
                for row in self.connection.execute(
                    "SELECT * FROM administrative_results WHERE event_id=? ORDER BY rowid", (event_id,))]

    def _migrate_v2(self) -> None:
        # CHECK constraints on stages/status require table rebuilds. Foreign keys
        # are disabled only outside a transaction, rechecked before commit, and
        # re-enabled on every exit. V1 IDs, result rows, and current heat survive.
        self.connection.execute('PRAGMA foreign_keys = OFF')
        try:
            self.connection.executescript(MIGRATION_V2)
            if self.connection.execute('PRAGMA foreign_key_check').fetchall():
                raise RaceError('Migration found invalid foreign keys; original database was preserved.')
            self.connection.execute('PRAGMA user_version = 2')
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute('PRAGMA foreign_keys = ON')


MIGRATION_V2 = """
BEGIN IMMEDIATE;
CREATE TABLE events_v2 (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    lanes INTEGER NOT NULL CHECK(lanes BETWEEN 2 AND 6),
    runs INTEGER NOT NULL CHECK(runs > 0),
    finalists INTEGER NOT NULL CHECK(finalists > 0),
    scoring TEXT NOT NULL, current_heat_id TEXT,
    status TEXT NOT NULL DEFAULT 'registration'
        CHECK(status IN ('registration','preliminary','ready_finals','tiebreak','finals','complete')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    late_policy_kind TEXT NOT NULL DEFAULT 'last', late_policy_value TEXT
);
INSERT INTO events_v2(id,name,lanes,runs,finalists,scoring,current_heat_id,status,created_at)
    SELECT id,name,lanes,runs,finalists,scoring,current_heat_id,status,created_at FROM events ORDER BY rowid;
DROP TABLE events;
ALTER TABLE events_v2 RENAME TO events;
CREATE TABLE heats_v2 (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('preliminary','tiebreak','finals')),
    number INTEGER NOT NULL CHECK(number > 0), round_number INTEGER NOT NULL DEFAULT 0,
    UNIQUE(event_id, stage, number)
);
INSERT INTO heats_v2(id,event_id,stage,number) SELECT id,event_id,stage,number FROM heats;
DROP TABLE heats;
ALTER TABLE heats_v2 RENAME TO heats;
CREATE INDEX heats_event ON heats(event_id);
CREATE TABLE late_batches (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    policy_kind TEXT NOT NULL, policy_value TEXT,
    target_runs INTEGER NOT NULL, missed_runs INTEGER NOT NULL,
    actual_runs INTEGER NOT NULL, reference_lanes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE racers ADD COLUMN late_entry INTEGER NOT NULL DEFAULT 0 CHECK(late_entry IN (0,1));
ALTER TABLE racers ADD COLUMN late_batch_id TEXT REFERENCES late_batches(id) ON DELETE SET NULL;
CREATE TABLE administrative_results (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    racer_id TEXT NOT NULL REFERENCES racers(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL REFERENCES late_batches(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal > 0),
    points TEXT NOT NULL, placement TEXT NOT NULL,
    UNIQUE(racer_id, batch_id, ordinal)
);
CREATE INDEX administrative_event ON administrative_results(event_id);
"""
