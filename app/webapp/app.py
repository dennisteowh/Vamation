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
try:
    from flask_cors import CORS
    CORS_AVAILABLE = True
except ImportError:
    CORS_AVAILABLE = False

# Global extraction queue manager (initialized after file_ops)
extraction_queue_manager = None

try:
    from PIL import Image, ImageOps
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
    
    # Server configuration - Local only
    HOST = '127.0.0.1'
    PORT = 5000
    DEBUG = False
    
    # File processing
    MAX_THUMBNAIL_SIZE = (400, 400)
    ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    MAX_CONCURRENT_EXTRACTIONS = int(os.environ.get('MAX_CONCURRENT_EXTRACTIONS', '2'))
    EXTRACTION_STATUS_TTL_SECONDS = int(os.environ.get('EXTRACTION_STATUS_TTL_SECONDS', '300'))
    
    # SD WebUI Configuration
    SD_WEBUI_URL = "http://127.0.0.1:7861"
    SD_WEBUI_PATH = r"D:\3D Objects\sd.webui\webui"  # Path to SD WebUI installation
    SD_WEBUI_STARTUP_TIMEOUT = 120  # seconds to wait for SD WebUI to start
    KEEP_WEBUI_ALIVE = os.environ.get('KEEP_WEBUI_ALIVE', 'false').lower() == 'true'  # Set to False to shut down SD WebUI when app closes (useful for development)
    
    # Eye detection model (optional YOLO model path)
    YOLO_MODEL_PATH = None  # Set to path of custom YOLO model if available
    
    # Inpainting defaults
    INPAINT_CONFIG = {
        "prompt": "masterpiece, best quality, highly detailed anime eyes, sharp clear pupils, beautiful iris detail, perfect symmetry, extremely detailed, 8k, ultra sharp focus",
        "negative_prompt": "blurry, low quality, distorted, malformed eyes, asymmetric, watermark",
        "sampler_name": "DPM++ 2M",
        "steps": 50,
        "cfg_scale": 7.0,
        "denoising_strength": 0.2,
        "inpaint_full_res": True,
        "inpaint_full_res_padding": 32,
        "inpainting_fill": 1,
        "width": 512,
        "height": 512,
    }
    
    ENABLE_IMAGE_ENHANCEMENT = os.environ.get('ENABLE_IMAGE_ENHANCEMENT', 'false').lower() == 'true'
    
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

    COOLDOWN_SECONDS = 20 * 60 * 60

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
        }

    def get_status(self) -> dict:
        if self.status_file.exists():
            try:
                return json.loads(self.status_file.read_text(encoding='utf-8'))
            except Exception:
                pass
        return self._default_status()

    def _save_status(self, status: dict) -> None:
        safe_save_json(status, self.status_file, create_backup=False)

    def _is_process_running(self) -> bool:
        if not self.lock_file.exists():
            return False
        try:
            pid = int(self.lock_file.read_text(encoding='utf-8').strip())
            os.kill(pid, 0)
            return True
        except Exception:
            try:
                self.lock_file.unlink(missing_ok=True)
            except Exception:
                pass
            status = self.get_status()
            status['running'] = False
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

            self.lock_file.write_text(str(process.pid), encoding='utf-8')
            status.update({
                'running': True,
                'last_triggered_at': self._now_iso(),
                'last_reason': reason,
                'last_exit_code': None,
                'last_error': None,
                'last_log_file': str(log_file),
            })
            self._save_status(status)

            def watch_process(pid: int):
                exit_code = None
                error_text = None
                try:
                    _, exit_code = os.waitpid(pid, 0)
                    exit_code = os.waitstatus_to_exitcode(exit_code)
                except ChildProcessError:
                    exit_code = None
                except Exception as e:
                    error_text = str(e)
                finally:
                    current = self.get_status()
                    current['running'] = False
                    current['last_finished_at'] = self._now_iso()
                    current['last_exit_code'] = exit_code
                    current['last_error'] = error_text
                    if exit_code == 0:
                        current['last_success_at'] = current['last_finished_at']
                    self._save_status(current)
                    try:
                        self.lock_file.unlink(missing_ok=True)
                    except Exception:
                        pass

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
        self._lock = threading.Lock()
    
    def load_metadata(self) -> Dict[str, Any]:
        """Load metadata from JSON file."""
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
            return {"posts": [], "summary": {}}
    
    def save_metadata(self, data: Dict[str, Any]) -> bool:
        """Save metadata to JSON file, then generate Excel using MetadataHandler."""
        with self._lock:
            try:
                # Update timestamp
                if 'summary' in data:
                    data['summary']['last_update'] = datetime.now().isoformat()
                
                # Save JSON with atomic write (source of truth)
                safe_save_json(data, self.json_path, create_backup=True)
                
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
        template_path = Path('templates') / 'playlist_template.html'
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
        template_path = Path('templates') / 'playlist_cascade_template.html'
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
        """Delete extracted files for a post and update metadata."""
        try:
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
            
            # Update metadata to mark zip files as not extracted (atomically)
            if metadata_manager.atomic_unmark_extracted(post_id):
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
            current_images = []
            for img_path in extracted_dir.rglob('*'):
                if img_path.is_file() and img_path.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS:
                    current_images.append({
                        'filename': img_path.name,
                        'path': str(img_path.relative_to(extracted_dir)),
                        'size': img_path.stat().st_size,
                        'modified': datetime.fromtimestamp(img_path.stat().st_mtime).isoformat()
                    })
            
            # Sort by filename (default order)
            current_images.sort(key=lambda x: x['filename'])
            
            # Get existing cascade metadata or create new
            existing_cascade = post.get('cascade_metadata', {})
            existing_images = {img['filename']: img for img in existing_cascade.get('images', [])}
            
            # Generate new cascade metadata
            cascade_images = []
            for i, img in enumerate(current_images):
                filename = img['filename']
                
                # Preserve existing metadata or create new
                if filename in existing_images:
                    existing = existing_images[filename]
                    cascade_images.append({
                        'filename': filename,
                        'visible': existing.get('visible', True),
                        'deleted': existing.get('deleted', False),
                        'custom_order': existing.get('custom_order', i),
                        'file_info': img
                    })
                else:
                    # New image - add with default values
                    cascade_images.append({
                        'filename': filename,
                        'visible': True,
                        'deleted': False,
                        'custom_order': i,
                        'file_info': img
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
            html = template.replace('{{post_id}}', str(post_id))
            html = html.replace('{{post_name}}', json.dumps(post.get('revised_post_name', 'Unknown Post'))[1:-1])  # Remove quotes from json.dumps
            html = html.replace('{{post_date}}', post.get('post_date', ''))
            html = html.replace('{{images_json}}', json.dumps(images))
            html = html.replace('{{total_images}}', str(len(images)))
            html = html.replace('{{first_image_name}}', json.dumps(images[0]['filename'] if images else 'No images')[1:-1])
            
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
            html = template.replace('{{post_id}}', str(post_id))
            html = html.replace('{{post_name}}', json.dumps(post.get('revised_post_name', 'Unknown Post'))[1:-1])  # Remove quotes from json.dumps
            html = html.replace('{{post_date}}', post.get('post_date', ''))
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
                'post_name': post.get('revised_post_name', post.get('post_name', '')),
                'post_date': post.get('post_date', ''),
                'description': post.get('description', ''),
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
                         if f.is_file() and f.suffix.lower() in Config.ALLOWED_IMAGE_EXTENSIONS]
            
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
        }
    })

@app.route('/api/posts')
@app.route('/api/metadata/posts')
def get_posts():
    """Get all posts with filtering, sorting, and pagination."""
    data = metadata_manager.load_metadata()
    posts = data.get('posts', [])
    
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
    
    # Add extraction status to each post
    slim_posts = []
    for post in posts_page:
        post_id = post.get('post_id')
        if post_id:
            # Use metadata-based extraction status
            zip_files = post.get('zip_files', []) or []
            extracted = any(zip_file.get('extracted', False) for zip_file in zip_files)
            cascade_metadata = post.get('cascade_metadata', {}) if isinstance(post.get('cascade_metadata'), dict) else {}
            cascade_images = cascade_metadata.get('images', []) if isinstance(cascade_metadata, dict) else []
            image_count = len(cascade_images) if cascade_images else int(cascade_metadata.get('visible_images') or cascade_metadata.get('total_images') or 0)
            profile_images = post.get('profile_images', []) or []
            profile_images = profile_images[:1] if profile_images else []
            zip_files = [{'filename': zf.get('filename'), 'extracted': zf.get('extracted', False), 'downloaded': zf.get('downloaded', False)} for zf in zip_files]
            description = post.get('description', '')[:280] + '...' if len(post.get('description', '')) > 280 else post.get('description', '')
            slim_post = {
                'post_id': post_id,
                'revised_post_name': post.get('revised_post_name'),
                'post_name': post.get('post_name'),
                'post_date': post.get('post_date'),
                'description': description,
                'profile_images': profile_images,
                'zip_files': zip_files,
                'extracted': extracted,
                'image_count': image_count,
            }
            slim_posts.append(slim_post)
    
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
    
    playlist = playlist_manager.create_playlist(name, description)
    if playlist:
        return jsonify({
            "message": "Playlist created successfully",
            "playlist": playlist
        }), 201
    else:
        return jsonify({"error": "Failed to create playlist"}), 500

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
    if playlist_manager.delete_playlist(playlist_id):
        return jsonify({"message": "Playlist deleted successfully"})
    else:
        return jsonify({"error": "Failed to delete playlist"}), 500

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
        return jsonify({"success": True, "message": "Image removed from playlist"})
    else:
        return jsonify({"success": False, "error": result.get('error', 'Failed to remove image')}), 500

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
    """Serve profile image for a post."""
    image_path = image_manager.get_profile_image_path(post_id)
    if not image_path or not image_path.exists():
        abort(404)
    
    return send_file(image_path)

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
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return jsonify({
            "success": True,
            "data": {
                "available": False,
                "sd_webui_running": False,
                "keep_alive_enabled": False,
                "message": "Image enhancement is disabled on this Zo deployment.",
                "dependencies": {
                    "requests": REQUESTS_AVAILABLE,
                    "cv2": CV2_AVAILABLE,
                    "mediapipe": MEDIAPIPE_AVAILABLE,
                    "yolo": YOLO_AVAILABLE,
                    "pil": PIL_AVAILABLE
                }
            }
        })
    
    try:
        is_running = sd_webui_manager.is_running()
        
        # Determine appropriate message for user
        message = ""
        if not is_running:
            if Config.KEEP_WEBUI_ALIVE:
                message = "SD WebUI is starting up. Please wait a moment and try again."
            else:
                message = "SD WebUI is not running. Enable KEEP_WEBUI_ALIVE in config or start SD WebUI manually."
        
        status = {
            "available": is_running,
            "sd_webui_running": is_running,
            "keep_alive_enabled": Config.KEEP_WEBUI_ALIVE,
            "message": message,
            "dependencies": {
                "requests": REQUESTS_AVAILABLE,
                "cv2": CV2_AVAILABLE,
                "mediapipe": MEDIAPIPE_AVAILABLE,
                "yolo": YOLO_AVAILABLE,
                "pil": PIL_AVAILABLE
            }
        }
        
        return jsonify({"success": True, "data": status})
        
    except Exception as e:
        logger.error(f"Error checking enhancement status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/start-sd-webui', methods=['POST'])
def start_sd_webui():
    """Start SD WebUI if not running."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()
    
    try:
        result = sd_webui_manager.start()
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error starting SD WebUI: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/<post_id>/<filename>/detect', methods=['POST'])
def detect_eyes_in_image(post_id, filename):
    """Detect eyes in image and return bounding boxes."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()
    
    try:
        # Load image
        image_path = FileOperationsManager.get_post_extracted_dir(post_id) / filename
        if not image_path.exists():
            return jsonify({"error": "Image not found"}), 404
        
        if not CV2_AVAILABLE:
            return jsonify({"error": "OpenCV not available"}), 500
        
        # Load image
        image_np = cv2.imread(str(image_path))
        if image_np is None:
            return jsonify({"error": "Failed to load image"}), 500
        
        # Detect eyes
        eye_regions = eye_inpainter.detect_eyes(image_np)
        
        if not eye_regions:
            return jsonify({"success": True, "data": {"eyes_detected": False, "regions": []}})
        
        # Convert to JSON-serializable format
        regions = [{
            "box": region["box"],
            "side": region.get("side", "detected"),
            "confidence": region.get("confidence", 1.0)
        } for region in eye_regions]
        
        return jsonify({
            "success": True,
            "data": {
                "eyes_detected": True,
                "regions": regions,
                "count": len(regions)
            }
        })
        
    except Exception as e:
        logger.error(f"Error detecting eyes in {post_id}/{filename}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/enhance/<post_id>/<filename>', methods=['POST'])
def enhance_image(post_id, filename):
    """Enhance image with eye inpainting."""
    if not Config.ENABLE_IMAGE_ENHANCEMENT:
        return _enhancement_disabled_response()
    
    try:
        # Check SD WebUI availability
        if not sd_webui_manager.is_running():
            if Config.KEEP_WEBUI_ALIVE:
                # In persistent mode, SD WebUI should already be running or starting
                return jsonify({
                    "error": "SD WebUI is still starting up. Please wait a moment and try again.",
                    "code": "WEBUI_STARTING"
                }), 503
            else:
                # Not in persistent mode - don't auto-start, inform user
                return jsonify({
                    "error": "SD WebUI is not running. Please enable KEEP_WEBUI_ALIVE or start SD WebUI manually.",
                    "code": "WEBUI_NOT_RUNNING"
                }), 503
        
        # Get enhancement config from request
        config_data = request.get_json() or {}
        
        # Build inpainting config
        inpaint_config = Config.INPAINT_CONFIG.copy()
        if 'prompt' in config_data:
            inpaint_config['prompt'] = config_data['prompt']
        if 'negative_prompt' in config_data:
            inpaint_config['negative_prompt'] = config_data['negative_prompt']
        if 'denoising_strength' in config_data:
            inpaint_config['denoising_strength'] = float(config_data['denoising_strength'])
        if 'cfg_scale' in config_data:
            inpaint_config['cfg_scale'] = float(config_data['cfg_scale'])
        if 'steps' in config_data:
            inpaint_config['steps'] = int(config_data['steps'])
        
        # Load image
        image_path = FileOperationsManager.get_post_extracted_dir(post_id) / filename
        if not image_path.exists():
            return jsonify({"error": "Image not found"}), 404
        
        if not CV2_AVAILABLE or not PIL_AVAILABLE:
            return jsonify({"error": "Required libraries not available"}), 500
        
        # Load image
        image_np = cv2.imread(str(image_path))
        if image_np is None:
            return jsonify({"error": "Failed to load image"}), 500
        
        # Check for custom mask
        custom_mask = config_data.get('custom_mask')
        
        logger.info(f"Custom mask received: {custom_mask}")
        
        if custom_mask:
            # Use custom rectangular mask (coordinates are normalized 0-1)
            h, w = image_np.shape[:2]
            x1 = int(custom_mask['x1'] * w)
            y1 = int(custom_mask['y1'] * h)
            x2 = int(custom_mask['x2'] * w)
            y2 = int(custom_mask['y2'] * h)
            logger.info(f"Creating custom mask for region: ({x1}, {y1}) to ({x2}, {y2}) in image size ({w}, {h})")
            mask = eye_inpainter.create_mask_from_rectangle(image_np.shape, x1, y1, x2, y2)
        else:
            # Auto-detect eyes
            logger.info("No custom mask - using auto-detection")
            eye_regions = eye_inpainter.detect_eyes(image_np)
            
            if not eye_regions:
                return jsonify({"error": "No eyes detected. Try using manual mask."}), 400
            
            logger.info(f"Detected {len(eye_regions)} eye regions")
            mask = eye_inpainter.create_mask(image_np.shape, eye_regions)
        
        if mask is None:
            return jsonify({"error": "Failed to create mask"}), 500
        
        # Convert to PIL
        image_pil = Image.fromarray(cv2.cvtColor(image_np, cv2.COLOR_BGR2RGB))
        mask_pil = Image.fromarray(mask).convert('RGB')
        
        # Inpaint
        result_pil = eye_inpainter.inpaint_via_api(image_pil, mask_pil, inpaint_config)
        
        if result_pil is None:
            return jsonify({"error": "Inpainting failed. Check SD WebUI connection."}), 500
        
        # Save enhanced image with iteration number
        # Extract base name without previous enhancement iterations
        stem = image_path.stem
        suffix = image_path.suffix
        
        # Remove any existing _enhanced### suffix to get the base name
        import re
        base_stem = re.sub(r'_enhanced\d+$', '', stem)
        
        # Find the next iteration number by checking existing files
        iteration = 1
        while True:
            enhanced_filename = f"{base_stem}_enhanced{iteration:03d}{suffix}"
            enhanced_path = image_path.parent / enhanced_filename
            if not enhanced_path.exists():
                break
            iteration += 1
        
        result_pil.save(enhanced_path)
        
        logger.info(f"Enhanced image saved: {enhanced_path} (iteration {iteration})")
        
        return jsonify({
            "success": True,
            "data": {
                "enhanced_filename": enhanced_filename,
                "original_filename": filename,
                "config": inpaint_config
            }
        })
        
    except Exception as e:
        logger.error(f"Error enhancing {post_id}/{filename}: {e}")
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
        
        if not enhanced_filename:
            return jsonify({"error": "No enhanced filename provided"}), 400
        
        # Check if enhanced file exists
        enhanced_path = FileOperationsManager.get_post_extracted_dir(post_id) / enhanced_filename
        if not enhanced_path.exists():
            return jsonify({"error": "Enhanced image not found"}), 404
        
        # Load the per-post metadata file (NOT the main posts_metadata.json)
        metadata_path = Config.POST_PAGES_DIR / f"{post_id}_metadata.json"
        if not metadata_path.exists():
            return jsonify({"error": "Post metadata file not found"}), 404
        
        with open(metadata_path, 'r', encoding='utf-8') as f:
            post_metadata = json.load(f)
        
        # Update the cascade_metadata images
        cascade_meta = post_metadata.get('cascade_metadata', {})
        images = cascade_meta.get('images', [])
        
        # Find the original image entry
        found = False
        for img_entry in images:
            if img_entry.get('filename') == filename:
                # Update filename to point to enhanced version
                img_entry['filename'] = enhanced_filename
                img_entry['enhanced'] = True
                img_entry['enhancement_date'] = datetime.now().isoformat()
                img_entry['original_filename'] = filename
                img_entry['enhanced_filename'] = enhanced_filename
                img_entry['enhancement_config'] = config
                found = True
                break
        
        if not found:
            # Image not found in metadata - add enhanced image as new entry
            file_info = {
                'size': enhanced_path.stat().st_size,
                'modified': datetime.fromtimestamp(enhanced_path.stat().st_mtime).isoformat()
            }
            images.append({
                'filename': enhanced_filename,
                'visible': True,
                'deleted': False,
                'custom_order': len(images),
                'file_info': file_info,
                'enhanced': True,
                'enhancement_date': datetime.now().isoformat(),
                'original_filename': filename,
                'enhanced_filename': enhanced_filename,
                'enhancement_config': config
            })
        
        # Update cascade metadata
        cascade_meta['last_updated'] = datetime.now().isoformat()
        cascade_meta['total_images'] = len(images)
        post_metadata['cascade_metadata'] = cascade_meta
        
        # Save the per-post metadata file back
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(post_metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Updated per-post metadata for {post_id}: {filename} -> {enhanced_filename}")
        
        # Regenerate ONLY the HTML files (NOT the metadata file) so changes persist on refresh
        try:
            # Reload the updated per-post metadata we just saved
            with open(metadata_path, 'r', encoding='utf-8') as f:
                updated_post_metadata = json.load(f)
            
            # Create a post dict with the updated cascade_metadata for HTML generation
            post_for_html = {
                'post_id': post_id,
                'revised_post_name': updated_post_metadata.get('post_name', ''),
                'post_name': updated_post_metadata.get('post_name', ''),
                'post_date': updated_post_metadata.get('post_date', ''),
                'cascade_metadata': updated_post_metadata.get('cascade_metadata', {})
            }
            
            # Regenerate ONLY the HTML files (single and cascade views)
            single_html = FileOperationsManager._create_single_view_html(post_id, post_for_html)
            single_path = Config.POST_PAGES_DIR / f"{post_id}.html"
            with open(single_path, 'w', encoding='utf-8') as f:
                f.write(single_html)
            
            cascade_html = FileOperationsManager._create_cascade_view_html(post_id, post_for_html)
            cascade_path = Config.POST_PAGES_DIR / f"{post_id}_cascade.html"
            with open(cascade_path, 'w', encoding='utf-8') as f:
                f.write(cascade_html)
            
            logger.info(f"Regenerated HTML files for post {post_id} after enhancement save")
        except Exception as html_error:
            logger.error(f"Failed to regenerate HTML after enhancement: {html_error}")
        
        return jsonify({
            "success": True, 
            "message": "Enhanced image saved",
            "new_filename": enhanced_filename
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
    return send_from_directory(Config.WEBAPP_DIR, 'index.html')

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
    return send_from_directory(Config.WEBAPP_DIR, filename)

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
    
    # Auto-launch SD WebUI if KEEP_WEBUI_ALIVE is enabled and it's not running
    # Only do this in the main process, not Flask's reloader child process
    if Config.KEEP_WEBUI_ALIVE and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        logger.info("KEEP_WEBUI_ALIVE is enabled - checking SD WebUI status...")
        if not sd_webui_manager.is_running():
            logger.info("SD WebUI is not running - starting it now...")
            # Start in background thread to not block app startup
            def start_webui_background():
                result = sd_webui_manager.start()
                if result['success']:
                    logger.info("SD WebUI started successfully in background")
                else:
                    logger.error(f"Failed to start SD WebUI: {result.get('error')}")
            
            webui_thread = threading.Thread(target=start_webui_background, daemon=True)
            webui_thread.start()
        else:
            logger.info("SD WebUI is already running")
    
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