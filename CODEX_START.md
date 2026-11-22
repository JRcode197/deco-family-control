# Codex Cloud: first task

Repository: JRcode197/deco-family-control

Read AGENTS.md, PRD.md, and README.md. Work in a new feature branch and open a pull request.

The product targets Deco X50 gateway at 192.168.21.1, firmware 1.11.1 Build 26081714 Rel. 32981. No Archer C6. Free local-first parental controls for family profiles Alice and Anna, device mapping, usage telemetry from verifiable source, immediate/scheduled blocks, daily quotas. No subscription bypass.

**Step 1:** run tests and code review. Add a *read-only* local API capabilities probe, sanitize logs and diagnostics, cover mock test cases. Then document how the user can run it at home and provide results. Do not attempt to contact 192.168.21.1 from Codex Cloud. Do not enable real router writes, assume HTTP API "block" actually blocks traffic, or hardcode router credentials. Validate enforcement only via disposable device on user's LAN after explicit authorization. Keep all usage labels honest: connected presence, instantaneous KB/s, byte counts, and application screen time are distinct.

Assess tplink-deco-api 2.2.0 (MIT) as external SDK; ha-deco-guard (GPL-3.0) only as a separate optional integration. Update tests, README and compatibility matrix, and deliver a PR. Never deploy as production parental protection without local block/unblock verification.
