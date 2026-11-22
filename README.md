# Deco Family Control

Local-first parental network control for **TP-Link Deco X50** (main router at `192.168.21.1`, firmware `1.11.1 Build 26081714 Rel.32981`). No subscription, no replacement router, no Archer C6.

**Status: prototype.** Safe mock mode works; local X50 API compatibility and actual network-block enforcement have **not** been verified. Do not rely on this application for child protection until real blocks have been tested with a nonessential device.

## Goals
- Family profiles (Alice, Anna and others), each containing multiple devices.
- Device inventory, sampled online presence, network speed telemetry and daily history with honest labels.
- Immediate blocks, weekly schedules and optional time quotas.
- Mobile-friendly admin dashboard; local SQLite, FastAPI and Docker.
- Written tests, read-only-first diagnostics and a protected management-device list.

## Quick start (simulation)
```sh
cp .env.example .env
# Set a strong ADMIN_PASSWORD in .env
docker compose up --build -d
# On the host: http://127.0.0.1:8088
```
The default compose binds to loopback only. Never expose HTTP Basic credentials to the public Internet. Access from iPhone should use a private authenticated HTTPS proxy/VPN.

## Critical limitations
- A Deco API success/blacklist update is **not** proof of Internet filtering. `ha-deco-guard` reports this distinction on different firmware. Router writes default to OFF.
- Wi-Fi presence is not actual screen time. Router-reported instant KB/s is not a byte counter or per-app usage. Per-site usage requires a separately deployed traffic/DNS data source and may remain incomplete.
- Subscription-gated HomeShield functions cannot simply be unlocked by this project.
- All real policies require an always-on host inside your LAN. Codex Cloud is for development, not for reaching private address `192.168.21.1`.

For implementation and acceptance criteria read [PRD.md](PRD.md), [AGENTS.md](AGENTS.md), and [CODEX_START.md](CODEX_START.md).

## References
- [tplink-deco-api](https://github.com/roquerodrigo/tplink-deco-api), MIT, Python SDK (external dependency).
- [Deco Guard](https://github.com/leewsimpson/ha-deco-guard), GPL-3.0-or-later, optional external integration only, no source copied.
- [Home Assistant TP-Link Deco](https://github.com/dkwolf1/ha-deco), technical reference.

No secrets, MAC addresses or actual children's traffic history should be committed to GitHub.
