# Vamation

Private Patreon archive + browser system for VAMA.

## Project intent

Vamation has two main responsibilities:

1. **Ingest and maintain archive metadata** from Patreon into `data/metadata/posts_metadata.json`
2. **Serve a private local browser app** for viewing and managing the archived content

Additional first-class data:

- `data/metadata/playlist_metadata.json` — curated playlist metadata
- `app/webapp/templates/` — templates used to generate new post pages

## Current structure

```text
Vamation/
  AGENTS.md
  README.md
  cookies.txt
  cookies_export_guide.txt

  app/
    webapp/
      app.py
      index.html
      js/
      styles/
      templates/
      posts/
      playlists/

  shared/
    path_config.py
    metadata_handler.py

  ingest/
    pipeline/
      integrated_pipeline.py
      integrated_pipeline_manual.py
    repair/
      clean_corruption.py
      delete_metadata_entry.py
      posthoc_update_metadata.py
    legacy/
      fix_favourite_field.py

  data/
    metadata/
      posts_metadata.json
      playlist_metadata.json
      exports/
        posts_metadata.xlsx
      backups/
    derived/

  warehouse/
    downloads/
    extracted/
    profile-images/

  scripts/
    run_pipeline.py
    run_webapp.py

  experiments/
    test_adetailer.py
```

## Metadata model

### 1. Master source content metadata
- `data/metadata/posts_metadata.json`
- canonical archive record
- pipeline updates this

### 2. Playlist metadata
- `data/metadata/playlist_metadata.json`
- curated metadata for playlist features

### 3. Per-post and generated app artefacts
- `app/webapp/posts/`
- `app/webapp/playlists/`
- derived/working files used by the browser app

### 4. Templates
- `app/webapp/templates/`
- governs page generation

## Pathing

All Python code should use `shared/path_config.py` for filesystem locations.

Default warehouse location is:
- `warehouse/` inside the project

You can override it with:
- `VAMATION_WAREHOUSE_DIR=/absolute/path/to/warehouse`

## Common entry points

Run pipeline:

```bash
python scripts/run_pipeline.py
```

Run web app:

```bash
python scripts/run_webapp.py
```

## Notes

- Image enhancement is enabled by default on this Zo deployment and now runs through Zo image editing rather than the old local Stable Diffusion/SD WebUI path.

## Notes for Zo migration

This repo was originally built on another machine and had hard-coded Windows paths. The project has now been restructured so that:

- paths are centralised
- warehouse storage is local by default
- scripts resolve relative to the project root
- metadata Excel exports live under `data/metadata/exports/`

Remaining functional work for Zo is separate from this restructure:
- validating Patreon auth refresh flow over time
- checking browser automation assumptions
- deciding how the private app should be hosted on Zo
