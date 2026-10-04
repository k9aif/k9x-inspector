# CLAUDE.md — K9X Inspector

Read `k9-aif-framework/CLAUDE.md` and `k9x-ecosystem/CLAUDE.md` first.

## What it is

An SBB service on the framework's `k9_aif_abb.k9_inspect` ABB (k9-aif >= 1.15): tracks applications from `.env`
(`INSPECTOR_APPS`), inspects them on GitHub push (webhook), on a schedule, or on demand, and stores a compliance
report and a generated guidelines document per inspection. Shell, sign-in and deployment follow K9X Sentinel.

## Rules to keep

- **Rules belong to the framework.** A new conformance rule is a `BaseInspectionRule` in
  `k9_aif_abb/k9_inspect/rules/` (with a test in `test_k9_inspect.py`), never a local copy here — the CLI and CI must
  apply the same rules as this service.
- **Never execute inspected code.** AST/YAML only; no import of the solution, no `pip install` of it, no running its
  tests. `tests/test_api.py` and the framework's `test_inspector_never_executes_code` pin this.
- **GitHub URLs only, validated** (`repos.validate`); the token is passed per git call, never written.
- **Deterministic documents.** The guidelines document is built from the report and the rules' text (no model call),
  so the same commit always yields the same document.
- **Viewer (demo) role:** reads everything, may start inspections (rate-limited), may not change the tracked list or
  inspect a server folder. Any new endpoint keeps that split (tests in test_api.py).
- Storage is SQLAlchemy Core (`store.py`); status values (running/done/failed) and triggers
  (manual/adhoc/schedule/webhook) are used by the UI.
