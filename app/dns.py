"""Local DNS policy configuration and explicit AdGuard Home rule export."""
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, ValidationError, model_validator

from app.deco import normalize_mac
from app.policy import schedule_active
from app.profiles import Day, Times, canonical, replace_config

Category = Literal["adult", "gambling", "threats", "social", "streaming", "games"]
Service = Literal["youtube", "tiktok", "roblox", "instagram", "twitch"]
CATEGORIES = {
    "adult": "Contenuti per adulti", "gambling": "Gioco d’azzardo", "threats": "Malware e phishing",
    "social": "Social network", "streaming": "Video e streaming", "games": "Giochi online",
}
SERVICES = {
    "youtube": ("YouTube", ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com",
                           "youtubei.googleapis.com", "youtube.googleapis.com", "youtube-nocookie.com")),
    "tiktok": ("TikTok", ("tiktok.com", "tiktokv.com", "tiktokcdn.com", "musical.ly")),
    "roblox": ("Roblox", ("roblox.com", "rbxcdn.com")),
    "instagram": ("Instagram", ("instagram.com", "cdninstagram.com")),
    "twitch": ("Twitch", ("twitch.tv", "ttvnw.net", "jtvnw.net")),
}
SAFE_SEARCH = {
    "www.google.com": "forcesafesearch.google.com",
    "www.google.it": "forcesafesearch.google.com",
    "www.bing.com": "strict.bing.com",
}
YOUTUBE_HOSTS = ("www.youtube.com", "m.youtube.com", "youtubei.googleapis.com",
                 "youtube.googleapis.com", "www.youtube-nocookie.com")
YOUTUBE_MODE = {"moderate": "restrictmoderate.youtube.com", "strict": "restrict.youtube.com"}
DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def dns_domain(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Inserisci un dominio DNS.")
    try:
        domain = value.strip().removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Dominio DNS non valido.") from None
    labels = domain.split(".")
    if (len(domain) > 253 or len(labels) < 2 or labels[-1].isdigit()
            or any(not DOMAIN_LABEL.fullmatch(label) for label in labels)):
        raise ValueError("Inserisci un dominio senza URL, indirizzi IP o caratteri jolly.")
    return domain


Domain = Annotated[str, BeforeValidator(dns_domain)]
ClientName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")]


class Domains(BaseModel):
    blocked_domains: set[Domain] = Field(default_factory=set, max_length=1000)
    allowed_domains: set[Domain] = Field(default_factory=set, max_length=1000)


class WebPolicy(Domains):
    categories: set[Category] = Field(default_factory=set)
    safe_search: bool = False
    youtube_mode: Literal["off", "moderate", "strict"] = "off"


class DeviceDns(Domains):
    client_name: ClientName


class ServiceWindow(Times):
    service: Service
    weekdays: set[Day] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def different_times(self):
        if self.start_time == self.end_time:
            raise ValueError("Gli orari di inizio e fine devono essere diversi.")
        return self

    def rule(self):
        return {"days_mask": sum(1 << day for day in self.weekdays),
                "start_time": self.start_time, "end_time": self.end_time}


class ServicePolicy(BaseModel):
    blocked_services: set[Service] = Field(default_factory=set)
    allowed_services: set[Service] = Field(default_factory=set)
    windows: list[ServiceWindow] = Field(default_factory=list, max_length=50)


class ImportDomains(Domains):
    user_ids: set[int] = Field(min_length=1, max_length=50)


class CategoryList(BaseModel):
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
    domains: set[Domain] = Field(min_length=1, max_length=10000)


def ordered_policy(policy: BaseModel) -> dict:
    values = policy.model_dump(mode="json")
    for name, value in policy:
        if isinstance(value, set):
            values[name] = sorted(value)
    if isinstance(policy, ServicePolicy):
        values["windows"] = [
            {**window.model_dump(mode="json"), "weekdays": sorted(window.weekdays)}
            for window in policy.windows
        ]
    return values


def matches_domain(host: str, domains: set[str]) -> bool:
    parts = host.split(".")
    return any(".".join(parts[index:]) in domains for index in range(len(parts) - 1))


def export_rules(client: DeviceDns, web: WebPolicy, services: ServicePolicy, categories: dict) -> list[str]:
    blocked = web.blocked_domains | client.blocked_domains
    allowed = web.allowed_domains | client.allowed_domains
    for name in web.categories:
        blocked.update(categories.get(name, []))
    for service in services.blocked_services:
        blocked.update(SERVICES[service][1])
    for service in services.allowed_services:
        allowed.update(SERVICES[service][1])

    suffix = "$client='" + client.client_name + "'"
    rules = ["||" + domain + "^" + suffix for domain in sorted(blocked) if not matches_domain(domain, allowed)]
    rules += ["@@||" + domain + "^" + suffix for domain in sorted(allowed)]
    rewrites = SAFE_SEARCH.copy() if web.safe_search else {}
    if web.youtube_mode != "off":
        rewrites.update(dict.fromkeys(YOUTUBE_HOSTS, YOUTUBE_MODE[web.youtube_mode]))
    for host, target in sorted(rewrites.items()):
        if not (matches_domain(host, allowed) or matches_domain(host, blocked)):
            rules.append(f"||{host}^{suffix},dnsrewrite=NOERROR;CNAME;{target}")
    return rules


def dns_router(store, settings, authenticate, mutation_guard) -> APIRouter:
    router = APIRouter(prefix="/api/dns", dependencies=[Depends(authenticate)])
    mutate = [Depends(mutation_guard)]
    zone = ZoneInfo(settings.timezone)

    def load_profile(user_id: int) -> tuple[WebPolicy, ServicePolicy]:
        rows = store.query("SELECT group_name,data FROM dns_profiles WHERE user_id=?", (user_id,))
        values = {row["group_name"]: json.loads(row["data"]) for row in rows}
        return WebPolicy.model_validate(values.get("web", {})), ServicePolicy.model_validate(values.get("services", {}))

    def check_profile(user_id: int):
        if not store.query("SELECT id FROM users WHERE id=?", (user_id,)):
            raise HTTPException(404, "Profilo inesistente.")

    def writable_device(user_id: int, mac: str) -> str:
        try:
            mac = normalize_mac(mac)
        except ValueError:
            raise HTTPException(422, "Indirizzo MAC non valido.") from None
        if mac in settings.protected_macs:
            raise HTTPException(403, "Dispositivo amministrativo protetto.")
        if not store.query("SELECT mac FROM device_profiles WHERE user_id=? AND mac=?", (user_id, mac)):
            raise HTTPException(404, "Dispositivo non assegnato a questo profilo.")
        return mac

    def category_sources():
        return {row["name"]: row for row in store.query(
            "SELECT name,source,updated_at,json_array_length(domains) AS count FROM dns_categories"
        )}

    @router.get("/users/{user_id}")
    def dns_profile(user_id: int):
        with store.lock:
            check_profile(user_id)
            web, services = load_profile(user_id)
            sources = category_sources()
            bindings = store.query("""SELECT b.mac,b.data FROM dns_devices b JOIN device_profiles p USING(mac)
                WHERE p.user_id=?""", (user_id,))
        now = datetime.now(zone)
        return JSONResponse({
            "status": "configurazione_locale",
            "verification": "da_verificare_sul_client",
            "web": ordered_policy(web),
            "services": ordered_policy(services),
            "active_service_windows": sorted({
                row.service for row in services.windows
                if row.service not in services.allowed_services and schedule_active(now, [row.rule()])
            }),
            "categories": CATEGORIES,
            "sources": sources,
            "catalog": {key: {"label": name, "domains": list(domains)} for key, (name, domains) in SERVICES.items()},
            "clients": {row["mac"]: json.loads(row["data"]) for row in bindings if row["mac"] not in settings.protected_macs},
        }, headers={"Cache-Control": "no-store"})

    @router.put("/users/{user_id}/web", dependencies=mutate)
    def save_web(user_id: int, payload: WebPolicy):
        with store.policy_change("filtri_web", f"profilo={user_id}") as db:
            check_profile(user_id)
            replace_config(db, user_id, {"web": ordered_policy(payload)})
        return {"ok": True}

    @router.put("/users/{user_id}/services", dependencies=mutate)
    def save_services(user_id: int, payload: ServicePolicy):
        with store.policy_change("filtri_servizi", f"profilo={user_id}") as db:
            check_profile(user_id)
            replace_config(db, user_id, {"services": ordered_policy(payload)})
        return {"ok": True}

    @router.put("/users/{user_id}/devices/{mac}", dependencies=mutate)
    def save_device(user_id: int, mac: str, payload: DeviceDns):
        try:
            with store.policy_change("collegamento_dns", f"profilo={user_id}") as db:
                mac = writable_device(user_id, mac)
                db.execute("""INSERT INTO dns_devices(mac,client_name,data) VALUES (?,?,?)
                    ON CONFLICT(mac) DO UPDATE SET client_name=excluded.client_name,data=excluded.data""",
                           (mac, payload.client_name, canonical(ordered_policy(payload))))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Nome client già in uso per un altro dispositivo.") from None
        return {"ok": True}

    @router.delete("/users/{user_id}/devices/{mac}", dependencies=mutate)
    def remove_device(user_id: int, mac: str):
        with store.policy_change("rimozione_collegamento_dns", f"profilo={user_id}") as db:
            mac = writable_device(user_id, mac)
            db.execute("DELETE FROM dns_devices WHERE mac=?", (mac,))
        return {"ok": True}

    @router.put("/categories/{category}", dependencies=mutate)
    def import_category(category: Category, payload: CategoryList):
        with store.policy_change("aggiornamento_lista_dns", f"categoria={category} domini={len(payload.domains)}") as db:
            db.execute("""INSERT INTO dns_categories(name,source,updated_at,domains) VALUES (?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                source=excluded.source,updated_at=excluded.updated_at,domains=excluded.domains""",
                       (category, payload.source, datetime.now(timezone.utc).isoformat(), json.dumps(sorted(payload.domains))))
        return {"ok": True}

    @router.delete("/categories/{category}", dependencies=mutate)
    def delete_category(category: Category):
        with store.policy_change("rimozione_lista_dns", f"categoria={category}") as db:
            db.execute("DELETE FROM dns_categories WHERE name=?", (category,))
        return {"ok": True}

    @router.post("/import", dependencies=mutate)
    def import_domains(payload: ImportDomains):
        with store.policy_change("importazione_domini_dns", f"profili={len(payload.user_ids)}") as db:
            for user_id in sorted(payload.user_ids):
                check_profile(user_id)
                web, _ = load_profile(user_id)
                merged = {**web.model_dump(),
                          "blocked_domains": web.blocked_domains | payload.blocked_domains,
                          "allowed_domains": web.allowed_domains | payload.allowed_domains}
                try:
                    updated = WebPolicy.model_validate(merged)
                except ValidationError:
                    raise HTTPException(422, "La lista del profilo supererebbe 1000 domini per tipo.") from None
                replace_config(db, user_id, {"web": ordered_policy(updated)})
        return {"ok": True}

    @router.get("/export")
    def dns_export():
        with store.lock:
            categories = {row["name"]: json.loads(row["domains"]) for row in store.query(
                "SELECT name,domains FROM dns_categories"
            )}
            devices = sorted((device for device in store.devices() if device["user_ids"]),
                             key=lambda device: (device["user_ids"][0], device["mac"]))
            bindings = {row["mac"]: row["data"] for row in store.query("SELECT mac,data FROM dns_devices")}
            user_ids = {user_id for device in devices for user_id in device["user_ids"]}
            policies = {user_id: load_profile(user_id) for user_id in user_ids}
        rules = ["! BEGIN Deco Family Control"]
        statuses = []
        modes = ("off", "moderate", "strict")
        for device in devices:
            if device["mac"] in settings.protected_macs:
                statuses.append({"mac": device["mac"], "status": "protetto"})
                continue
            if device["mac"] not in bindings:
                statuses.append({"mac": device["mac"], "status": "client_non_collegato"})
                continue
            client = DeviceDns.model_validate_json(bindings[device["mac"]])
            web, services = WebPolicy(), ServicePolicy()
            for user_id in device["user_ids"]:
                profile_web, profile_services = policies[user_id]
                web.blocked_domains.update(profile_web.blocked_domains)
                web.allowed_domains.update(profile_web.allowed_domains)
                web.categories.update(profile_web.categories)
                web.safe_search |= profile_web.safe_search
                web.youtube_mode = max(web.youtube_mode, profile_web.youtube_mode, key=modes.index)
                services.blocked_services.update(profile_services.blocked_services)
                services.allowed_services.update(profile_services.allowed_services)
            rules += export_rules(client, web, services, categories)
            statuses.append({"mac": device["mac"], "status": "esportabile"})
        rules.append("! END Deco Family Control")
        return JSONResponse({
            "provider": "AdGuard Home",
            "status": "applicazione_manuale",
            "verification": "da_verificare_sul_client",
            "rules": "\n".join(rules),
            "devices": statuses,
        }, headers={"Cache-Control": "no-store"})

    return router
