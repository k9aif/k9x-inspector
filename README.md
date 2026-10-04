# K9X Inspector

Continuous conformance inspection for solutions built on the [K9-AIF framework](https://github.com/k9aif/k9-aif-framework).

Point it at GitHub repositories (any number, listed in `.env`) or inspect any public repository on demand. Each
inspection reads the solution — Python as syntax trees, YAML as data, **never importing or running it** — applies
the framework's inspection rules and produces:

- a **compliance report**: verdict, rule pass rate, findings ranked critical / violation / warning / recommendation,
  each with file, line and fix (HTML in the UI, Markdown, JSON);
- a **guidelines document**: a remediation plan in priority order, why each rule exists, and the framework
  configuration (Shield, Guardian, Zero Trust) that closes the governance findings.

Rules live in the framework (`k9_aif_abb.k9_inspect`, k9-aif >= 1.15), so `k9aif inspect <folder>` in CI and this
service apply the same checks. Inspector installs k9-aif from PyPI and builds on that ABB.

## Triggers

| Trigger | |
|---|---|
| GitHub push | `POST /api/webhook/github` (HMAC `INSPECTOR_WEBHOOK_SECRET`) re-inspects every tracked application on that repository and branch |
| Schedule | `INSPECTOR_SCHEDULE` (`06:30`, `every 6h`, `off`); skips applications whose branch has not moved |
| Inspect now | any signed-in user, for a tracked application or any public GitHub URL (viewers rate-limited) |

## UI

Sign-in page with the demo login (`demo` / `demo`, read-only viewer). Tabs: **Applications** (tracked, latest
verdict, Inspect now), **Inspect** (any GitHub URL; server folder for the admin when `INSPECTOR_ALLOW_LOCAL=true`),
**Reports** (history, findings filterable by severity), **Generated Docs** (guidelines and report, download
.md/.json), **Rules**, **Architecture**, plus **About**.

## Run

```bash
cp .env.example .env            # set INSPECTOR_PASSWORD, INSPECTOR_APPS, ...
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
./run.sh                        # http://localhost:8115
.venv/bin/python -m pytest -q   # no network needed
```

Container (single image, `runtime/` mounted for the database and clones): `ubuntu/build-run.sh all`.

## GitHub webhook

Repository → Settings → Webhooks → Add: payload URL `https://<inspector host>/api/webhook/github`, content type
`application/json`, secret = `INSPECTOR_WEBHOOK_SECRET`, events: just the push event.

## Security

Only `https://github.com/<owner>/<repo>` URLs; shallow, time-limited, size-capped clones; branch and subfolder
validated; code never executed. Private repositories need a read-only `INSPECTOR_GITHUB_TOKEN` (sent as a header for
the git call only, never stored in the clone or logged). Admin password and session secret in `.env` only.
