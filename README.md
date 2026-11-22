# Deco Family Control — X50

A **local-first, no-subscription** parental network policy prototype for TP-Link Deco. User profiles, device assignment, manual block policies, weekly blocking windows, observed Wi-Fi presence and local audit trail. Responsive web UI works in an iPhone browser. Data stays in local SQLite; neither children's data nor router credentials go to a SaaS service.

> **Status: working mock/demo and unverified real Deco integration, not a tested protection product.** Do not rely on it to protect children until real traffic blocking and recovery have been verified on your X50. The program does **not** unlock paid HomeShield features, collect iOS screen time, measure true app usage, or provide detailed browsing history.

## Hardware / scenario

- Deco **X50**, master `192.168.21.1` (router mode)
- Firmware **1.11.1 Build 26081714 Rel. 32981** (reported on 23 Nov 2026)
- Family profiles (examples): **Alice**, **Anna**; each profile may own several devices.
- No Archer C6, no new router. An **always-on local** Linux/Docker host is required for schedules (can be separate from the Deco). Codex cloud is for development, **not** for controlling an inaccessible private LAN.

## Features implemented

- Live/simulated Deco device list; assign multiple device MACs to users.
- Manual user/device block policy, schedules per weekday including overnight, user/device quota settings.
- Background 60s polling, local SQLite event log, manual refresh, mobile-friendly control panel.
- Daily per-device and per-user **sampled connection presence**, plus SDK-provided instantaneous upload/download rate snapshots in KB/s (average/max, **not byte totals**); shared-person minutes count each observed minute only once even with two connected devices. Not browsing minutes.
- Preview-only default; no router writes. Router management must be tested on a disposable device.
- Authentication, anti-CSRF header on mutations, loopback-only default compose binding and no stored secrets in the repo.

## Non-goals and unresolved integration risks

1. `tplink-deco-api` 2.2.0 uses the Deco local API; actual protocol compatibility with the X50 firmware has **not been verified**. `get_client_list()` may return incomplete/offline data. Credentials are the **admin web interface login** (often TP-Link ID) accepted by local firmware; do not assume `admin` works on every model.
2. The SDK's `clients.block()` may change the router's block list **without enforcing the internet block** on some firmwares. [Deco Guard](https://github.com/leewsimpson/ha-deco-guard) reports that on BE85 an SSH/TMP app protocol is necessary. Its API may not work with X50; nothing in this project claims otherwise. Test actual internet reachability after block and after unblock.
3. Current `managed_block` tracks accepted control commands, **not verified enforcement**. For strong enforcement use the `ha_guard` actuator only if the separate Home Assistant Deco Guard integration passes X50 tests. It is GPL-3.0 and is **not copied or bundled** here.
4. HomeShield `patrol_insights` and DPI endpoints are documented but may be subscription gated. No attempt is made to circumvent paid functions. Sites and apps history require a separately configured DNS logger such as AdGuard Home and have privacy/coverage limits.
5. iOS private MAC set to **Fisso** for home SSID. VPN, private relay, encrypted DNS and other Wi-Fi networks can bypass DNS-based controls. Router-based blocks operate only on traffic via this Deco, not cellular/offline apps.
6. When the local host is down, new schedules are not enforced. Existing device blocks may persist on the router. Do not deploy to a machine which sleeps.
7. Auto quota enforcement intentionally defaults **OFF** because observed Wi-Fi presence can include idle devices. We cannot equate device online time with time spent online.

## Quickstart (safe simulation)

```sh
cp .env.example .env
# Replace ADMIN_PASSWORD with a long unique secret. Never commit .env.
docker compose up --build -d
# On the host machine only:
open http://127.0.0.1:8088
```

Sign in with `ADMIN_USER` / `ADMIN_PASSWORD`. Add Alice and Anna, assign mock devices, create a schedule. `DECO_MODE=mock`, `DECO_WRITE_ENABLED=false`: **no changes to your router**. HTTP Basic is suitable only behind localhost or HTTPS/VPN; do not expose port 8088 publicly. To reach from iPhone, prefer an authenticated local HTTPS reverse proxy or private VPN such as Tailscale. The default port binding is deliberately loopback-only.

## Staged, real-network X50 verification

1. Save credentials only in `.env`: set `DECO_MODE=deco`, `DECO_ROUTER_IP=192.168.21.1`, `DECO_USERNAME`, `DECO_PASSWORD`; leave writes **false**, and restart. Check `/api/state` for discovered devices and `last_error`. All operations use the Deco LAN interface.
2. In UI, create profiles and assign MACs. Verify Apple Private Wi-Fi Address **Fixed** (Fisso) on that SSID. Never identify a child solely by current IP address.
3. Create a **test** profile / unimportant device, disconnect any secondary networks, and prepare local access to restore the router in the Deco app if necessary.
4. First populate `PROTECTED_MACS` with the administration/automation host MACs, then set `DECO_WRITE_ENABLED=true` and restart. Set a temporary manual block on **only the test MAC**. Independently test that device's Internet truly stops, on Wi-Fi only, and returns after unblock. Check for collateral effects. If only the UI blocklist changes, disable writes and do not use SDK actuator for enforcement.
5. If SDK is insufficient, separately install **Home Assistant + Deco Guard** and validate its X50 compatibility; configure `BLOCK_ACTUATOR=ha_guard`, `HA_URL`, `HA_TOKEN` only after its own successful device test. HA's HTTP 200 means command accepted, still not packet-level verification.
6. Only after successful tests apply to children's devices. Keep `QUOTA_ENFORCEMENT=false` until actual activity sensing is implemented and verified. Follow upgrade-safe tests after every firmware update.

## Development

Requires Python 3.14 for the real SDK, but mock tests use the basic requirements on Python 3.12+:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=local-test-pass-not-for-production DATABASE_PATH=./var/family.sqlite3 uvicorn app.main:app --reload --host 127.0.0.1 --port 8088
pytest -q
```

SDK install: `pip install -r requirements-deco.txt` using Python 3.14.

## Next development tasks for Codex

See [`PRD.md`](PRD.md) and [`CODEX_START.md`](CODEX_START.md). Highest priority: X50 read-only capability probe and safe real block/unblock test, followed by detailed usage telemetry sourced from an appropriate local data source. Never fake web histories or infer app usage from LAN presence.

## Third-party projects evaluated

- [`roquerodrigo/tplink-deco-api`](https://github.com/roquerodrigo/tplink-deco-api): MIT, typed Python SDK, documentation, tests; used as an **external dependency**, no code copied.
- [`leewsimpson/ha-deco-guard`](https://github.com/leewsimpson/ha-deco-guard): GPL-3.0-or-later; useful evidence about block enforcement and possible **separate** optional Home Assistant integration, not vendored.
- [`amosyuen/ha-tplink-deco`](https://github.com/amosyuen/ha-tplink-deco): Home Assistant integration for read-only client/health information; maintainer announced migration to `dkwolf1/ha-deco`; use as technical reference only.

This project is written independently under the MIT license. Use of third-party components remains governed by their own licenses.