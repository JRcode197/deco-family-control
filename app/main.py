"""Local-only API and responsive dashboard; not for public Internet exposure."""
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo
import asyncio
import hmac
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Header, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, SecretStr, StringConstraints, field_validator

from app.config import Settings, from_env, private_router_ip, release_version
from app.deco import Adapter, MockAdapter, DecoSdkAdapter, normalize_mac
from app.diagnostics import PROBE_INTERVAL_SECONDS, ProbeCooldown
from app.engine import Engine
from app.policy import next_transitions, profile_reason
from app.profiles import TEMPLATES, Window, profiles_router
from app.store import Store

log = logging.getLogger(__name__)
security = HTTPBasic()
SUPERVISOR_IP = "172.30.32.2"
NO_CACHE = {"Cache-Control": "no-store"}


class DecoProbeCredentials(BaseModel):
    router_ip: IPv4Address
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=254)] = Field(repr=False)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("router_ip")
    @classmethod
    def private_address(cls, value: IPv4Address) -> IPv4Address:
        return private_router_ip(value)


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


def create_app(settings: Settings | None = None, *, store: Store | None = None, adapter=None) -> FastAPI:
    settings = settings or from_env()
    store = store or Store(settings.database_path)
    adapter = adapter or (MockAdapter() if settings.mode == "mock" else DecoSdkAdapter(
        settings.router_ip, settings.router_user, settings.router_password, direct=settings.ha_ingress,
    ))
    engine = Engine(store, adapter, settings)
    inventory_only = settings.ha_ingress and settings.mode == "deco"
    release = release_version()

    def admin_authenticate(credentials: HTTPBasicCredentials = Depends(security)):
        valid_user = hmac.compare_digest(credentials.username.encode(), settings.admin_user.encode())
        valid_password = hmac.compare_digest(credentials.password.encode(), settings.admin_password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(status_code=401, detail="Credenziali non valide",
                                headers={"WWW-Authenticate": "Basic"})
        return credentials.username

    def supervisor_authenticate(request: Request):
        # Uvicorn uses the TCP peer address because proxy headers are disabled.
        if request.client is None or request.client.host != SUPERVISOR_IP:
            raise HTTPException(403, "Apri l'app da Home Assistant.")

    authenticate = supervisor_authenticate if settings.ha_ingress else admin_authenticate

    def mutation_guard(x_family_action: str | None = Header(None),
                       _=Depends(authenticate)):
        # An ordinary HTML cross-origin form cannot send this custom header.
        if x_family_action != "1":
            raise HTTPException(403, "Missing anti-CSRF action header")

    async def periodic(stop: asyncio.Event):
        while not stop.is_set() and not settings.inventory_setup_error:
            try:
                await asyncio.to_thread(engine.poll)
            except Exception:
                log.warning("Interrogazione periodica del Deco non riuscita")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except TimeoutError:
                pass

    def policy_guard(_=Depends(mutation_guard)):
        if inventory_only:
            raise HTTPException(403, "L'inventario Home Assistant è in sola lettura sul Deco.")

    def inventory_ready():
        if settings.inventory_setup_error:
            raise HTTPException(503, settings.inventory_setup_error, headers=NO_CACHE)

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

    app = FastAPI(title="Deco Family Control", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None if settings.ha_ingress else "/openapi.json")
    app.state.engine = engine
    app.state.store = store
    app.include_router(profiles_router(store, settings, engine, authenticate, mutation_guard))

    @app.get("/", dependencies=[Depends(authenticate)])
    def home():
        return FileResponse(Path(__file__).parent / "static" / "index.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/dashboard.js", dependencies=[Depends(authenticate)], include_in_schema=False)
    def dashboard_script():
        return FileResponse(Path(__file__).parent / "static" / "dashboard.js",
                            media_type="text/javascript", headers=NO_CACHE)

    @app.get("/api/state", dependencies=[Depends(authenticate)])
    def state():
        now = datetime.now(ZoneInfo(settings.timezone))
        day = now.date().isoformat()
        with store.lock:
            last_poll = engine.last_poll
            last_error = settings.inventory_setup_error or engine.last_error
            devices = store.devices(sample_minute=engine.last_sample_minute, snapshot_at=last_poll)
            users = store.users()
            windows = store.windows()
            quotas = store.query("SELECT user_id,weekday,minutes FROM weekly_quotas")
            usage_today = store.usage(day)
            audit = store.audit(30)
        for user in users:
            rules = [w for w in windows if w["user_id"] == user["id"]]
            user["policy_reason"] = profile_reason(now, user, rules)
            user.update(next_transitions(now, user, rules))
        return JSONResponse({
            "release": release,
            "mode": settings.mode,
            "ha_ingress": settings.ha_ingress,
            "inventory_only": inventory_only,
            "inventory_setup_required": bool(settings.inventory_setup_error),
            "router_ip": settings.router_ip,
            "writes_enabled": settings.writes_enabled and not settings.ha_ingress,
            "quota_enforcement": engine.presence_quota_simulation,
            "timezone": settings.timezone,
            "actuator": settings.actuator,
            "protected_macs": list(settings.protected_macs),
            "last_poll": last_poll,
            "last_error": last_error,
            "users": users,
            "devices": devices,
            "windows": windows,
            "templates": TEMPLATES,
            "weekly_quotas": quotas,
            "usage_today": usage_today,
            "audit": audit,
            "stats_label": "Minuti di presenza in rete osservata dal Deco (non tempo schermo né traffico Internet)",
            "capabilities": {
                "client_inventory": last_poll is not None and not last_error,
                "manual_block": "da_verificare_su_x50" if settings.mode == "deco" else "simulato",
                "site_history": False,
                "app_usage": False,
                "bandwidth_per_device": "velocita_istantanea_campionata" if settings.mode == "deco" else False,
            },
        }, headers=NO_CACHE)

    @app.post("/api/refresh", dependencies=[Depends(mutation_guard), Depends(inventory_ready)])
    async def refresh():
        try:
            return await asyncio.to_thread(engine.poll)
        except Exception:
            raise HTTPException(503, "Impossibile interrogare il Deco. Controlla la diagnostica.") from None

    @app.get("/api/diagnostics", dependencies=[Depends(authenticate)])
    def diagnostics():
        return JSONResponse(asdict(engine.diagnostics), headers={"Cache-Control": "no-store"})

    @app.post("/api/diagnostics", dependencies=[Depends(mutation_guard), Depends(inventory_ready)])
    async def probe_diagnostics():
        return await run_diagnostics()

    @app.post("/api/diagnostics/deco", dependencies=[Depends(mutation_guard)])
    async def probe_local_deco(request: Request):
        try:
            credentials = DecoProbeCredentials.model_validate_json(await request.body())
        except ValueError:
            # The default validation response can echo the submitted payload.
            raise HTTPException(422, "Inserisci un IPv4 privato, il nome utente e la password del Deco.") from None
        probe_adapter = DecoSdkAdapter(
            str(credentials.router_ip), credentials.username,
            credentials.password.get_secret_value(), direct=True,
        )
        return await run_diagnostics(probe_adapter)

    async def run_diagnostics(probe_adapter: Adapter | None = None):
        try:
            report = await asyncio.to_thread(engine.probe_diagnostics, probe_adapter)
        except ProbeCooldown:
            raise HTTPException(
                429, "Attendi un minuto tra una verifica e l'altra.",
                headers={"Retry-After": str(PROBE_INTERVAL_SECONDS), "Cache-Control": "no-store"},
            ) from None
        return JSONResponse(asdict(report), headers={"Cache-Control": "no-store"})

    @app.post("/api/assign", dependencies=[Depends(mutation_guard)])
    def assign(payload: Assignment):
        try:
            mac = normalize_mac(payload.mac)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store.policy_change("assegnazione_dispositivo", f"profilo={payload.user_id or 'nessuno'}") as db:
            if payload.user_id is not None and not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
                raise HTTPException(404, "Profilo inesistente.")
            result = db.execute("""UPDATE devices SET user_id=?,
                profile_exempt=CASE WHEN user_id IS ? THEN profile_exempt ELSE 0 END WHERE mac=?""",
                (payload.user_id, payload.user_id, mac))
            if not result.rowcount:
                raise HTTPException(404, "Dispositivo non trovato. Aggiorna l’elenco.")
        return {"ok": True}

    @app.post("/api/manual-block", dependencies=[Depends(policy_guard)])
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
            store.execute("UPDATE users SET manually_blocked=?,override_until=NULL WHERE id=?", (int(payload.blocked), payload.user_id))
            store.log("manual_rule", "ok", detail=f"user={payload.user_id} blocked={payload.blocked}")
        return {"ok": True, "actions": engine.enforce()}

    @app.post("/api/quota", dependencies=[Depends(policy_guard)])
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

    @app.post("/api/windows", dependencies=[Depends(policy_guard)])
    def new_window(payload: Window):
        if payload.start_time == payload.end_time:
            raise HTTPException(422, "Start and end must differ")
        if not store.query("SELECT id FROM users WHERE id=?", (payload.user_id,)):
            raise HTTPException(404, "Utente inesistente")
        w_id = store.execute("""INSERT INTO windows(user_id,weekday,start_time,end_time)
            VALUES(?,?,?,?)""", (payload.user_id, payload.weekday,
                                  payload.start_time, payload.end_time))
        return {"id": w_id, "actions": engine.enforce()}

    @app.delete("/api/windows/{window_id}", dependencies=[Depends(policy_guard)])
    def delete_window(window_id: int):
        store.execute("DELETE FROM windows WHERE id=?", (window_id,))
        return {"ok": True, "actions": engine.enforce()}

    @app.get("/api/usage", dependencies=[Depends(authenticate)])
    def usage(days: int = Query(7, ge=1, le=90), start: date | None = None,
              end: date | None = None, user_id: int | None = None, mac: str | None = None):
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        if (start is None) != (end is None):
            raise HTTPException(422, "Specifica entrambe le date.")
        if start is None:
            start, end = today - timedelta(days=days - 1), today
        if not 0 <= (end - start).days < 366 or end > today:
            raise HTTPException(422, "Scegli un periodo valido fino a 366 giorni e senza date future.")
        if mac is not None:
            try:
                mac = normalize_mac(mac)
            except ValueError:
                raise HTTPException(422, "Indirizzo MAC non valido.") from None
        devices, profiles = store.usage_report(start, end, user_id=user_id, mac=mac)
        return JSONResponse({
            "data": devices,
            "profiles": profiles,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": settings.timezone,
            "poll_seconds": settings.poll_seconds,
            "retention_days": settings.retention_days,
            "source": "Inventario Deco" if settings.mode == "deco" else "Simulazione",
            "label": "Presenza in rete osservata per minuto UTC e velocità istantanee (KB/s). "
                     "I profili usano le assegnazioni attuali.",
        }, headers=NO_CACHE)

    @app.delete("/api/usage", dependencies=[Depends(mutation_guard)])
    def clear_usage(through: date | None = None):
        return {"removed": store.clear_samples(through)}

    return app


def __getattr__(name: str):
    # Preserve app.main:app for direct Uvicorn startup.
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
