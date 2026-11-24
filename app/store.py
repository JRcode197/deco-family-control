"""SQLite persistence. All public DB operations share a re-entrant lock."""
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from threading import RLock
from datetime import date, datetime, timedelta, timezone

from app.deco import Client

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  quota_minutes INTEGER,
  manually_blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (
  mac TEXT PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL DEFAULT '',
  online INTEGER NOT NULL DEFAULT 0,
  client_type TEXT NOT NULL DEFAULT 'unknown',
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  last_seen TEXT,
  managed_block INTEGER NOT NULL DEFAULT 0,
  manually_blocked INTEGER NOT NULL DEFAULT 0,
  quota_minutes INTEGER,
  last_action TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK(weekday >= 0 AND weekday <= 6),
  start_time TEXT NOT NULL, end_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_quotas (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
  minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 1440),
  PRIMARY KEY (user_id, weekday)
);
CREATE TABLE IF NOT EXISTS dns_profiles (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_name TEXT NOT NULL,
  data TEXT NOT NULL,
  PRIMARY KEY (user_id,group_name)
);
CREATE TABLE IF NOT EXISTS dns_devices (
  mac TEXT PRIMARY KEY REFERENCES devices(mac) ON DELETE CASCADE,
  client_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dns_categories (
  name TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  domains TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_copies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  time TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  restored_at TEXT
);
CREATE TABLE IF NOT EXISTS minute_samples (
  day TEXT NOT NULL, minute TEXT NOT NULL,
  mac TEXT NOT NULL REFERENCES devices(mac) ON DELETE CASCADE,
  up_kbs INTEGER, down_kbs INTEGER,
  PRIMARY KEY (minute, mac)
);
CREATE INDEX IF NOT EXISTS ix_sample_mac_day ON minute_samples (mac, day);
CREATE INDEX IF NOT EXISTS ix_sample_day ON minute_samples (day);
CREATE TABLE IF NOT EXISTS sample_retention (
  id INTEGER PRIMARY KEY CHECK(id=1),
  days INTEGER NOT NULL CHECK(days IN (0,7,30,90,180))
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  time TEXT NOT NULL, mac TEXT, action TEXT NOT NULL,
  status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                migrations = {
                    "users": {
                        "avatar": "TEXT NOT NULL DEFAULT ''",
                        "description": "TEXT NOT NULL DEFAULT ''",
                        "age_band": "TEXT NOT NULL DEFAULT ''",
                        "override_until": "TEXT",
                    },
                    "devices": {
                        "profile_exempt": "INTEGER NOT NULL DEFAULT 0",
                        "last_online": "TEXT",
                    },
                    "windows": {
                        "kind": "TEXT NOT NULL DEFAULT 'offtime'",
                        "label": "TEXT NOT NULL DEFAULT ''",
                        "enabled": "INTEGER NOT NULL DEFAULT 1",
                        "days_mask": "INTEGER",
                        "paused_until": "TEXT",
                    },
                }
                for table, columns in migrations.items():
                    existing = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
                    for name, definition in columns.items():
                        if name not in existing:
                            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                            if name == "last_online":
                                self.db.execute("""UPDATE devices SET last_online=(
                                    SELECT substr(MAX(minute),1,16) || ':00+00:00'
                                    FROM minute_samples WHERE mac=devices.mac
                                )""")

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.lock, self.db:
            return self.db.execute(sql, args).lastrowid

    def users(self) -> list[dict]:
        return self.query("SELECT * FROM users ORDER BY name")

    def devices(self, sample_minute: str | None = None, *, snapshot_at: str | None = None) -> list[dict]:
        return self.query("""SELECT d.*, u.name AS user_name, s.up_kbs, s.down_kbs,
          CASE WHEN d.last_seen=? THEN 1 ELSE 0 END AS in_latest_snapshot
          FROM devices d LEFT JOIN users u ON u.id = d.user_id
          LEFT JOIN minute_samples s ON d.online=1 AND s.mac=d.mac AND s.minute=?
          ORDER BY d.name""", (snapshot_at, sample_minute))

    def windows(self) -> list[dict]:
        return self.query("SELECT * FROM windows ORDER BY user_id, weekday, start_time")

    def audit(self, limit: int = 50) -> list[dict]:
        return self.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def log(self, action: str, status: str, mac: str = "", detail: str = ""):
        self.execute("INSERT INTO audit(time,mac,action,status,detail) VALUES (?,?,?,?,?)", (
            datetime.now().astimezone().isoformat(timespec="seconds"), mac, action, status, detail[:300]))

    @contextmanager
    def policy_change(self, action: str, detail: str):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield self.db
            self.db.execute("INSERT INTO audit(time,action,status,detail) VALUES (?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), action, "salvata", detail))

    def record_inventory(self, clients: list[Client], seen_at: str, local_day: str, minute: str):
        with self.lock, self.db:
            # Devices omitted from a successful snapshot keep their assignments and history.
            self.db.execute("UPDATE devices SET online=0 WHERE online=1")
            self.db.executemany("""INSERT INTO devices(mac,name,ip,online,client_type,last_seen,last_online)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET
              name=excluded.name, ip=excluded.ip, online=excluded.online,
              client_type=excluded.client_type, last_seen=excluded.last_seen,
              last_online=COALESCE(excluded.last_online,devices.last_online)""",
                ((c.mac, c.name, c.ip, int(c.online), c.client_type, seen_at, seen_at if c.online else None) for c in clients))
            self.db.executemany("""INSERT INTO minute_samples(day,minute,mac,up_kbs,down_kbs)
              VALUES(?,?,?,?,?) ON CONFLICT(minute,mac) DO UPDATE SET
              up_kbs=excluded.up_kbs,down_kbs=excluded.down_kbs""",
                ((local_day, minute, c.mac, c.up_kbs, c.down_kbs) for c in clients if c.online))

    def usage(self, day: str) -> dict:
        individual = self.query("""SELECT mac, COUNT(*) AS minutes FROM minute_samples
          WHERE day=? GROUP BY mac""", (day,))
        collective = self.query("""SELECT d.user_id, COUNT(DISTINCT s.minute) AS minutes
          FROM minute_samples s JOIN devices d ON d.mac=s.mac
          WHERE s.day=? AND d.user_id IS NOT NULL GROUP BY d.user_id""", (day,))
        return {"devices": {x["mac"]: x["minutes"] for x in individual},
                "users": {x["user_id"]: x["minutes"] for x in collective}}

    def usage_report(self, start: date, end: date, *, user_id: int | None = None,
                     mac: str | None = None) -> tuple[list[dict], list[dict]]:
        where = ["s.day BETWEEN ? AND ?"]
        args = [start.isoformat(), end.isoformat()]
        if user_id is not None:
            where.append("d.user_id=?")
            args.append(user_id)
        if mac is not None:
            where.append("d.mac=?")
            args.append(mac)
        scope = " AND ".join(where)
        with self.lock:
            devices = self.query(f"""SELECT day, d.user_id, d.mac, COUNT(*) AS minutes,
              COUNT(up_kbs) AS up_samples, COUNT(down_kbs) AS down_samples,
              ROUND(AVG(up_kbs), 1) AS average_up_kbs,
              ROUND(AVG(down_kbs), 1) AS average_down_kbs,
              MAX(up_kbs) AS max_up_kbs, MAX(down_kbs) AS max_down_kbs
              FROM minute_samples s JOIN devices d USING(mac) WHERE {scope}
              GROUP BY day, d.user_id, d.mac ORDER BY day DESC, d.mac""", tuple(args))
            profiles = self.query(f"""SELECT day, d.user_id, COUNT(DISTINCT s.minute) AS minutes
              FROM minute_samples s JOIN devices d USING(mac) WHERE {scope} AND d.user_id IS NOT NULL
              GROUP BY day, d.user_id ORDER BY day DESC, d.user_id""", tuple(args))
        return devices, profiles

    def daily_usage(self, days: int = 7, *, today: date | None = None) -> list[dict]:
        today = today or date.today()
        devices, _ = self.usage_report(today - timedelta(days=days - 1), today)
        return devices

    def sample_retention(self) -> int | None:
        rows = self.query("SELECT days FROM sample_retention WHERE id=1")
        return rows[0]["days"] if rows else None

    def prune_samples(self, days: int, today: str) -> int:
        with self.lock, self.db:
            return self._prune_samples(self.db, days, today)

    @staticmethod
    def _prune_samples(db, days: int, today: str) -> int:
        if days == 0:
            return 0
        first_day = date.fromisoformat(today) - timedelta(days=days - 1)
        return db.execute("DELETE FROM minute_samples WHERE day<?", (first_day.isoformat(),)).rowcount

    def set_sample_retention(self, days: int | None, fallback: int, today: str) -> int:
        with self.policy_change("conservazione_campioni", f"giorni={days if days is not None else fallback}") as db:
            if days is None:
                db.execute("DELETE FROM sample_retention WHERE id=1")
            else:
                db.execute("""INSERT INTO sample_retention(id,days) VALUES (1,?)
                    ON CONFLICT(id) DO UPDATE SET days=excluded.days""", (days,))
            return self._prune_samples(db, fallback if days is None else days, today)

    def clear_samples(self, through: date | None = None) -> int:
        with self.policy_change("cancellazione_campioni", "") as db:
            if through is None:
                return db.execute("DELETE FROM minute_samples").rowcount
            return db.execute("DELETE FROM minute_samples WHERE day<=?", (through.isoformat(),)).rowcount

    def close(self):
        with self.lock:
            self.db.close()
