# PRD — Deco Family Control (project for Codex Cloud)

## 1. Goal

Build a local-first, completely free **network-level** parental control web app for a three-node Deco mesh with an **X50 main router, 192.168.21.1, firmware 1.11.1 Build 26081714 Rel. 32981**, without needing apps on the children's iPhones/iPads or changing the router hardware. In-home only. Control should be available from parent's smartphone via secure LAN/VPN access. Run enforcement on an always-on home host; use Codex cloud only to **develop** and test source code.

## 2. Priority requirements

**P0**
- Discovered network devices persisted by normalized MAC; assign/unassign to named profiles (initial examples Alice and Anna), many-to-one mapping, stable across IP change.
- Admin sign-in; safe default no Deco writes; user-friendly Italian mobile dashboard.
- Manual Internet block/unblock by individual device or by owner profile.
- Recurring scheduled Internet windows with daily/weekday combinations and overnight crossing.
- Local audit with status and error, anti-CSRF on mutation, secrets only in deployment environment.
- Prove X50-specific **actual traffic blocking** and recovery with nonessential test device; never infer enforcement from API success.

**P1**
- Observed online presence statistics daily/weekly/monthly per device/user, clearly marked as sampling not screen time. Initial 7-day table includes sampled instantaneous KB/s averages/maxima where SDK provides them; these are NOT transferred byte totals.
- Real network traffic statistics (if router exposes trustworthy byte counters): unit tests for resets, reboot/wrap, device aggregation, clock drift; no fabricated data.
- Daily **active usage** quota per user/device, only once a trustworthy activity measure exists; configurable schedules for school days/vacations.
- Add safe override (15/30/60 min) with persisted expiry, clock/DST protection, change audit and crash recovery.
- Device-to-parent drift reconciliation, auto-discovery and safe alerts for new MACs, optional quarantine opt-in (never block all unknown devices by default).

**P2**
- Optional AdGuard Home DNS logging & per-user website domain/category reports; privacy minimization, retention controls, disabled by default, DNS bypass detection where technically feasible. No claim of exact app activity.
- Notifications via Home Assistant/local web, backup export, pagination, graphs with actual data quality provenance.
- Device and family policy templates, safe household exclusions (admin/HA/main router), rollback.

## 3. Technical architecture

- Python 3.14, FastAPI, SQLite with safe transactions, accessible mobile web dashboard.
- Local SDK: `tplink-deco-api==2.2.0` (MIT). Adapter interface (read devices, read blocked list, block/unblock). Use only official SDK method names confirmed in README.
- Alternative actuator: external Home Assistant + `ha-deco-guard` (GPL-3.0) via HA service API. **Do not copy any GPL project code** into MIT project. Installer/admin must install it separately.
- Scheduler active in a dedicated, **single instance** on local host. Avoid multi-worker duplicate polling and router login floods. Never block management devices (`PROTECTED_MACS` mandatory before real writes).
- Mode `mock` and `DECO_WRITE_ENABLED=false` by default; no access to private LAN from Codex cloud.
- Local HTTPS/VPN recommended; bind to loopback by default, do not open Internet-facing port.

## 4. Reality check from parent screenshots

App screenshot: X50 main in basement, 192.168.21.1, firmware 1.11.1 Build 26081714 Rel.32981. Screen already contains a HomeShield parent profile `alice`, two online devices and 0m traffic hours, while advanced statistics graph is subscription-locked. **Do not treat screenshot '0m' as evidence of no actual use.** There is no router hardware revision shown; firmware compatibility must be validated rather than inferred.

## 5. KPIs / acceptance

- P0 simulated functional tests pass; 0 real router writes with default configuration.
- Integration check identifies X50 client names/MAC/online state correctly with live router in read-only mode; no leaked passwords in logs.
- Tested block on a disposable device prevents Internet traffic; local/admin devices unaffected; unblock restores. Record exact build and strategy; if not verified, mark integration *unsupported*, no green badge.
- Same user with two simultaneously online clients is counted once per sampled minute; per-device counts are separated; UI uses unambiguous metric label.
- Auto quotas never accidentally count offline app time or claim to track iOS screen time.
- No proprietary HomeShield license bypass or reverse-engineered circumvention of paid entitlements.

## 6. Known risks / decisions

- SDK documented `clients.block()` may affect only web blocklist and fail to enforce packet routing (reported by Deco Guard BE85). Do not claim actual enforcement without field test on X50.
- Alternative SSH/TMP app channel may be firmware-specific. Don't expose router credentials or repeatedly retry failed authentication.
- Client's fixed Apple private MAC helps stable mapping; rotating private MAC can cause identity drift.
- DNS-over-HTTPS/QUIC/VPN may bypass DNS filtering; mobile data/out-of-home beyond router scope.
- Cloud deployment (Vercel, etc.) cannot call private router at `192.168.21.1`. Run runtime at home, not in Codex cloud.

## 7. Repository workflow

Private repo by default, branch protection, CI test on Python 3.14, no secrets, issue-based roadmap. Codex should read this PRD, inspect SDK pinned version, run tests and make small reviewable PRs instead of broad unreviewed code changes.