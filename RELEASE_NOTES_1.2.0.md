## K9X Inspector 1.2.0

- About shows the running version and build commit, the installed k9-aif, the latest k9-aif on PyPI, the rule
  count and the last update's result; `/api/health` and the new public `/api/about` report the same.
- Update button for any signed-in user, demo included: pull-based and idempotent. It requests an update only when the
  build or k9-aif is behind; the host's updater (`ubuntu/update.sh`, systemd path unit from
  `build-run.sh install-updater`) pulls, rebuilds with the latest k9-aif, restarts, health checks and rolls back an
  unhealthy build. Nothing reaches into the host.
- A rebuild now always installs the latest k9-aif (the pip layer's cache is keyed on the latest PyPI version). Before,
  a cached layer could keep an old framework: inspector.k9x.ai reported k9-aif 1.18.1 while requiring 1.26.1.
- `.containerignore`: `.env`, `runtime/` and `.venv` are no longer copied into the image.
