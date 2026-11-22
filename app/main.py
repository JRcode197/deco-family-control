"""Local-only API and responsive dashboard; not for public Internet exposure."""
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import asyncio
import hmac
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, field_validator

from app.config import Settings, from_env
from app.deco import MockAdapter, DecoSdkAdapter, normalize_mac
from app.engine import Engine
from app.policy import to_minutes
from app.store import Store

log = logging.getLogger(__name__)
security = HTTPBasic()


class NewUser(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class Assignment(BaseModel):
    mac: str
    user_id: int | None = None


class BlockRequest(BaseModel):
    mac: str | None = None
    user_id: int | None = None
    blocked: bool


class Quota(BaseModel):
    user_id: int | None = None
    mac: str | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440)


class Window(BaseModel):
    user_id: int
    weekday: int = Field(ge=0, le=6)
    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("Use HH:MM")
        to_minutes(value)
        return value


def create_app(settings: Settings | None = None, *, store: Store | None = None, adapter=None) -> FastAPI:
    settings = settings or from_env()
    store = store or Store(settings.database_path)
    adapter = adapter or (MockAdapter() if settings.mode == "mock" else
                          DecoSdkAdapter(settings.router_ip, settings.router_user, settings.router_password))
    engine = Engine(store, adapter, settings)

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
        valid_user = hmac.compare_digest(credentials.username.encode(), settings.admin_user.encode())
        valid_password = hmac.compare_digest(credentials.password.encode(), settings.admin_password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(status_code=401, detail="Credenziali non valide",
                                headers={"WWW-Authenticate": "Basic"})
        return credentials.username

    def mutation_guard(x_family_action: str | None = Header(None),
                       _=Depends(authenticate)):
        # An ordinary HTML cross-origin form cannot send this custom header.
        if x_family_action != "1":
            raise HTTPException(403, "Missing anti-CSRF action header")

    async def periodic(stop: asyncio.Event):
        while not stop.is_set():
            try:
                await asyncio.to_thread(engine.poll)
            except Exception:
                log.warning("Interrogazione periodica del Deco non riuscita")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = asyncio.create_task(periodic(stop))
        try:
            yield
        finally:
            stop.set()
            try:
                await task
            finally:
                store.close()

    app = FastAPI(title="Deco Family Control", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.engine = engine
    app.state.store = store

    @app.get("/", dependencies=[Depends(authenticate)])
    def home():
        return FileResponse(Path(__file__).parent / "static" / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/state", dependencies=[Depends(authenticate)])
    def state():
        day = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        return {
            "mode": settings.mode,
            "router_ip": settings.router_ip,
            "writes_enabled": settings.writes_enabled,
            "quota_enforcement": settings.quota_enforcement,
            "actuator": settings.actuator,
            "protected_macs": list(settings.protected_macs),
            "last_poll": engine.last_poll,
            "last_error": engine.last_error,
            "users": store.users(),
            "devices": store.devices(),
            "windows": store.windows(),
            "usage_today": store.usage(day),
            "audit": store.audit(30),
            "stats_label": "Minuti di presenza Wi-Fi osservata (non tempo schermo né traffico Internet)",
            "capabilities": {
                "client_inventory": engine.last_poll is not None and not engine.last_error,
                "manual_block": "da_verificare_su_x50" if settings.mode == "deco" else "simulato",
                "site_history": False,
                "app_usage": False,
                "bandwidth_per_device": "velocita_istantanea_campionata" if settings.mode == "deco" else False,
            },
        }

    @app.post("/api/refresh", dependencies=[Depends(mutation_guard)])
    async def refresh():
        try:
            return await asyncio.to_thread(engine.poll)
        except Exception:
            raise HTTPException(503, "Impossibile interrogare il Deco. Controlla la diagnostica.") from None

    @app.get("/api/diagnostics", dependencies=[Depends(authenticate)])
    def diagnostics():
        return JSONResponse(asdict(engine.diagnostics), headers={"Cache-Control": "no-store"})

    @app.post("/api/diagnostics", dependencies=[Depends(mutation_guard)])
    def probe_diagnostics():
        report = engine.probe_diagnostics()
        return JSONResponse(asdict(report), headers={"Cache-Control": "no-store"})

    @app.post("/api/users", dependencies=[Depends(mutation_guard)])
    def new_user(payload: NewUser):
        try:
            return {"id": store.execute("INSERT INTO users(name) VALUES (?)", (payload.name.strip(),))}
        except Exception as exc:
            raise HTTPException(409, "Utente già presente o non valido") from exc

    @app.post("/api/assign", dependencies=[Depends(mutation_guard)])
    def assign(payload: Assignment):
        try:
            mac = normalize_mac(payload.mac)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if payload.user_id is not None and not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
            raise HTTPException(404, "Utente inesistente")
        if not store.query("SELECT mac FROM devices WHERE mac=?", (mac,)):
            raise HTTPException(404, "Dispositivo non trovato: aggiorna elenco")
        store.execute("UPDATE devices SET user_id=? WHERE mac=?", (payload.user_id, mac))
        store.log("assign", "ok", mac, str(payload.user_id))
        return {"ok": True}

    @app.post("/api/manual-block", dependencies=[Depends(mutation_guard)])
    def manual_block(payload: BlockRequest):
        if (payload.mac is None) == (payload.user_id is None):
            raise HTTPException(422, "Specify exactly one of mac or user_id")
        if payload.mac is not None:
            mac = normalize_mac(payload.mac)
            if mac in settings.protected_macs:
                raise HTTPException(403, "Dispositivo amministrativo protetto")
            if not store.query("SELECT mac FROM devices WHERE mac=?", (mac,)):
                raise HTTPException(404, "Dispositivo inesistente")
            store.execute("UPDATE devices SET manually_blocked=? WHERE mac=?", (int(payload.blocked), mac))
            store.log("manual_rule", "ok", mac, f"blocked={payload.blocked}")
        else:
            if not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
                raise HTTPException(404, "Utente inesistente")
            store.execute("UPDATE users SET manually_blocked=? WHERE id=?", (int(payload.blocked), payload.user_id))
            store.log("manual_rule", "ok", detail=f"user={payload.user_id} blocked={payload.blocked}")
        return {"ok": True, "actions": engine.enforce()}

    @app.post("/api/quota", dependencies=[Depends(mutation_guard)])
    def quota(payload: Quota):
        if (payload.mac is None) == (payload.user_id is None):
            raise HTTPException(422, "Specify exactly one target")
        if payload.mac is not None:
            mac = normalize_mac(payload.mac)
            if not store.query("SELECT mac FROM devices WHERE mac=?", (mac,)):
                raise HTTPException(404, "Dispositivo inesistente")
            store.execute("UPDATE devices SET quota_minutes=? WHERE mac=?", (payload.minutes, mac))
        else:
            if not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
                raise HTTPException(404, "Utente inesistente")
            store.execute("UPDATE users SET quota_minutes=? WHERE id=?", (payload.minutes, payload.user_id))
        return {"ok": True, "note": "Quota based on observed Wi-Fi presence; not screen time"}

    @app.post("/api/windows", dependencies=[Depends(mutation_guard)])
    def new_window(payload: Window):
        if payload.start_time == payload.end_time:
            raise HTTPException(422, "Start and end must differ")
        if not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
            raise HTTPException(404, "Utente inesistente")
        w_id = store.execute("""INSERT INTO windows(user_id,weekday,start_time,end_time)
            VALUES(?,?,?,?)""", (payload.user_id, payload.weekday,
                                  payload.start_time, payload.end_time))
        return {"id": w_id, "actions": engine.enforce()}

    @app.delete("/api/windows/{window_id}", dependencies=[Depends(mutation_guard)])
    def delete_window(window_id: int):
        store.execute("DELETE FROM windows WHERE id=?", (window_id,))
        return {"ok": True, "actions": engine.enforce()}

    @app.get("/api/usage", dependencies=[Depends(authenticate)])
    def usage(days: int = Query(7, ge=1, le=90)):
        return {"data": store.daily_usage(days),
                "label": "Observed connection minutes and sampled instantaneous speeds (KB/s), NOT data usage totals, app usage or visited websites"}

    return app


app = create_app()
