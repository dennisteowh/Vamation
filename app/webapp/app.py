#!/usr/bin/env python3
"""
VAMA Gallery Backend - Local File Management System
A Flask-based local web server for managing VAMA gallery files and metadata.

================================================================================
CRITICAL: METADATA ARCHITECTURE & HANDLING RULES
================================================================================

This application manages THREE DISTINCT types of metadata. Understanding their 
roles and proper handling is ESSENTIAL to avoid data corruption.

1. MAIN POSTS METADATA (metadata/posts_metadata.json)
   -----------------------------------------------------------------------------
   - **Purpose**: Central registry of ALL posts (read-only archive)
   - **Location**: metadata/posts_metadata.json
   - **Contents**: List of all posts with basic info (post_id, name, date, etc.)
   - **Update Policy**: 
     * ONLY updated when new posts are extracted
     * NEVER updated during enhancement, deletion, or image operations
     * Acts as the source of truth for post existence
   - **Why Important**: If corrupted, the entire post list is lost

2. PER-POST METADATA (webapp/posts/{post_id}_metadata.json)
   -----------------------------------------------------------------------------
   - **Purpose**: Working metadata for individual posts (MUTABLE)
   - **Location**: webapp/posts/{post_id}_metadata.json (one file per post)
   - **Contents**: 
     * Post details (name, date, description)
     * cascade_metadata with full image list
     * Image-level data: visibility, deletion, custom order
     * Enhancement metadata: enhanced_filename, enhancement_config, etc.
     * Playlist assignments
   - **Update Policy**:
     * Updated when images are enhanced/saved/deleted
     * Updated when playlist assignments change
     * Updated when image order/visibility changes
     * ONLY regenerated from scratch if the file is completely missing
     * HTML regeneration should NEVER overwrite this file
   - **Why Important**: Contains all user edits and enhancement history
     If corrupted or regenerated incorrectly, ALL enhancements and 
     custom changes for that post are LOST

3. PLAYLIST METADATA (webapp/playlists/{playlist_id}_metadata.json)
   -----------------------------------------------------------------------------
   - **Purpose**: Playlist configuration and image references
   - **Location**: webapp/playlists/{playlist_id}_metadata.json
   - **Contents**:
     * Playlist name, creation date, description
     * List of images with post_id references
     * Custom order for playlist view
   - **Update Policy**:
     * Updated when images are added/removed from playlist
     * Updated when playlist order changes
     * Regenerated when playlist HTML is missing
   - **Why Important**: Contains user's curated collections

================================================================================
METADATA UPDATE RULES (READ THIS CAREFULLY)
================================================================================

RULE 1: SEPARATION OF CONCERNS
   - HTML generation should NEVER modify metadata files
   - Metadata updates should NEVER regenerate from filesystem unless absolutely necessary
   - Always load existing metadata first, then modify specific fields

RULE 2: PRESERVE USER DATA
   - Enhancement metadata (enhanced_filename, enhancement_config, etc.) is SACRED
   - Custom image orders, visibility settings, playlist assignments are SACRED
   - Only overwrite if the file is genuinely missing or corrupt

RULE 3: ATOMIC UPDATES
   - Read full metadata file
   - Modify only the specific fields that changed
   - Write back the complete metadata
   - Never do partial reads/writes that could corrupt the JSON

RULE 4: REGENERATION POLICIES
   - Per-post metadata: ONLY regenerate if file doesn't exist
   - HTML files: Can regenerate freely, they're derived from metadata
   - Main posts metadata: ONLY append new posts, never regenerate

RULE 5: BACKUPS
   - User keeps backups manually
   - But preventing corruption is CRITICAL - recovery is painful
   - Always prefer careful updates over regeneration

================================================================================
COMMON MISTAKES TO AVOID
================================================================================

❌ DON'T: Call _generate_cascade_metadata() when HTML is missing
   ✅ DO: Use _generate_post_html_only() which loads existing metadata

❌ DON'T: Regenerate per-post metadata during HTML generation
   ✅ DO: Only generate HTML files, read metadata from existing JSON

❌ DON'T: Overwrite metadata when making small changes
   ✅ DO: Load existing metadata, update specific fields, write back

❌ DON'T: Scan filesystem and rebuild metadata "to be safe"
   ✅ DO: Trust the metadata as source of truth

❌ DON'T: Update main posts_metadata.json during image operations
   ✅ DO: Only update per-post metadata files

================================================================================
FUNCTION RESPONSIBILITIES
================================================================================

_generate_cascade_metadata(): 
   - Scans filesystem and rebuilds cascade metadata from scratch
   - DESTRUCTIVE: Loses enhancement info, custom orders, etc.
   - ONLY use during initial post extraction or manual full reset

_generate_post_html():
   - Generates HTML AND overwrites metadata JSON
   - ONLY use for manual "regenerate everything" operations

_generate_post_html_only():
   - Generates ONLY HTML files, preserves existing metadata
   - USE THIS for auto-generation when HTML is missing

_create_post_metadata_file():
   - Creates/overwrites the per-post metadata JSON
   - ONLY use during initial extraction or full regeneration

save_enhanced_image():
   - Updates per-post metadata with enhancement info
   - Loads existing metadata, updates specific image entry, writes back
   - THIS IS THE CORRECT PATTERN FOR UPDATES

================================================================================
"""

import os
import sys
import json
import shutil
import zipfile
import logging
import threading
import mimetypes
import tempfile
import subprocess
import time
import base64
import io
import atexit
import uuid
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import (
    APP_DIR,
    COOKIES_FILE,
    DOWNLOADS_DIR,
    EXTRACTED_DIR,
    LOGS_DIR,
    METADATA_DIR,
    PLAYLISTS_DIR,
    PLAYLIST_METADATA_JSON,
    POST_PAGES_DIR,
    POSTS_METADATA_JSON,
    POSTS_METADATA_XLSX,
    PROFILE_IMAGES_DIR,
    PROJECT_ROOT as PATH_PROJECT_ROOT,
    THUMBNAILS_DIR,
    TEMPLATES_DIR,
    ensure_common_directories,
)

# SD WebUI and eye detection imports
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from flask import Flask, request, jsonify, send_file, send_from_directory, abort
from werkzeug.exceptions import HTTPException
try:
    from flask_cors import CORS
    CORS_AVAILABLE = True
except ImportError:
    CORS_AVAILABLE = False

# Global extraction queue manager (initialized after file_ops)
extraction_queue_manager = None

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageSequence, ImageFile
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from werkzeug.utils import secure_filename


# ============================================================================
# ATOMIC FILE WRITE HELPERS
# ============================================================================

def safe_save_json(data: dict, filepath: Path, create_backup: bool = True) -> None:
    """Atomically save JSON data to file with optional backup.
    
    Args:
        data: Dictionary to save as JSON
        filepath: Path to save file
        create_backup: Whether to create a backup of existing file
    """
    filepath = Path(filepath)
    
    # Create backup of existing file
    if create_backup and filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + '.backup')
        shutil.copy2(filepath, backup_path)
    
    # Write to temporary file in same directory (ensures same filesystem)
    temp_fd, temp_path = tempfile.mkstemp(
        dir=filepath.parent,
        suffix='.tmp',
        prefix=f'.{filepath.name}_'
    )
    
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # Force write to disk
        
        # Atomic replace
        os.replace(temp_path, filepath)
        
    except Exception as e:
        # Clean up temp file on failure
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e


def safe_save_excel(df: 'pd.DataFrame', filepath: Path, sheet_name: str = 'Posts',
                   summary_df: Optional['pd.DataFrame'] = None, create_backup: bool = True) -> None:
    """Atomically save Excel file with optional backup.
    
    Args:
        df: Main DataFrame to save
        filepath: Path to save file
        sheet_name: Name of main sheet
        summary_df: Optional summary DataFrame to add as second sheet
        create_backup: Whether to create a backup of existing file
    """
    filepath = Path(filepath)
    
    # Create backup of existing file
    if create_backup and filepath.exists():
        backup_path = filepath.with_suffix(filepath.suffix + '.backup')
        shutil.copy2(filepath, backup_path)
    
    # Write to temporary file in same directory
    temp_fd, temp_path = tempfile.mkstemp(
        dir=filepath.parent,
        suffix='.tmp.xlsx',
        prefix=f'.{filepath.stem}_'
    )
    os.close(temp_fd)  # Close fd, pandas will open the file
    
    try:
        with pd.ExcelWriter(temp_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # Freeze the top row
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes = 'A2'
            
            # Add summary sheet if provided
            if summary_df is not None:
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
        
        # Atomic replace
        os.replace(temp_path, filepath)
        
    except Exception as e:
        # Clean up temp file on failure
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e


def load_post_metadata_file(post_id: str) -> tuple[Path, dict]:
    """Load per-post metadata JSON for a post."""
    metadata_path = Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Post metadata file not found for {post_id}")

    with open(metadata_path, 'r', encoding='utf-8') as f:
        return metadata_path, json.load(f)


def ensure_focus_archive(post_metadata: dict) -> dict:
    """Return the mutable focus archive block, creating it if missing."""
    focus_archive = post_metadata.get('focus_archive')
    if not isinstance(focus_archive, dict):
        focus_archive = {}
        post_metadata['focus_archive'] = focus_archive

    items = focus_archive.get('items')
    if not isinstance(items, list):
        focus_archive['items'] = []

    if not focus_archive.get('last_updated'):
        focus_archive['last_updated'] = datetime.now().isoformat()

    return focus_archive


def ensure_enhancement_presets(post_metadata: dict) -> dict:
    """Return the mutable enhancement preset block, creating it if missing."""
    presets = post_metadata.get('enhancement_presets')
    if not isinstance(presets, dict):
        presets = {}
        post_metadata['enhancement_presets'] = presets

    items = presets.get('items')
    if not isinstance(items, list):
        presets['items'] = []

    if not presets.get('last_updated'):
        presets['last_updated'] = datetime.now().isoformat()

    return presets


def get_focus_archive_dir(post_id: str) -> Path:
    """Return the on-disk directory for a post's archived focus assets."""
    archive_dir = FileOperationsManager.get_post_extracted_dir(post_id) / '.vamation-focus'
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def build_enhancement_preset_payload(post_id: str, post_metadata: dict) -> dict:
    """Build the API response payload for saved enhancement presets."""
    presets = ensure_enhancement_presets(post_metadata)
    focus_archive = ensure_focus_archive(post_metadata)
    focus_items_by_id = {
        item.get('asset_id'): item
        for item in focus_archive.get('items', [])
        if item.get('asset_id')
    }

    items = []
    for preset in presets.get('items', []):
        if not isinstance(preset, dict):
            continue

        preset_id = preset.get('preset_id')
        name = (preset.get('name') or '').strip()
        prompt_text = preset.get('prompt_text') or ''
        if not preset_id or not name or not prompt_text:
            continue

        reference_ids = []
        reference_items = []
        for asset_id in preset.get('reference_asset_ids') or []:
            if not asset_id or asset_id in reference_ids:
                continue
            reference_ids.append(asset_id)
            asset = focus_items_by_id.get(asset_id)
            if not asset:
                continue
            reference_items.append({
                'asset_id': asset_id,
                'asset_type': asset.get('asset_type', 'raw_crop'),
                'image_url': f"/api/enhance/{post_id}/focus-assets/{asset_id}/image",
            })

        items.append({
            'preset_id': preset_id,
            'name': name,
            'prompt_text': prompt_text,
            'reference_asset_ids': reference_ids,
            'reference_count': len(reference_ids),
            'reference_items': reference_items,
            'created_at': preset.get('created_at'),
            'last_used_at': preset.get('last_used_at'),
        })

    items.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    return {
        'items': items,
        'count': len(items),
        'last_updated': presets.get('last_updated'),
    }


def is_internal_edit_artifact_path(path: Path) -> bool:
    """Return True when a path lives inside Vamation's per-post internal edit artifact folders."""
    blocked_parts = {'.vamation-focus', '.vamation-edit-runs'}
    return any(part in blocked_parts for part in path.parts)


def get_edit_run_root(post_id: str) -> Path:
    """Return the on-disk root for packaged edit runs for a post."""
    run_root = FileOperationsManager.get_post_extracted_dir(post_id) / '.vamation-edit-runs'
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def normalize_pixel_box(x: int, y: int, w: int, h: int, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    image_w, image_h = image_size
    left = clamp_int(x, 0, image_w)
    top = clamp_int(y, 0, image_h)
    right = clamp_int(x + max(w, 1), 0, image_w)
    bottom = clamp_int(y + max(h, 1), 0, image_h)

    if right <= left:
        right = min(image_w, left + 1)
    if bottom <= top:
        bottom = min(image_h, top + 1)

    return left, top, right, bottom


def expand_pixel_box(box: tuple[int, int, int, int], padding: int, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    image_w, image_h = image_size
    return (
        clamp_int(left - padding, 0, image_w),
        clamp_int(top - padding, 0, image_h),
        clamp_int(right + padding, 0, image_w),
        clamp_int(bottom + padding, 0, image_h),
    )


def relative_pixel_box(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return (
        inner[0] - outer[0],
        inner[1] - outer[1],
        inner[2] - outer[0],
        inner[3] - outer[1],
    )


def make_binary_mask(size: tuple[int, int], box: tuple[int, int, int, int], shape: str = 'rect') -> 'Image.Image':
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for masked focus editing")

    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    if shape == 'ellipse':
        draw.ellipse(box, fill=255)
    else:
        draw.rectangle(box, fill=255)
    return mask


def inset_pixel_box(
    box: tuple[int, int, int, int],
    inset_x: int,
    inset_y: int,
    min_size: int = 8,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)

    max_inset_x = max(0, (width - min_size) // 2)
    max_inset_y = max(0, (height - min_size) // 2)
    safe_inset_x = clamp_int(inset_x, 0, max_inset_x)
    safe_inset_y = clamp_int(inset_y, 0, max_inset_y)

    return (
        left + safe_inset_x,
        top + safe_inset_y,
        right - safe_inset_x,
        bottom - safe_inset_y,
    )


def derive_merge_mask_box(
    mask_box: tuple[int, int, int, int],
    context_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = mask_box
    width = max(1, right - left)
    height = max(1, bottom - top)

    inset_x = max(
        Config.ENHANCE_MERGE_INSET_MIN_PX,
        int(round(width * Config.ENHANCE_MERGE_INSET_RATIO)),
    )
    inset_y = max(
        Config.ENHANCE_MERGE_INSET_MIN_PX,
        int(round(height * Config.ENHANCE_MERGE_INSET_RATIO)),
    )

    merge_box = inset_pixel_box(mask_box, inset_x, inset_y, min_size=Config.ENHANCE_MERGE_MIN_SIZE)
    return normalize_pixel_box(
        merge_box[0],
        merge_box[1],
        merge_box[2] - merge_box[0],
        merge_box[3] - merge_box[1],
        context_size,
    )


def resolve_focus_reference_items(post_id: str, post_metadata: dict, asset_ids: list[str]) -> list[dict]:
    """Resolve selected focus asset ids to concrete archive items with files."""
    focus_archive = ensure_focus_archive(post_metadata)
    items_by_id = {
        item.get('asset_id'): item
        for item in focus_archive.get('items', [])
        if item.get('asset_id')
    }

    resolved = []
    seen = set()
    for asset_id in asset_ids:
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)

        item = items_by_id.get(asset_id)
        if not item:
            raise ValueError(f"Unknown focus reference asset: {asset_id}")

        asset_path = get_focus_archive_dir(post_id) / item.get('filename', '')
        if not asset_path.exists():
            raise ValueError(f"Missing focus reference file for asset: {asset_id}")

        resolved.append({
            **item,
            'absolute_path': str(asset_path),
        })

    return resolved


def create_reference_edit_run(
    post_id: str,
    source_image_path: Path,
    source_image_filename: str,
    bbox: dict,
    prompt_text: str,
    reference_items: list[dict],
) -> dict:
    """Package the current edit request into a masked-focus run folder."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for masked focus editing")

    timestamp = datetime.now().strftime('%Y%m%dT%H%M%S%f')
    run_id = f"{source_image_path.stem}-{timestamp}"
    run_dir = get_edit_run_root(post_id) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(source_image_path) as img:
        try:
            img.seek(0)
        except Exception:
            pass
        source = ImageOps.exif_transpose(img.copy()).convert('RGBA')

    width, height = source.size
    x1 = max(0.0, min(1.0, float(bbox['x1'])))
    y1 = max(0.0, min(1.0, float(bbox['y1'])))
    x2 = max(0.0, min(1.0, float(bbox['x2'])))
    y2 = max(0.0, min(1.0, float(bbox['y2'])))

    mask_box = normalize_pixel_box(
        int(round(min(x1, x2) * width)),
        int(round(min(y1, y2) * height)),
        int(round(abs(x2 - x1) * width)),
        int(round(abs(y2 - y1) * height)),
        source.size,
    )
    context_box = expand_pixel_box(mask_box, Config.ENHANCE_CONTEXT_PAD, source.size)
    rel_mask_box = relative_pixel_box(mask_box, context_box)

    context_crop = source.crop(context_box)
    blurred_context = context_crop.filter(ImageFilter.GaussianBlur(radius=Config.ENHANCE_BLUR_RADIUS))
    target_mask = make_binary_mask(context_crop.size, rel_mask_box, Config.ENHANCE_MASK_SHAPE)

    focus_input = blurred_context.copy()
    focus_input.paste(context_crop, (0, 0), target_mask)

    focus_input_path = run_dir / 'focus-input.png'
    focus_input.save(focus_input_path, format='PNG')
    mask_path = run_dir / 'mask.png'
    target_mask.save(mask_path, format='PNG')

    prompt_path = run_dir / 'prompt.txt'
    prompt_path.write_text((prompt_text or '').rstrip() + '\n', encoding='utf-8')

    references_dir = run_dir / 'references'
    references_dir.mkdir(parents=True, exist_ok=True)
    packaged_references = []

    for index, item in enumerate(reference_items, start=1):
        source_ref_path = Path(item['absolute_path'])
        suffix = source_ref_path.suffix or '.png'
        packaged_name = f"{index:02d}-{item['asset_id']}-{item['asset_type']}{suffix}"
        packaged_path = references_dir / packaged_name
        shutil.copy2(source_ref_path, packaged_path)
        packaged_references.append({
            'asset_id': item['asset_id'],
            'asset_type': item.get('asset_type'),
            'source_image_filename': item.get('source_image_filename'),
            'source_enhanced_filename': item.get('source_enhanced_filename'),
            'filename': packaged_name,
            'absolute_path': str(packaged_path),
        })

    request_payload = {
        'run_id': run_id,
        'created_at': datetime.now().isoformat(),
        'post_id': post_id,
        'source_image_filename': source_image_filename,
        'source_image_path': str(source_image_path),
        'focus_input_path': str(focus_input_path),
        'mask_image': str(mask_path),
        'prompt_file': str(prompt_path),
        'bbox': {
            'x1': x1,
            'y1': y1,
            'x2': x2,
            'y2': y2,
        },
        'image_size': {'width': width, 'height': height},
        'mask_box': {
            'left': mask_box[0],
            'top': mask_box[1],
            'right': mask_box[2],
            'bottom': mask_box[3],
        },
        'context_box': {
            'left': context_box[0],
            'top': context_box[1],
            'right': context_box[2],
            'bottom': context_box[3],
        },
        'relative_mask_box': {
            'left': rel_mask_box[0],
            'top': rel_mask_box[1],
            'right': rel_mask_box[2],
            'bottom': rel_mask_box[3],
        },
        'settings': {
            'mask_shape': Config.ENHANCE_MASK_SHAPE,
            'context_pad': Config.ENHANCE_CONTEXT_PAD,
            'blur_radius': Config.ENHANCE_BLUR_RADIUS,
            'merge_feather': Config.ENHANCE_MERGE_FEATHER,
            'merge_mask_shape': Config.ENHANCE_MERGE_MASK_SHAPE,
            'merge_inset_ratio': Config.ENHANCE_MERGE_INSET_RATIO,
            'merge_inset_min_px': Config.ENHANCE_MERGE_INSET_MIN_PX,
            'merge_min_size': Config.ENHANCE_MERGE_MIN_SIZE,
        },
        'reference_assets': packaged_references,
    }
    safe_save_json(request_payload, run_dir / 'request.json', create_backup=False)
    safe_save_json(
        {
            'run_id': run_id,
            'status': 'prepared',
            'created_at': request_payload['created_at'],
            'artifacts': {
                'focus_input': str(focus_input_path),
                'mask_image': str(mask_path),
                'prompt_file': str(prompt_path),
                'request': str(run_dir / 'request.json'),
                'references_dir': str(references_dir),
            },
            'reference_count': len(packaged_references),
        },
        run_dir / 'run_manifest.json',
        create_backup=False,
    )

    return {
        'run_id': run_id,
        'run_dir': str(run_dir),
        'focus_input_path': str(focus_input_path),
        'mask_image_path': str(mask_path),
        'prompt_file': str(prompt_path),
        'reference_count': len(packaged_references),
        'reference_assets': packaged_references,
    }


def crop_image_to_bbox(image_path: Path, bbox: dict) -> tuple['Image.Image', dict]:
    """Crop an image using a normalized 0-1 bbox."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for focus archive cropping")

    required_keys = {'x1', 'y1', 'x2', 'y2'}
    if not isinstance(bbox, dict) or not required_keys.issubset(bbox.keys()):
        raise ValueError("A valid normalized bounding box is required")

    with Image.open(image_path) as img:
        try:
            img.seek(0)
        except Exception:
            pass
        source = ImageOps.exif_transpose(img.copy())

    width, height = source.size
    x1 = max(0.0, min(1.0, float(bbox['x1'])))
    y1 = max(0.0, min(1.0, float(bbox['y1'])))
    x2 = max(0.0, min(1.0, float(bbox['x2'])))
    y2 = max(0.0, min(1.0, float(bbox['y2'])))

    left = max(0, min(width - 1, int(round(min(x1, x2) * width))))
    top = max(0, min(height - 1, int(round(min(y1, y2) * height))))
    right = max(left + 1, min(width, int(round(max(x1, x2) * width))))
    bottom = max(top + 1, min(height, int(round(max(y1, y2) * height))))

    crop = source.crop((left, top, right, bottom))
    crop_box = {
        'left': left,
        'top': top,
        'right': right,
        'bottom': bottom,
        'width': right - left,
        'height': bottom - top,
    }
    return crop, crop_box


def append_focus_archive_item(
    post_id: str,
    post_metadata: dict,
    source_image_path: Path,
    source_image_filename: str,
    asset_type: str,
    bbox: dict,
    prompt_text: str = '',
    reference_asset_ids: Optional[list[str]] = None,
    source_run_id: Optional[str] = None,
    source_enhanced_filename: Optional[str] = None,
) -> dict:
    """Create a focus archive crop, persist it on disk, and register it in metadata."""
    crop, crop_box = crop_image_to_bbox(source_image_path, bbox)
    archive_dir = get_focus_archive_dir(post_id)
    asset_id = uuid.uuid4().hex[:12]
    asset_filename = f"{asset_type}-{asset_id}.png"
    asset_path = archive_dir / asset_filename
    crop.save(asset_path, format='PNG')

    focus_archive = ensure_focus_archive(post_metadata)
    item = {
        'asset_id': asset_id,
        'asset_type': asset_type,
        'filename': asset_filename,
        'source_image_filename': source_image_filename,
        'source_enhanced_filename': source_enhanced_filename,
        'bbox': {
            'x1': float(bbox['x1']),
            'y1': float(bbox['y1']),
            'x2': float(bbox['x2']),
            'y2': float(bbox['y2']),
        },
        'crop_box': crop_box,
        'prompt_text': prompt_text or '',
        'reference_asset_ids': list(reference_asset_ids or []),
        'source_run_id': source_run_id,
        'created_at': datetime.now().isoformat(),
        'file_size': asset_path.stat().st_size,
        'width': crop_box['width'],
        'height': crop_box['height'],
    }
    focus_archive['items'].insert(0, item)
    focus_archive['last_updated'] = datetime.now().isoformat()
    return item


def merge_reference_edit_run(run_dir: Path, output_path: Path) -> dict:
    """Merge a locally edited focus output back into the original image."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow is required for masked focus editing")

    request_path = run_dir / 'request.json'
    if not request_path.exists():
        raise FileNotFoundError(f"Missing edit request metadata: {request_path}")

    request_payload = json.loads(request_path.read_text(encoding='utf-8'))
    original_path = Path(request_payload['source_image_path'])
    focus_output_path = run_dir / 'focus-output.png'
    if not focus_output_path.exists():
        raise FileNotFoundError(f"Missing focus output image: {focus_output_path}")

    with Image.open(original_path) as original_img:
        original = ImageOps.exif_transpose(original_img.copy()).convert('RGBA')
    with Image.open(focus_output_path) as focus_output_img:
        focus_output = ImageOps.exif_transpose(focus_output_img.copy()).convert('RGBA')

    context_box_payload = request_payload['context_box']
    rel_mask_payload = request_payload['relative_mask_box']
    crop_box = (
        int(context_box_payload['left']),
        int(context_box_payload['top']),
        int(context_box_payload['right']),
        int(context_box_payload['bottom']),
    )
    mask_box = (
        int(rel_mask_payload['left']),
        int(rel_mask_payload['top']),
        int(rel_mask_payload['right']),
        int(rel_mask_payload['bottom']),
    )
    original_context = original.crop(crop_box)
    if focus_output.size != original_context.size:
        focus_output = focus_output.resize(original_context.size, Image.Resampling.LANCZOS)

    settings = request_payload.get('settings', {})
    merge_box = derive_merge_mask_box(mask_box, original_context.size)
    merge_mask = make_binary_mask(
        original_context.size,
        merge_box,
        settings.get('merge_mask_shape', Config.ENHANCE_MERGE_MASK_SHAPE),
    )
    feather = float(settings.get('merge_feather', Config.ENHANCE_MERGE_FEATHER))
    if feather > 0:
        merge_mask = merge_mask.filter(ImageFilter.GaussianBlur(radius=feather))

    source_mask_path = run_dir / 'source-mask.png'
    if not source_mask_path.exists():
        make_binary_mask(
            original_context.size,
            mask_box,
            settings.get('mask_shape', Config.ENHANCE_MASK_SHAPE),
        ).save(source_mask_path, format='PNG')

    merge_core_mask_path = run_dir / 'merge-core-mask.png'
    make_binary_mask(
        original_context.size,
        merge_box,
        settings.get('merge_mask_shape', Config.ENHANCE_MERGE_MASK_SHAPE),
    ).save(merge_core_mask_path, format='PNG')

    merge_mask_path = run_dir / 'merge-mask.png'
    merge_mask.save(merge_mask_path, format='PNG')

    merged_context = Image.composite(focus_output, original_context, merge_mask)
    merged_full = original.copy()
    merged_full.paste(merged_context, (crop_box[0], crop_box[1]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_full.convert('RGB').save(output_path)

    manifest_path = run_dir / 'run_manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    else:
        manifest = {'run_id': request_payload.get('run_id')}
    manifest.update({
        'status': 'merged',
        'updated_at': datetime.now().isoformat(),
        'artifacts': {
            **manifest.get('artifacts', {}),
            'focus_output': str(focus_output_path),
            'source_mask': str(source_mask_path),
            'merge_core_mask': str(merge_core_mask_path),
            'merge_mask': str(merge_mask_path),
            'merged_output': str(output_path),
        },
        'merge': {
            'source_box': {
                'left': mask_box[0],
                'top': mask_box[1],
                'right': mask_box[2],
                'bottom': mask_box[3],
            },
            'merge_box': {
                'left': merge_box[0],
                'top': merge_box[1],
                'right': merge_box[2],
                'bottom': merge_box[3],
            },
            'feather': feather,
            'merge_mask_shape': settings.get('merge_mask_shape', Config.ENHANCE_MERGE_MASK_SHAPE),
        },
    })
    safe_save_json(manifest, manifest_path, create_backup=False)

    return {
        'focus_output_path': str(focus_output_path),
        'merge_mask_path': str(merge_mask_path),
        'merged_output_path': str(output_path),
    }


def get_zo_access_token() -> Optional[str]:
    token = os.environ.get('ZO_CLIENT_IDENTITY_TOKEN') or os.environ.get('ZO_API_KEY')
    if not token or token == 'none':
        return None
    return token


def launch_zo_edit_request(run_dir: Path, prompt: str, model_name: Optional[str] = None) -> subprocess.Popen:
    """Ask Zo to use its image-edit tool and save a local focus output."""
    token = get_zo_access_token()
    if not token:
        raise RuntimeError("ZO_CLIENT_IDENTITY_TOKEN is required for image enhancement")

    request_payload = {
        'input': prompt,
        'output_format': {
            'type': 'object',
            'properties': {
                'success': {'type': 'boolean'},
            },
            'required': ['success'],
        },
    }
    if model_name:
        request_payload['model_name'] = model_name

    request_path = run_dir / 'zo-edit-request.json'
    response_path = run_dir / 'zo-edit-response.json'
    error_path = run_dir / 'zo-edit-error.txt'
    safe_save_json(request_payload, request_path, create_backup=False)

    child_code = """
import json, os, sys, urllib.request
request_path, response_path, error_path = sys.argv[1:4]
token = os.environ.get('ZO_CLIENT_IDENTITY_TOKEN') or os.environ.get('ZO_API_KEY')
if not token or token == 'none':
    with open(error_path, 'w', encoding='utf-8') as f:
        f.write('Missing Zo access token')
    raise SystemExit(1)
payload = json.loads(open(request_path, 'r', encoding='utf-8').read())
body = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(
    'https://api.zo.computer/zo/ask',
    data=body,
    headers={
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    },
)
try:
    with urllib.request.urlopen(req, timeout=900) as resp:
        raw = resp.read().decode('utf-8')
    with open(response_path, 'w', encoding='utf-8') as f:
        f.write(raw)
except Exception as exc:
    with open(error_path, 'w', encoding='utf-8') as f:
        f.write(str(exc))
    raise
"""

    return subprocess.Popen(
        [sys.executable, '-c', child_code, str(request_path), str(response_path), str(error_path)],
        cwd=str(run_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )


def build_zo_image_edit_prompt(
    prepared_run: dict,
    positive_prompt: str,
    reference_items: list[dict],
    output_path: Path,
) -> str:
    reference_lines = []
    for item in reference_items[:3]:
        reference_path = Path(item['absolute_path'])
        reference_lines.append(
            f"- {reference_path} ({item.get('asset_type', 'reference')}, source {item.get('source_image_filename', '')})"
        )
    references_block = "\n".join(reference_lines) if reference_lines else "- none"

    return (
        "Use the edit image tool powered by Nano Banana.\n"
        f"Primary image to edit: {prepared_run['focus_input_path']}\n"
        "Additional focus references to consult if your tool supports them:\n"
        f"{references_block}\n\n"
        "Edit instructions:\n"
        f"{positive_prompt.strip()}\n\n"
        "Hard constraints:\n"
        "- Only edit the target content in the main image.\n"
        "- Preserve the existing anime art style, linework, colours, and surrounding context.\n"
        "- Keep the canvas size identical to the input image.\n"
        "- Save the edited result exactly to this absolute PNG path: "
        f"{output_path}\n"
        "- Do not save anywhere else.\n"
        "- Return JSON only."
    )


def wait_for_complete_focus_output(
    zo_process: subprocess.Popen,
    focus_output_path: Path,
    timeout_seconds: float = 600,
    poll_interval: float = 0.5,
) -> None:
    """Wait for Zo to finish and verify the output image can be fully loaded."""
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        exit_code = zo_process.poll()
        if exit_code is None:
            time.sleep(poll_interval)
            continue

        error_path = focus_output_path.parent / 'zo-edit-error.txt'
        response_path = focus_output_path.parent / 'zo-edit-response.json'

        if exit_code != 0:
            error_message = None
            if error_path.exists():
                error_message = error_path.read_text(encoding='utf-8').strip()
            elif response_path.exists():
                error_message = response_path.read_text(encoding='utf-8').strip()
            raise RuntimeError(error_message or f"Zo image edit request exited with status {exit_code}")

        if not focus_output_path.exists() or focus_output_path.stat().st_size <= 0:
            raise RuntimeError("Zo image edit completed without writing focus-output.png")

        last_error = None
        verify_deadline = min(deadline, time.time() + 15)
        while time.time() < verify_deadline:
            try:
                with Image.open(focus_output_path) as focus_output_img:
                    focus_output_img.load()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)

        raise RuntimeError(f"Zo image edit wrote an unreadable focus output: {last_error}")

    if zo_process.poll() is None:
        zo_process.terminate()
    raise TimeoutError("Timed out waiting for Zo image edit output")


def run_enhancement_pipeline(post_id: str, filename: str, config_data: dict) -> dict:
    """Run the full masked-focus enhancement pipeline and return the success payload."""
    reference_asset_ids = config_data.get('reference_asset_ids') or []
    reference_items = []
    prompt_text = (config_data.get('prompt') or '').strip()
    custom_mask = config_data.get('custom_mask')

    if not prompt_text:
        raise ValueError("A prompt is required")
    if not custom_mask:
        raise ValueError("A manual selection is required")
    if not REQUESTS_AVAILABLE or not PIL_AVAILABLE:
        raise RuntimeError("Required libraries not available")
    if not get_zo_access_token():
        raise RuntimeError("Zo image editing is not configured on this host")

    image_path = FileOperationsManager.get_post_extracted_dir(post_id) / filename
    if not image_path.exists():
        raise FileNotFoundError("Image not found")

    if reference_asset_ids:
        _, post_metadata = load_post_metadata_file(post_id)
        reference_items = resolve_focus_reference_items(post_id, post_metadata, reference_asset_ids)

    stem = image_path.stem
    suffix = image_path.suffix
    base_stem = re.sub(r'_enhanced\d+$', '', stem)
    iteration = 1
    while True:
        enhanced_filename = f"{base_stem}_enhanced{iteration:03d}{suffix}"
        enhanced_path = image_path.parent / enhanced_filename
        if not enhanced_path.exists():
            break
        iteration += 1

    prepared_run = create_reference_edit_run(
        post_id=post_id,
        source_image_path=image_path,
        source_image_filename=filename,
        bbox=custom_mask,
        prompt_text=prompt_text,
        reference_items=reference_items,
    )

    run_dir = Path(prepared_run['run_dir'])
    focus_output_path = run_dir / 'focus-output.png'
    prompt = build_zo_image_edit_prompt(prepared_run, prompt_text, reference_items, focus_output_path)
    zo_process = launch_zo_edit_request(
        run_dir,
        prompt,
        model_name=Config.ZO_IMAGE_ORCHESTRATOR_MODEL,
    )

    wait_for_complete_focus_output(zo_process, focus_output_path)

    merge_reference_edit_run(run_dir, enhanced_path)
    logger.info(f"Enhanced image saved via Zo image edit: {enhanced_path} (iteration {iteration})")

    enhancement_config = {
        'prompt': prompt_text,
        'provider': 'zo-nano-banana',
        'reference_asset_ids': [item['asset_id'] for item in reference_items],
        'reference_asset_count': len(reference_items),
        'source_run_id': prepared_run['run_id'],
        'focus_input_path': prepared_run['focus_input_path'],
        'focus_output_path': str(focus_output_path),
    }
    return {
        "enhanced_filename": enhanced_filename,
        "original_filename": filename,
        "config": enhancement_config,
        "source_run_id": prepared_run['run_id'],
        "reference_asset_count": len(reference_items),
    }


class EnhancementJobManager:
    """Run enhancement requests asynchronously so the browser can poll for completion."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def start_job(self, post_id: str, filename: str, config_data: dict) -> str:
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        with self._lock:
            self._jobs[job_id] = {
                'job_id': job_id,
                'post_id': post_id,
                'filename': filename,
                'status': 'queued',
                'created_at': now,
                'updated_at': now,
                'result': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, post_id, filename, dict(config_data)),
            daemon=True,
        )
        thread.start()
        return job_id

    def _run_job(self, job_id: str, post_id: str, filename: str, config_data: dict) -> None:
        self._update(job_id, status='running')
        try:
            result = run_enhancement_pipeline(post_id, filename, config_data)
            self._update(job_id, status='succeeded', result=result)
        except Exception as exc:
            logger.error(f"Error enhancing {post_id}/{filename} in job {job_id}: {exc}")
            self._update(job_id, status='failed', error=str(exc))

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job['updated_at'] = datetime.now().isoformat()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


enhancement_job_manager = EnhancementJobManager()


def canonical_base_filename(filename: str) -> str:
    """Strip Vamation enhancement iteration suffixes from a filename."""
    path = Path(filename)
    stem = re.sub(r'_enhanced\d+$', '', path.stem)
    return stem + path.suffix


def ensure_image_alternate_metadata(img_entry: dict) -> None:
    """Ensure a gallery image entry has the alternate-version structure."""
    current_filename = img_entry.get('filename')
    if not current_filename:
        return

    base_filename = (
        img_entry.get('base_image_filename')
        or img_entry.get('original_filename')
        or canonical_base_filename(current_filename)
    )
    img_entry['base_image_filename'] = base_filename
    img_entry['original_filename'] = base_filename
    img_entry['active_alternate_filename'] = current_filename

    alternates = img_entry.get('alternate_versions')
    if not isinstance(alternates, list):
        alternates = []

    by_filename = {}
    normalized = []
    for alt in alternates:
        if not isinstance(alt, dict):
            continue
        alt_filename = alt.get('filename')
        if not alt_filename or alt_filename in by_filename:
            continue
        by_filename[alt_filename] = alt
        normalized.append(alt)

    if base_filename not in by_filename:
        normalized.insert(0, {
            'filename': base_filename,
            'kind': 'original',
            'created_at': img_entry.get('enhancement_date') or img_entry.get('file_info', {}).get('modified') or datetime.now().isoformat(),
            'source_run_id': None,
            'prompt_text': '',
            'active': current_filename == base_filename,
        })

    if current_filename not in {alt.get('filename') for alt in normalized}:
        normalized.append({
            'filename': current_filename,
            'kind': 'enhanced' if current_filename != base_filename else 'original',
            'created_at': img_entry.get('enhancement_date') or datetime.now().isoformat(),
            'source_run_id': None,
            'prompt_text': img_entry.get('enhancement_config', {}).get('prompt', ''),
            'active': True,
        })

    for alt in normalized:
        alt['active'] = alt.get('filename') == current_filename
        alt.setdefault('kind', 'original' if alt.get('filename') == base_filename else 'enhanced')
        alt.setdefault('created_at', datetime.now().isoformat())
        alt.setdefault('source_run_id', None)
        alt.setdefault('prompt_text', '')

    img_entry['alternate_versions'] = normalized


def find_image_entry_by_variant(images: list[dict], filename: str) -> Optional[dict]:
    """Find a gallery image entry by active filename, base filename, or any stored alternate."""
    target_base = canonical_base_filename(filename)
    for img_entry in images:
        current_filename = img_entry.get('filename')
        if current_filename == filename:
            return img_entry

        if img_entry.get('base_image_filename') == target_base:
            return img_entry

        for alt in img_entry.get('alternate_versions', []) or []:
            if alt.get('filename') == filename:
                return img_entry
    return None


def add_or_update_alternate_version(
    img_entry: dict,
    alt_filename: str,
    *,
    kind: str,
    source_run_id: Optional[str] = None,
    prompt_text: str = '',
    created_at: Optional[str] = None,
    active: Optional[bool] = None,
) -> None:
    """Insert or refresh an alternate version entry and mark it active."""
    ensure_image_alternate_metadata(img_entry)
    alternates = img_entry.get('alternate_versions', [])
    created_at = created_at or datetime.now().isoformat()

    target = None
    for alt in alternates:
        if alt.get('filename') == alt_filename:
            target = alt
            break

    if target is None:
        target = {
            'filename': alt_filename,
            'kind': kind,
            'created_at': created_at,
            'source_run_id': source_run_id,
            'prompt_text': prompt_text,
            'active': bool(active) if active is not None else True,
        }
        alternates.append(target)
    else:
        target['kind'] = kind
        target['source_run_id'] = source_run_id
        target['prompt_text'] = prompt_text
        target['created_at'] = created_at

    if active is None:
        img_entry['filename'] = alt_filename
        img_entry['active_alternate_filename'] = alt_filename
        for alt in alternates:
            alt['active'] = alt.get('filename') == alt_filename
    else:
        current_active = img_entry.get('active_alternate_filename') or img_entry.get('filename')
        if not current_active:
            current_active = img_entry.get('base_image_filename') or canonical_base_filename(alt_filename)
        img_entry['active_alternate_filename'] = current_active
        for alt in alternates:
            alt['active'] = alt.get('filename') == current_active

    img_entry['alternate_versions'] = alternates


def build_alternate_payload(post_id: str, img_entry: dict) -> dict:
    """Build the API response payload for a gallery image's alternates."""
    ensure_image_alternate_metadata(img_entry)
    base_filename = img_entry['base_image_filename']
    active_filename = img_entry.get('filename')
    alternates = []
    for alt in img_entry.get('alternate_versions', []):
        alt_filename = alt.get('filename')
        if not alt_filename:
            continue
        alternates.append({
            'filename': alt_filename,
            'kind': alt.get('kind', 'enhanced'),
            'created_at': alt.get('created_at'),
            'source_run_id': alt.get('source_run_id'),
            'prompt_text': alt.get('prompt_text', ''),
            'active': alt_filename == active_filename,
            'display_label': 'Original' if alt_filename == base_filename else alt_filename,
            'image_url': f"/api/images/content/{post_id}/{alt_filename}",
        })

    alternates.sort(key=lambda item: (0 if item['filename'] == base_filename else 1, item.get('created_at') or ''))
    return {
        'base_image_filename': base_filename,
        'active_filename': active_filename,
        'alternate_count': len(alternates),
        'alternates': alternates,
    }


def regenerate_post_html_from_metadata(post_id: str, post_metadata: dict) -> None:
    """Regenerate single + cascade HTML from already-updated per-post metadata."""
    post_for_html = {
        'post_id': post_id,
        'revised_post_name': post_metadata.get('post_name', ''),
        'post_name': post_metadata.get('post_name', ''),
        'post_date': post_metadata.get('post_date', ''),
        'cascade_metadata': post_metadata.get('cascade_metadata', {}),
    }

    single_html = FileOperationsManager._create_single_view_html(post_id, post_for_html)
    single_path = Config.POST_PAGES_DIR / f"{post_id}.html"
    with open(single_path, 'w', encoding='utf-8') as f:
        f.write(single_html)

    cascade_html = FileOperationsManager._create_cascade_view_html(post_id, post_for_html)
    cascade_path = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
    with open(cascade_path, 'w', encoding='utf-8') as f:
        f.write(cascade_html)


# Configuration
class Config:
    PROJECT_ROOT = PATH_PROJECT_ROOT
    BASE_DIR = PROJECT_ROOT
    WAREHOUSE_DIR = EXTRACTED_DIR.parent
    METADATA_DIR = METADATA_DIR
    DOWNLOADS_DIR = DOWNLOADS_DIR
    WEBAPP_DIR = APP_DIR
    
    METADATA_JSON = POSTS_METADATA_JSON
    METADATA_EXCEL = POSTS_METADATA_XLSX
    PLAYLIST_METADATA_JSON = PLAYLIST_METADATA_JSON
    PROFILE_IMAGES_DIR = PROFILE_IMAGES_DIR
    LOGS_DIR = LOGS_DIR
    
    EXTRACTED_DIR = EXTRACTED_DIR
    
    POST_PAGES_DIR = POST_PAGES_DIR
    PLAYLISTS_DIR = PLAYLISTS_DIR
    TEMPLATES_DIR = TEMPLATES_DIR
    
    THUMBNAILS_DIR = THUMBNAILS_DIR
    PROFILE_PREVIEWS_DIR = THUMBNAILS_DIR / 'profile-previews'
    
    # Server configuration - Local only
    HOST = '127.0.0.1'
    PORT = 5000
    DEBUG = False
    
    # File processing
    MAX_THUMBNAIL_SIZE = (400, 400)
    PROFILE_PREVIEW_SIZE = (480, 720)
    PROFILE_PREVIEW_FORMAT = 'WEBP'
    PROFILE_PREVIEW_QUALITY = 82
    ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    MAX_CONCURRENT_EXTRACTIONS = int(os.environ.get('MAX_CONCURRENT_EXTRACTIONS', '2'))
    EXTRACTION_STATUS_TTL_SECONDS = int(os.environ.get('EXTRACTION_STATUS_TTL_SECONDS', '300'))
    
    ENHANCE_CONTEXT_PAD = int(os.environ.get('VAMATION_ENHANCE_CONTEXT_PAD', '80'))
    ENHANCE_BLUR_RADIUS = float(os.environ.get('VAMATION_ENHANCE_BLUR_RADIUS', '10'))
    ENHANCE_MERGE_FEATHER = float(os.environ.get('VAMATION_ENHANCE_MERGE_FEATHER', '16'))
    ENHANCE_MASK_SHAPE = os.environ.get('VAMATION_ENHANCE_MASK_SHAPE', 'rect')
    ENHANCE_MERGE_MASK_SHAPE = os.environ.get('VAMATION_ENHANCE_MERGE_MASK_SHAPE', 'ellipse')
    ENHANCE_MERGE_INSET_RATIO = float(os.environ.get('VAMATION_ENHANCE_MERGE_INSET_RATIO', '0.08'))
    ENHANCE_MERGE_INSET_MIN_PX = int(os.environ.get('VAMATION_ENHANCE_MERGE_INSET_MIN_PX', '6'))
    ENHANCE_MERGE_MIN_SIZE = int(os.environ.get('VAMATION_ENHANCE_MERGE_MIN_SIZE', '12'))
    ZO_IMAGE_ORCHESTRATOR_MODEL = os.environ.get('VAMATION_ZO_IMAGE_ORCHESTRATOR_MODEL', 'zo:google/gemini-3.1-pro-preview')
    SD_WEBUI_URL = "http://127.0.0.1:7861"
    SD_WEBUI_PATH = r"D:\3D Objects\sd.webui\webui"
    SD_WEBUI_STARTUP_TIMEOUT = 120
    KEEP_WEBUI_ALIVE = False
    YOLO_MODEL_PATH = None
    
    ENABLE_IMAGE_ENHANCEMENT = True
    
    @classmethod
    def ensure_directories(cls):
        ensure_common_directories()

# Setup logging
ensure_common_directories()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Config.WEBAPP_DIR / 'vama_gallery.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# BACKGROUND UPDATE MANAGER
# ============================================================================

class BackgroundUpdateManager:
    """Non-blocking app-triggered pipeline updater with lock/status files."""

    COOLDOWN_SECONDS = 2

    def __init__(self):
        self._lock = threading.Lock()
        self.status_file = Config.LOGS_DIR / 'pipeline_update_status.json'
        self.lock_file = Config.LOGS_DIR / 'pipeline_update.lock'

    def _now_iso(self) -> str:
        return datetime.utcnow().isoformat() + 'Z'

    def _default_status(self) -> dict:
        return {
            'running': False,
            'last_triggered_at': None,
            'last_finished_at': None,
            'last_success_at': None,
            'last_reason': None,
            'last_exit_code': None,
            'last_error': None,
            'last_log_file': str(Config.LOGS_DIR / 'pipeline_update.log'),
            'last_summary': None,
            'active_pid': None,
        }

    def _load_pipeline_summary(self) -> dict | None:
        try:
            payload = json.loads(Config.METADATA_JSON.read_text(encoding='utf-8'))
            summary = payload.get('summary') or {}
            run_details = summary.get('run_details') or {}
            return {
                'total_posts': summary.get('total_posts'),
                'processed_in_range': run_details.get('processed_in_range', 0),
                'added_posts': run_details.get('added_posts', 0),
                'updated_posts': run_details.get('updated_posts', 0),
                'date_range': summary.get('date_range') or {},
                'extraction_date': summary.get('extraction_date'),
            }
        except Exception:
            return None

    def get_status(self) -> dict:
        if self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding='utf-8'))
            except Exception:
                pass
        return self._default_status()

    def _save_status(self, status: dict) -> None:
        safe_save_json(status, self.status_file, create_backup=False)

    def _clear_lock(self) -> None:
        try:
            self.lock_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _pid_matches_pipeline(self, pid: int) -> bool:
        try:
            cmdline_path = Path(f'/proc/{pid}/cmdline')
            if not cmdline_path.exists():
                return False
            raw = cmdline_path.read_bytes().decode('utf-8', errors='ignore')
            cmdline = raw.replace('\x00', ' ')
            return (
                'Projects/Vamation/scripts/run_pipeline.py' in cmdline or
                'Projects/Vamation/ingest/pipeline/integrated_pipeline.py' in cmdline or
                'python scripts/run_pipeline.py' in cmdline
            )
        except Exception:
            return False

    def _is_process_running(self) -> bool:
        status = self.get_status()
        pid = status.get('active_pid')
        if pid is None and self.lock_file.exists():
            try:
                pid = int(self.lock_file.read_text(encoding='utf-8').strip())
            except Exception:
                pid = None

        if pid is None:
            self._clear_lock()
            if status.get('running'):
                status['running'] = False
                status['active_pid'] = None
                self._save_status(status)
            return False

        try:
            os.kill(int(pid), 0)
            if self._pid_matches_pipeline(int(pid)):
                return True
        except Exception:
            pass

        self._clear_lock()
        status['running'] = False
        status['active_pid'] = None
        self._save_status(status)
        return False

    def maybe_trigger_update(self, reason: str = 'app-load') -> dict:
        with self._lock:
            status = self.get_status()

            if self._is_process_running():
                status['running'] = True
                return {
                    'started': False,
                    'skipped': 'already-running',
                    'status': status,
                }

            last_success = status.get('last_success_at')
            if last_success:
                try:
                    last_dt = datetime.fromisoformat(last_success.replace('Z', '+00:00'))
                    age = (datetime.utcnow() - last_dt.replace(tzinfo=None)).total_seconds()
                    if age < self.COOLDOWN_SECONDS:
                        status['running'] = False
                        return {
                            'started': False,
                            'skipped': 'cooldown',
                            'status': status,
                        }
                except Exception:
                    pass

            log_file = Config.LOGS_DIR / 'pipeline_update.log'
            with open(log_file, 'ab') as lf:
                process = subprocess.Popen(
                    ['python', 'scripts/run_pipeline.py'],
                    cwd=str(Config.PROJECT_ROOT),
                    stdout=lf,
                    stderr=lf,
                    start_new_session=True,
                )

            status.update({
                'running': True,
                'last_triggered_at': self._now_iso(),
                'last_reason': reason,
                'last_exit_code': None,
                'last_error': None,
                'last_log_file': str(log_file),
                'active_pid': process.pid,
            })
            self._save_status(status)
            self.lock_file.write_text(str(process.pid), encoding='utf-8')

            def watch_process(pid: int):
                exit_code = None
                error_text = None
                try:
                    exit_code = process.wait()
                except Exception as e:
                    error_text = str(e)
                finally:
                    current = self.get_status()
                    current['running'] = False
                    current['last_finished_at'] = self._now_iso()
                    current['last_exit_code'] = exit_code
                    current['last_error'] = error_text
                    current['active_pid'] = None
                    if exit_code == 0:
                        current['last_success_at'] = current['last_finished_at']
                        current['last_summary'] = self._load_pipeline_summary()
                    self._save_status(current)
                    self._clear_lock()

            threading.Thread(target=watch_process, args=(process.pid,), daemon=True).start()

            return {
                'started': True,
                'status': status,
            }

background_update_manager = BackgroundUpdateManager()


# Initialize Flask app
app = Flask(__name__, static_folder=str(Config.WEBAPP_DIR))
if CORS_AVAILABLE:
    CORS(app)


# ============================================================================
# SD WebUI MANAGER
# ============================================================================

class SDWebUIManager:
    """Manages Stable Diffusion WebUI subprocess lifecycle."""
    
    def __init__(self):
        self.process = None
        self.url = Config.SD_WEBUI_URL
        self.webui_path = Config.SD_WEBUI_PATH
        self._lock = threading.Lock()
        
        # Register cleanup on exit (only if KEEP_WEBUI_ALIVE is False)
        if not Config.KEEP_WEBUI_ALIVE:
            atexit.register(self.shutdown)
    
    def is_running(self) -> bool:
        """Check if SD WebUI API is accessible."""
        if not REQUESTS_AVAILABLE:
            return False
        
        # CRITICAL: Only check API endpoints, not web UI root
        # The API must be enabled with --api flag for inpainting to work
        api_endpoints = [
            '/sdapi/v1/sd-models',  # Primary API check (same as test_adetailer.py)
            '/sdapi/v1/options',    # Alternative API check
        ]
        
        for endpoint in api_endpoints:
            try:
                url = f"{self.url}{endpoint}"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    logger.info(f"SD WebUI API is running (verified via {endpoint})")
                    return True
            except requests.exceptions.Timeout:
                logger.debug(f"Timeout checking {endpoint}")
                continue
            except requests.exceptions.ConnectionError:
                logger.debug(f"Connection error checking {endpoint}")
                continue
            except Exception as e:
                logger.debug(f"Error checking {endpoint}: {e}")
                continue
        
        logger.warning("SD WebUI may be running but API is not enabled. Ensure --api flag is used.")
        return False
    
    def start(self) -> dict:
        """Start SD WebUI if not already running."""
        with self._lock:
            if self.is_running():
                logger.info("SD WebUI is already running")
                return {"success": True, "message": "Already running"}
            
            if not Path(self.webui_path).exists():
                error = f"SD WebUI path not found: {self.webui_path}"
                logger.error(error)
                return {"success": False, "error": error}
            
            try:
                logger.info(f"Starting SD WebUI from {self.webui_path}")
                
                # Start SD WebUI with API enabled
                # Try VAMA-specific batch file first (has --api built-in), then standard files
                launch_script = Path(self.webui_path) / "webui-user-vama.bat"
                if not launch_script.exists():
                    launch_script = Path(self.webui_path) / "webui-user.bat"
                if not launch_script.exists():
                    launch_script = Path(self.webui_path) / "webui.bat"
                if not launch_script.exists():
                    launch_script = Path(self.webui_path) / "launch.py"
                
                # Set up environment
                env = os.environ.copy()
                
                # Determine command based on script type
                if launch_script.suffix == '.bat':
                    # For batch files, don't pass args - they're already in the batch file
                    # The webui-user-vama.bat has COMMANDLINE_ARGS set internally
                    cmd = ["cmd.exe", "/c", str(launch_script)]
                else:
                    # For Python script, pass --nowebui and --api directly
                    cmd = ["python", str(launch_script), "--nowebui", "--api"]
                
                logger.info(f"Launching with command: {' '.join(cmd)}")
                
                # Always use CREATE_NEW_CONSOLE so user can see SD WebUI loading
                # This is important for debugging and knowing when it's ready
                creation_flags = subprocess.CREATE_NEW_CONSOLE
                
                if Config.KEEP_WEBUI_ALIVE:
                    # For persistent mode, still show console but don't track process
                    # This way user can see SD WebUI status and close it manually if needed
                    if launch_script.suffix == '.bat':
                        # Don't use start /b, just run the batch file directly
                        cmd = ["cmd.exe", "/c", str(launch_script)]
                
                process_handle = subprocess.Popen(
                    cmd,
                    cwd=self.webui_path,
                    env=env,
                    creationflags=creation_flags
                )
                
                # Only track process if we'll need to shut it down later
                if not Config.KEEP_WEBUI_ALIVE:
                    self.process = process_handle
                else:
                    # Don't track process handle for persistent mode
                    self.process = None
                    logger.info("SD WebUI launched in persistent mode (process not tracked)")
                
                # Wait for startup with timeout
                logger.info("Waiting for SD WebUI to start (this may take 30-60 seconds)...")
                start_time = time.time()
                check_count = 0
                while time.time() - start_time < Config.SD_WEBUI_STARTUP_TIMEOUT:
                    if self.is_running():
                        elapsed = time.time() - start_time
                        logger.info(f"SD WebUI started successfully after {elapsed:.1f} seconds")
                        return {"success": True, "message": "Started successfully"}
                    check_count += 1
                    if check_count % 3 == 0:  # Log every 9 seconds
                        elapsed = time.time() - start_time
                        logger.info(f"Still waiting... ({elapsed:.0f}s elapsed)")
                    time.sleep(3)  # Check every 3 seconds instead of 5
                
                # Timeout
                error = "SD WebUI startup timeout"
                logger.error(error)
                return {"success": False, "error": error}
                
            except Exception as e:
                error = f"Failed to start SD WebUI: {e}"
                logger.error(error)
                return {"success": False, "error": str(e)}
    
    def shutdown(self):
        """Stop SD WebUI process (only if KEEP_WEBUI_ALIVE is False)."""
        if Config.KEEP_WEBUI_ALIVE:
            logger.info("KEEP_WEBUI_ALIVE is enabled - SD WebUI will continue running")
            return
        
        with self._lock:
            if self.process:
                logger.info("Shutting down SD WebUI...")
                try:
                    self.process.terminate()
                    self.process.wait(timeout=10)
                except:
                    self.process.kill()
                self.process = None


# ============================================================================
# EYE INPAINTER
# ============================================================================

class EyeInpainter:
    """Handles eye detection and inpainting for anime images."""
    
    def __init__(self, yolo_model_path=None):
        """Initialize face detection."""
        self.yolo_model = None
        if YOLO_AVAILABLE and yolo_model_path and Path(yolo_model_path).exists():
            try:
                logger.info(f"Loading YOLO model: {yolo_model_path}")
                self.yolo_model = YOLO(yolo_model_path)
                logger.info("YOLO model loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load YOLO model: {e}")
        
        # MediaPipe face detection (fallback)
        self.mp_face_mesh = None
        self.face_mesh = None
        self.mp_face_detection = None
        self.face_detection = None
        
        if MEDIAPIPE_AVAILABLE:
            self.mp_face_mesh = mp.solutions.face_mesh
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=5,
                refine_landmarks=True,
                min_detection_confidence=0.1
            )
            
            self.mp_face_detection = mp.solutions.face_detection
            self.face_detection = self.mp_face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.1
            )
        
        # Eye landmark indices (MediaPipe 468 landmarks)
        self.LEFT_EYE_INDICES = [33, 133, 160, 159, 158, 157, 173, 144, 145, 153, 154, 155, 163, 7]
        self.RIGHT_EYE_INDICES = [362, 263, 387, 386, 385, 384, 398, 373, 374, 380, 381, 382, 390, 249]
    
    def detect_eyes(self, image_np):
        """Detect eye regions - tries YOLO first, then MediaPipe. Returns list of bounding boxes."""
        if not CV2_AVAILABLE:
            return []
        
        h, w = image_np.shape[:2]
        eye_regions = []
        
        # Method 1: Custom YOLO model (if loaded)
        if self.yolo_model is not None:
            try:
                results = self.yolo_model(image_np, verbose=False)
                if len(results) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        confidence = float(box.conf[0])
                        
                        if confidence > 0.3:
                            eye_regions.append({
                                'box': (x1, y1, x2, y2),
                                'side': 'detected',
                                'confidence': confidence
                            })
                    
                    if eye_regions:
                        return eye_regions
            except Exception as e:
                logger.warning(f"YOLO detection failed: {e}")
        
        # Method 2: MediaPipe face detection
        if MEDIAPIPE_AVAILABLE and self.face_detection:
            try:
                rgb_image = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
                results = self.face_detection.process(rgb_image)
                if results.detections:
                    for detection in results.detections:
                        bbox = detection.location_data.relative_bounding_box
                        x = int(bbox.xmin * w)
                        y = int(bbox.ymin * h)
                        face_w = int(bbox.width * w)
                        face_h = int(bbox.height * h)
                        
                        # Anime-specific eye proportions
                        eye_y = y + int(face_h * 0.32)
                        eye_h = int(face_h * 0.22)
                        
                        # Left eye
                        left_eye_x = x + int(face_w * 0.20)
                        eye_w = int(face_w * 0.28)
                        eye_regions.append({
                            'box': (max(0, left_eye_x), max(0, eye_y),
                                   min(w, left_eye_x + eye_w), min(h, eye_y + eye_h)),
                            'side': 'left'
                        })
                        
                        # Right eye
                        right_eye_x = x + int(face_w * 0.52)
                        eye_regions.append({
                            'box': (max(0, right_eye_x), max(0, eye_y),
                                   min(w, right_eye_x + eye_w), min(h, eye_y + eye_h)),
                            'side': 'right'
                        })
                
                if eye_regions:
                    return eye_regions
            except Exception as e:
                logger.warning(f"MediaPipe face detection failed: {e}")
        
        # Method 3: Face mesh for precise landmarks
        if MEDIAPIPE_AVAILABLE and self.face_mesh and not eye_regions:
            try:
                rgb_image = cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB)
                results = self.face_mesh.process(rgb_image)
                
                if results.multi_face_landmarks:
                    for face_landmarks in results.multi_face_landmarks:
                        landmarks = face_landmarks.landmark
                        
                        # Left eye
                        left_eye_points = [(int(landmarks[idx].x * w), int(landmarks[idx].y * h)) 
                                          for idx in self.LEFT_EYE_INDICES]
                        x, y, w_box, h_box = cv2.boundingRect(np.array(left_eye_points))
                        padding = 20
                        eye_regions.append({
                            'box': (max(0, x-padding), max(0, y-padding), 
                                   min(w, x+w_box+padding*2), min(h, y+h_box+padding*2)),
                            'side': 'left'
                        })
                        
                        # Right eye
                        right_eye_points = [(int(landmarks[idx].x * w), int(landmarks[idx].y * h)) 
                                           for idx in self.RIGHT_EYE_INDICES]
                        x, y, w_box, h_box = cv2.boundingRect(np.array(right_eye_points))
                        eye_regions.append({
                            'box': (max(0, x-padding), max(0, y-padding), 
                                   min(w, x+w_box+padding*2), min(h, y+h_box+padding*2)),
                            'side': 'right'
                        })
            except Exception as e:
                logger.warning(f"MediaPipe face mesh failed: {e}")
        
        return eye_regions
    
    def create_mask(self, image_shape, eye_regions):
        """Create inpainting mask with white regions for eyes."""
        if not CV2_AVAILABLE:
            return None
        
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        
        for region in eye_regions:
            x1, y1, x2, y2 = region['box']
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            axes = ((x2 - x1) // 2, (y2 - y1) // 2)
            cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
        
        return mask
    
    def create_mask_from_rectangle(self, image_shape, x1, y1, x2, y2):
        """Create mask from rectangular selection."""
        if not CV2_AVAILABLE:
            return None
        
        mask = np.zeros(image_shape[:2], dtype=np.uint8)
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        axes = ((x2 - x1) // 2, (y2 - y1) // 2)
        cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
        return mask
    
    def encode_image(self, image):
        """Convert PIL Image to base64 string."""
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    def inpaint_via_api(self, image_pil, mask_pil, config=None):
        """Send inpainting request to SD WebUI API."""
        if not REQUESTS_AVAILABLE:
            return None
        
        if config is None:
            config = Config.INPAINT_CONFIG.copy()
        
        payload = {
            "init_images": [self.encode_image(image_pil)],
            "mask": self.encode_image(mask_pil),
            "mask_blur": 4,
            "inpainting_mask_invert": 0,
            **config
        }
        
        try:
            response = requests.post(
                f"{Config.SD_WEBUI_URL}/sdapi/v1/img2img",
                json=payload,
                timeout=300
            )
            
            # Check response before raising for status
            if response.status_code == 404:
                logger.error(f"API endpoint not found. SD WebUI may not have --api flag enabled.")
                logger.error(f"Start SD WebUI with: webui-user.bat (ensure COMMANDLINE_ARGS='--api')")
                return None
            
            response.raise_for_status()
            
            result = response.json()
            if 'images' in result and len(result['images']) > 0:
                img_data = base64.b64decode(result['images'][0])
                return Image.open(io.BytesIO(img_data))
            else:
                logger.error("No image returned from API")
                return None
                
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot connect to SD API at {Config.SD_WEBUI_URL}")
            logger.error("Ensure SD WebUI is running and accessible")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error during inpainting: {e}")
            logger.error(f"Response: {e.response.text if hasattr(e, 'response') else 'N/A'}")
            return None
        except Exception as e:
            logger.error(f"Error during inpainting: {e}")
            return None


# Initialize managers
sd_webui_manager = SDWebUIManager()
eye_inpainter = EyeInpainter(yolo_model_path=Config.YOLO_MODEL_PATH)


# Metadata Manager with Excel synchronization
class MetadataManager:
    """Manages metadata JSON and Excel synchronization."""
    
    def __init__(self):
        self.json_path = Config.METADATA_JSON
        self.excel_path = Config.METADATA_EXCEL
        self._lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._cached_metadata = None
        self._cached_mtime_ns = None
        self._cached_size = None
        self._gallery_index = []
        self._gallery_index_signature = None
        self._warm_status = {
            'state': 'idle',
            'started_at': None,
            'finished_at': None,
            'duration_ms': None,
            'error': None,
        }

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cached_metadata = None
            self._cached_mtime_ns = None
            self._cached_size = None
            self._gallery_index = []
            self._gallery_index_signature = None

    def get_cache_status(self) -> Dict[str, Any]:
        with self._cache_lock:
            return {
                'loaded': self._cached_metadata is not None,
                'mtime_ns': self._cached_mtime_ns,
                'size': self._cached_size,
                'gallery_index_loaded': bool(self._gallery_index),
                'gallery_index_count': len(self._gallery_index),
                'warm_status': dict(self._warm_status),
            }

    def _build_gallery_index(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        posts = data.get('posts', []) or []
        index = []

        for post in posts:
            post_id = post.get('post_id')
            if not post_id:
                continue

            zip_files_raw = post.get('zip_files', []) or []
            profile_images_raw = post.get('profile_images', []) or []
            if not zip_files_raw or not profile_images_raw:
                continue

            zip_files = [{
                'filename': zf.get('filename'),
                'extracted': zf.get('extracted', False),
                'downloaded': zf.get('downloaded', False),
            } for zf in zip_files_raw]
            extracted = any(zf.get('extracted', False) for zf in zip_files)

            cascade_metadata_raw = post.get('cascade_metadata', {}) if isinstance(post.get('cascade_metadata'), dict) else {}
            cascade_images_raw = cascade_metadata_raw.get('images', []) if isinstance(cascade_metadata_raw, dict) else []
            cascade_images = []
            if isinstance(cascade_images_raw, list) and cascade_images_raw:
                first_image = cascade_images_raw[0]
                if isinstance(first_image, dict):
                    cascade_images = [{
                        'filename': first_image.get('filename'),
                        'visible': first_image.get('visible', True),
                        'deleted': first_image.get('deleted', False),
                    }]

            image_count = len(cascade_images_raw) if cascade_images_raw else int(cascade_metadata_raw.get('visible_images') or cascade_metadata_raw.get('total_images') or 0)
            description_raw = post.get('description', '') or ''
            description = description_raw[:280] + '...' if len(description_raw) > 280 else description_raw

            index.append({
                'post_id': post_id,
                'revised_post_name': post.get('revised_post_name'),
                'post_name': post.get('post_name'),
                'post_date': post.get('post_date'),
                'description': description,
                'display': post.get('display', True),
                'favourite': post.get('favourite', False),
                'profile_images': profile_images_raw[:1] if profile_images_raw else [],
                'zip_files': zip_files,
                'extracted': extracted,
                'image_count': image_count,
                'cascade_metadata': {
                    'images': cascade_images,
                    'total_images': cascade_metadata_raw.get('total_images'),
                    'visible_images': cascade_metadata_raw.get('visible_images'),
                },
            })

        return index

    def _ensure_gallery_index(self, data: Optional[Dict[str, Any]] = None, signature=None) -> List[Dict[str, Any]]:
        with self._cache_lock:
            if self._gallery_index_signature == signature and self._gallery_index:
                return list(self._gallery_index)

        if data is None:
            data = self.load_metadata()
            signature = self._get_file_signature()

        started_at = time.perf_counter()
        index = self._build_gallery_index(data)
        duration_ms = (time.perf_counter() - started_at) * 1000

        with self._cache_lock:
            self._gallery_index = index
            self._gallery_index_signature = signature

        logger.info('Gallery index rebuilt in %.1fms with %s posts', duration_ms, len(index))
        return list(index)

    def get_gallery_index(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        data = self.load_metadata(force_reload=force_reload)
        signature = self._get_file_signature()
        if force_reload:
            with self._cache_lock:
                self._gallery_index = []
                self._gallery_index_signature = None
        return self._ensure_gallery_index(data=data, signature=signature)

    def _get_file_signature(self):
        stat = self.json_path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _read_metadata_from_disk(self) -> Dict[str, Any]:
        with open(self.json_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _store_cache(self, data: Dict[str, Any], mtime_ns=None, size=None) -> Dict[str, Any]:
        if mtime_ns is None or size is None:
            mtime_ns, size = self._get_file_signature()
        with self._cache_lock:
            self._cached_metadata = data
            self._cached_mtime_ns = mtime_ns
            self._cached_size = size
        return data

    def load_metadata(self, force_reload: bool = False) -> Dict[str, Any]:
        """Load metadata from JSON file."""
        try:
            mtime_ns, size = self._get_file_signature()

            if not force_reload:
                with self._cache_lock:
                    if (
                        self._cached_metadata is not None and
                        self._cached_mtime_ns == mtime_ns and
                        self._cached_size == size
                    ):
                        return self._cached_metadata

            with self._lock:
                if not force_reload:
                    with self._cache_lock:
                        if (
                            self._cached_metadata is not None and
                            self._cached_mtime_ns == mtime_ns and
                            self._cached_size == size
                        ):
                            return self._cached_metadata

                data = self._read_metadata_from_disk()
                self._store_cache(data, mtime_ns=mtime_ns, size=size)
                self._ensure_gallery_index(data=data, signature=(mtime_ns, size))
                return data
        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
            return {"posts": [], "summary": {}}

    def warm_cache(self, force_reload: bool = False) -> Dict[str, Any]:
        started_at = datetime.utcnow().isoformat() + 'Z'
        started_perf = time.perf_counter()
        with self._cache_lock:
            self._warm_status = {
                'state': 'warming',
                'started_at': started_at,
                'finished_at': None,
                'duration_ms': None,
                'error': None,
            }

        try:
            data = self.load_metadata(force_reload=force_reload)
            self.get_gallery_index(force_reload=force_reload)
            duration_ms = (time.perf_counter() - started_perf) * 1000
            finished_at = datetime.utcnow().isoformat() + 'Z'
            with self._cache_lock:
                self._warm_status = {
                    'state': 'ready',
                    'started_at': started_at,
                    'finished_at': finished_at,
                    'duration_ms': round(duration_ms, 1),
                    'error': None,
                }
            logger.info("Metadata cache warm-up completed in %.1fms", duration_ms)
            return data
        except Exception as e:
            duration_ms = (time.perf_counter() - started_perf) * 1000
            finished_at = datetime.utcnow().isoformat() + 'Z'
            with self._cache_lock:
                self._warm_status = {
                    'state': 'error',
                    'started_at': started_at,
                    'finished_at': finished_at,
                    'duration_ms': round(duration_ms, 1),
                    'error': str(e),
                }
            logger.error(f"Metadata cache warm-up failed: {e}")
            raise

    def save_metadata(self, data: Dict[str, Any]) -> bool:
        """Save metadata to JSON file, then generate Excel using MetadataHandler."""
        with self._lock:
            try:
                # Update timestamp
                if 'summary' in data:
                    data['summary']['last_update'] = datetime.now().isoformat()
                
                # Save JSON with atomic write (source of truth)
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                # Generate Excel using MetadataHandler
                if PANDAS_AVAILABLE:
                    try:
                        import sys
                        sys.path.insert(0, str(Config.BASE_DIR))
                        from shared.metadata_handler import MetadataHandler
                        
                        logger.info("Converting JSON to Excel using MetadataHandler...")
                        handler = MetadataHandler()
                        success = handler.json_to_excel(create_backup=True)
                        
                        if not success:
                            logger.warning("Excel generation failed, but JSON was saved successfully")
                    except Exception as e:
                        logger.warning(f"Failed to generate Excel using MetadataHandler: {e}")
                        logger.info("JSON file was saved successfully. You can manually run metadata_handler.py later.")
                
                logger.info("Metadata saved successfully")
                return True
                
            except Exception as e:
                logger.error(f"Failed to save metadata: {e}")
                return False
    
    def atomic_update_post(self, post_id: str, updates: Dict[str, Any]) -> bool:
        """Atomically update a post - load, modify, save all under lock."""
        with self._lock:
            try:
                # Load metadata inside lock
                data = self.load_metadata()
                
                # Find and update the post
                post_found = False
                for post in data.get('posts', []):
                    if post.get('post_id') == post_id:
                        post.update(updates)
                        post_found = True
                        break
                
                if not post_found:
                    logger.warning(f"Post {post_id} not found for update")
                    return False
                
                # Save inside the same lock (metadata handler will use its own lock too)
                if 'summary' in data:
                    data['summary']['last_update'] = datetime.now().isoformat()
                
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                # Excel generation in background to avoid blocking multiple requests
                if PANDAS_AVAILABLE:
                    threading.Thread(target=self._generate_excel_background, daemon=True).start()
                
                logger.info(f"Post {post_id} updated successfully")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update post {post_id}: {e}")
                return False
    
    def update_post_images(self, post_id: str, images: List[Dict[str, Any]]) -> bool:
        """Update the images array for a post (for reordering/visibility in cascade mode)."""
        with self._lock:
            try:
                # Load metadata inside lock
                data = self.load_metadata()
                
                # Find and update the post
                post_found = False
                for post in data.get('posts', []):
                    if post.get('post_id') == post_id:
                        # Update custom_order for each image
                        for idx, img in enumerate(images):
                            img['custom_order'] = idx
                        post['cascade_metadata']['images'] = images
                        post['cascade_metadata']['total_images'] = len(images)
                        post['cascade_metadata']['visible_images'] = len([
                            img for img in images if img.get('visible', True) and not img.get('deleted', False)
                        ])
                        post_found = True
                        break
                
                if not post_found:
                    logger.warning(f"Post {post_id} not found for images update")
                    return False
                
                # Save inside the same lock
                if 'summary' in data:
                    data['summary']['last_update'] = datetime.now().isoformat()
                
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                logger.info(f"Post {post_id} images updated successfully")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update post images {post_id}: {e}")
                return False
    
    def _generate_excel_background(self):
        """Generate Excel in background - has its own internal lock via MetadataHandler."""
        try:
            import sys
            sys.path.insert(0, str(Config.BASE_DIR))
            from shared.metadata_handler import MetadataHandler
            
            logger.info("Generating Excel in background...")
            handler = MetadataHandler()
            handler.json_to_excel(create_backup=True)
        except Exception as e:
            logger.warning(f"Background Excel generation failed: {e}")
    
    def atomic_mark_extracted(self, post_id: str, html_files: Dict[str, Any]) -> bool:
        """Atomically mark post as extracted with HTML file links."""
        with self._lock:
            try:
                data = self.load_metadata()
                
                for post in data.get('posts', []):
                    if post.get('post_id') == post_id:
                        # Mark all zip files as extracted
                        for zip_info in post.get('zip_files', []):
                            zip_info['extracted'] = True
                            zip_info['extraction_date'] = datetime.now().isoformat()
                        
                        # Add HTML file metadata
                        break
                
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                if PANDAS_AVAILABLE:
                    threading.Thread(target=self._generate_excel_background, daemon=True).start()
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to mark post {post_id} as extracted: {e}")
                return False
    
    def atomic_unmark_extracted(self, post_id: str) -> bool:
        """Atomically unmark post as extracted."""
        with self._lock:
            try:
                data = self.load_metadata()
                
                for post in data.get('posts', []):
                    if post.get('post_id') == post_id:
                        # Mark all zip files as not extracted
                        for zip_info in post.get('zip_files', []):
                            zip_info['extracted'] = False
                            zip_info.pop('extraction_date', None)
                        
                        break
                
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                if PANDAS_AVAILABLE:
                    threading.Thread(target=self._generate_excel_background, daemon=True).start()
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to unmark post {post_id} as extracted: {e}")
                return False
    
    def atomic_delete_post(self, post_id: str) -> bool:
        """Atomically delete post from metadata."""
        with self._lock:
            try:
                data = self.load_metadata()
                data['posts'] = [p for p in data['posts'] if p.get('post_id') != post_id]
                
                safe_save_json(data, self.json_path, create_backup=True)
                self._store_cache(data)
                
                if PANDAS_AVAILABLE:
                    threading.Thread(target=self._generate_excel_background, daemon=True).start()
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to delete post {post_id}: {e}")
                return False
    
    def update_post(self, post_id: str, updates: Dict[str, Any]) -> bool:
        """Update a specific post in metadata."""
        data = self.load_metadata()
        posts = data.get('posts', [])
        
        for post in posts:
            if post.get('post_id') == post_id:
                post.update(updates)
                return self.save_metadata(data)
        
        logger.warning(f"Post {post_id} not found for update")
        return False


# Playlist Manager
class PlaylistManager:
    """Manages playlist operations with atomic safety."""
    
    def __init__(self, metadata_manager: MetadataManager):
        self.metadata_manager = metadata_manager
        self.playlist_metadata_path = Config.PLAYLIST_METADATA_JSON
        self._lock = threading.Lock()
        self._ensure_playlist_metadata()

    @staticmethod
    def _playlist_template_path(name: str) -> Path:
        return Config.WEBAPP_DIR / 'templates' / name

    @staticmethod
    def _image_exists(image: Dict[str, Any]) -> bool:
        post_id = str(image.get('post_id') or '')
        filename = image.get('filename') or ''
        if not post_id or not filename:
            return False
        return (Config.EXTRACTED_DIR / post_id / filename).exists()

    @staticmethod
    def _should_drop_legacy_image(image: Dict[str, Any]) -> bool:
        filename = (image.get('filename') or '').lower()
        return filename.endswith('.png') and '_enhanced' in filename

    def _normalize_playlist(self, playlist: Dict[str, Any], drop_missing: bool = False) -> Dict[str, int]:
        images = playlist.get('images', []) or []
        cleaned = []
        removed_legacy = 0
        removed_missing = 0

        for image in images:
            if self._should_drop_legacy_image(image):
                removed_legacy += 1
                continue
            if drop_missing and not self._image_exists(image):
                removed_missing += 1
                continue
            cleaned.append(image)

        for idx, image in enumerate(cleaned):
            image['custom_order'] = idx

        playlist['images'] = cleaned
        playlist['total_images'] = len(cleaned)
        playlist['cover_image'] = (
            {'post_id': cleaned[0].get('post_id'), 'filename': cleaned[0].get('filename')}
            if cleaned else None
        )
        playlist['modified_date'] = datetime.now().isoformat()
        return {
            'removed_legacy': removed_legacy,
            'removed_missing': removed_missing,
            'total_images': len(cleaned),
        }

    def repair_and_regenerate_all(self, drop_missing: bool = True) -> Dict[str, Any]:
        with self._lock:
            data = self._load_playlist_metadata()
            results = []
            generated = 0
            for playlist in data.get('playlists', []):
                stats = self._normalize_playlist(playlist, drop_missing=drop_missing)
                ok = self._generate_html_files(playlist['playlist_id'], playlist)
                if ok:
                    generated += 1
                results.append({
                    'playlist_id': playlist.get('playlist_id'),
                    'name': playlist.get('name'),
                    'generated': ok,
                    **stats,
                })

            self._save_playlist_metadata(data)
            return {
                'success': True,
                'playlists': results,
                'generated': generated,
                'total': len(results),
            }

    def _ensure_playlist_metadata(self):
        """Ensure playlist metadata file exists with proper structure."""
        if not self.playlist_metadata_path.exists():
            initial_data = {
                'playlists': [],
                'last_updated': datetime.now().isoformat()
            }
            safe_save_json(initial_data, self.playlist_metadata_path, create_backup=False)
            logger.info(f"Created playlist metadata file: {self.playlist_metadata_path}")
    
    def _load_playlist_metadata(self) -> dict:
        """Load playlist metadata from separate file."""
        try:
            if self.playlist_metadata_path.exists():
                with open(self.playlist_metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {'playlists': [], 'last_updated': datetime.now().isoformat()}
        except Exception as e:
            logger.error(f"Failed to load playlist metadata: {e}")
            return {'playlists': [], 'last_updated': datetime.now().isoformat()}
    
    def _save_playlist_metadata(self, data: dict):
        """Save playlist metadata to separate file."""
        data['last_updated'] = datetime.now().isoformat()
        safe_save_json(data, self.playlist_metadata_path, create_backup=True)
    
    def create_playlist(self, name: str, description: str = '') -> Dict[str, Any]:
        """Create a new playlist and generate its HTML files."""
        with self._lock:
            try:
                data = self._load_playlist_metadata()
                
                # Generate unique ID
                playlist_id = f"playlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}"
                
                playlist = {
                    'playlist_id': playlist_id,
                    'name': name,
                    'description': description,
                    'created_date': datetime.now().isoformat(),
                    'modified_date': datetime.now().isoformat(),
                    'cover_image': {},
                    'images': [],
                    'total_images': 0
                }
                
                data['playlists'].append(playlist)
                self._save_playlist_metadata(data)
                
                # Generate HTML files immediately
                self._generate_html_files(playlist_id, playlist)
                
                logger.info(f"Created playlist: {playlist_id} - {name}")
                return {'success': True, 'playlist': playlist}
                
            except Exception as e:
                logger.error(f"Failed to create playlist: {e}")
                return {'success': False, 'error': str(e)}
    
    def add_images(self, playlist_id: str, images: List[Dict[str, str]]) -> Dict[str, Any]:
        """Add images to playlist and regenerate HTML."""
        with self._lock:
            try:
                data = self._load_playlist_metadata()
                playlist = self._find_playlist(data, playlist_id)
                
                if not playlist:
                    return {'success': False, 'error': 'Playlist not found'}
                
                # Deduplicate
                existing = {f"{img['post_id']}:{img['filename']}" for img in playlist.get('images', [])}
                
                for img in images:
                    key = f"{img['post_id']}:{img['filename']}"
                    if key not in existing:
                        post_id = img['post_id']
                        filename = img['filename']
                        
                        # Calculate paths
                        image_filepath = str(Config.EXTRACTED_DIR / post_id / filename)
                        post_filepath = str(Config.POST_PAGES_DIR / f"{post_id}_cascade.html")
                        
                        playlist['images'].append({
                            'post_id': post_id,
                            'filename': filename,
                            'image_filepath': image_filepath,
                            'post_filepath': post_filepath,
                            'custom_order': len(playlist['images']),
                            'added_date': datetime.now().isoformat()
                        })
                        existing.add(key)
                
                playlist['total_images'] = len(playlist['images'])
                playlist['modified_date'] = datetime.now().isoformat()
                
                # Auto-set cover if empty
                if not playlist.get('cover_image') and playlist['images']:
                    first = playlist['images'][0]
                    playlist['cover_image'] = {'post_id': first['post_id'], 'filename': first['filename']}
                
                self._save_playlist_metadata(data)
                self._generate_html_files(playlist_id, playlist)
                
                return {'success': True, 'playlist': playlist}
                
            except Exception as e:
                logger.error(f"Failed to add images to playlist: {e}")
                return {'success': False, 'error': str(e)}
    
    def update_playlist(self, playlist_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update playlist metadata (name, description, or images array for reordering)."""
        with self._lock:
            try:
                data = self._load_playlist_metadata()
                playlist = self._find_playlist(data, playlist_id)
                
                if not playlist:
                    return {'success': False, 'error': 'Playlist not found'}
                
                # Update allowed fields
                if 'name' in updates:
                    playlist['name'] = updates['name'].strip()
                if 'description' in updates:
                    playlist['description'] = updates['description'].strip()
                
                # Handle images array update (for reordering)
                if 'images' in updates:
                    images = updates['images']
                    if isinstance(images, list):
                        # Update custom_order for each image
                        for idx, img in enumerate(images):
                            img['custom_order'] = idx
                        playlist['images'] = images
                        playlist['total_images'] = len(images)
                        
                        # Update cover image if needed
                        if images:
                            playlist['cover_image'] = {
                                'post_id': images[0].get('post_id'),
                                'filename': images[0].get('filename')
                            }
                        else:
                            playlist['cover_image'] = None
                
                playlist['modified_date'] = datetime.now().isoformat()
                
                self._save_playlist_metadata(data)
                self._generate_html_files(playlist_id, playlist)
                
                return {'success': True, 'playlist': playlist}
                
            except Exception as e:
                logger.error(f"Failed to update playlist: {e}")
                return {'success': False, 'error': str(e)}
    
    def remove_image(self, playlist_id: str, filename: str, post_id: str = None) -> Dict[str, Any]:
        """Remove a single image from a playlist."""
        with self._lock:
            try:
                data = self._load_playlist_metadata()
                playlist = self._find_playlist(data, playlist_id)
                
                if not playlist:
                    return {'success': False, 'error': 'Playlist not found'}
                
                # Find and remove the image
                original_count = len(playlist.get('images', []))
                if post_id:
                    # Remove by both filename and post_id for accuracy
                    playlist['images'] = [
                        img for img in playlist.get('images', [])
                        if not (img.get('filename') == filename and img.get('post_id') == post_id)
                    ]
                else:
                    # Remove by filename only
                    playlist['images'] = [
                        img for img in playlist.get('images', [])
                        if img.get('filename') != filename
                    ]
                
                new_count = len(playlist['images'])
                
                if new_count == original_count:
                    return {'success': False, 'error': 'Image not found in playlist'}
                
                # Update custom_order for remaining images
                for idx, img in enumerate(playlist['images']):
                    img['custom_order'] = idx
                
                # Update metadata
                playlist['total_images'] = new_count
                playlist['modified_date'] = datetime.now().isoformat()
                
                # Update cover image if needed
                if playlist['images']:
                    first = playlist['images'][0]
                    playlist['cover_image'] = {
                        'post_id': first.get('post_id'),
                        'filename': first.get('filename')
                    }
                else:
                    playlist['cover_image'] = None
                
                self._save_playlist_metadata(data)
                self._generate_html_files(playlist_id, playlist)
                
                logger.info(f"Removed image {filename} from playlist {playlist_id}")
                return {'success': True, 'playlist': playlist}
                
            except Exception as e:
                logger.error(f"Failed to remove image from playlist: {e}")
                return {'success': False, 'error': str(e)}
    
    def delete_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """Delete playlist and all its generated files."""
        with self._lock:
            try:
                data = self._load_playlist_metadata()
                
                playlist = self._find_playlist(data, playlist_id)
                if not playlist:
                    return {'success': False, 'error': 'Playlist not found'}
                
                # Remove from metadata
                data['playlists'] = [p for p in data['playlists'] if p.get('playlist_id') != playlist_id]
                self._save_playlist_metadata(data)
                
                # Delete all generated files
                files_to_delete = [
                    Config.PLAYLISTS_DIR / f"{playlist_id}.html",
                    Config.PLAYLISTS_DIR / f"{playlist_id}_cascade.html",
                    Config.PLAYLISTS_DIR / f"{playlist_id}_metadata.json"
                ]
                
                for file_path in files_to_delete:
                    if file_path.exists():
                        file_path.unlink()
                        logger.info(f"Deleted: {file_path}")
                
                logger.info(f"Deleted playlist: {playlist_id}")
                return {'success': True}
                
            except Exception as e:
                logger.error(f"Failed to delete playlist: {e}")
                return {'success': False, 'error': str(e)}
    
    def get_all_playlists(self) -> List[Dict[str, Any]]:
        """Get all playlists."""
        try:
            data = self._load_playlist_metadata()
            return data.get('playlists', [])
        except Exception as e:
            logger.error(f"Failed to get playlists: {e}")
            return []
    
    def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Get single playlist."""
        try:
            data = self._load_playlist_metadata()
            return self._find_playlist(data, playlist_id)
        except Exception as e:
            logger.error(f"Failed to get playlist: {e}")
            return None
    
    def _find_playlist(self, data: dict, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Helper to find playlist in metadata."""
        for playlist in data.get('playlists', []):
            if playlist.get('playlist_id') == playlist_id:
                return playlist
        return None
    
    def _generate_html_files(self, playlist_id: str, playlist: Dict[str, Any]) -> bool:
        """Generate all HTML files for a playlist."""
        try:
            self._normalize_playlist(playlist, drop_missing=False)
            # Generate single view
            single_html = self._create_single_view(playlist_id, playlist)
            single_path = Config.PLAYLISTS_DIR / f"{playlist_id}.html"
            with open(single_path, 'w', encoding='utf-8') as f:
                f.write(single_html)
            
            # Generate cascade view
            cascade_html = self._create_cascade_view(playlist_id, playlist)
            cascade_path = Config.PLAYLISTS_DIR / f"{playlist_id}_cascade.html"
            with open(cascade_path, 'w', encoding='utf-8') as f:
                f.write(cascade_html)
            
            # Generate metadata JSON
            metadata_path = Config.PLAYLISTS_DIR / f"{playlist_id}_metadata.json"
            metadata = {
                'playlist_id': playlist_id,
                'name': playlist['name'],
                'description': playlist.get('description', ''),
                'created_date': playlist.get('created_date', ''),
                'modified_date': playlist.get('modified_date', ''),
                'total_images': playlist.get('total_images', 0),
                'images': playlist.get('images', [])
            }
            safe_save_json(metadata, metadata_path)
            
            logger.info(f"Generated HTML files for playlist: {playlist_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to generate HTML for playlist {playlist_id}: {e}")
            return False
    
    def _create_single_view(self, playlist_id: str, playlist: Dict[str, Any]) -> str:
        """Create single view HTML for playlist."""
        template_path = self._playlist_template_path('playlist_template.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            template = f.read()
        
        images = playlist.get('images', [])
        
        # Replace post_id occurrences (including in URLs)
        html = template.replace('{{post_id}}', playlist_id)
        html = html.replace('{{post_name}}', playlist['name'])
        html = html.replace('{{post_date}}', playlist.get('created_date', ''))
        html = html.replace('{{images_json}}', json.dumps(images))
        html = html.replace('{{total_images}}', str(len(images)))
        html = html.replace('{{first_image_name}}', images[0]['filename'] if images else '')
        
        return html
    
    def _create_cascade_view(self, playlist_id: str, playlist: Dict[str, Any]) -> str:
        """Create cascade view HTML for playlist."""
        template_path = self._playlist_template_path('playlist_cascade_template.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            template = f.read()
        
        images = playlist.get('images', [])
        
        # Replace post_id occurrences (including in URLs)
        html = template.replace('{{post_id}}', playlist_id)
        html = html.replace('{{post_name}}', playlist['name'])
        html = html.replace('{{post_date}}', playlist.get('created_date', ''))
        html = html.replace('{{images_json}}', json.dumps(images))
        html = html.replace('{{total_images}}', str(len(images)))
        html = html.replace('{{visible_images}}', str(len(images)))
        
        return html


# File Operations Manager
class FileOperationsManager:
    """Manages file extraction, deletion, and organization."""
    
    @staticmethod
    def _sanitize_filename_component(value: str) -> str:
        safe = "".join(c for c in (value or "") if c.isalnum() or c in (' ', '-', '_', '(', ')', '.')).strip()
        return safe or "post"

    @staticmethod
    def _build_local_zip_filename(post: Dict[str, Any], zip_info: Dict[str, Any]) -> str:
        title = post.get('revised_post_name') or post.get('post_name') or 'post'
        post_id = str(post.get('post_id') or '')
        original_name = zip_info.get('filename') or f'{post_id}.zip'
        safe_title = FileOperationsManager._sanitize_filename_component(title)
        return f"{safe_title}_{post_id}_{original_name}"

    @staticmethod
    def _create_patreon_session() -> requests.Session:
        session = requests.Session()
        cookies_path = COOKIES_FILE
        if not cookies_path.exists():
            raise FileNotFoundError(f"Patreon cookies file not found: {cookies_path}")

        raw = cookies_path.read_text(encoding='utf-8').strip()
        cookies = []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cookies = parsed
        except Exception:
            cookies = []

        if cookies:
            for cookie in cookies:
                name = cookie.get('name')
                value = cookie.get('value')
                if not name or value is None:
                    continue
                session.cookies.set(name=name, value=value, domain=cookie.get('domain', '.patreon.com'))
        else:
            for part in raw.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                name, value = part.split('=', 1)
                session.cookies.set(name=name.strip(), value=value.strip(), domain='.patreon.com')

        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/vnd.api+json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.patreon.com/',
            'Origin': 'https://www.patreon.com'
        })
        return session

    @staticmethod
    def _clear_post_generated_outputs(post_id: str, clear_extracted: bool = False) -> None:
        post_files = [
            Config.POST_PAGES_DIR / f"{post_id}.html",
            Config.POST_PAGES_DIR / f"{post_id}_cascade.html",
            Config.POST_PAGES_DIR / f"{post_id}_metadata.json",
        ]
        for file_path in post_files:
            if file_path.exists():
                file_path.unlink()
        if clear_extracted:
            extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir)

    @staticmethod
    def _download_missing_zip_files(post: Dict[str, Any], progress_callback=None, progress_start: int = 10, progress_end: int = 40) -> tuple[list, bool]:
        zip_files = post.get('zip_files', []) or []
        if not zip_files:
            return zip_files, False

        session = FileOperationsManager._create_patreon_session()
        freshly_downloaded = False
        total = len(zip_files)

        for index, zip_info in enumerate(zip_files, start=1):
            filename = zip_info.get('filename') or f"{post.get('post_id')}_{index}.zip"
            local_filename = zip_info.get('local_filename') or FileOperationsManager._build_local_zip_filename(post, zip_info)
            zip_info['local_filename'] = local_filename
            download_url = zip_info.get('download_url')
            expected_size = zip_info.get('size_bytes')
            destination = Config.DOWNLOADS_DIR / local_filename

            percent = progress_start + int(((index - 1) / max(total, 1)) * max(progress_end - progress_start, 1))
            if progress_callback:
                progress_callback({
                    'step': 'downloading_files',
                    'message': f'Downloading {filename} ({index}/{total})...',
                    'percent': percent,
                })

            if destination.exists() and destination.stat().st_size > 0 and (
                not expected_size or destination.stat().st_size == expected_size
            ):
                zip_info['downloaded'] = True
                continue

            if not download_url:
                raise ValueError(f"Missing download URL for ZIP '{filename}' in post {post.get('post_id')}")

            last_error = None
            temp_destination = destination.with_suffix(destination.suffix + '.part')
            for attempt in range(3):
                try:
                    if temp_destination.exists():
                        temp_destination.unlink()
                    with session.get(download_url, stream=True, timeout=60) as response:
                        response.raise_for_status()
                        with open(temp_destination, 'wb') as handle:
                            for chunk in response.iter_content(chunk_size=1024 * 1024):
                                if chunk:
                                    handle.write(chunk)
                    if expected_size and temp_destination.stat().st_size != expected_size:
                        raise ValueError(
                            f"Downloaded size mismatch for {filename}: got {temp_destination.stat().st_size}, expected {expected_size}"
                        )
                    os.replace(temp_destination, destination)
                    zip_info['downloaded'] = True
                    zip_info['download_date'] = datetime.now().isoformat()
                    zip_info['local_filename'] = local_filename
                    freshly_downloaded = True
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if temp_destination.exists():
                        temp_destination.unlink()
                    time.sleep(2)
            if last_error:
                raise RuntimeError(f"Failed to download {filename}: {last_error}")

        return zip_files, freshly_downloaded
    
    @staticmethod
    def get_post_zip_files(post_id: str) -> List[Path]:
        """Get all zip files for a specific post."""
        zip_files = []
        downloads_dir = Config.DOWNLOADS_DIR
        
        for zip_path in downloads_dir.glob(f"*{post_id}*.zip"):
            zip_files.append(zip_path)
        
        return zip_files
    
    @staticmethod
    def get_post_extracted_dir(post_id: str) -> Path:
        """Get the extraction directory for a post."""
        return Config.EXTRACTED_DIR / post_id
    
    @staticmethod
    def is_post_extracted(post_id: str) -> bool:
        """Check if a post has been extracted using metadata."""
        data = metadata_manager.load_metadata()
        for post in data.get('posts', []):
            if post.get('post_id') == post_id:
                zip_files = post.get('zip_files', [])
                # Check if any zip file is marked as extracted
                return any(zip_file.get('extracted', False) for zip_file in zip_files)
        return False
    
    @staticmethod
    def extract_post_files(post_id: str, progress_callback=None) -> dict:
        """Download missing zip files for a post if needed, then extract them safely with progress tracking and HTML generation."""
        progress = {'step': 'starting', 'message': 'Initializing download...', 'percent': 0}
        
        def update_progress(step, message, percent):
            progress.update({'step': step, 'message': message, 'percent': percent})
            if progress_callback:
                progress_callback(progress.copy())
            logger.info(f"Extraction progress for {post_id}: {step} - {message} ({percent}%)")
        
        try:
            update_progress('loading_metadata', 'Loading post metadata...', 10)
            
            # Load metadata to get zip file information
            data = metadata_manager.load_metadata()
            post = None
            for p in data.get('posts', []):
                if p.get('post_id') == post_id:
                    post = p
                    break
            
            if not post:
                logger.error(f"Post {post_id} not found in metadata")
                return {'success': False, 'error': 'Post not found', 'progress': progress}
            
            zip_files = post.get('zip_files', [])
            if not zip_files:
                logger.warning(f"No zip files defined in metadata for post {post_id}")
                return {'success': False, 'error': 'No zip files defined for this post', 'progress': progress}

            update_progress('downloading_files', 'Checking and downloading ZIP files...', 12)
            zip_files, freshly_downloaded = FileOperationsManager._download_missing_zip_files(
                post,
                progress_callback=progress_callback,
                progress_start=12,
                progress_end=42,
            )
            post['zip_files'] = zip_files

            downloaded_zips = [zf for zf in zip_files if zf.get('downloaded', False)]
            if not downloaded_zips:
                logger.warning(f"No downloaded zip files available for post {post_id}")
                return {'success': False, 'error': 'No downloaded zip files available', 'progress': progress}

            if freshly_downloaded:
                update_progress('resetting_outputs', 'Fresh download detected, resetting generated outputs...', 45)
                FileOperationsManager._clear_post_generated_outputs(post_id, clear_extracted=True)
            else:
                update_progress('resetting_outputs', 'Resetting extracted folder before regeneration...', 45)
                FileOperationsManager._clear_post_generated_outputs(post_id, clear_extracted=True)

            update_progress('extracting_files', 'Extracting ZIP files...', 50)

            extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
            extracted_dir.mkdir(parents=True, exist_ok=True)

            extraction_successful = True
            available_zip_count = len(downloaded_zips)
            for i, zip_info in enumerate(downloaded_zips, start=1):
                zip_filename = zip_info.get('local_filename')
                if not zip_filename:
                    extraction_successful = False
                    continue

                progress_percent = 50 + int((i - 1) / max(available_zip_count, 1) * 20)
                update_progress('extracting_files', f'Extracting {zip_filename}...', progress_percent)

                zip_path = Config.DOWNLOADS_DIR / zip_filename
                if not zip_path.exists():
                    logger.warning(f"Zip file not found: {zip_path}")
                    extraction_successful = False
                    continue

                try:
                    logger.info(f"Extracting {zip_path} to {extracted_dir}")
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(extracted_dir)
                    zip_info['extracted'] = True
                    zip_info['extraction_date'] = datetime.now().isoformat()
                except Exception as e:
                    logger.error(f"Failed to extract {zip_path}: {e}")
                    extraction_successful = False

            if not extraction_successful:
                return {'success': False, 'error': 'Failed to extract some files', 'progress': progress}

            update_progress('updating_metadata', 'Regenerating cascade metadata...', 72)
            FileOperationsManager._generate_cascade_metadata(post_id, post, data)

            update_progress('creating_web_pages', 'Generating post HTML from templates...', 82)
            html_result = FileOperationsManager._generate_post_html(post_id, post)
            if not html_result['success']:
                return {'success': False, 'error': f'Failed to create web pages: {html_result["error"]}', 'progress': progress}

            update_progress('checking_completion', 'Verifying download + extraction completion...', 92)
            verification = FileOperationsManager._verify_extraction_completion(post_id)
            if not verification['success']:
                return {'success': False, 'error': f'Extraction verification failed: {verification["error"]}', 'progress': progress}

            update_progress('saving_metadata', 'Saving regenerated metadata...', 96)
            html_files = {
                'single_view': f'/posts/{post_id}.html',
                'cascade_view': f'/posts/{post_id}_cascade.html',
                'metadata_file': f'/posts/{post_id}_metadata.json',
                'created_date': datetime.now().isoformat()
            }

            if metadata_manager.atomic_update_post(post_id, {
                'zip_files': zip_files,
                'cascade_metadata': post.get('cascade_metadata', {}),
            }):
                update_progress('completed', 'Download and regeneration completed successfully!', 100)
                logger.info(f"Successfully downloaded, extracted, and regenerated files for post {post_id}")
                return {'success': True, 'progress': progress}
            else:
                logger.error(f"Failed to update metadata after extraction for post {post_id}")
                return {'success': False, 'error': 'Failed to save final metadata', 'progress': progress}
            
        except Exception as e:
            logger.error(f"Failed to extract files for post {post_id}: {e}")
            return {'success': False, 'error': str(e), 'progress': progress}
    
    @staticmethod
    def delete_extracted_files(post_id: str) -> bool:
        """Delete extracted/generated files and downloaded ZIP files for a post, then update metadata."""
        try:
            data = metadata_manager.load_metadata()
            post = None
            for p in data.get('posts', []):
                if p.get('post_id') == post_id:
                    post = p
                    break

            # Delete extracted directory
            extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir)
                logger.info(f"Deleted extracted directory for post {post_id}")
            
            # Delete generated HTML and JSON files in posts directory
            post_files = [
                Config.POST_PAGES_DIR / f"{post_id}.html",
                Config.POST_PAGES_DIR / f"{post_id}_cascade.html",
                Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
            ]
            
            for file_path in post_files:
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"Deleted post file: {file_path.name}")

            zip_paths_to_delete = []
            seen_zip_paths = set()

            if post:
                for zip_info in post.get('zip_files', []) or []:
                    candidates = []
                    local_filename = zip_info.get('local_filename')
                    if local_filename:
                        candidates.append(Config.DOWNLOADS_DIR / local_filename)

                    local_path = zip_info.get('local_path')
                    if local_path:
                        candidates.append(Path(local_path))

                    for candidate in candidates:
                        try:
                            resolved = candidate.resolve(strict=False)
                        except Exception:
                            resolved = candidate
                        key = str(resolved)
                        if key in seen_zip_paths:
                            continue
                        seen_zip_paths.add(key)
                        zip_paths_to_delete.append(candidate)

            for discovered_zip in FileOperationsManager.get_post_zip_files(post_id):
                try:
                    resolved = discovered_zip.resolve(strict=False)
                except Exception:
                    resolved = discovered_zip
                key = str(resolved)
                if key in seen_zip_paths:
                    continue
                seen_zip_paths.add(key)
                zip_paths_to_delete.append(discovered_zip)

            for zip_path in zip_paths_to_delete:
                if zip_path.exists() and zip_path.is_file():
                    zip_path.unlink()
                    logger.info(f"Deleted downloaded ZIP for post {post_id}: {zip_path.name}")
            
            metadata_updates = None
            if post:
                updated_zip_files = []
                for zip_info in post.get('zip_files', []) or []:
                    updated_zip = dict(zip_info)
                    updated_zip['extracted'] = False
                    updated_zip['downloaded'] = False
                    for field in ['local_filename', 'local_path', 'download_date', 'downloaded_at', 'extraction_date']:
                        if field in updated_zip:
                            updated_zip[field] = None
                    updated_zip_files.append(updated_zip)
                metadata_updates = {'zip_files': updated_zip_files}

            if metadata_updates and metadata_manager.atomic_update_post(post_id, metadata_updates):
                logger.info(f"Updated metadata after deleting extracted/generated files and ZIPs for post {post_id}")
                return True
            elif metadata_updates is None and metadata_manager.atomic_unmark_extracted(post_id):
                logger.info(f"Updated metadata after deleting extracted files for post {post_id}")
                return True
            else:
                logger.error(f"Failed to update metadata after deleting extracted files for post {post_id}")
                return False
            
        except Exception as e:
            logger.error(f"Failed to delete extracted files for post {post_id}: {e}")
            return False
    
    @staticmethod
    def get_post_images(post_id: str, include_hidden: bool = False) -> List[Dict[str, Any]]:
        """Get list of images for a post using cascade metadata for filtering and ordering."""
        extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
        if not extracted_dir.exists():
            return []
        
        # Load metadata to get cascade information
        try:
            data = metadata_manager.load_metadata()
            post = None
            for p in data.get('posts', []):
                if p.get('post_id') == post_id:
                    post = p
                    break
            
            if post and post.get('cascade_metadata'):
                # Use cascade metadata for filtering and ordering
                cascade_meta = post['cascade_metadata']
                cascade_images = cascade_meta.get('images', [])
                
                # Filter based on visibility and deletion status
                filtered_images = []
                for img_meta in cascade_images:
                    # Check if file still exists
                    img_path = extracted_dir / img_meta['filename']
                    if not img_path.exists():
                        continue
                    
                    # Apply visibility filters
                    if img_meta.get('deleted', False):
                        continue  # Always skip deleted images
                    
                    if not include_hidden and not img_meta.get('visible', True):
                        continue  # Skip hidden images unless requested
                    
                    # Build image info
                    file_info = img_meta.get('file_info', {})
                    image_info = {
                        'filename': img_meta['filename'],
                        'path': file_info.get('path', img_meta['filename']),
                        'size': file_info.get('size', img_path.stat().st_size if img_path.exists() else 0),
                        'modified': file_info.get('modified', datetime.fromtimestamp(img_path.stat().st_mtime).isoformat() if img_path.exists() else ''),
                        'visible': img_meta.get('visible', True),
                        'custom_order': img_meta.get('custom_order', 0)
                    }
                    filtered_images.append(image_info)
                
                # Sort according to cascade metadata
                sort_mode = cascade_meta.get('sort_mode', 'filename')
                if sort_mode == 'custom':
                    filtered_images.sort(key=lambda x: x.get('custom_order', 0))
                elif sort_mode == 'filename':
                    filtered_images.sort(key=lambda x: x['filename'])
                elif sort_mode == 'modified':
                    filtered_images.sort(key=lambda x: x['modified'], reverse=True)
                elif sort_mode == 'size':
                    filtered_images.sort(key=lambda x: x['size'], reverse=True)
                
                return filtered_images
            
            else:
                # Fallback: generate images list from filesystem (no cascade metadata)
                images = []
                for img_path in extracted_dir.rglob('*'):
                    if is_internal_edit_artifact_path(img_path):
                        continue
                    if img_path.is_file() and img_path.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS:
                        images.append({
                            'filename': img_path.name,
                            'path': str(img_path.relative_to(extracted_dir)),
                            'size': img_path.stat().st_size,
                            'modified': datetime.fromtimestamp(img_path.stat().st_mtime).isoformat(),
                            'visible': True,
                            'custom_order': 0
                        })
                
                # Sort by filename (default)
                images.sort(key=lambda x: x['filename'])
                return images
        
        except Exception as e:
            logger.error(f"Error getting images for post {post_id}: {e}")
            return []
    
    @staticmethod
    def _generate_cascade_metadata(post_id: str, post: Dict[str, Any], metadata: Dict[str, Any]):
        """Generate cascade metadata for extracted images."""
        try:
            extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
            if not extracted_dir.exists():
                return
            
            # Get current images from filesystem
            current_images_by_base = {}
            for img_path in extracted_dir.rglob('*'):
                if is_internal_edit_artifact_path(img_path):
                    continue
                if img_path.is_file() and img_path.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS:
                    file_info = {
                        'filename': img_path.name,
                        'path': str(img_path.relative_to(extracted_dir)),
                        'size': img_path.stat().st_size,
                        'modified': datetime.fromtimestamp(img_path.stat().st_mtime).isoformat()
                    }
                    base_filename = canonical_base_filename(img_path.name)
                    current_images_by_base.setdefault(base_filename, []).append(file_info)
            
            # Get existing cascade metadata or create new
            existing_cascade = post.get('cascade_metadata', {})
            existing_images = {}
            for img in existing_cascade.get('images', []):
                if not isinstance(img, dict) or not img.get('filename'):
                    continue
                ensure_image_alternate_metadata(img)
                existing_images[img.get('base_image_filename') or canonical_base_filename(img['filename'])] = img
            
            # Generate new cascade metadata
            cascade_images = []
            for i, base_filename in enumerate(sorted(current_images_by_base.keys())):
                group_files = sorted(current_images_by_base[base_filename], key=lambda x: x['filename'])
                files_by_name = {item['filename']: item for item in group_files}
                existing = existing_images.get(base_filename)

                if existing:
                    active_filename = existing.get('active_alternate_filename') or existing.get('filename') or base_filename
                    if active_filename not in files_by_name:
                        active_filename = base_filename if base_filename in files_by_name else group_files[0]['filename']

                    existing_alt_meta = {
                        alt.get('filename'): alt
                        for alt in existing.get('alternate_versions', []) or []
                        if isinstance(alt, dict) and alt.get('filename')
                    }
                    alternate_versions = []
                    ordered_filenames = sorted(files_by_name.keys(), key=lambda name: (0 if name == base_filename else 1, name))
                    for alt_filename in ordered_filenames:
                        prev = existing_alt_meta.get(alt_filename, {})
                        alternate_versions.append({
                            'filename': alt_filename,
                            'kind': prev.get('kind', 'original' if alt_filename == base_filename else 'enhanced'),
                            'created_at': prev.get('created_at') or files_by_name[alt_filename]['modified'],
                            'source_run_id': prev.get('source_run_id'),
                            'prompt_text': prev.get('prompt_text', ''),
                            'active': alt_filename == active_filename,
                        })

                    cascade_images.append({
                        'filename': active_filename,
                        'visible': existing.get('visible', True),
                        'deleted': existing.get('deleted', False),
                        'custom_order': existing.get('custom_order', i),
                        'file_info': files_by_name[active_filename],
                        'enhanced': active_filename != base_filename,
                        'enhancement_date': existing.get('enhancement_date'),
                        'original_filename': base_filename,
                        'base_image_filename': base_filename,
                        'active_alternate_filename': active_filename,
                        'enhanced_filename': active_filename if active_filename != base_filename else None,
                        'enhancement_config': existing.get('enhancement_config'),
                        'alternate_versions': alternate_versions,
                    })
                else:
                    active_filename = base_filename if base_filename in files_by_name else group_files[0]['filename']
                    alternate_versions = []
                    ordered_filenames = sorted(files_by_name.keys(), key=lambda name: (0 if name == base_filename else 1, name))
                    for alt_filename in ordered_filenames:
                        alternate_versions.append({
                            'filename': alt_filename,
                            'kind': 'original' if alt_filename == base_filename else 'enhanced',
                            'created_at': files_by_name[alt_filename]['modified'],
                            'source_run_id': None,
                            'prompt_text': '',
                            'active': alt_filename == active_filename,
                        })

                    cascade_images.append({
                        'filename': active_filename,
                        'visible': True,
                        'deleted': False,
                        'custom_order': i,
                        'file_info': files_by_name[active_filename],
                        'enhanced': active_filename != base_filename,
                        'enhancement_date': None,
                        'original_filename': base_filename,
                        'base_image_filename': base_filename,
                        'active_alternate_filename': active_filename,
                        'enhanced_filename': active_filename if active_filename != base_filename else None,
                        'enhancement_config': {},
                        'alternate_versions': alternate_versions,
                    })
            
            # Update post's cascade metadata
            post['cascade_metadata'] = {
                'last_updated': datetime.now().isoformat(),
                'sort_mode': existing_cascade.get('sort_mode', 'filename'),
                'total_images': len(cascade_images),
                'visible_images': len([img for img in cascade_images if img.get('visible', True) and not img.get('deleted', False)]),
                'images': cascade_images
            }
            
            logger.info(f"Generated cascade metadata for post {post_id}: {len(cascade_images)} images")
            
        except Exception as e:
            logger.error(f"Failed to generate cascade metadata for post {post_id}: {e}")
    
    @staticmethod
    def _generate_post_html(post_id: str, post: Dict[str, Any]) -> dict:
        """Generate HTML files for single and cascade views, and metadata JSON."""
        try:
            # Create templates if they don't exist
            FileOperationsManager._create_html_templates()
            
            # Generate single view HTML
            single_html = FileOperationsManager._create_single_view_html(post_id, post)
            single_path = Config.POST_PAGES_DIR / f"{post_id}.html"
            with open(single_path, 'w', encoding='utf-8') as f:
                f.write(single_html)
            
            # Generate cascade view HTML
            cascade_html = FileOperationsManager._create_cascade_view_html(post_id, post)
            cascade_path = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
            with open(cascade_path, 'w', encoding='utf-8') as f:
                f.write(cascade_html)
            
            # Generate metadata JSON file
            FileOperationsManager._create_post_metadata_file(post_id, post)
            
            logger.info(f"Generated HTML files and metadata for post {post_id}")
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Failed to generate HTML files for post {post_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _generate_post_html_only(post_id: str, post: Dict[str, Any]) -> dict:
        """Generate ONLY HTML files without touching metadata (for auto-regeneration when HTML is missing)."""
        try:
            # Create templates if they don't exist
            FileOperationsManager._create_html_templates()
            
            # Load existing metadata from the per-post JSON file if it exists
            metadata_path = Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
            if metadata_path.exists():
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    post_metadata = json.load(f)
                # Use the metadata from file instead of the passed post object
                # This preserves enhancement metadata and other edits
                post = post_metadata
            
            # Generate single view HTML
            single_html = FileOperationsManager._create_single_view_html(post_id, post)
            single_path = Config.POST_PAGES_DIR / f"{post_id}.html"
            with open(single_path, 'w', encoding='utf-8') as f:
                f.write(single_html)
            
            # Generate cascade view HTML
            cascade_html = FileOperationsManager._create_cascade_view_html(post_id, post)
            cascade_path = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
            with open(cascade_path, 'w', encoding='utf-8') as f:
                f.write(cascade_html)
            
            # DO NOT regenerate metadata file - preserve existing metadata
            logger.info(f"Generated HTML files only for post {post_id} (metadata preserved)")
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Failed to generate HTML files for post {post_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _create_html_templates():
        """Create HTML templates if they don't exist."""
        try:
            templates_dir = Config.TEMPLATES_DIR
            templates_dir.mkdir(parents=True, exist_ok=True)
            
            # Only create if the template files don't already exist
            single_template = templates_dir / 'post_template.html'
            cascade_template = templates_dir / 'cascade_template.html'
            
            if single_template.exists() and cascade_template.exists():
                return True
                
            logger.info("HTML templates already exist, skipping creation")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create HTML templates: {e}")
            return False
    
    @staticmethod
    def _create_single_view_html(post_id: str, post: Dict[str, Any]) -> str:
        """Create HTML for single image view."""
        try:
            template_path = Config.TEMPLATES_DIR / 'post_template.html'
            if not template_path.exists():
                raise FileNotFoundError(f"Single view template not found: {template_path}")
            
            with open(template_path, 'r', encoding='utf-8') as f:
                template = f.read()
            
            cascade_metadata = post.get('cascade_metadata', {})
            all_images = cascade_metadata.get('images', [])
            
            # Filter to only visible, non-deleted images for single view navigation
            images = [img for img in all_images if img.get('visible', True) and not img.get('deleted', False)]
            
            # Replace template variables - escape strings for safe JavaScript embedding
            safe_post_name = str(post.get('revised_post_name') or post.get('post_name') or 'Unknown Post')
            safe_post_date = str(post.get('post_date') or '')
            first_image_name = str(images[0].get('filename') or 'No images') if images else 'No images'
            html = template.replace('{{post_id}}', str(post_id))
            html = html.replace('{{post_name}}', json.dumps(safe_post_name)[1:-1])  # Remove quotes from json.dumps
            html = html.replace('{{post_date}}', safe_post_date)
            html = html.replace('{{images_json}}', json.dumps(images))
            html = html.replace('{{total_images}}', str(len(images)))
            html = html.replace('{{first_image_name}}', json.dumps(first_image_name)[1:-1])
            
            return html
            
        except Exception as e:
            logger.error(f"Failed to create single view HTML for {post_id}: {e}")
            return f"<html><body><h1>Error loading post {post_id}</h1><p>{e}</p></body></html>"
    
    @staticmethod
    def _create_cascade_view_html(post_id: str, post: Dict[str, Any]) -> str:
        """Create HTML for cascade view."""
        try:
            template_path = Config.TEMPLATES_DIR / 'cascade_template.html'
            if not template_path.exists():
                raise FileNotFoundError(f"Cascade view template not found: {template_path}")

            with open(template_path, 'r', encoding='utf-8') as f:
                template = f.read()

            cascade_metadata = post.get('cascade_metadata', {})
            images = cascade_metadata.get('images', [])
            sort_mode = cascade_metadata.get('sort_mode', 'filename')
            
            # Sort images based on sort mode and apply visibility filters
            if sort_mode == 'custom':
                # Sort by custom_order for user-defined ordering
                images = sorted(images, key=lambda x: x.get('custom_order', 999))
            else:
                # Sort by filename (default)
                images = sorted(images, key=lambda x: x.get('filename', ''))
            
            # Calculate visible images (not deleted and visible=True)
            visible_images = len([img for img in images if img.get('visible', True) and not img.get('deleted', False)])

            # Replace template variables - escape strings for safe JavaScript embedding
            safe_post_name = str(post.get('revised_post_name') or post.get('post_name') or 'Unknown Post')
            safe_post_date = str(post.get('post_date') or '')
            html = template.replace('{{post_id}}', str(post_id))
            html = html.replace('{{post_name}}', json.dumps(safe_post_name)[1:-1])  # Remove quotes from json.dumps
            html = html.replace('{{post_date}}', safe_post_date)
            html = html.replace('{{images_json}}', json.dumps(images))
            html = html.replace('{{total_images}}', str(len(images)))
            html = html.replace('{{visible_images}}', str(visible_images))

            return html
            
        except Exception as e:
            logger.error(f"Failed to create cascade view HTML for {post_id}: {e}")
            return f"<html><body><h1>Error loading cascade for post {post_id}</h1><p>{e}</p></body></html>"
    
    @staticmethod
    def _create_post_metadata_file(post_id: str, post: Dict[str, Any]) -> dict:
        """Create post-specific metadata JSON file."""
        try:
            cascade_metadata = post.get('cascade_metadata', {})
            
            post_metadata = {
                'post_id': post_id,
                'post_name': str(post.get('revised_post_name') or post.get('post_name') or ''),
                'post_date': str(post.get('post_date') or ''),
                'description': str(post.get('description') or ''),
                'extraction_date': datetime.now().isoformat(),
                'cascade_metadata': cascade_metadata,
            }
            
            metadata_path = Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(post_metadata, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Created post metadata file for {post_id}")
            return {'success': True}
            
        except Exception as e:
            logger.error(f"Failed to create post metadata file for {post_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _verify_extraction_completion(post_id: str) -> dict:
        """Verify that all extraction components are properly created."""
        try:
            checks = {
                'extracted_dir': Config.EXTRACTED_DIR / post_id,
                'single_html': Config.POST_PAGES_DIR / f"{post_id}.html",
                'cascade_html': Config.POST_PAGES_DIR / f"{post_id}_cascade.html",
                'metadata_json': Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
            }
            
            missing = []
            for check_name, path in checks.items():
                if not path.exists():
                    missing.append(check_name)
            
            if missing:
                return {'success': False, 'error': f'Missing components: {", ".join(missing)}'}
            
            # Verify at least one image exists in extracted directory
            extracted_dir = checks['extracted_dir']
            image_files = [f for f in extracted_dir.rglob('*') 
                         if not is_internal_edit_artifact_path(f) and f.is_file() and f.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS]
            
            if not image_files:
                return {'success': False, 'error': 'No image files found in extracted directory'}
            
            logger.info(f"Extraction verification passed for {post_id}")
            return {'success': True, 'components': list(checks.keys()), 'image_count': len(image_files)}
            
        except Exception as e:
            logger.error(f"Failed to verify extraction completion for {post_id}: {e}")
            return {'success': False, 'error': str(e)}


class ExtractionQueueManager:
    """FIFO download/extraction queue with configurable parallel workers."""

    def __init__(self, file_ops_manager: FileOperationsManager, max_concurrent: int = 4, status_ttl_seconds: int = 300):
        self.file_ops = file_ops_manager
        self.max_concurrent = max(1, int(max_concurrent or 1))
        self.status_ttl_seconds = max(60, int(status_ttl_seconds or 300))

        self._queue = deque()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._workers: List[threading.Thread] = []
        self._status_logger_thread: Optional[threading.Thread] = None

    def start(self):
        """Start worker threads once."""
        with self._lock:
            if self._workers:
                return

            for i in range(self.max_concurrent):
                worker = threading.Thread(target=self._worker_loop, args=(i + 1,), daemon=True)
                worker.start()
                self._workers.append(worker)

            self._status_logger_thread = threading.Thread(target=self._status_logger_loop, daemon=True)
            self._status_logger_thread.start()

            logger.info(f"ExtractionQueueManager started with max_concurrent={self.max_concurrent}")

    def _status_logger_loop(self):
        """Print queue status periodically for validation/debugging."""
        while not self._stop_event.is_set():
            with self._lock:
                self._cleanup_finished_locked()
                queued_post_ids = list(self._queue)
                running_post_ids = [
                    post_id for post_id, job in self._jobs.items()
                    if job.get('status') == 'running'
                ]

            logger.info(
                "[ExtractionQueue] running=%s queued=%s max=%s running_posts=%s queued_posts=%s",
                len(running_post_ids),
                len(queued_post_ids),
                self.max_concurrent,
                running_post_ids,
                queued_post_ids
            )
            time.sleep(4)

    def _cleanup_finished_locked(self):
        now = time.time()
        expired_post_ids = []

        for post_id, job in self._jobs.items():
            status = job.get('status')
            completed_at = job.get('completed_at_ts')
            if status in ('completed', 'failed') and completed_at and (now - completed_at) > self.status_ttl_seconds:
                expired_post_ids.append(post_id)

        for post_id in expired_post_ids:
            del self._jobs[post_id]

    def _build_response(self, post_id: str, job: Dict[str, Any], queue_position: int = None) -> Dict[str, Any]:
        status = job.get('status', 'unknown')
        progress = job.get('progress')
        in_progress = status in ('queued', 'running')
        completed = status in ('completed', 'failed')

        response = {
            'post_id': post_id,
            'status': status,
            'in_progress': in_progress,
            'completed': completed,
            'started': job.get('started', False),
            'progress': progress,
            'queue_position': queue_position,
            'max_concurrent': self.max_concurrent,
            'created_at': job.get('created_at'),
            'updated_at': job.get('updated_at')
        }

        if completed:
            response['extraction_success'] = status == 'completed'
            if status == 'failed':
                response['error'] = job.get('error')

        return response

    def enqueue(self, post_id: str) -> Dict[str, Any]:
        """Queue download request. De-duplicates queued/running job per post."""
        with self._lock:
            self._cleanup_finished_locked()

            existing = self._jobs.get(post_id)
            if existing and existing.get('status') in ('queued', 'running'):
                queue_position = None
                if existing.get('status') == 'queued':
                    try:
                        queue_position = list(self._queue).index(post_id) + 1
                    except ValueError:
                        queue_position = None
                response = self._build_response(post_id, existing, queue_position=queue_position)
                response['already_queued'] = True
                return response

            now_iso = datetime.now().isoformat()
            job = {
                'status': 'queued',
                'started': False,
                'error': None,
                'created_at': now_iso,
                'updated_at': now_iso,
                'created_at_ts': time.time(),
                'completed_at_ts': None,
                'progress': {
                    'step': 'queued',
                    'message': 'Queued for download...',
                    'percent': 0
                }
            }

            self._jobs[post_id] = job
            self._queue.append(post_id)

            queue_position = len(self._queue)
            response = self._build_response(post_id, job, queue_position=queue_position)
            response['already_queued'] = False
            return response

    def get_job_status(self, post_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._cleanup_finished_locked()

            job = self._jobs.get(post_id)
            if not job:
                return None

            queue_position = None
            if job.get('status') == 'queued':
                try:
                    queue_position = list(self._queue).index(post_id) + 1
                except ValueError:
                    queue_position = None

            return self._build_response(post_id, job, queue_position=queue_position)

    def get_active_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._cleanup_finished_locked()

            queued_positions = {pid: idx + 1 for idx, pid in enumerate(self._queue)}
            active_jobs = []
            for post_id, job in self._jobs.items():
                if job.get('status') in ('queued', 'running'):
                    active_jobs.append(
                        self._build_response(
                            post_id,
                            job,
                            queue_position=queued_positions.get(post_id)
                        )
                    )

            active_jobs.sort(
                key=lambda j: (
                    0 if j.get('status') == 'running' else 1,
                    j.get('queue_position') if j.get('queue_position') is not None else 999999,
                    j.get('created_at') or ''
                )
            )
            return active_jobs

    def _worker_loop(self, worker_id: int):
        logger.info(f"Extraction worker {worker_id} started")

        while not self._stop_event.is_set():
            post_id = None

            with self._lock:
                if self._queue:
                    post_id = self._queue.popleft()
                    job = self._jobs.get(post_id)
                    if job:
                        job['status'] = 'running'
                        job['started'] = True
                        job['updated_at'] = datetime.now().isoformat()
                        job['progress'] = {
                            'step': 'starting',
                            'message': 'Starting download...',
                            'percent': 1
                        }

            if not post_id:
                time.sleep(0.1)
                continue

            def progress_callback(progress_data):
                with self._lock:
                    job = self._jobs.get(post_id)
                    if not job:
                        return

                    job['status'] = 'running'
                    job['started'] = True
                    job['updated_at'] = datetime.now().isoformat()
                    job['progress'] = progress_data.copy()

            try:
                logger.info(f"Worker {worker_id} extracting post {post_id}")
                result = self.file_ops.extract_post_files(post_id, progress_callback=progress_callback)

                with self._lock:
                    job = self._jobs.get(post_id)
                    if not job:
                        continue

                    job['updated_at'] = datetime.now().isoformat()
                    job['completed_at_ts'] = time.time()

                    if result.get('success'):
                        job['status'] = 'completed'
                        job['error'] = None
                        job['progress'] = {
                            'step': 'completed',
                            'message': 'Extraction completed successfully!',
                            'percent': 100
                        }
                    else:
                        error_message = result.get('error', 'Extraction failed')
                        job['status'] = 'failed'
                        job['error'] = error_message
                        job['progress'] = {
                            'step': 'failed',
                            'message': error_message,
                            'percent': 0,
                            'error': error_message
                        }

            except Exception as e:
                logger.error(f"Worker {worker_id} failed on post {post_id}: {e}")
                with self._lock:
                    job = self._jobs.get(post_id)
                    if job:
                        job['updated_at'] = datetime.now().isoformat()
                        job['completed_at_ts'] = time.time()
                        job['status'] = 'failed'
                        job['error'] = str(e)
                        job['progress'] = {
                            'step': 'failed',
                            'message': str(e),
                            'percent': 0,
                            'error': str(e)
                        }

# Image Processing Manager
class ImageManager:
    """Handles image serving and thumbnail generation."""
    
    def __init__(self):
        self._profile_preview_lock = threading.Lock()
        self._profile_backfill_started = False
        self._profile_preview_jobs = set()

    @staticmethod
    def _profile_preview_path_for_source(image_path: Path) -> Path:
        return Config.PROFILE_PREVIEWS_DIR / f"{image_path.stem}_{Config.PROFILE_PREVIEW_SIZE[0]}x{Config.PROFILE_PREVIEW_SIZE[1]}.webp"

    @staticmethod
    def _normalise_profile_preview_source(image_path: Path):
        if not PIL_AVAILABLE:
            return None

        with Image.open(image_path) as img:
            try:
                img.seek(0)
            except Exception:
                pass

            frame = img.copy()
            frame = ImageOps.exif_transpose(frame)
            if frame.mode not in ('RGB', 'RGBA'):
                frame = frame.convert('RGBA' if 'A' in frame.getbands() else 'RGB')
            if frame.mode == 'RGBA':
                background = Image.new('RGB', frame.size, (255, 255, 255))
                background.paste(frame, mask=frame.getchannel('A'))
                frame = background
            elif frame.mode != 'RGB':
                frame = frame.convert('RGB')
            return frame

    @classmethod
    def generate_profile_preview(cls, image_path: Path) -> Optional[Path]:
        if not PIL_AVAILABLE or not image_path.exists():
            return None

        preview_path = cls._profile_preview_path_for_source(image_path)
        try:
            Config.PROFILE_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

            if preview_path.exists() and preview_path.stat().st_mtime >= image_path.stat().st_mtime:
                return preview_path

            frame = cls._normalise_profile_preview_source(image_path)
            if frame is None:
                return None

            target_w, target_h = Config.PROFILE_PREVIEW_SIZE
            fitted = ImageOps.fit(frame, (target_w, target_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.2))
            fitted.save(
                preview_path,
                format=Config.PROFILE_PREVIEW_FORMAT,
                quality=Config.PROFILE_PREVIEW_QUALITY,
                method=6,
                optimize=True,
            )
            return preview_path
        except Exception as e:
            logger.warning(f"Failed to generate profile preview for {image_path.name}: {e}")
            return None

    def get_profile_preview_path(self, post_id: str) -> Optional[Path]:
        image_path = self.get_profile_image_path(post_id)
        if not image_path or not image_path.exists():
            return None

        with self._profile_preview_lock:
            preview_path = self.generate_profile_preview(image_path)
        return preview_path if preview_path and preview_path.exists() else None

    def get_existing_profile_preview_path(self, post_id: str) -> Optional[Path]:
        image_path = self.get_profile_image_path(post_id)
        if not image_path or not image_path.exists():
            return None
        preview_path = self._profile_preview_path_for_source(image_path)
        if preview_path.exists() and preview_path.stat().st_mtime >= image_path.stat().st_mtime:
            return preview_path
        return None

    def ensure_profile_preview_background(self, post_id: str) -> bool:
        image_path = self.get_profile_image_path(post_id)
        if not image_path or not image_path.exists():
            return False

        preview_path = self._profile_preview_path_for_source(image_path)
        if preview_path.exists() and preview_path.stat().st_mtime >= image_path.stat().st_mtime:
            return False

        with self._profile_preview_lock:
            if post_id in self._profile_preview_jobs:
                return False
            self._profile_preview_jobs.add(post_id)

        def _run():
            try:
                self.generate_profile_preview(image_path)
            except Exception as e:
                logger.warning(f"Background profile preview generation failed for {post_id}: {e}")
            finally:
                with self._profile_preview_lock:
                    self._profile_preview_jobs.discard(post_id)

        threading.Thread(target=_run, name=f'profile-preview-{post_id}', daemon=True).start()
        return True

    def backfill_profile_previews(self, limit: Optional[int] = None) -> Dict[str, Any]:
        generated = 0
        skipped = 0
        errors = 0
        Config.PROFILE_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

        files = sorted(
            [p for p in Config.PROFILE_IMAGES_DIR.iterdir() if p.is_file() and p.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for idx, image_path in enumerate(files):
            if limit is not None and idx >= limit:
                break

            preview_path = self._profile_preview_path_for_source(image_path)
            try:
                if preview_path.exists() and preview_path.stat().st_mtime >= image_path.stat().st_mtime:
                    skipped += 1
                    continue
                with self._profile_preview_lock:
                    result = self.generate_profile_preview(image_path)
                if result and result.exists():
                    generated += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

        return {
            'generated': generated,
            'skipped': skipped,
            'errors': errors,
            'scanned': min(len(files), limit) if limit is not None else len(files),
        }

    def start_profile_preview_backfill(self, limit: Optional[int] = None) -> None:
        with self._profile_preview_lock:
            if self._profile_backfill_started:
                return
            self._profile_backfill_started = True

        def _run():
            started = time.perf_counter()
            try:
                stats = self.backfill_profile_previews(limit=limit)
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.info(
                    "Profile preview backfill completed in %.1fms (generated=%s, skipped=%s, errors=%s, scanned=%s)",
                    elapsed_ms,
                    stats['generated'],
                    stats['skipped'],
                    stats['errors'],
                    stats['scanned'],
                )
            except Exception as e:
                logger.warning(f"Profile preview backfill failed: {e}")

        threading.Thread(target=_run, name='profile-preview-backfill', daemon=True).start()
    
    @staticmethod
    def get_profile_image_path(post_id: str) -> Optional[Path]:
        """Get the profile image path for a post."""
        try:
            data = metadata_manager.load_metadata()
            for post in data.get('posts', []):
                if str(post.get('post_id')) != str(post_id):
                    continue
                for image_info in post.get('profile_images', []) or []:
                    filename = image_info.get('filename')
                    if filename:
                        candidate = Config.PROFILE_IMAGES_DIR / filename
                        if candidate.exists():
                            return candidate
                break
        except Exception:
            pass

        # Check common profile image patterns
        patterns = [
            f"{post_id}_main.png",
            f"{post_id}_main.jpg", 
            f"{post_id}_main.jpeg",
            f"{post_id}.png",
            f"{post_id}.jpg"
        ]
        
        for pattern in patterns:
            path = Config.PROFILE_IMAGES_DIR / pattern
            if path.exists():
                return path
        
        return None
    
    @staticmethod
    def get_content_image_path(post_id: str, filename: str) -> Optional[Path]:
        """Get the path to a content image."""
        extracted_dir = FileOperationsManager.get_post_extracted_dir(post_id)
        if not extracted_dir.exists():
            return None
        
        # Search for the file in the extracted directory
        for img_path in extracted_dir.rglob(filename):
            if img_path.is_file():
                return img_path
        
        return None
    
    @staticmethod
    def generate_thumbnail(image_path: Path, size: tuple = None) -> Optional[Path]:
        """Generate a thumbnail for an image."""
        if not PIL_AVAILABLE:
            return None
            
        if size is None:
            size = Config.MAX_THUMBNAIL_SIZE
        
        try:
            # Create thumbnail path
            thumb_name = f"{image_path.stem}_{size[0]}x{size[1]}{image_path.suffix}"
            thumb_path = Config.THUMBNAILS_DIR / thumb_name
            
            # Check if thumbnail already exists and is newer than source
            if thumb_path.exists() and thumb_path.stat().st_mtime > image_path.stat().st_mtime:
                return thumb_path
            
            # Generate thumbnail
            with Image.open(image_path) as img:
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'LA', 'P'):
                    img = img.convert('RGB')
                
                # Create thumbnail
                img.thumbnail(size, Image.Resampling.LANCZOS)
                
                # Save thumbnail
                img.save(thumb_path, optimize=True, quality=85)
                
            logger.debug(f"Generated thumbnail: {thumb_path}")
            return thumb_path
            
        except Exception as e:
            logger.error(f"Failed to generate thumbnail for {image_path}: {e}")
            return None

# Initialize managers
metadata_manager = MetadataManager()
playlist_manager = PlaylistManager(metadata_manager)
file_ops = FileOperationsManager()
image_manager = ImageManager()
extraction_queue_manager = ExtractionQueueManager(
    file_ops,
    max_concurrent=Config.MAX_CONCURRENT_EXTRACTIONS,
    status_ttl_seconds=Config.EXTRACTION_STATUS_TTL_SECONDS
)

def _should_start_queue_manager() -> bool:
    """Start queue manager only in the serving process (avoid Flask debug reloader parent)."""
    if os.environ.get('VAMATION_DISABLE_BACKGROUND_INIT') == '1':
        return False
    if not Config.DEBUG:
        return True
    return os.environ.get('WERKZEUG_RUN_MAIN') == 'true'

if _should_start_queue_manager():
    extraction_queue_manager.start()
else:
    logger.info("Skipping extraction queue manager startup in reloader parent process")

def _should_warm_metadata_cache() -> bool:
    if os.environ.get('VAMATION_DISABLE_BACKGROUND_INIT') == '1':
        return False
    if not Config.DEBUG:
        return True
    return os.environ.get('WERKZEUG_RUN_MAIN') == 'true'

def _start_metadata_cache_warmup() -> None:
    if not _should_warm_metadata_cache():
        logger.info("Skipping metadata cache warm-up in reloader parent process")
        return

    def _warm():
        try:
            metadata_manager.warm_cache(force_reload=False)
        except Exception:
            pass

    threading.Thread(target=_warm, name='metadata-cache-warmup', daemon=True).start()

_start_metadata_cache_warmup()

def _start_profile_preview_backfill() -> None:
    if os.environ.get('VAMATION_DISABLE_BACKGROUND_INIT') == '1':
        return
    if Config.DEBUG and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return
    image_manager.start_profile_preview_backfill()

_start_profile_preview_backfill()

# API Routes
    
# API Routes

@app.route('/api/update/status', methods=['GET'])
def get_update_status():
    return jsonify(background_update_manager.get_status())

@app.route('/api/update/trigger', methods=['POST'])
def trigger_update():
    payload = request.get_json(silent=True) or {}
    reason = payload.get('reason') or 'app-load'
    result = background_update_manager.maybe_trigger_update(reason)
    return jsonify(result)

@app.route('/api/status')
def api_status():
    """Get API status and metadata summary."""
    data = metadata_manager.load_metadata()
    posts = data.get('posts', [])
    cache_status = metadata_manager.get_cache_status()
    
    return jsonify({
        "status": "healthy",
        "posts_count": len(posts),
        "last_update": data.get('summary', {}).get('last_update'),
        "extracted_count": sum(1 for p in posts if file_ops.is_post_extracted(p.get('post_id', ''))),
        "server_info": {
            "host": Config.HOST,
            "port": Config.PORT,
            "debug": Config.DEBUG
        },
        "features": {
            "pandas_available": PANDAS_AVAILABLE,
            "cors_available": CORS_AVAILABLE,
            "pil_available": PIL_AVAILABLE
        },
        "metadata_cache": {
            "loaded": cache_status.get('loaded', False),
            "size_bytes": cache_status.get('size'),
            "warm_status": cache_status.get('warm_status', {}),
        }
    })

@app.route('/api/posts')
@app.route('/api/metadata/posts')
def get_posts():
    """Get all posts with filtering, sorting, and pagination."""
    request_started_at = time.perf_counter()
    index_started_at = time.perf_counter()
    posts = metadata_manager.get_gallery_index()
    index_prep_ms = (time.perf_counter() - index_started_at) * 1000
    data = metadata_manager.load_metadata()
    
    # Apply visibility filter
    show_hidden = request.args.get('show_hidden', 'false').lower() == 'true'
    if not show_hidden:
        posts = [p for p in posts if p.get('display', True)]
    
    # Filter: only show posts with ZIP files and profile images
    posts = [p for p in posts if (
        p.get('zip_files') and len(p.get('zip_files', [])) > 0 and
        p.get('profile_images') and len(p.get('profile_images', [])) > 0
    )]
    
    # Apply status filter (favourited, extracted, not-extracted)
    filter_by = request.args.get('filter', '').strip()
    if filter_by == 'favourited':
        posts = [p for p in posts if p.get('favourite') == True]
    elif filter_by == 'extracted':
        posts = [p for p in posts if any(zip_file.get('extracted', False) for zip_file in p.get('zip_files', []))]
    elif filter_by == 'not-extracted':
        posts = [p for p in posts if (
            p.get('zip_files') and len(p.get('zip_files', [])) > 0 and
            not any(zip_file.get('extracted', False) for zip_file in p.get('zip_files', []))
        )]
    
    # Apply search filter
    search = request.args.get('search', '').strip()
    if search:
        search_lower = search.lower()
        posts = [p for p in posts if (
            search_lower in p.get('revised_post_name', '').lower() or
            search_lower in p.get('post_name', '').lower() or
            search_lower in p.get('description', '').lower() or
            search_lower in p.get('post_id', '')
        )]
    
    # Apply sorting
    sort_by = request.args.get('sort_by', 'post_date')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Sort posts with None-safe sorting
    reverse = (sort_order == 'desc')
    if sort_by == 'post_date':
        # Handle None values by treating them as empty string (sorts to end/beginning)
        posts.sort(key=lambda x: x.get('post_date') or '', reverse=reverse)
    elif sort_by == 'revised_post_name':
        # Handle None values for revised_post_name
        posts.sort(key=lambda x: (x.get('revised_post_name') or '').lower(), reverse=reverse)
    
    # Store total before pagination
    total = len(posts)
    
    # Apply pagination
    def _parse_int_arg(name, default):
        raw = request.args.get(name)
        if raw is None or raw == '':
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    page = max(_parse_int_arg('page', 1), 1)
    per_page_param = request.args.get('per_page')
    if per_page_param is None:
        per_page_param = request.args.get('limit')

    try:
        per_page_param = int(per_page_param) if per_page_param not in (None, '') else 50
    except (TypeError, ValueError):
        per_page_param = 50
    
    # Handle special case: -1 means return all posts (for backwards compatibility)
    if per_page_param == -1:
        per_page = total
    else:
        per_page = min(max(per_page_param, 1), 200)
    
    start = (page - 1) * per_page
    end = start + per_page
    posts_page = posts[start:end]
    
    slim_posts = [dict(post) for post in posts_page]
    
    total_request_ms = (time.perf_counter() - request_started_at) * 1000
    logger.info(
        "GET /api/posts completed in %.1fms (index %.1fms, total_posts=%s, page_posts=%s, page=%s, per_page=%s, search=%s, filter=%s, sort=%s:%s)",
        total_request_ms,
        index_prep_ms,
        len(data.get('posts', [])),
        len(slim_posts),
        page,
        per_page,
        bool(search),
        filter_by or 'all',
        sort_by,
        sort_order,
    )

    return jsonify({
        "posts": slim_posts,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 0
        },
        "summary": data.get('summary', {})
    })

@app.route('/api/posts/<post_id>')
@app.route('/api/metadata/posts/<post_id>')
def get_post(post_id):
    """Get single post metadata."""
    data = metadata_manager.load_metadata()
    
    # Find post
    post = None
    for p in data.get('posts', []):
        if p.get('post_id') == post_id:
            post = p.copy()
            break
    
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    # Add extraction status and image info
    zip_files = post.get('zip_files', [])
    post['extracted'] = any(zip_file.get('extracted', False) for zip_file in zip_files)
    if post['extracted']:
        post['images'] = file_ops.get_post_images(post_id)
    
    return jsonify(post)

@app.route('/api/posts/<post_id>', methods=['PUT'])
@app.route('/api/metadata/posts/<post_id>', methods=['PUT'])
def update_post(post_id):
    """Update post metadata."""
    update_data = request.get_json()
    if not update_data:
        return jsonify({"error": "No update data provided"}), 400
    
    # Handle images array update (for reordering/visibility in cascade mode)
    if 'images' in update_data:
        images = update_data['images']
        if isinstance(images, list):
            # Update the images array in metadata
            if metadata_manager.update_post_images(post_id, images):
                return jsonify({"success": True, "message": "Post images updated successfully"})
            else:
                return jsonify({"success": False, "error": "Failed to update post images"}), 500
    
    # Validate and filter allowed fields for other metadata
    allowed_fields = ['revised_post_name', 'display', 'description', 'favourite']
    filtered_updates = {}
    
    for field in allowed_fields:
        if field in update_data:
            if field == 'revised_post_name' and not update_data[field].strip():
                return jsonify({"error": "Post name cannot be empty"}), 400
            filtered_updates[field] = update_data[field]
    
    if not filtered_updates:
        return jsonify({"error": "No valid fields to update"}), 400
    
    # Use atomic update method to prevent race conditions
    if metadata_manager.atomic_update_post(post_id, filtered_updates):
        return jsonify({"message": "Post updated successfully"})
    else:
        return jsonify({"error": "Failed to update post"}), 500

# Playlist API Routes
@app.route('/api/playlists', methods=['GET'])
def get_playlists():
    """Get all playlists."""
    playlists = playlist_manager.get_all_playlists()
    return jsonify({"playlists": playlists})

@app.route('/api/playlists', methods=['POST'])
def create_playlist():
    """Create a new playlist."""
    data = request.get_json()
    if not data or 'name' not in data:
        return jsonify({"error": "Playlist name is required"}), 400
    
    name = data['name'].strip()
    description = data.get('description', '').strip()
    
    if not name:
        return jsonify({"error": "Playlist name cannot be empty"}), 400
    
    result = playlist_manager.create_playlist(name, description)
    if result.get('success'):
        return jsonify({
            "success": True,
            "message": "Playlist created successfully",
            "playlist": result.get('playlist')
        }), 201
    else:
        return jsonify({"error": result.get('error', 'Failed to create playlist')}), 500

@app.route('/api/playlists/<playlist_id>', methods=['GET'])
def get_playlist(playlist_id):
    """Get a specific playlist."""
    playlist = playlist_manager.get_playlist(playlist_id)
    if playlist:
        return jsonify({"playlist": playlist})
    else:
        return jsonify({"error": "Playlist not found"}), 404

@app.route('/api/playlists/<playlist_id>', methods=['PUT'])
def update_playlist_route(playlist_id):
    """Update playlist metadata (name, description, or images array for reordering)."""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No update data provided"}), 400
    
    result = playlist_manager.update_playlist(playlist_id, data)
    if result.get('success'):
        return jsonify({
            "success": True,
            "message": "Playlist updated successfully",
            "playlist": result.get('playlist')
        })
    else:
        return jsonify({"success": False, "error": result.get('error', 'Failed to update playlist')}), 500

@app.route('/api/playlists/<playlist_id>', methods=['DELETE'])
def delete_playlist(playlist_id):
    """Delete a playlist and all its files."""
    result = playlist_manager.delete_playlist(playlist_id)
    if result.get('success'):
        return jsonify({"success": True, "message": "Playlist deleted successfully"})
    else:
        status = 404 if result.get('error') == 'Playlist not found' else 500
        return jsonify({"error": result.get('error', 'Failed to delete playlist')}), status

@app.route('/api/playlists/<playlist_id>/images', methods=['POST'])
def add_images_to_playlist(playlist_id):
    """Add images to a playlist."""
    data = request.get_json()
    if not data or 'images' not in data:
        return jsonify({"success": False, "error": "Images list is required"}), 400
    
    images = data['images']
    if not isinstance(images, list) or not images:
        return jsonify({"success": False, "error": "Images must be a non-empty list"}), 400
    
    result = playlist_manager.add_images(playlist_id, images)
    if result.get('success'):
        return jsonify({"success": True, "message": "Images added successfully", "playlist": result.get('playlist')})
    else:
        return jsonify({"success": False, "error": result.get('error', 'Failed to add images')}), 500

@app.route('/api/playlists/<playlist_id>/images/<filename>', methods=['DELETE'])
def delete_image_from_playlist(playlist_id, filename):
    """Remove a single image from a playlist."""
    data = request.get_json()
    post_id = data.get('post_id') if data else None
    
    result = playlist_manager.remove_image(playlist_id, filename, post_id)
    if result.get('success'):
        return jsonify({"success": True, "message": "Image removed from playlist", "playlist": result.get('playlist')})
    else:
        status = 404 if result.get('error') in {'Playlist not found', 'Image not found in playlist'} else 500
        return jsonify({"success": False, "error": result.get('error', 'Failed to remove image')}), status

## OLD SYNCHRONOUS ROUTE - DISABLED IN FAVOR OF THREADED VERSION BELOW
## (See line ~1462 for the new async implementation with progress tracking)
# @app.route('/api/posts/<post_id>/extract', methods=['POST'])
# def extract_post(post_id):
#     ... (commented out)

@app.route('/api/posts/<post_id>/extracted', methods=['DELETE'])
def delete_extracted(post_id):
    """Delete extracted files for a post."""
    if file_ops.delete_extracted_files(post_id):
        return jsonify({"message": "Extracted files deleted successfully"})
    else:
        return jsonify({"error": "Failed to delete extracted files"}), 500

@app.route('/api/posts/<post_id>/all', methods=['DELETE'])
def delete_all_post_files(post_id):
    """Delete all files for a post."""
    # Delete all files
    if not file_ops.delete_all_post_files(post_id):
        return jsonify({"error": "Failed to delete files"}), 500
    
    # Remove from metadata atomically
    if metadata_manager.atomic_delete_post(post_id):
        return jsonify({"message": "All files and metadata deleted successfully"})
    else:
        return jsonify({"error": "Files deleted but failed to update metadata"}), 500

@app.route('/api/posts/<post_id>/images')
def get_post_images(post_id):
    """Get list of images for a post."""
    if not file_ops.is_post_extracted(post_id):
        return jsonify({"error": "Post not extracted"}), 404
    
    images = file_ops.get_post_images(post_id)
    return jsonify({"images": images})

@app.route('/api/images/profile/<post_id>')
def serve_profile_image(post_id):
    """Serve the original profile image for a post."""
    image_path = image_manager.get_profile_image_path(post_id)
    if not image_path or not image_path.exists():
        abort(404)
    
    response = send_file(image_path, conditional=True, max_age=3600)
    response.headers['Cache-Control'] = 'public, max-age=3600, stale-while-revalidate=86400'
    return response

@app.route('/api/images/profile-preview/<post_id>')
def serve_profile_preview(post_id):
    """Serve gallery preview image for a post, falling back to the original while generating in background."""
    image_path = image_manager.get_profile_image_path(post_id)
    if not image_path or not image_path.exists():
        abort(404)

    preview_path = image_manager.get_existing_profile_preview_path(post_id)
    if preview_path and preview_path.exists():
        response = send_file(preview_path, conditional=True, max_age=86400)
        response.headers['Cache-Control'] = 'public, max-age=86400, stale-while-revalidate=604800'
        return response

    image_manager.ensure_profile_preview_background(post_id)
    response = send_file(image_path, conditional=True, max_age=300)
    response.headers['Cache-Control'] = 'public, max-age=300, stale-while-revalidate=3600'
    response.headers['X-Vamation-Preview-Status'] = 'fallback-original'
    return response

@app.route('/api/images/content/<post_id>/<path:filename>')
def serve_content_image(post_id, filename):
    """Serve content image from extracted files."""
    image_path = image_manager.get_content_image_path(post_id, filename)
    if not image_path or not image_path.exists():
        abort(404)
    
    return send_file(image_path)

@app.route('/api/images/thumbnail/<post_id>/<path:filename>')
def serve_thumbnail(post_id, filename):
    """Serve thumbnail of content image."""
    image_path = image_manager.get_content_image_path(post_id, filename)
    if not image_path or not image_path.exists():
        abort(404)
    
    # Try to generate/serve thumbnail
    thumb_path = image_manager.generate_thumbnail(image_path)
    if thumb_path and thumb_path.exists():
        return send_file(thumb_path)
    
    # Fallback to original image
    return send_file(image_path)

@app.route('/api/cascade/<post_id>/images')
def get_cascade_images(post_id):
    """Get images for cascade view with pagination."""
    # Check if post is extracted using metadata
    data = metadata_manager.load_metadata()
    post = None
    for p in data.get('posts', []):
        if p.get('post_id') == post_id:
            post = p
            break
    
    if not post:
        return jsonify({"error": "Post not found"}), 404
    
    # Check extraction status from metadata
    zip_files = post.get('zip_files', [])
    is_extracted = any(zip_file.get('extracted', False) for zip_file in zip_files)
    
    if not is_extracted:
        return jsonify({"error": "Post not extracted"}), 404
    
    # Get include_hidden parameter for edit mode
    include_hidden = request.args.get('include_hidden', 'false').lower() == 'true'
    
    images = file_ops.get_post_images(post_id, include_hidden=include_hidden)
    
    # Pagination
    page = int(request.args.get('page', 1))
    limit = min(int(request.args.get('limit', 100)), 500)
    
    total = len(images)
    start = (page - 1) * limit
    end = start + limit
    images_page = images[start:end]
    
    return jsonify({
        "success": True,
        "data": {
            "images": images_page,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit
            },
            "cascade_metadata": post.get('cascade_metadata', {})
        }
    })

@app.route('/api/cascade/<post_id>/metadata', methods=['GET'])
def get_cascade_metadata(post_id):
    """Get cascade metadata for a post."""
    data = metadata_manager.load_metadata()
    
    for post in data.get('posts', []):
        if post.get('post_id') == post_id:
            cascade_meta = post.get('cascade_metadata', {
                'sort_mode': 'filename',
                'total_images': 0,
                'visible_images': 0,
                'images': []
            })
            return jsonify({"success": True, "data": cascade_meta})
    
    return jsonify({"error": "Post not found"}), 404

@app.route('/api/cascade/<post_id>/order', methods=['PUT'])
def update_cascade_order(post_id):
    """Update image order for cascade view."""
    update_data = request.get_json()
    if not update_data or 'order' not in update_data:
        return jsonify({"error": "No order data provided"}), 400
    
    try:
        data = metadata_manager.load_metadata()
        
        # Find post
        for post in data.get('posts', []):
            if post.get('post_id') == post_id:
                if 'cascade_metadata' not in post:
                    return jsonify({"error": "No cascade metadata found"}), 404
                
                # Update with comprehensive image data from frontend
                new_images = update_data['order']
                cascade_meta = post['cascade_metadata']
                
                # Update the images array with new order and visibility status
                updated_images = []
                for idx, new_img_data in enumerate(new_images):
                    # Find existing image data or create new entry
                    existing_img = None
                    for img in cascade_meta.get('images', []):
                        if img.get('filename') == new_img_data.get('filename'):
                            existing_img = img
                            break
                    
                    if existing_img:
                        # Update existing image with new data
                        existing_img['custom_order'] = idx
                        existing_img['visible'] = new_img_data.get('visible', True)
                        existing_img['deleted'] = new_img_data.get('deleted', False)
                        updated_images.append(existing_img)
                    else:
                        # Create new image entry (shouldn't happen normally)
                        updated_images.append({
                            'filename': new_img_data.get('filename'),
                            'custom_order': idx,
                            'visible': new_img_data.get('visible', True),
                            'deleted': new_img_data.get('deleted', False)
                        })
                
                # Update cascade metadata
                cascade_meta['images'] = updated_images
                cascade_meta['sort_mode'] = 'custom'
                cascade_meta['last_updated'] = datetime.now().isoformat()
                cascade_meta['total_images'] = len(updated_images)
                cascade_meta['visible_images'] = len([img for img in updated_images if img.get('visible', True) and not img.get('deleted', False)])
                
                # Save metadata
                if metadata_manager.save_metadata(data):
                    logger.info(f"Updated cascade order for post {post_id} with {len(updated_images)} images")
                    return jsonify({"message": "Cascade order updated successfully"})
                else:
                    return jsonify({"error": "Failed to save metadata"}), 500
        
        return jsonify({"error": "Post not found"}), 404
        
    except Exception as e:
        logger.error(f"Error updating cascade order for post {post_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/posts/<post_id>/delete_cascade', methods=['POST'])
def delete_cascade_html(post_id):
    """Delete cascade HTML file to force regeneration."""
    try:
        cascade_file = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
        if cascade_file.exists():
            cascade_file.unlink()
            logger.info(f"Deleted cascade HTML file for post {post_id}")
        return jsonify({"message": "Cascade file deleted successfully"})
    except Exception as e:
        logger.error(f"Error deleting cascade file for post {post_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/posts/<post_id>/delete_single', methods=['POST'])
def delete_single_html(post_id):
    """Delete single view HTML file to force regeneration."""
    try:
        single_file = Config.POST_PAGES_DIR / f"{post_id}.html"
        if single_file.exists():
            single_file.unlink()
            logger.info(f"Deleted single view HTML file for post {post_id}")
        return jsonify({"message": "Single view file deleted successfully"})
    except Exception as e:
        logger.error(f"Error deleting single view file for post {post_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cascade/<post_id>/images/<filename>', methods=['PUT'])
def update_image_metadata(post_id, filename):
    """Update individual image metadata (visibility, etc.)."""
    update_data = request.get_json()
    if not update_data:
        return jsonify({"error": "No update data provided"}), 400
    
    try:
        data = metadata_manager.load_metadata()
        
        # Find post
        for post in data.get('posts', []):
            if post.get('post_id') == post_id:
                if 'cascade_metadata' not in post:
                    return jsonify({"error": "No cascade metadata found"}), 404
                
                cascade_meta = post['cascade_metadata']
                
                # Find and update specific image
                for img in cascade_meta.get('images', []):
                    if img['filename'] == filename:
                        # Update allowed fields
                        allowed_fields = ['visible', 'deleted']
                        for field in allowed_fields:
                            if field in update_data:
                                img[field] = update_data[field]
                        
                        cascade_meta['last_updated'] = datetime.now().isoformat()
                        
                        # Update summary counts
                        cascade_meta['visible_images'] = len([
                            img for img in cascade_meta['images']
                            if img.get('visible', True) and not img.get('deleted', False)
                        ])
                        
                        # Save metadata
                        if metadata_manager.save_metadata(data):
                            return jsonify({"success": True, "message": "Image metadata updated"})
                        else:
                            return jsonify({"error": "Failed to save metadata"}), 500
                
                return jsonify({"error": "Image not found"}), 404
        
        return jsonify({"error": "Post not found"}), 404
        
    except Exception as e:
        logger.error(f"Error updating image metadata for {post_id}/{filename}: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/cascade/<post_id>/sort', methods=['PUT'])
def update_cascade_sort(post_id):
    """Update sort mode for cascade view."""
    update_data = request.get_json()
    if not update_data or 'sort_mode' not in update_data:
        return jsonify({"error": "No sort mode provided"}), 400
    
    sort_mode = update_data['sort_mode']
    allowed_modes = ['filename', 'modified', 'size', 'custom']
    
    if sort_mode not in allowed_modes:
        return jsonify({"error": f"Invalid sort mode. Allowed: {allowed_modes}"}), 400
    
    try:
        data = metadata_manager.load_metadata()
        
        # Find post
        for post in data.get('posts', []):
            if post.get('post_id') == post_id:
                if 'cascade_metadata' not in post:
                    return jsonify({"error": "No cascade metadata found"}), 404
                
                cascade_meta = post['cascade_metadata']
                cascade_meta['sort_mode'] = sort_mode
                cascade_meta['last_updated'] = datetime.now().isoformat()
                
                # Save metadata
                if metadata_manager.save_metadata(data):
                    return jsonify({"success": True, "message": "Sort mode updated"})
                else:
                    return jsonify({"error": "Failed to save metadata"}), 500
        
        return jsonify({"error": "Post not found"}), 404
        
    except Exception as e:
        logger.error(f"Error updating sort mode for post {post_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ============================================================================
# ENHANCEMENT API ENDPOINTS
# ============================================================================

def _enhancement_disabled_response():
    return jsonify({
        "success": False,
        "error": "Image enhancement is disabled on this Zo deployment.",
        "code": "ENHANCEMENT_DISABLED"
    }), 403

@app.route('/api/enhance/status', methods=['GET'])
def check_enhancement_status():
    """Check if enhancement is available."""
    token_available = get_zo_access_token() is not None
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return jsonify({
            "success": True,
            "data": {
                "available": False,
                "message": "Image enhancement is disabled on this Zo deployment.",
                "provider": "zo-nano-banana",
                "dependencies": {
                    "requests": REQUESTS_AVAILABLE,
                    "pil": PIL_AVAILABLE,
                    "zo_token": token_available,
                }
            }
        })

    try:
        status = {
            "available": REQUESTS_AVAILABLE and PIL_AVAILABLE and token_available,
            "message": "" if token_available else "Zo image editing is not configured on this host.",
            "provider": "zo-nano-banana",
            "dependencies": {
                "requests": REQUESTS_AVAILABLE,
                "pil": PIL_AVAILABLE,
                "zo_token": token_available,
            }
        }
        return jsonify({"success": True, "data": status})
    except Exception as e:
        logger.error(f"Error checking enhancement status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/start-sd-webui', methods=['POST'])
def start_sd_webui():
    return jsonify({
        "success": False,
        "error": "SD WebUI is obsolete in this version. Enhancement now runs through Zo image editing.",
        "code": "OBSOLETE_ENDPOINT"
    }), 410

@app.route('/api/enhance/<post_id>/<filename>/detect', methods=['POST'])
def detect_eyes_in_image(post_id, filename):
    return jsonify({
        "success": False,
        "error": "Automatic eye detection is obsolete. This enhancer requires an explicit manual selection.",
        "code": "OBSOLETE_ENDPOINT"
    }), 410

@app.route('/api/enhance/<post_id>/<filename>', methods=['POST'])
def enhance_image(post_id, filename):
    """Start an asynchronous enhancement job."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()

    try:
        config_data = request.get_json() or {}
        job_id = enhancement_job_manager.start_job(post_id, filename, config_data)
        return jsonify({
            "success": True,
            "data": {
                "job_id": job_id,
                "status": "queued",
            }
        }), 202
    except Exception as e:
        logger.error(f"Error starting enhancement for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/jobs/<job_id>', methods=['GET'])
def get_enhancement_job(job_id):
    """Return the current status of an enhancement job."""
    job = enhancement_job_manager.get_job(job_id)
    if not job:
        return jsonify({"error": "Enhancement job not found"}), 404

    payload = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job.get("result") is not None:
        payload["result"] = job["result"]
    if job.get("error"):
        payload["error"] = job["error"]

    return jsonify({"success": True, "data": payload})


@app.route('/api/enhance/<post_id>/focus-assets', methods=['GET'])
def list_focus_assets(post_id):
    """List archived focus assets for a post."""
    try:
        _, post_metadata = load_post_metadata_file(post_id)
        focus_archive = ensure_focus_archive(post_metadata)
        items = []
        for item in focus_archive.get('items', []):
            asset_id = item.get('asset_id')
            if not asset_id:
                continue
            enriched = dict(item)
            enriched['image_url'] = f"/api/enhance/{post_id}/focus-assets/{asset_id}/image"
            items.append(enriched)

        return jsonify({
            "success": True,
            "data": {
                "items": items,
                "count": len(items),
                "last_updated": focus_archive.get('last_updated'),
            }
        })
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except Exception as e:
        logger.error(f"Error listing focus assets for {post_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/<post_id>/presets', methods=['GET'])
def list_enhancement_presets(post_id):
    """List saved enhancement presets for a post."""
    try:
        _, post_metadata = load_post_metadata_file(post_id)
        payload = build_enhancement_preset_payload(post_id, post_metadata)
        return jsonify({"success": True, "data": payload})
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except Exception as e:
        logger.error(f"Error listing enhancement presets for {post_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/<post_id>/presets', methods=['POST'])
def save_enhancement_preset(post_id):
    """Save a named enhancement preset for a post."""
    try:
        data_json = request.get_json() or {}
        name = (data_json.get('name') or '').strip()
        prompt_text = (data_json.get('prompt_text') or '').strip()
        reference_asset_ids = data_json.get('reference_asset_ids') or []

        if not name:
            return jsonify({"error": "Preset name is required"}), 400
        if not prompt_text:
            return jsonify({"error": "Preset prompt is required"}), 400

        metadata_path, post_metadata = load_post_metadata_file(post_id)
        if reference_asset_ids:
            resolve_focus_reference_items(post_id, post_metadata, reference_asset_ids)

        presets = ensure_enhancement_presets(post_metadata)
        items = [item for item in presets.get('items', []) if isinstance(item, dict)]
        preset_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        item = {
            'preset_id': preset_id,
            'name': name,
            'prompt_text': prompt_text,
            'reference_asset_ids': list(dict.fromkeys(reference_asset_ids)),
            'created_at': now,
            'last_used_at': None,
        }
        items.insert(0, item)
        presets['items'] = items
        presets['last_updated'] = now

        safe_save_json(post_metadata, metadata_path)
        payload = build_enhancement_preset_payload(post_id, post_metadata)

        logger.info(f"Saved enhancement preset for {post_id}: {name}")
        return jsonify({
            "success": True,
            "message": "Enhancement preset saved",
            "data": {
                "preset": next((entry for entry in payload['items'] if entry['preset_id'] == preset_id), None),
                "presets": payload,
            }
        })
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving enhancement preset for {post_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/<post_id>/focus-assets/<asset_id>/image', methods=['GET'])
def serve_focus_asset(post_id, asset_id):
    """Serve an archived focus asset image."""
    try:
        _, post_metadata = load_post_metadata_file(post_id)
        focus_archive = ensure_focus_archive(post_metadata)
        item = next((entry for entry in focus_archive.get('items', []) if entry.get('asset_id') == asset_id), None)
        if not item:
            abort(404)

        asset_path = get_focus_archive_dir(post_id) / item.get('filename', '')
        if not asset_path.exists():
            abort(404)

        return send_file(asset_path)
    except FileNotFoundError:
        abort(404)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving focus asset {asset_id} for {post_id}: {e}")
        abort(500)


@app.route('/api/enhance/<post_id>/<filename>/save-crop', methods=['POST'])
def save_focus_crop(post_id, filename):
    """Save the currently selected crop into the post-scoped focus archive."""
    try:
        data_json = request.get_json() or {}
        bbox = data_json.get('custom_mask')
        prompt_text = data_json.get('prompt', '')
        reference_asset_ids = data_json.get('reference_asset_ids') or []

        if not bbox:
            return jsonify({"error": "No selection provided"}), 400

        source_image_path = FileOperationsManager.get_post_extracted_dir(post_id) / filename
        if not source_image_path.exists():
            return jsonify({"error": "Image not found"}), 404

        metadata_path, post_metadata = load_post_metadata_file(post_id)
        item = append_focus_archive_item(
            post_id=post_id,
            post_metadata=post_metadata,
            source_image_path=source_image_path,
            source_image_filename=filename,
            asset_type='raw_crop',
            bbox=bbox,
            prompt_text=prompt_text,
            reference_asset_ids=reference_asset_ids,
        )
        safe_save_json(post_metadata, metadata_path)

        logger.info(f"Saved raw focus crop for {post_id}/{filename} as {item['filename']}")
        return jsonify({
            "success": True,
            "message": "Focus crop saved",
            "data": {
                "item": {
                    **item,
                    "image_url": f"/api/enhance/{post_id}/focus-assets/{item['asset_id']}/image",
                }
            }
        })
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except Exception as e:
        logger.error(f"Error saving focus crop for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/<post_id>/<filename>/alternates', methods=['GET'])
def list_image_alternates(post_id, filename):
    """List alternate versions for the gallery image represented by filename."""
    try:
        _, post_metadata = load_post_metadata_file(post_id)
        cascade_meta = post_metadata.get('cascade_metadata', {})
        images = cascade_meta.get('images', [])
        img_entry = find_image_entry_by_variant(images, filename)
        if not img_entry:
            return jsonify({"error": "Image entry not found"}), 404

        payload = build_alternate_payload(post_id, img_entry)
        return jsonify({"success": True, "data": payload})
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except Exception as e:
        logger.error(f"Error listing alternates for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/enhance/<post_id>/<filename>/alternates/activate', methods=['POST'])
def activate_image_alternate(post_id, filename):
    """Switch the gallery-default displayed version to a selected alternate."""
    try:
        data_json = request.get_json() or {}
        target_filename = data_json.get('target_filename')
        if not target_filename:
            return jsonify({"error": "No target filename provided"}), 400

        target_path = FileOperationsManager.get_post_extracted_dir(post_id) / target_filename
        if not target_path.exists():
            return jsonify({"error": "Target alternate image not found"}), 404

        metadata_path, post_metadata = load_post_metadata_file(post_id)
        cascade_meta = post_metadata.get('cascade_metadata', {})
        images = cascade_meta.get('images', [])
        img_entry = find_image_entry_by_variant(images, filename)
        if not img_entry:
            return jsonify({"error": "Image entry not found"}), 404

        ensure_image_alternate_metadata(img_entry)
        valid_filenames = {alt.get('filename') for alt in img_entry.get('alternate_versions', [])}
        if target_filename not in valid_filenames:
            return jsonify({"error": "Target filename is not a known alternate for this image"}), 400

        base_filename = img_entry['base_image_filename']
        add_or_update_alternate_version(
            img_entry,
            target_filename,
            kind='original' if target_filename == base_filename else 'enhanced',
            source_run_id=next((alt.get('source_run_id') for alt in img_entry.get('alternate_versions', []) if alt.get('filename') == target_filename), None),
            prompt_text=next((alt.get('prompt_text', '') for alt in img_entry.get('alternate_versions', []) if alt.get('filename') == target_filename), ''),
            created_at=next((alt.get('created_at') for alt in img_entry.get('alternate_versions', []) if alt.get('filename') == target_filename), datetime.now().isoformat()),
        )
        img_entry['enhanced'] = target_filename != base_filename
        img_entry['enhancement_date'] = datetime.now().isoformat()
        img_entry['enhanced_filename'] = target_filename if target_filename != base_filename else None
        img_entry['file_info'] = {
            'filename': target_filename,
            'path': target_filename,
            'size': target_path.stat().st_size,
            'modified': datetime.fromtimestamp(target_path.stat().st_mtime).isoformat(),
        }

        cascade_meta['last_updated'] = datetime.now().isoformat()
        post_metadata['cascade_metadata'] = cascade_meta
        safe_save_json(post_metadata, metadata_path)
        regenerate_post_html_from_metadata(post_id, post_metadata)

        payload = build_alternate_payload(post_id, img_entry)
        return jsonify({
            "success": True,
            "message": "Active alternate updated",
            "data": payload,
        })
    except FileNotFoundError:
        return jsonify({"error": "Post metadata file not found"}), 404
    except Exception as e:
        logger.error(f"Error activating alternate for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/<post_id>/<filename>/save', methods=['POST'])
def save_enhanced_image(post_id, filename):
    """Save enhanced image and update metadata."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()
    
    try:
        data_json = request.get_json() or {}
        enhanced_filename = data_json.get('enhanced_filename')
        config = data_json.get('config', {})
        bbox = data_json.get('custom_mask')
        prompt_text = data_json.get('prompt', '')
        reference_asset_ids = data_json.get('reference_asset_ids') or []
        source_run_id = data_json.get('source_run_id')
        
        if not enhanced_filename:
            return jsonify({"error": "No enhanced filename provided"}), 400
        
        # Check if enhanced file exists
        enhanced_path = FileOperationsManager.get_post_extracted_dir(post_id) / enhanced_filename
        if not enhanced_path.exists():
            return jsonify({"error": "Enhanced image not found"}), 404
        
        # Load the per-post metadata file (NOT the main posts_metadata.json)
        metadata_path, post_metadata = load_post_metadata_file(post_id)
        
        # Update the cascade_metadata images
        cascade_meta = post_metadata.get('cascade_metadata', {})
        images = cascade_meta.get('images', [])
        
        img_entry = find_image_entry_by_variant(images, filename)

        if img_entry:
            ensure_image_alternate_metadata(img_entry)
            base_filename = img_entry.get('base_image_filename') or canonical_base_filename(filename)
            current_active_filename = img_entry.get('active_alternate_filename') or img_entry.get('filename') or base_filename
            add_or_update_alternate_version(
                img_entry,
                enhanced_filename,
                kind='enhanced',
                source_run_id=source_run_id,
                prompt_text=prompt_text or config.get('prompt', ''),
                created_at=datetime.now().isoformat(),
                active=False,
            )
            img_entry['filename'] = current_active_filename
            img_entry['active_alternate_filename'] = current_active_filename
            img_entry['enhanced'] = current_active_filename != base_filename
            img_entry['enhancement_date'] = datetime.now().isoformat()
            img_entry['original_filename'] = base_filename
            img_entry['base_image_filename'] = base_filename
            img_entry['enhanced_filename'] = current_active_filename if current_active_filename != base_filename else None
            img_entry['enhancement_config'] = config
            current_active_path = FileOperationsManager.get_post_extracted_dir(post_id) / current_active_filename
            if current_active_path.exists():
                img_entry['file_info'] = {
                    'filename': current_active_filename,
                    'path': current_active_filename,
                    'size': current_active_path.stat().st_size,
                    'modified': datetime.fromtimestamp(current_active_path.stat().st_mtime).isoformat()
                }
        else:
            # Image not found in metadata - add enhanced image as new entry
            base_filename = canonical_base_filename(filename)
            base_path = FileOperationsManager.get_post_extracted_dir(post_id) / base_filename
            file_info = {
                'filename': base_filename,
                'path': base_filename,
                'size': base_path.stat().st_size if base_path.exists() else enhanced_path.stat().st_size,
                'modified': datetime.fromtimestamp((base_path.stat().st_mtime if base_path.exists() else enhanced_path.stat().st_mtime)).isoformat()
            }
            img_entry = {
                'filename': base_filename,
                'visible': True,
                'deleted': False,
                'custom_order': len(images),
                'file_info': file_info,
                'enhanced': False,
                'enhancement_date': datetime.now().isoformat(),
                'original_filename': base_filename,
                'base_image_filename': base_filename,
                'active_alternate_filename': base_filename,
                'enhanced_filename': None,
                'enhancement_config': config,
                'alternate_versions': [
                    {
                        'filename': base_filename,
                        'kind': 'original',
                        'created_at': datetime.now().isoformat(),
                        'source_run_id': None,
                        'prompt_text': '',
                        'active': True,
                    },
                    {
                        'filename': enhanced_filename,
                        'kind': 'enhanced',
                        'created_at': datetime.now().isoformat(),
                        'source_run_id': source_run_id,
                        'prompt_text': prompt_text or config.get('prompt', ''),
                        'active': False,
                    }
                ]
            }
            images.append(img_entry)
        
        # Update cascade metadata
        cascade_meta['last_updated'] = datetime.now().isoformat()
        cascade_meta['total_images'] = len(images)
        cascade_meta['visible_images'] = len([img for img in images if img.get('visible', True) and not img.get('deleted', False)])
        post_metadata['cascade_metadata'] = cascade_meta

        archived_focus_item = None
        if bbox:
            archived_focus_item = append_focus_archive_item(
                post_id=post_id,
                post_metadata=post_metadata,
                source_image_path=enhanced_path,
                source_image_filename=filename,
                source_enhanced_filename=enhanced_filename,
                asset_type='edited_focus',
                bbox=bbox,
                prompt_text=prompt_text or config.get('prompt', ''),
                reference_asset_ids=reference_asset_ids,
                source_run_id=source_run_id,
            )
        
        # Save the per-post metadata file back
        safe_save_json(post_metadata, metadata_path)
        
        logger.info(f"Updated per-post metadata for {post_id}: {filename} -> {enhanced_filename}")
        
        # Regenerate ONLY the HTML files (NOT the metadata file) so changes persist on refresh
        try:
            regenerate_post_html_from_metadata(post_id, post_metadata)
            logger.info(f"Regenerated HTML files for post {post_id} after enhancement save")
        except Exception as html_error:
            logger.error(f"Failed to regenerate HTML after enhancement: {html_error}")
        
        alternates_payload = build_alternate_payload(post_id, img_entry)
        return jsonify({
            "success": True, 
            "message": "Enhanced image saved",
            "new_filename": enhanced_filename,
            "alternates": alternates_payload,
            "focus_archive_item": {
                **archived_focus_item,
                "image_url": f"/api/enhance/{post_id}/focus-assets/{archived_focus_item['asset_id']}/image",
            } if archived_focus_item else None
        })
        
    except Exception as e:
        logger.error(f"Error saving enhanced image for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/<post_id>/<filename>/discard', methods=['POST'])
def discard_enhanced_image(post_id, filename):
    """Discard enhanced image (delete temporary file)."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()
    
    try:
        data_json = request.get_json() or {}
        enhanced_filename = data_json.get('enhanced_filename')
        
        if not enhanced_filename:
            return jsonify({"error": "No enhanced filename provided"}), 400
        
        # Delete enhanced file
        enhanced_path = FileOperationsManager.get_post_extracted_dir(post_id) / enhanced_filename
        if enhanced_path.exists():
            enhanced_path.unlink()
            logger.info(f"Discarded enhanced image: {enhanced_path}")
        
        return jsonify({"success": True, "message": "Enhanced image discarded"})
        
    except Exception as e:
        logger.error(f"Error discarding enhanced image for {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/posts/<post_id>/extract', methods=['POST'])
@app.route('/api/files/extract/<post_id>', methods=['POST'])
def extract_post_files(post_id):
    """Queue download + extraction for a post."""
    try:
        logger.info(f"Queueing extraction for post {post_id}")
        
        # Load metadata to check if post exists
        data = metadata_manager.load_metadata()
        post = None
        for p in data.get('posts', []):
            if p.get('post_id') == post_id:
                post = p
                break
        
        if not post:
            logger.error(f"Post {post_id} not found")
            return jsonify({"error": "Post not found"}), 404
        
        # Check if post has ZIP files
        zip_files = post.get('zip_files', [])
        if not zip_files:
            logger.error(f"No ZIP files found for post {post_id}")
            return jsonify({"error": "No ZIP files found for this post"}), 400
        
        # Check if already extracted
        if any(zip_file.get('extracted', False) for zip_file in zip_files):
            logger.info(f"Post {post_id} already extracted")
            return jsonify({"success": True, "message": "Post already extracted"})
        
        queue_result = extraction_queue_manager.enqueue(post_id)

        if queue_result.get('already_queued'):
            return jsonify({
                "success": True,
                "message": "Extraction already queued",
                "status": queue_result.get('status'),
                "in_progress": queue_result.get('in_progress', True),
                "queue_position": queue_result.get('queue_position')
            })

        return jsonify({
            "success": True,
            "message": "Extraction queued",
            "status": queue_result.get('status'),
            "in_progress": queue_result.get('in_progress', True),
            "queue_position": queue_result.get('queue_position')
        })
            
    except Exception as e:
        logger.error(f"Error extracting post {post_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/posts/<post_id>/extract/progress', methods=['GET'])
def get_extraction_progress(post_id):
    """Get current download/extraction progress for a post."""
    status = extraction_queue_manager.get_job_status(post_id)

    if not status:
        return jsonify({
            "success": True,
            "in_progress": False,
            "completed": False,
            "progress": None,
            "status": "idle"
        })

    response = {
        "success": True,
        "status": status.get('status'),
        "in_progress": status.get('in_progress', False),
        "completed": status.get('completed', False),
        "progress": status.get('progress'),
        "queue_position": status.get('queue_position'),
        "max_concurrent": status.get('max_concurrent')
    }

    if status.get('completed'):
        response["extraction_success"] = status.get('extraction_success', False)
        if status.get('error'):
            response["error"] = status.get('error')

    return jsonify(response)


@app.route('/api/posts/extract/active', methods=['GET'])
def get_active_extractions():
    """Get all queued/running download jobs."""
    jobs = extraction_queue_manager.get_active_jobs()

    status_filter = (request.args.get('status') or '').strip().lower()
    if status_filter in ('queued', 'running'):
        jobs = [job for job in jobs if (job.get('status') or '').lower() == status_filter]

    return jsonify({
        "success": True,
        "jobs": jobs,
        "max_concurrent": Config.MAX_CONCURRENT_EXTRACTIONS
    })

@app.route('/api/posts/<post_id>/files', methods=['DELETE'])
@app.route('/api/files/extracted/<post_id>', methods=['DELETE'])
def delete_extracted_files(post_id):
    """Delete extracted files for a post."""
    try:
        logger.info(f"Deleting extracted files for post {post_id}")
        
        success = file_ops.delete_extracted_files(post_id)
        
        if success:
            logger.info(f"Successfully deleted extracted files for post {post_id}")
            return jsonify({"success": True, "message": "Extracted files deleted"})
        else:
            logger.error(f"Failed to delete extracted files for post {post_id}")
            return jsonify({"error": "Failed to delete extracted files"}), 500
            
    except Exception as e:
        logger.error(f"Error deleting extracted files for post {post_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500

# Post Pages Routes
@app.route('/posts/<post_id>.html')
def serve_post_page(post_id):
    """Serve the generated HTML page for a post."""
    try:
        post_file = Config.POST_PAGES_DIR / f"{post_id}.html"
        if not post_file.exists():
            # Try to auto-generate HTML files
            try:
                data = metadata_manager.load_metadata()
                post = None
                for p in data.get('posts', []):
                    if p.get('post_id') == post_id:
                        post = p
                        break
                
                if post:
                    logger.info(f"Auto-generating HTML files for post {post_id}")
                    # Only regenerate HTML, NOT metadata - metadata should be preserved
                    html_result = FileOperationsManager._generate_post_html_only(post_id, post)
                    if html_result.get('success') and post_file.exists():
                        return send_from_directory(Config.POST_PAGES_DIR, f"{post_id}.html")
            except Exception as auto_gen_error:
                logger.error(f"Error auto-generating HTML for post {post_id}: {auto_gen_error}")
            
            return f"<html><body><h1>Post {post_id} not found</h1><p>The post page has not been generated yet. Please extract the post files first.</p></body></html>", 404
        
        return send_from_directory(Config.POST_PAGES_DIR, f"{post_id}.html")
    except Exception as e:
        logger.error(f"Error serving post page {post_id}: {e}")
        return f"<html><body><h1>Error</h1><p>Failed to load post {post_id}: {e}</p></body></html>", 500

@app.route('/api/posts/<post_id>/generate_html', methods=['POST'])
def generate_post_html(post_id):
    """Generate HTML files for a specific post."""
    try:
        # Load metadata to get post info
        data = metadata_manager.load_metadata()
        
        # Find the post
        post = None
        for p in data.get('posts', []):
            if p.get('post_id') == post_id:
                post = p
                break
        
        if not post:
            return jsonify({"error": "Post not found"}), 404
        
        # Regenerate cascade metadata from filesystem before creating HTML
        FileOperationsManager._generate_cascade_metadata(post_id, post, data)
        
        # Generate HTML files AND metadata (full regeneration for manual trigger)
        html_result = FileOperationsManager._generate_post_html(post_id, post)
        
        if html_result.get('success'):
            return jsonify({"message": f"HTML files generated successfully for post {post_id}"})
        else:
            return jsonify({"error": f"Failed to generate HTML: {html_result.get('error')}"}), 500
            
    except Exception as e:
        logger.error(f"Error generating HTML for post {post_id}: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/posts/<post_id>_cascade.html')
def serve_cascade_page(post_id):
    """Serve the generated cascade HTML page for a post."""
    try:
        cascade_file = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
        if not cascade_file.exists():
            # Try to auto-generate HTML files
            try:
                data = metadata_manager.load_metadata()
                post = None
                for p in data.get('posts', []):
                    if p.get('post_id') == post_id:
                        post = p
                        break
                
                if post:
                    logger.info(f"Auto-generating HTML files for post {post_id}")
                    # Only regenerate HTML, NOT metadata - metadata should be preserved
                    html_result = FileOperationsManager._generate_post_html_only(post_id, post)
                    if html_result.get('success') and cascade_file.exists():
                        return send_from_directory(Config.POST_PAGES_DIR, f"{post_id}_cascade.html")
            except Exception as auto_gen_error:
                logger.error(f"Error auto-generating HTML for post {post_id}: {auto_gen_error}")
            
            return f"<html><body><h1>Cascade view for post {post_id} not found</h1><p>The cascade page has not been generated yet. Please extract the post files first.</p></body></html>", 404
        
        return send_from_directory(Config.POST_PAGES_DIR, f"{post_id}_cascade.html")
    except Exception as e:
        logger.error(f"Error serving cascade page {post_id}: {e}")
        return f"<html><body><h1>Error</h1><p>Failed to load cascade for post {post_id}: {e}</p></body></html>", 500

# Playlist serving routes
@app.route('/playlists/<playlist_id>.html')
def serve_playlist_page(playlist_id):
    """Serve the generated single view HTML page for a playlist."""
    try:
        playlist_file = Config.PLAYLISTS_DIR / f"{playlist_id}.html"
        if not playlist_file.exists():
            # Try to auto-regenerate if playlist exists in metadata
            playlist = playlist_manager.get_playlist(playlist_id)
            if playlist:
                logger.info(f"Auto-regenerating HTML files for playlist {playlist_id}")
                playlist_manager._generate_html_files(playlist_id, playlist)
            
            # Check again after regeneration
            if not playlist_file.exists():
                return f"<html><body><h1>Playlist {playlist_id} not found</h1><p>The playlist page has not been generated yet.</p></body></html>", 404
        
        return send_from_directory(Config.PLAYLISTS_DIR, f"{playlist_id}.html")
    except Exception as e:
        logger.error(f"Error serving playlist page {playlist_id}: {e}")
        return f"<html><body><h1>Error</h1><p>Failed to load playlist {playlist_id}: {e}</p></body></html>", 500

@app.route('/playlists/<playlist_id>_cascade.html')
def serve_playlist_cascade(playlist_id):
    """Serve the generated cascade HTML page for a playlist."""
    try:
        cascade_file = Config.PLAYLISTS_DIR / f"{playlist_id}_cascade.html"
        if not cascade_file.exists():
            # Try to auto-regenerate if playlist exists in metadata
            playlist = playlist_manager.get_playlist(playlist_id)
            if playlist:
                logger.info(f"Auto-regenerating HTML files for playlist {playlist_id}")
                playlist_manager._generate_html_files(playlist_id, playlist)
            
            # Check again after regeneration
            if not cascade_file.exists():
                return f"<html><body><h1>Playlist cascade for {playlist_id} not found</h1><p>The playlist cascade page has not been generated yet.</p></body></html>", 404
        
        return send_from_directory(Config.PLAYLISTS_DIR, f"{playlist_id}_cascade.html")
    except Exception as e:
        logger.error(f"Error serving playlist cascade {playlist_id}: {e}")
        return f"<html><body><h1>Error</h1><p>Failed to load cascade for playlist {playlist_id}: {e}</p></body></html>", 500

@app.route('/')
def serve_index():
    """Serve the main application."""
    return _send_webapp_file('index.html')

def _send_webapp_file(filename: str):
    response = send_from_directory(Config.WEBAPP_DIR, filename)
    lower = filename.lower()
    if lower.endswith(('.html', '.js', '.css')):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

@app.route('/extracted/<path:filename>')
def serve_extracted_files(filename):
    """Serve extracted image files."""
    return send_from_directory(Config.EXTRACTED_DIR, filename)

@app.route('/metadata/<path:filename>')
def serve_metadata_files(filename):
    """Serve metadata files (profile images, etc.)."""
    return send_from_directory(Config.METADATA_DIR, filename)

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files."""
    return _send_webapp_file(filename)

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

def main():
    """Main application entry point."""
    # Ensure directories exist
    Config.ensure_directories()
    
    logger.info("Starting VAMA Gallery API server...")
    logger.info(f"Base directory: {Config.BASE_DIR}")
    logger.info(f"Metadata JSON: {Config.METADATA_JSON}")
    logger.info(f"Metadata Excel: {Config.METADATA_EXCEL}")
    logger.info(f"Server: http://{Config.HOST}:{Config.PORT}")
    
    if not PANDAS_AVAILABLE:
        logger.warning("pandas not available - Excel synchronization disabled")
    if not CORS_AVAILABLE:
        logger.warning("flask-cors not available - CORS disabled")
    if not PIL_AVAILABLE:
        logger.warning("Pillow not available - thumbnail generation disabled")
    
    # Start the Flask development server
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        threaded=True
    )

if __name__ == '__main__':
    main()
