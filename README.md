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

### Home Assistant

On Home Assistant installations with the app store, add this repository, install **Deco Family Control**, and select **Start → Open web UI**. Home Assistant Ingress handles access, including from the iPhone companion app. The Deco connection details are entered in the diagnostic form for one read-only check. See [the Home Assistant instructions](DOCS.md).

### Read-only diagnostics on the home LAN

**Deco X50 block-list response:** the local firmware may encode an empty `black_list` as `{"client_list": {}}`, not `{"client_list": []}`. The diagnostic accepts **only an empty object** as an empty list, yielding `blocklist.status=read_only_detected` and `count=0`. Missing data or nonempty unexpected objects remain `read_error`. An empty list does **not** mean the app's block/unblock commands enforce Internet denial; that still requires a physical test.



**Authentication note for Deco X50:** The local SDK uses a signed `md5(username + password)` handshake and its standard examples use `admin` as **API username**, even when the Deco browser login presents only a password field. Start with `admin` and the **owner TP-Link ID password**, not the person's name, Wi-Fi key, or dashboard password. First confirm the password works at the router's LAN web page. An `authentication_failed` status is not conclusive evidence that a password is wrong: an incompatible firmware protocol, a rejected session, or temporary lockout may produce a similar symptom. Avoid repeated attempts; if owner web login succeeds but API probing fails, report only the redacted JSON, X50 hardware revision and firmware. Do **not** put passwords, tokens, cookies or raw logs in GitHub. The probe never writes to the router.



1. On a machine connected to the home LAN, start the app using the Quickstart above. Keep `DECO_MODE=mock` and `DECO_WRITE_ENABLED=false` for this one-off check. The `.env` file only needs the usual dashboard login settings and defaults.
2. Sign in to the local dashboard. In **Diagnostica Deco X50**, enter the Deco's private IPv4 address, username and password for its web interface. Choose **Esegui verifica in sola lettura**. The probe uses those details for one session covering the client inventory, gateway firmware, and blocklist. The password field clears on submission. The app retains only the redacted report in memory.
3. Open **Risultato JSON** and return that JSON in the issue or PR, along with the hardware revision from the Deco app if known. The report contains statuses, counts, and recognized firmware metadata. Share only that report and the requested metadata. `/api/state`, screenshots and database exports can contain household device identifiers.

The authenticated `GET /api/diagnostics` returns the latest report from memory. The manual check uses `POST /api/diagnostics/deco` with a JSON body containing `router_ip`, `username` and `password`. It requires `X-Family-Action: 1`. After a failed check, you can correct the form and submit again after one minute. Manual diagnostics connect directly to the selected IPv4 address and reject redirects. The pinned SDK uses HTTPS with certificate verification disabled for the router. Run the check on a trusted home LAN.

For continuous local polling, set `DECO_MODE=deco`, `DECO_ROUTER_IP`, `DECO_USERNAME` and `DECO_PASSWORD` in the ignored `.env` and recreate the service. **Usa configurazione del server** runs the probe using that configuration. After a rejected login in this mode, correct the configuration and recreate the service.

### Separate physical block/unblock test

Run this only on the home LAN after explicitly authorizing router writes for the test. Use a fresh test database with no active rules or quotas, a spare Wi-Fi client with a stable MAC, and a second administration device. Record that both devices can reach the Internet initially. Disable cellular and other fallback connections on the spare device. Add the management host and device MACs to `PROTECTED_MACS` before enabling local commands.

Using the SDK actuator, issue a manual block for **only the spare device**. Record whether the router accepted the command and whether a fresh HTTPS request from the device fails while the administration device stays online. Unblock the spare device and verify a fresh HTTPS request succeeds again. If the app cannot undo the block, remove it from the Deco app using the administration device. Restore `DECO_WRITE_ENABLED=false` and recreate the service.

Report the firmware, actuator, command response, connectivity before/during/after, and whether the administration device stayed online. Omit device identifiers and credentials. Use **physically verified** only when the test shows both loss and recovery of connectivity. A blocklist entry or HTTP success records **command accepted (unverified)**. Repeat the test after firmware or actuator changes.

### Compatibility matrix

| Capability | SDK or integration evidence | Deco X50 · 1.11.1 Build 26081714 Rel. 32981 |
| --- | --- | --- |
| Client inventory and gateway firmware | SDK 2.2.0 documents local inventory and mesh device-list reads. The diagnostic checks them individually. | Pending a local **read-only detected** result. An explicit HTTP 404 or 501 for a read is **not supported**. |
| Blocklist visibility | `clients.get_blocked()` reads the mesh blocklist. The diagnostic validates the list shape and reports only its count. | Read-only check pending. |
| Actual Internet block and recovery | `clients.block()` sends a blocklist operation. `clients.unblock()` sends the inverse operation. | **Unverified** until the separate physical test. |
| Presence and sampled speeds | Presence counts observed Wi-Fi connection minutes. SDK speed fields are instantaneous KB/s samples. SDK `get_client_totals()` sums speeds. | Sample availability pending. Byte counters, iOS screen time and app/site history are unavailable in this integration. |
| Optional Home Assistant Deco Guard | Separate GPL-3.0-or-later integration. Upstream reports testing on BE85 fw 1.2.4 and uses the app management protocol to complement the web API. | X50 compatibility and physical enforcement pending. |

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

### Issue #1 review and test coverage

- The API uses HTTP Basic for the dashboard and protected routes, plus `X-Family-Action` for actions. The diagnostic's cached GET performs no router I/O. Neither the diagnostic nor the application's failure logs include raw SDK responses, exception messages or session URLs.
- SQLite uses one connection guarded by a lock. Shutdown waits for the polling worker before closing it. Policy and snapshot writes currently commit statement by statement. Multi-statement atomicity and crash recovery still need dedicated tests.
- Fake SDK sessions cover endpoint absence, empty and malformed results, credential failure, retry backoff, read-only operation selection and redaction. API tests cover authentication, the action header and result caching. Real firmware response shapes, device reachability, and sustained polling under concurrent requests require separate verification.

## Next development tasks for Codex

See [`PRD.md`](PRD.md) and [`CODEX_START.md`](CODEX_START.md). Collect the X50 read-only results and run the separate block/unblock field test. Detailed usage telemetry requires an appropriate local data source.

## Third-party projects evaluated

- [`roquerodrigo/tplink-deco-api`](https://github.com/roquerodrigo/tplink-deco-api): MIT, typed Python SDK, documentation, tests; used as an **external dependency**, no code copied.
- [`leewsimpson/ha-deco-guard`](https://github.com/leewsimpson/ha-deco-guard): GPL-3.0-or-later; useful evidence about block enforcement and possible **separate** optional Home Assistant integration, not vendored.
- [`amosyuen/ha-tplink-deco`](https://github.com/amosyuen/ha-tplink-deco): Home Assistant integration for read-only client/health information; maintainer announced migration to `dkwolf1/ha-deco`; use as technical reference only.

This project is written independently under the MIT license. Use of third-party components remains governed by their own licenses.
