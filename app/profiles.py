"""Authenticated family profile configuration and policy previews."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import sqlite3
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, field_validator, model_validator

from app.deco import normalize_mac
from app.policy import device_reason, next_transitions, pending, profile_reason, schedule_active, weekdays_mask
from app.store import Store

Day = Annotated[int, Field(ge=0, le=6)]
DailyMinutes = Annotated[int, Field(ge=1, le=1440)]
RuleGroup = Literal["offtime", "bedtime", "quotas"]
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
SCHEDULE_FIELDS = ("weekday", "start_time", "end_time", "kind", "label", "enabled", "days_mask", "paused_until")
INSERT_SCHEDULE = """INSERT INTO windows
    (user_id,weekday,start_time,end_time,kind,label,enabled,days_mask,paused_until)
    VALUES (?,?,?,?,?,?,?,?,?)"""
TEMPLATES = (
    {"label": "Notti scolastiche", "weekdays": [0, 1, 2, 3, 4], "start_time": "20:30", "end_time": "07:00", "kind": "bedtime"},
    {"label": "Notti del fine settimana", "weekdays": [5, 6], "start_time": "21:30", "end_time": "08:00", "kind": "bedtime"},
    {"label": "Studio", "weekdays": [0, 1, 2, 3, 4], "start_time": "16:00", "end_time": "17:30", "kind": "offtime"},
    {"label": "Cena", "weekdays": list(range(7)), "start_time": "19:30", "end_time": "20:15", "kind": "offtime"},
)


class Profile(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    avatar: str = Field(default="", max_length=12)
    description: str = Field(default="", max_length=300)
    age_band: str = Field(default="", max_length=40)


class Times(BaseModel):
    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not CLOCK.fullmatch(value):
            raise ValueError("Usa un orario valido nel formato HH:MM.")
        return value


class Window(Times):
    user_id: int
    weekday: Day


class Schedule(Times):
    weekdays: set[Day] = Field(min_length=1, max_length=7)
    kind: Literal["offtime", "bedtime"] = "offtime"
    label: str = Field(default="", max_length=60)
    enabled: bool = True

    @model_validator(mode="after")
    def different_times(self):
        if self.start_time == self.end_time:
            raise ValueError("Gli orari di inizio e fine devono essere diversi.")
        return self

    def values(self) -> tuple:
        return (min(self.weekdays), self.start_time, self.end_time, self.kind,
                self.label, int(self.enabled), sum(1 << day for day in self.weekdays))


class Pause(BaseModel):
    minutes: int | None = Field(default=None, ge=1, le=1440)
    until: AwareDatetime | None = None

    @model_validator(mode="after")
    def one_expiry(self):
        if self.minutes is not None and self.until is not None:
            raise ValueError("Scegli una durata o un orario di fine.")
        return self

    def expiry(self, now: datetime) -> str | None:
        if self.until is not None:
            utc = self.until.astimezone(timezone.utc)
            if not now < utc <= now + timedelta(days=1):
                raise HTTPException(422, "Scegli un orario futuro entro 24 ore.")
            return utc.isoformat()
        return (now + timedelta(minutes=self.minutes)).isoformat() if self.minutes else None


class ProfileAction(Pause):
    action: Literal["block", "unblock", "pause", "resume"]

    @model_validator(mode="after")
    def pause_requires_expiry(self):
        if (self.action == "pause") != (self.minutes is not None or self.until is not None):
            raise ValueError("Indica una scadenza per la sospensione.")
        return self


class DeviceException(BaseModel):
    exempt: bool


class WeeklyQuotas(BaseModel):
    days: dict[Day, DailyMinutes] = Field(max_length=7)


class CopySelection(BaseModel):
    source_id: int
    groups: set[RuleGroup] = Field(min_length=1, max_length=3)


class CopyRequest(CopySelection):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def profile_config(store: Store, user_id: int, groups: set[str], *, copying: bool = False) -> dict:
    result = {}
    schedules = store.query("""SELECT * FROM windows WHERE user_id=?
        ORDER BY weekday,start_time,end_time,label,enabled,days_mask,paused_until,id""", (user_id,))
    for group in sorted(groups):
        if group == "quotas":
            result[group] = {str(row["weekday"]): row["minutes"] for row in store.query(
                "SELECT weekday,minutes FROM weekly_quotas WHERE user_id=?", (user_id,)
            )}
            continue
        rows = []
        for window in schedules:
            if window["kind"] == group:
                row = {key: window[key] for key in SCHEDULE_FIELDS}
                row["days_mask"] = weekdays_mask(window)
                if copying:
                    row["paused_until"] = None
                rows.append(row)
        result[group] = sorted(rows, key=canonical)
    return result


def replace_config(db, user_id: int, config: dict):
    for group, value in config.items():
        if group == "quotas":
            db.execute("DELETE FROM weekly_quotas WHERE user_id=?", (user_id,))
            db.executemany("INSERT INTO weekly_quotas(user_id,weekday,minutes) VALUES (?,?,?)",
                           ((user_id, int(day), minutes) for day, minutes in value.items()))
        else:
            db.execute("DELETE FROM windows WHERE user_id=? AND kind=?", (user_id, group))
            db.executemany(INSERT_SCHEDULE, ((user_id, *(row[key] for key in SCHEDULE_FIELDS)) for row in value))


def profiles_router(store, settings, engine, authenticate, mutation_guard) -> APIRouter:
    router = APIRouter(prefix="/api/users", dependencies=[Depends(authenticate)])
    mutating = [Depends(mutation_guard)]
    zone = ZoneInfo(settings.timezone)

    def after_change():
        if settings.mode == "mock" and settings.writes_enabled:
            return {"ok": True, "actions": engine.enforce()}
        return {"ok": True}

    def profile(user_id: int) -> dict:
        matches = store.query("SELECT * FROM users WHERE id=?", (user_id,))
        if not matches:
            raise HTTPException(404, "Profilo inesistente.")
        return matches[0]

    def windows(user_id: int) -> list[dict]:
        return store.query("SELECT * FROM windows WHERE user_id=?", (user_id,))

    def check_schedule(user_id: int, window_id: int):
        if not store.query("SELECT id FROM windows WHERE id=? AND user_id=?", (window_id, user_id)):
            raise HTTPException(404, "Fascia oraria inesistente.")

    @router.post("", dependencies=mutating)
    def create_profile(payload: Profile):
        try:
            with store.policy_change("creazione_profilo", "") as db:
                cursor = db.execute("INSERT INTO users(name,avatar,description,age_band) VALUES (?,?,?,?)",
                                    (payload.name, payload.avatar, payload.description, payload.age_band))
                return {"id": cursor.lastrowid}
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Profilo già presente.") from None

    @router.put("/{user_id}", dependencies=mutating)
    def update_user(user_id: int, payload: Profile):
        try:
            with store.policy_change("modifica_profilo", f"profilo={user_id}") as db:
                profile(user_id)
                db.execute("UPDATE users SET name=?,avatar=?,description=?,age_band=? WHERE id=?",
                           (payload.name, payload.avatar, payload.description, payload.age_band, user_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Profilo già presente.") from None
        return {"ok": True}

    @router.delete("/{user_id}", dependencies=mutating)
    def delete_user(user_id: int):
        with store.policy_change("eliminazione_profilo", f"profilo={user_id}") as db:
            profile(user_id)
            db.execute("UPDATE devices SET profile_exempt=0 WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
        return after_change()

    @router.get("/{user_id}/preview")
    def preview_user(user_id: int):
        with store.lock:
            user = profile(user_id)
            rules = windows(user_id)
            devices = store.query("SELECT * FROM devices WHERE user_id=?", (user_id,))
        now = datetime.now(zone)
        reason = profile_reason(now, user, rules)
        return JSONResponse({
            "checked_at": now.isoformat(),
            "timezone": settings.timezone,
            "reason": reason,
            "override_until": user["override_until"] if pending(now, user["override_until"]) else None,
            "schedules": {
                w["id"]: {
                    "active": schedule_active(now, [w]),
                    "paused": pending(now, w["paused_until"]),
                } for w in rules
            },
            "devices": [{
                "mac": d["mac"],
                "protected": d["mac"] in settings.protected_macs,
                "reason": None if d["mac"] in settings.protected_macs else device_reason(d, reason),
            } for d in devices],
            **next_transitions(now, user, rules),
        }, headers={"Cache-Control": "no-store"})

    @router.post("/{user_id}/action", dependencies=mutating)
    def profile_action(user_id: int, payload: ProfileAction):
        now = datetime.now(timezone.utc)
        with store.policy_change("regola_profilo", f"profilo={user_id} operazione={payload.action}") as db:
            user = profile(user_id)
            manual = user["manually_blocked"]
            until = None
            if payload.action == "block":
                manual = 1
            elif payload.action == "unblock":
                manual = 0
                released = {**user, "manually_blocked": 0, "override_until": None}
                local = now.astimezone(zone)
                rules = windows(user_id)
                if profile_reason(local, released, rules):
                    release = next_transitions(local, released, rules)["next_release"]
                    limit = now + timedelta(days=1)
                    until = min(datetime.fromisoformat(release), limit).isoformat() if release else limit.isoformat()
            elif payload.action == "pause":
                until = payload.expiry(now)
            db.execute("UPDATE users SET manually_blocked=?,override_until=? WHERE id=?", (manual, until, user_id))
        return after_change()

    @router.put("/{user_id}/devices/{mac}/exception", dependencies=mutating)
    def device_exception(user_id: int, mac: str, payload: DeviceException):
        try:
            mac = normalize_mac(mac)
        except ValueError:
            raise HTTPException(422, "Indirizzo MAC non valido.") from None
        with store.policy_change("eccezione_dispositivo", f"profilo={user_id}") as db:
            changed = db.execute("UPDATE devices SET profile_exempt=? WHERE mac=? AND user_id=?",
                                 (int(payload.exempt), mac, user_id))
            if not changed.rowcount:
                raise HTTPException(404, "Dispositivo non assegnato a questo profilo.")
        return after_change()

    @router.post("/{user_id}/schedules", dependencies=mutating)
    def create_schedule(user_id: int, payload: Schedule):
        with store.policy_change("creazione_fascia", f"profilo={user_id}") as db:
            profile(user_id)
            window_id = db.execute(INSERT_SCHEDULE, (user_id, *payload.values(), None)).lastrowid
        return {"id": window_id, **after_change()}

    @router.put("/{user_id}/schedules/{window_id}", dependencies=mutating)
    def update_schedule(user_id: int, window_id: int, payload: Schedule):
        with store.policy_change("modifica_fascia", f"profilo={user_id} fascia={window_id}") as db:
            check_schedule(user_id, window_id)
            db.execute("""UPDATE windows SET weekday=?,start_time=?,end_time=?,kind=?,label=?,enabled=?,days_mask=?
                WHERE id=?""", (*payload.values(), window_id))
        return after_change()

    @router.post("/{user_id}/schedules/{window_id}/pause", dependencies=mutating)
    def pause_schedule(user_id: int, window_id: int, payload: Pause):
        with store.policy_change("sospensione_fascia", f"profilo={user_id} fascia={window_id}") as db:
            check_schedule(user_id, window_id)
            db.execute("UPDATE windows SET paused_until=? WHERE id=?",
                       (payload.expiry(datetime.now(timezone.utc)), window_id))
        return after_change()

    @router.delete("/{user_id}/schedules/{window_id}", dependencies=mutating)
    def remove_schedule(user_id: int, window_id: int):
        with store.policy_change("eliminazione_fascia", f"profilo={user_id} fascia={window_id}") as db:
            check_schedule(user_id, window_id)
            db.execute("DELETE FROM windows WHERE id=?", (window_id,))
        return after_change()

    @router.put("/{user_id}/weekly-quotas", dependencies=mutating)
    def save_quotas(user_id: int, payload: WeeklyQuotas):
        with store.policy_change("obiettivi_settimanali", f"profilo={user_id}") as db:
            profile(user_id)
            replace_config(db, user_id, {"quotas": payload.days})
        return {"ok": True, "enforcement": False}

    def copy_preview(user_id: int, payload: CopySelection) -> dict:
        if user_id == payload.source_id:
            raise HTTPException(422, "Scegli due profili diversi.")
        profile(user_id)
        profile(payload.source_id)
        before = profile_config(store, user_id, payload.groups)
        after = profile_config(store, payload.source_id, payload.groups, copying=True)
        revision = sha256(canonical({"target": user_id, "source": payload.source_id,
                                     "before": before, "after": after}).encode()).hexdigest()
        return {"before": before, "after": after, "revision": revision}

    @router.post("/{user_id}/copy/preview", dependencies=mutating)
    def preview_copy(user_id: int, payload: CopySelection):
        with store.lock:
            result = copy_preview(user_id, payload)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @router.post("/{user_id}/copy", dependencies=mutating)
    def copy_rules(user_id: int, payload: CopyRequest):
        with store.policy_change("copia_regole", f"profilo={user_id} origine={payload.source_id}") as db:
            preview = copy_preview(user_id, payload)
            if preview["revision"] != payload.revision:
                raise HTTPException(409, "Le regole sono cambiate. Aggiorna l’anteprima.")
            replace_config(db, user_id, preview["after"])
            copy_id = db.execute("""INSERT INTO schedule_copies(user_id,time,before_json,after_json)
                VALUES(?,?,?,?)""", (user_id, datetime.now(timezone.utc).isoformat(),
                                     canonical(preview["before"]), canonical(preview["after"]))).lastrowid
        return {"id": copy_id, **after_change()}

    @router.get("/{user_id}/copy")
    def copy_history(user_id: int):
        with store.lock:
            profile(user_id)
            rows = store.query("""SELECT id,time,before_json,restored_at FROM schedule_copies
                WHERE user_id=? ORDER BY id DESC LIMIT 30""", (user_id,))
        for row in rows:
            row["groups"] = list(json.loads(row.pop("before_json")))
        return JSONResponse(rows, headers={"Cache-Control": "no-store"})

    @router.post("/{user_id}/copy/{copy_id}/restore", dependencies=mutating)
    def restore_rules(user_id: int, copy_id: int):
        with store.policy_change("ripristino_regole", f"profilo={user_id} copia={copy_id}") as db:
            rows = store.query("SELECT * FROM schedule_copies WHERE id=? AND user_id=?", (copy_id, user_id))
            if not rows:
                raise HTTPException(404, "Copia inesistente.")
            copy = rows[0]
            before = json.loads(copy["before_json"])
            if copy["restored_at"] or canonical(profile_config(store, user_id, set(before))) != copy["after_json"]:
                raise HTTPException(409, "La copia è già stata ripristinata o le regole sono cambiate.")
            replace_config(db, user_id, before)
            db.execute("UPDATE schedule_copies SET restored_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), copy_id))
        return after_change()

    return router
