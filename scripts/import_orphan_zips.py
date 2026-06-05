#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('VAMATION_DISABLE_BACKGROUND_INIT', '1')

from app.webapp.app import FileOperationsManager, metadata_manager

META_PATH = PROJECT_ROOT / 'data' / 'metadata' / 'posts_metadata.json'
MANIFEST_PATH = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'pre_reset_recovery_manifest_20260530T135607Z.json'
STATE_PATH = PROJECT_ROOT / 'data' / 'metadata' / 'backups' / 'recovery_progress.json'
DOWNLOADS_DIR = PROJECT_ROOT / 'warehouse' / 'downloads'


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


MANUAL_ORPHAN_ZIP_MAP = {
    'orphaned_50608b79': 'orphaned_8e5807bd_yor briar-01410-1392875415.zip',
    'orphaned_6849b53a': 'orphaned_4c71560e_Gwen_Stacy.zip',
    'orphaned_7effcb69': 'orphaned_282f9c18_mona (genshin impact) (2).zip',
    'orphaned_68788e9a': 'orphaned_c19d4672_mona (genshin impact).zip',
    'orphaned_c17937a0': 'orphaned_cd77cc06_sparkle (honkai star rail).zip',
    'orphaned_c882ad1e': 'orphaned_90167e81_sparkle (honkai star rail),.zip',
    'orphaned_b41ff006': 'orphaned_be61b8e0_sparkle (2).zip',
}


def find_orphan_zip(post_id: str) -> Path | None:
    manual = MANUAL_ORPHAN_ZIP_MAP.get(post_id)
    if manual:
        manual_path = DOWNLOADS_DIR / manual
        if manual_path.exists():
            return manual_path
    pid = post_id.lower()
    matches = [p for p in DOWNLOADS_DIR.glob('*.zip') if p.name.lower().startswith('orphaned') and pid in p.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def load_targets() -> tuple[dict[str, Any], set[str], list[dict[str, Any]]]:
    meta = load_json(META_PATH)
    manifest = load_json(MANIFEST_PATH)
    target_ids = {str(p['post_id']) for p in manifest.get('posts_with_extracted_zip', [])}
    orphans = [p for p in meta.get('posts', []) if str(p.get('post_id', '')).lower().startswith('orphaned_')]
    return meta, target_ids, orphans


def update_state_for_completed(post_ids: list[str]) -> None:
    state = load_json(STATE_PATH)
    changed = 0
    for post in state.get('posts', []):
        if str(post.get('post_id')) in post_ids:
            if post.get('status') != 'completed':
                changed += 1
            post['status'] = 'completed'
            post['attempts'] = max(int(post.get('attempts', 0)), 1)
            post['started_at'] = post.get('started_at') or now_iso()
            post['completed_at'] = now_iso()
            post['last_error'] = None
    counts = {'queued': 0, 'in_progress': 0, 'completed': 0, 'failed': 0}
    for post in state.get('posts', []):
        status = post.get('status', 'queued')
        if status in counts:
            counts[status] += 1
    state['summary'] = {'total_selected': len(state.get('posts', [])), **counts}
    state['updated_at'] = now_iso()
    save_json(STATE_PATH, state)
    print('updated_recovery_state', changed)


def metadata_only_update(post: dict[str, Any], zip_path: Path) -> bool:
    zip_files = post.get('zip_files') or []
    if not zip_files:
        return False
    zip_info = dict(zip_files[0])
    zip_info['local_filename'] = zip_path.name
    zip_info['downloaded'] = True
    zip_info['download_date'] = zip_info.get('download_date') or now_iso()
    zip_info['extracted'] = False
    zip_info['extraction_date'] = None
    return metadata_manager.atomic_update_post(str(post['post_id']), {'zip_files': [zip_info]})


def full_local_recover(post: dict[str, Any], zip_path: Path) -> bool:
    zip_files = post.get('zip_files') or []
    if not zip_files:
        return False
    zip_info = dict(zip_files[0])
    zip_info['local_filename'] = zip_path.name
    zip_info['downloaded'] = True
    zip_info['download_date'] = zip_info.get('download_date') or now_iso()
    zip_info['extracted'] = False
    zip_info['extraction_date'] = None
    if not metadata_manager.atomic_update_post(str(post['post_id']), {'zip_files': [zip_info], 'cascade_metadata': {}}):
        return False
    result = FileOperationsManager.extract_post_files(str(post['post_id']))
    return bool(result.get('success'))


def main() -> int:
    meta, target_ids, orphans = load_targets()
    in_768 = [p for p in orphans if str(p['post_id']) in target_ids]
    out_768 = [p for p in orphans if str(p['post_id']) not in target_ids]

    recovered_ids: list[str] = []
    metadata_only_ids: list[str] = []
    failures: list[dict[str, str]] = []

    for post in in_768:
        zip_path = find_orphan_zip(str(post['post_id']))
        if not zip_path:
            failures.append({'post_id': str(post['post_id']), 'reason': 'uploaded orphan zip not found'})
            continue
        print('FULL_RECOVER', post['post_id'], zip_path.name)
        if full_local_recover(post, zip_path):
            recovered_ids.append(str(post['post_id']))
        else:
            failures.append({'post_id': str(post['post_id']), 'reason': 'full local recovery failed'})

    for post in out_768:
        zip_path = find_orphan_zip(str(post['post_id']))
        if not zip_path:
            failures.append({'post_id': str(post['post_id']), 'reason': 'uploaded orphan zip not found'})
            continue
        print('METADATA_ONLY', post['post_id'], zip_path.name)
        if metadata_only_update(post, zip_path):
            metadata_only_ids.append(str(post['post_id']))
        else:
            failures.append({'post_id': str(post['post_id']), 'reason': 'metadata-only update failed'})

    if recovered_ids:
        update_state_for_completed(recovered_ids)

    print(json.dumps({
        'orphans_total': len(orphans),
        'orphans_in_768': len(in_768),
        'orphans_not_in_768': len(out_768),
        'full_recovered': recovered_ids,
        'metadata_only_updated': metadata_only_ids,
        'failures': failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
