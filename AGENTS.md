# Vamation

## Purpose
- Private Patreon archive + browser system for the creator VAMA.
- `data/metadata/posts_metadata.json` is the master source of truth for source content.
- `data/metadata/playlist_metadata.json` is curated metadata for the playlist feature.
- `app/webapp/templates/` governs generation of new post pages.

## Structure
- `app/webapp/` — Flask browser app, frontend assets, generated post + playlist pages.
- `shared/` — shared project utilities and canonical path config.
- `ingest/pipeline/` — Patreon ingestion/update pipelines.
- `ingest/repair/` — maintenance and metadata repair scripts.
- `ingest/legacy/` — one-off scripts kept for reference; likely obsolete.
- `data/metadata/` — canonical metadata files.
- `warehouse/` — local storage for downloads, extracted files, and profile images.
- `scripts/` — small launchers for common tasks.
- `experiments/` — isolated tests and prototypes.

## Rules
- Treat metadata as authoritative; do not rebuild it casually from the filesystem.
- Orphans are a metadata state, not a separate storage class.
- Preserve per-post metadata and template-driven generation behaviour when editing the web app.
- Prefer `shared/path_config.py` for all filesystem locations.

## Common entry points
- Pipeline: `scripts/run_pipeline.py`
- Web app: `scripts/run_webapp.py`
- Shared metadata sync: `shared/metadata_handler.py`
