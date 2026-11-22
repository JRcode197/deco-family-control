"""SQLite persistence. All public DB operations share a re-entrant lock."""
import sqlite3
from pathlib import Path
from threading import RLock
from datetime import datetime

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
CREATE TABLE IF NOT EXISTS minute_samples (
  day TEXT NOT NULL, minute TEXT NOT NULL,
  mac TEXT NOT NULL REFERENCES devices(mac) ON DELETE CASCADE,
  up_kbs INTEGER, down_kbs INTEGER,
  PRIMARY KEY (minute, mac)
);
CREATE INDEX IF NOT EXISTS ix_sample_mac_day ON minute_samples (mac, day);
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
            self.db.commit()

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def users(self) -> list[dict]:
        return self.query("SELECT * FROM users ORDER BY name")

    def devices(self) -> list[dict]:
        return self.query("SELECT d.*, u.name AS user_name FROM devices d LEFT JOIN users u ON u.id = d.user_id ORDER BY d.name")

    def windows(self) -> list[dict]:
        return self.query("SELECT * FROM windows ORDER BY user_id, weekday, start_time")

    def audit(self, limit: int = 50) -> list[dict]:
        return self.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def log(self, action: str, status: str, mac: str = "", detail: str = ""):
        self.execute("INSERT INTO audit(time,mac,action,status,detail) VALUES (?,?,?,?,?)", (
            datetime.now().astimezone().isoformat(timespec="seconds"), mac, action, status, detail[:300]))

    def upsert_client(self, client, seen_at: str):
        self.execute("""INSERT INTO devices(mac,name,ip,online,client_type,last_seen)
          VALUES(?,?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET
          name=excluded.name, ip=excluded.ip, online=excluded.online,
          client_type=excluded.client_type, last_seen=excluded.last_seen""",
          (client.mac, client.name, client.ip, int(client.online), client.client_type, seen_at))

    def seen_offline_except(self, macs: list[str]):
        if macs:
            placeholders = ",".join("?" for _ in macs)
            self.execute(f"UPDATE devices SET online=0 WHERE mac NOT IN ({placeholders})", tuple(macs))
        else:
            self.execute("UPDATE devices SET online=0")

    def observe(self, local_day: str, minute: str, mac: str,
                up_kbs: int | None = None, down_kbs: int | None = None):
        self.execute("""INSERT INTO minute_samples(day,minute,mac,up_kbs,down_kbs)
          VALUES (?,?,?,?,?) ON CONFLICT(minute,mac) DO UPDATE SET
          up_kbs=excluded.up_kbs,down_kbs=excluded.down_kbs""",
          (local_day, minute, mac, up_kbs, down_kbs))

    def usage(self, day: str) -> dict:
        individual = self.query("""SELECT mac, COUNT(*) AS minutes FROM minute_samples
          WHERE day=? GROUP BY mac""", (day,))
        collective = self.query("""SELECT d.user_id, COUNT(DISTINCT s.minute) AS minutes
          FROM minute_samples s JOIN devices d ON d.mac=s.mac
          WHERE s.day=? AND d.user_id IS NOT NULL GROUP BY d.user_id""", (day,))
        return {"devices": {x["mac"]: x["minutes"] for x in individual},
                "users": {x["user_id"]: x["minutes"] for x in collective}}

    def daily_usage(self, days: int = 7) -> list[dict]:
        return self.query("""SELECT day, d.user_id, d.mac, COUNT(*) AS minutes,
          ROUND(AVG(up_kbs), 1) AS average_up_kbs,
          ROUND(AVG(down_kbs), 1) AS average_down_kbs,
          MAX(up_kbs) AS max_up_kbs, MAX(down_kbs) AS max_down_kbs
          FROM minute_samples s JOIN devices d USING(mac)
          WHERE day>=date('now', ?)
          GROUP BY day, d.user_id, d.mac ORDER BY day DESC""", (f"-{days-1} days",))

    def close(self):
        with self.lock:
            self.db.close()
