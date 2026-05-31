from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app" / "webapp"
DATA_DIR = PROJECT_ROOT / "data"
METADATA_DIR = DATA_DIR / "metadata"
DERIVED_DIR = DATA_DIR / "derived"
WAREHOUSE_DIR = Path(os.environ.get("VAMATION_WAREHOUSE_DIR", str(PROJECT_ROOT / "warehouse"))).resolve()
DOWNLOADS_DIR = WAREHOUSE_DIR / "downloads"
EXTRACTED_DIR = WAREHOUSE_DIR / "extracted"
PROFILE_IMAGES_DIR = WAREHOUSE_DIR / "profile-images"
POST_PAGES_DIR = APP_DIR / "posts"
PLAYLISTS_DIR = APP_DIR / "playlists"
TEMPLATES_DIR = APP_DIR / "templates"
THUMBNAILS_DIR = APP_DIR / "cache" / "thumbnails"
LOGS_DIR = PROJECT_ROOT / "logs"
COOKIES_FILE = PROJECT_ROOT / "cookies.txt"
POSTS_METADATA_JSON = METADATA_DIR / "posts_metadata.json"
POSTS_METADATA_XLSX = METADATA_DIR / "exports" / "posts_metadata.xlsx"
PLAYLIST_METADATA_JSON = METADATA_DIR / "playlist_metadata.json"


def ensure_common_directories() -> None:
    for directory in [
        DATA_DIR,
        METADATA_DIR,
        DERIVED_DIR,
        WAREHOUSE_DIR,
        DOWNLOADS_DIR,
        EXTRACTED_DIR,
        PROFILE_IMAGES_DIR,
        POST_PAGES_DIR,
        PLAYLISTS_DIR,
        TEMPLATES_DIR,
        THUMBNAILS_DIR,
        LOGS_DIR,
        POSTS_METADATA_XLSX.parent,
        METADATA_DIR / "backups",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
