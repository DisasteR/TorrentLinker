# TorrentLinker

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on `Vietnoirien/TorrentLinker` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Releases

Before tagging a release, update the en and fr guides (and their screenshots, README, `CONTEXT.md`) if the release changes what users see or do, then export the Catalogue: `.venv/bin/python scripts/export_catalogue.py` (it uploads to the private `Vietnoirien/torrentlinker-catalogue` release and deletes the local copy). Tagged builds refuse an export older than 48 h. Full steps: "Publishing a release" in `docs/guide/en/development.md`.
