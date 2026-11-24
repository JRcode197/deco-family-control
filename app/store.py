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
CREATE TABLE IF NOT EXISTS inventory_observations (
  minute TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  succeeded INTEGER NOT NULL CHECK (succeeded IN (0,1))
);
CREATE INDEX IF NOT EXISTS ix_inventory_observations_day ON inventory_observations(day,minute);
CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
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


def utc_minute(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# "s" denotes a device sample and "p" the membership effective in its UTC minute.
MEMBERSHIP_AT_SAMPLE = """p.mac=s.mac AND p.start_minute<=s.minute
    AND (p.end_minute IS NULL OR s.minute<p.end_minute)"""


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
                        "alias": "TEXT NOT NULL DEFAULT ''",
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

                if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='device_profiles'").fetchone():
                    self.db.execute("""CREATE TABLE device_profiles (
                        mac TEXT NOT NULL REFERENCES devices(mac) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        exempt INTEGER NOT NULL DEFAULT 0 CHECK(exempt IN (0,1)),
                        PRIMARY KEY(mac,user_id)
                    )""")
                    self.db.execute("CREATE INDEX ix_device_profiles_user ON device_profiles(user_id,mac)")
                    self.db.execute("""INSERT INTO device_profiles(mac,user_id,exempt)
                        SELECT d.mac,d.user_id,d.profile_exempt FROM devices d JOIN users u ON u.id=d.user_id""")

                if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='device_profile_history'").fetchone():
                    self.db.execute("""CREATE TABLE device_profile_history (
                        mac TEXT NOT NULL REFERENCES devices(mac) ON DELETE CASCADE,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        start_minute TEXT NOT NULL,
                        end_minute TEXT CHECK (end_minute>=start_minute),
                        PRIMARY KEY (mac,user_id,start_minute)
                    )""")
                    self.db.execute("""CREATE INDEX ix_device_profile_history_user
                        ON device_profile_history(user_id,mac,start_minute)""")
                    minute = utc_minute(datetime.now(timezone.utc))
                    self.db.execute("INSERT INTO metadata(key,value) VALUES ('profile_history_since',?)", (minute,))
                    self.db.execute("""INSERT INTO device_profile_history(mac,user_id,start_minute)
                        SELECT mac,user_id,? FROM device_profiles""", (minute,))

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.lock, self.db:
            return self.db.execute(sql, args).lastrowid

    def users(self) -> list[dict]:
        return self.query("SELECT * FROM users ORDER BY name")

    def devices(self, sample_minute: str | None = None, *, snapshot_at: str | None = None) -> list[dict]:
        with self.lock:
            devices = self.query("""SELECT d.*, s.up_kbs, s.down_kbs,
              CASE WHEN d.last_seen=? THEN 1 ELSE 0 END AS in_latest_snapshot
              FROM devices d LEFT JOIN minute_samples s ON d.online=1 AND s.mac=d.mac AND s.minute=?
              ORDER BY COALESCE(NULLIF(d.alias,''),d.name) COLLATE NOCASE, d.mac""",
                (snapshot_at, sample_minute))
            assignments = self.query("""SELECT p.*,u.name FROM device_profiles p
                JOIN users u ON u.id=p.user_id ORDER BY p.user_id""")
        by_mac = {}
        for membership in assignments:
            by_mac.setdefault(membership["mac"], []).append(membership)
        for device in devices:
            memberships = by_mac.get(device["mac"], [])
            device["user_ids"] = [row["user_id"] for row in memberships]
            device["user_names"] = [row["name"] for row in memberships]
            device["profile_exemptions"] = [row["user_id"] for row in memberships if row["exempt"]]
            # Preserve the former API fields for single-profile clients.
            device["user_id"] = device["user_ids"][0] if memberships else None
            device["user_name"] = device["user_names"][0] if memberships else None
            device["profile_exempt"] = memberships[0]["exempt"] if memberships else 0
        return devices

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

    def change_device_profiles(self, mac: str, user_ids: set[int], now: datetime, *, add_only=False):
        """Change current memberships and their history in the caller's transaction."""
        current = {row["user_id"] for row in self.query("SELECT user_id FROM device_profiles WHERE mac=?", (mac,))}
        removed = set() if add_only else current - user_ids
        added = user_ids - current
        minute = utc_minute(now)
        last = self.query("""SELECT MAX(COALESCE(end_minute,start_minute)) AS minute
            FROM device_profile_history WHERE mac=?""", (mac,))[0]["minute"]
        minute = max(minute, last or minute)
        self.db.executemany("DELETE FROM device_profiles WHERE mac=? AND user_id=?",
                            ((mac, user_id) for user_id in removed))
        self.db.executemany("""UPDATE device_profile_history SET end_minute=MAX(start_minute,?)
            WHERE mac=? AND user_id=? AND end_minute IS NULL""",
                            ((minute, mac, user_id) for user_id in removed))
        self.db.executemany("INSERT INTO device_profiles(mac,user_id) VALUES (?,?)",
                            ((mac, user_id) for user_id in added))
        self.db.executemany("""INSERT INTO device_profile_history(mac,user_id,start_minute) VALUES (?,?,?)
            ON CONFLICT(mac,user_id,start_minute) DO UPDATE SET end_minute=NULL""",
                            ((mac, user_id, minute) for user_id in added))

    def _record_observation(self, day: str, minute: str, succeeded: bool):
        self.db.execute("""INSERT INTO inventory_observations(minute,day,succeeded) VALUES (?,?,?)
            ON CONFLICT(minute) DO UPDATE SET succeeded=MAX(succeeded,excluded.succeeded)""",
                        (minute, day, int(succeeded)))

    def record_inventory_failure(self, day: str, minute: str):
        with self.lock, self.db:
            self._record_observation(day, minute, False)

    def record_inventory(self, clients: list[Client], seen_at: str, local_day: str, minute: str):
        with self.lock, self.db:
            self._record_observation(local_day, minute, True)
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
        collective = self.query(f"""SELECT p.user_id, COUNT(DISTINCT s.minute) AS minutes
          FROM minute_samples s JOIN device_profile_history p ON {MEMBERSHIP_AT_SAMPLE}
          WHERE s.day=? GROUP BY p.user_id""", (day,))
        return {"devices": {x["mac"]: x["minutes"] for x in individual},
                "users": {x["user_id"]: x["minutes"] for x in collective}}

    def usage_report(self, start: date, end: date, *, user_id: int | None = None,
                     mac: str | None = None) -> dict:
        where = ["s.day BETWEEN ? AND ?"]
        args = [start.isoformat(), end.isoformat()]
        if mac is not None:
            where.append("s.mac=?")
            args.append(mac)
        base_scope = " AND ".join(where)
        device_scope = base_scope
        device_args = tuple(args)
        if user_id is not None:
            device_scope += f" AND EXISTS (SELECT 1 FROM device_profile_history p WHERE {MEMBERSHIP_AT_SAMPLE} AND p.user_id=?)"
            device_args += (user_id,)
        speeds = """COUNT(up_kbs) AS up_samples, COUNT(down_kbs) AS down_samples,
            ROUND(AVG(up_kbs), 1) AS average_up_kbs, ROUND(AVG(down_kbs), 1) AS average_down_kbs,
            MAX(up_kbs) AS max_up_kbs, MAX(down_kbs) AS max_down_kbs"""
        profile_filter = " AND p.user_id=?" if user_id is not None else ""
        profile_args = (*args, user_id) if user_id is not None else tuple(args)
        membership = ("?" if user_id is not None else
                      f"(SELECT MIN(p.user_id) FROM device_profile_history p WHERE {MEMBERSHIP_AT_SAMPLE})")
        daily_args = (*device_args, user_id) if user_id is not None else device_args
        with self.lock:
            devices = self.query(f"""WITH daily AS (
                SELECT s.day,s.mac,MAX(s.minute) AS minute,COUNT(*) AS minutes,{speeds}
                FROM minute_samples s WHERE {device_scope} GROUP BY s.day,s.mac
            ) SELECT s.day,{membership} AS user_id,s.mac,s.minutes,
                s.up_samples,s.down_samples,s.average_up_kbs,s.average_down_kbs,s.max_up_kbs,s.max_down_kbs
                FROM daily s ORDER BY s.day DESC,s.mac""", daily_args)
            profiles = self.query(f"""SELECT s.day,p.user_id,COUNT(DISTINCT s.minute) AS minutes
                FROM minute_samples s JOIN device_profile_history p ON {MEMBERSHIP_AT_SAMPLE} WHERE {base_scope}{profile_filter}
                GROUP BY s.day,p.user_id ORDER BY s.day DESC,p.user_id""", profile_args)
            device_totals = self.query(f"""SELECT s.mac,COUNT(*) AS minutes,
                COUNT(DISTINCT s.day) AS observed_days, {speeds}
                FROM minute_samples s WHERE {device_scope} GROUP BY s.mac""", device_args)
            profile_totals = self.query(f"""SELECT p.user_id,COUNT(DISTINCT s.minute) AS minutes,
                COUNT(DISTINCT s.day) AS observed_days,COUNT(DISTINCT s.mac) AS observed_devices, {speeds}
                FROM minute_samples s JOIN device_profile_history p ON {MEMBERSHIP_AT_SAMPLE} WHERE {base_scope}{profile_filter}
                GROUP BY p.user_id""", profile_args)
            network_days = self.query(f"""SELECT s.day,COUNT(DISTINCT s.minute) AS minutes
                FROM minute_samples s WHERE {device_scope} GROUP BY s.day ORDER BY s.day DESC""", device_args)
            coverage_start = self.query("SELECT MIN(minute) AS minute FROM inventory_observations")[0]["minute"]
            # UTC hour buckets keep the two repeated hours of the DST transition distinct.
            bucket = "substr(minute,1,13)" if start == end else "day"
            coverage = self.query(f"""SELECT {bucket} AS bucket, SUM(succeeded) AS successful_minutes,
                COUNT(*)-SUM(succeeded) AS failure_only_minutes FROM inventory_observations
                WHERE day BETWEEN ? AND ? GROUP BY bucket ORDER BY bucket""",
                (start.isoformat(), end.isoformat()))
            profile_history_since = self.query("SELECT value FROM metadata WHERE key='profile_history_since'")[0]["value"]
        return {"data": devices, "profiles": profiles, "device_totals": device_totals,
                "profile_totals": profile_totals, "network_days": network_days,
                "coverage": coverage, "coverage_start": coverage_start, "profile_history_since": profile_history_since}

    def daily_usage(self, days: int = 7, *, today: date | None = None) -> list[dict]:
        today = today or date.today()
        return self.usage_report(today - timedelta(days=days - 1), today)["data"]

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
        cutoff = (first_day.isoformat(),)
        db.execute("DELETE FROM inventory_observations WHERE day<?", cutoff)
        return db.execute("DELETE FROM minute_samples WHERE day<?", cutoff).rowcount

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
                db.execute("DELETE FROM inventory_observations")
                return db.execute("DELETE FROM minute_samples").rowcount
            cutoff = (through.isoformat(),)
            db.execute("DELETE FROM inventory_observations WHERE day<=?", cutoff)
            return db.execute("DELETE FROM minute_samples WHERE day<=?", cutoff).rowcount

    def close(self):
        with self.lock:
            self.db.close()
