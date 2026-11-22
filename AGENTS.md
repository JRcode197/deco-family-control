# Codex working rules

Read `PRD.md`, `README.md`, and `CODEX_START.md` before changing code.

- Keep this project specifically for **Deco X50**. No Archer C6, no router replacement, no paid HomeShield features.
- Default to mock mode and *zero* real router writes. Never enable `DECO_WRITE_ENABLED` in code, CI, screenshots, sample fixtures or cloud environment.
- No secrets in Git, no router access from cloud Codex (LAN `192.168.21.1` is private), no attempts to bypass subscription licenses.
- A successful API response or a changed block list does not prove real Internet blocking. Field-test on an unimportant device before claiming success.
- Do not import/copy source from GPL projects into the MIT repo. Treat `ha-deco-guard` as an optional external runtime integration.
- Label statistics honestly: connected presence, instantaneous speed, actual byte counters and actual screen time are four different things.
- Any change to limits or schedules requires isolated deterministic tests (incl. DST and midnight), rollback and protected management devices.
- Run `pytest -q`, include tests with changes, and make small reviewable commits.
- Start with read-only X50 capability checks and user-visible diagnostics. If unsupported, report limitations without pretending that paid features are unlocked.