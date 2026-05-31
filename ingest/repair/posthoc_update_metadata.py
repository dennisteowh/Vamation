#!/usr/bin/env python3
"""
VAMA Patreon Post-hoc Metadata Updater v1.0

Updates metadata files to reflect manually downloaded files in the downloads folder.
Combines filename fixing and metadata updating in a single integrated process.

Features:
- Scans downloads folder and matches files by size to metadata
- Fixes filenames to match naming convention
- Handles duplicate files (same size = delete, different size = warn & keep)
- Updates both JSON and Excel metadata files
- Creates orphaned entries for unmatched files
- Comprehensive logging and validation

New Naming Convention: {PostTitle}_{PostID}_{OriginalFilename}.zip
Orphaned Files: orphaned_{hash}_{OriginalFilename}.zip

Author: AI Assistant
Date: 2025-11-19
"""

import json
import time
import shutil
import hashlib
import tempfile
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import pytz

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import DOWNLOADS_DIR, POSTS_METADATA_JSON, POSTS_METADATA_XLSX, ensure_common_directories

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("⚠️  pandas not available - Excel export will be skipped")
    print("💡 Install with: pip install pandas openpyxl")


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

# SGT timezone
SGT = pytz.timezone('Asia/Singapore')


class PosthocMetadataUpdater:
    """Updates metadata from manually downloaded files with filename fixing."""
    
    def __init__(self):
        """Initialize the updater."""
        ensure_common_directories()
        self.downloads_path = DOWNLOADS_DIR
        self.metadata_path = POSTS_METADATA_JSON.parent
        
        self.json_file = POSTS_METADATA_JSON
        self.excel_file = POSTS_METADATA_XLSX
        
        print(f"🔄 VAMA Patreon Post-hoc Metadata Updater v1.0")
        print(f"=" * 60)
        print(f"📁 Downloads path: {self.downloads_path}")
        
        # Validate paths
        if not self.downloads_path.exists():
            raise Exception(f"Downloads directory not found: {self.downloads_path}")
        if not self.metadata_path.exists():
            raise Exception(f"Metadata directory not found: {self.metadata_path}")
        if not self.json_file.exists():
            raise Exception(f"JSON metadata file not found: {self.json_file}")
        
        # Statistics
        self.total_files = 0
        self.matched_files = 0
        self.renamed_files = 0
        self.deleted_duplicates = 0
        self.conflict_files = 0
        self.orphaned_files = 0
        self.updated_metadata = 0
        self.skipped_files = 0
        
        # Operation log
        self.operation_log = []
    
    def load_metadata(self) -> Tuple[Dict, Dict]:
        """Load metadata and create size-based lookup."""
        print(f"\n📖 Loading metadata...")
        
        with open(self.json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        posts = json_data.get('posts', [])
        
        # Create size-based lookup: size_bytes -> (post_id, post_name, zip_file_info, post_index, zip_index)
        size_lookup = {}
        
        for post_idx, post in enumerate(posts):
            post_id = post.get('post_id')
            post_name = post.get('post_name', 'Untitled')
            zip_files = post.get('zip_files', [])
            
            for zip_idx, zip_file in enumerate(zip_files):
                size_bytes = zip_file.get('size_bytes')
                
                if size_bytes and size_bytes > 0:
                    if size_bytes in size_lookup:
                        print(f"⚠️  Size conflict detected: {size_bytes} bytes (multiple ZIP files have same size)")
                        # Keep both entries in a list
                        if not isinstance(size_lookup[size_bytes], list):
                            size_lookup[size_bytes] = [size_lookup[size_bytes]]
                        size_lookup[size_bytes].append((post_id, post_name, zip_file, post_idx, zip_idx))
                    else:
                        size_lookup[size_bytes] = (post_id, post_name, zip_file, post_idx, zip_idx)
        
        print(f"📊 Metadata loaded:")
        print(f"   • Posts: {len(posts)}")
        print(f"   • ZIP files: {sum(len(p.get('zip_files', [])) for p in posts)}")
        print(f"   • Size conflicts: {sum(1 for v in size_lookup.values() if isinstance(v, list))}")
        
        return json_data, size_lookup
    
    def scan_downloads_folder(self) -> List[Tuple[Path, int]]:
        """Scan downloads folder and return file info."""
        print(f"\n📦 Scanning downloads folder...")
        
        zip_files = list(self.downloads_path.glob("*.zip"))
        file_info = []
        
        for zip_path in zip_files:
            try:
                file_stat = zip_path.stat()
                file_info.append((zip_path, file_stat.st_size))
            except Exception as e:
                print(f"⚠️  Error reading file {zip_path.name}: {e}")
        
        self.total_files = len(file_info)
        print(f"📊 Found {self.total_files} ZIP files")
        
        return file_info
    
    def create_safe_filename(self, post_name: str, post_id: str, original_filename: str) -> str:
        """Create safe filename using naming convention."""
        # Clean post name
        safe_title = "".join(c for c in post_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        
        # Handle extremely long titles
        if len(safe_title) > 100:
            safe_title = safe_title[:97] + "..."
        
        # Create new filename
        new_filename = f"{safe_title}_{post_id}_{original_filename}"
        
        # Ensure filename isn't too long for filesystem
        if len(new_filename) > 200:
            max_title_len = 200 - len(post_id) - len(original_filename) - 2
            if max_title_len > 10:
                safe_title = safe_title[:max_title_len-3] + "..."
                new_filename = f"{safe_title}_{post_id}_{original_filename}"
            else:
                new_filename = f"{post_id}_{original_filename}"
        
        return new_filename
    
    def create_orphaned_filename(self, original_filename: str, file_size: int) -> str:
        """Create filename for orphaned files."""
        # Generate unique hash based on filename and size
        hash_input = f"orphaned_{original_filename}_{file_size}_{datetime.now(SGT).isoformat()}"
        file_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        
        # Create orphaned filename
        name_parts = original_filename.rsplit('.', 1)
        if len(name_parts) == 2:
            return f"orphaned_{file_hash}_{name_parts[0]}.{name_parts[1]}"
        else:
            return f"orphaned_{file_hash}_{original_filename}"
    
    def create_orphaned_metadata_entry(self, original_filename: str, local_filename: str, file_size: int, file_path: Path) -> Dict:
        """Create metadata entry for orphaned file."""
        file_stat = file_path.stat()
        
        # Generate unique post_id
        hash_input = f"orphaned_{local_filename}_{file_size}"
        file_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        orphaned_post_id = f"orphaned_{file_hash}"
        
        # Use filename (without extension) as post name
        name_parts = local_filename.rsplit('.', 1)
        base_name = name_parts[0] if len(name_parts) == 2 else local_filename
        
        # Remove orphaned prefix if it exists
        if base_name.startswith('orphaned_'):
            display_name = base_name[9:]  # Remove "orphaned_" prefix
            if '_' in display_name:
                display_name = display_name.split('_', 1)[1]  # Remove hash part
        else:
            display_name = base_name
        
        post_name = f"[ORPHANED] {display_name}"
        
        return {
            'post_id': orphaned_post_id,
            'post_name': post_name,
            'revised_post_name': post_name,
            'display': True,
            'post_date': None,
            'scraped_date': datetime.now(SGT).isoformat(),
            'description': f"Orphaned file found in downloads: {local_filename}",
            'patreon_url': '',
            'post_type': 'orphaned',
            'has_zip_files': True,
            'zip_files': [{
                'filename': original_filename,
                'size_bytes': file_size,
                'size_mb': round(file_size / (1024 * 1024), 2),
                'media_id': f"orphaned_{file_hash}",
                'mimetype': 'application/zip',
                'download_url': '',
                'downloaded': True,
                'extracted': False,
                'download_date': datetime.fromtimestamp(file_stat.st_mtime, tz=SGT).isoformat(),
                'local_filename': local_filename
            }],
            'profile_images': [],
            'profile_images_count': 0
        }
    
    def handle_file_conflicts(self, current_path: Path, target_path: Path) -> str:
        """Handle file conflicts when renaming. Returns action taken."""
        if not target_path.exists():
            return "no_conflict"
        
        # Both files exist - compare sizes
        current_size = current_path.stat().st_size
        target_size = target_path.stat().st_size
        
        if current_size == target_size:
            # Same size - assume duplicate, delete current file
            current_path.unlink()
            return "deleted_duplicate"
        else:
            # Different sizes - conflict, keep both
            return "size_conflict"
    
    def process_single_file(self, file_path: Path, file_size: int, size_lookup: Dict, json_data: Dict) -> None:
        """Process a single file - match, rename if needed, update metadata."""
        current_name = file_path.name
        print(f"\n📁 Processing: {current_name} ({file_size:,} bytes)")
        
        # Try to match by size
        if file_size in size_lookup:
            lookup_entry = size_lookup[file_size]
            
            # Handle size conflicts (multiple metadata entries with same size)
            if isinstance(lookup_entry, list):
                print(f"   ⚠️  Multiple metadata entries found for size {file_size:,} bytes")
                print(f"   💡 Using first match (this may need manual review)")
                post_id, post_name, zip_file, post_idx, zip_idx = lookup_entry[0]
            else:
                post_id, post_name, zip_file, post_idx, zip_idx = lookup_entry
            
            original_filename = zip_file.get('filename', '')
            self.matched_files += 1
            
            print(f"   ✅ Matched to post {post_id}: {post_name}")
            print(f"   📄 Expected filename: {original_filename}")
            
            # Create expected filename
            expected_filename = self.create_safe_filename(post_name, post_id, original_filename)
            expected_path = file_path.parent / expected_filename
            
            # Check if filename needs fixing
            conflict_action = "no_action"  # Default for when no rename is needed
            
            if current_name == expected_filename:
                print(f"   ✅ Filename already correct")
                self.skipped_files += 1
            else:
                print(f"   🔄 Need to rename to: {expected_filename}")
                
                # Handle potential conflicts
                conflict_action = self.handle_file_conflicts(file_path, expected_path)
                
                if conflict_action == "no_conflict":
                    # Safe to rename
                    try:
                        file_path.rename(expected_path)
                        print(f"   ✅ Successfully renamed")
                        self.operation_log.append({
                            'action': 'rename',
                            'old_name': current_name,
                            'new_name': expected_filename,
                            'post_id': post_id,
                            'size_bytes': file_size,
                            'status': 'success'
                        })
                        self.renamed_files += 1
                    except Exception as e:
                        print(f"   ❌ Failed to rename: {e}")
                        self.operation_log.append({
                            'action': 'rename',
                            'old_name': current_name,
                            'new_name': expected_filename,
                            'post_id': post_id,
                            'size_bytes': file_size,
                            'status': 'failed',
                            'error': str(e)
                        })
                
                elif conflict_action == "deleted_duplicate":
                    print(f"   🗑️  Deleted duplicate file (same size as existing)")
                    self.operation_log.append({
                        'action': 'delete_duplicate',
                        'deleted_file': current_name,
                        'kept_file': expected_filename,
                        'post_id': post_id,
                        'size_bytes': file_size,
                        'status': 'success'
                    })
                    self.deleted_duplicates += 1
                
                elif conflict_action == "size_conflict":
                    print(f"   ⚠️  SIZE CONFLICT: Target file exists with different size")
                    print(f"       Current file: {file_size:,} bytes")
                    print(f"       Target file: {expected_path.stat().st_size:,} bytes")
                    print(f"   ⚠️  Keeping both files - manual review required")
                    self.operation_log.append({
                        'action': 'size_conflict',
                        'current_file': current_name,
                        'target_file': expected_filename,
                        'current_size': file_size,
                        'target_size': expected_path.stat().st_size,
                        'post_id': post_id,
                        'status': 'conflict'
                    })
                    self.conflict_files += 1
            
            # Update metadata (unless file was deleted)
            if conflict_action != "deleted_duplicate":
                final_filename = expected_filename if current_name != expected_filename else current_name
                self._update_zip_metadata(json_data, post_idx, zip_idx, final_filename, file_size)
                self.updated_metadata += 1
        
        else:
            # No match found - create orphaned entry
            print(f"   ❌ No metadata match found - creating orphaned entry")
            
            # Create orphaned filename
            orphaned_filename = self.create_orphaned_filename(current_name, file_size)
            orphaned_path = file_path.parent / orphaned_filename
            
            # Rename to orphaned convention if needed
            if current_name != orphaned_filename:
                if not orphaned_path.exists():
                    try:
                        file_path.rename(orphaned_path)
                        print(f"   🔄 Renamed to orphaned convention: {orphaned_filename}")
                        final_filename = orphaned_filename
                    except Exception as e:
                        print(f"   ⚠️  Failed to rename to orphaned convention: {e}")
                        final_filename = current_name
                else:
                    print(f"   ⚠️  Orphaned filename already exists, keeping original name")
                    final_filename = current_name
            else:
                final_filename = current_name
            
            # Create orphaned metadata entry
            orphaned_entry = self.create_orphaned_metadata_entry(
                current_name, final_filename, file_size, orphaned_path if final_filename == orphaned_filename else file_path
            )
            json_data['posts'].append(orphaned_entry)
            
            self.operation_log.append({
                'action': 'create_orphaned',
                'original_name': current_name,
                'orphaned_name': final_filename,
                'post_id': orphaned_entry['post_id'],
                'size_bytes': file_size,
                'status': 'success'
            })
            self.orphaned_files += 1
            self.updated_metadata += 1
            print(f"   🆕 Created orphaned entry: {orphaned_entry['post_id']}")
    
    def _update_zip_metadata(self, json_data: Dict, post_idx: int, zip_idx: int, local_filename: str, file_size: int) -> None:
        """Update ZIP file metadata with download information."""
        posts = json_data['posts']
        zip_file = posts[post_idx]['zip_files'][zip_idx]
        
        zip_file['downloaded'] = True
        zip_file['download_date'] = datetime.now(SGT).isoformat()
        zip_file['local_filename'] = local_filename
        
        # Verify size consistency
        if zip_file.get('size_bytes') != file_size:
            print(f"   ⚠️  Size mismatch in metadata: expected {zip_file.get('size_bytes', 'N/A')}, got {file_size}")
    
    def save_updated_metadata(self, json_data: Dict) -> bool:
        """Save updated JSON and regenerate Excel file using MetadataHandler."""
        print(f"\n💾 Saving updated metadata...")
        
        try:
            # Update summary - preserve existing structure and add new fields only
            if 'summary' not in json_data:
                json_data['summary'] = {}
            
            summary = json_data['summary']
            
            # Update core tracking fields
            summary['last_update'] = datetime.now(SGT).isoformat()
            summary['status'] = 'UPDATED'
            summary['total_posts'] = len(json_data['posts'])
            
            # Calculate statistics if not already present or update them
            posts = json_data['posts']
            summary['posts_with_images'] = len([p for p in posts if p.get('profile_images_count', 0) > 0])
            summary['total_images_downloaded'] = sum([p.get('profile_images_count', 0) for p in posts])
            summary['posts_with_zip_files'] = len([p for p in posts if p.get('has_zip_files', False)])
            
            # Calculate date range
            valid_dates = [p['post_date'] for p in posts if p.get('post_date')]
            if valid_dates:
                if 'date_range' not in summary:
                    summary['date_range'] = {}
                summary['date_range']['earliest'] = min(valid_dates)
                summary['date_range']['latest'] = max(valid_dates)
            
            # Save JSON file (source of truth)
            safe_save_json(json_data, self.json_file, create_backup=True)
            print(f"✅ JSON file updated: {self.json_file}")
            
            # Regenerate Excel using MetadataHandler
            if PANDAS_AVAILABLE:
                try:
                    import sys
                    sys.path.insert(0, str(self.script_dir))
                    from shared.metadata_handler import MetadataHandler
                    
                    print(f"🔄 Converting JSON to Excel using MetadataHandler...")
                    handler = MetadataHandler()
                    success = handler.json_to_excel(create_backup=True)
                    
                    if not success:
                        print(f"⚠️  Excel generation failed, but JSON was saved successfully")
                except Exception as e:
                    print(f"⚠️  Failed to generate Excel using MetadataHandler: {e}")
                    print(f"💡 JSON file was saved successfully. You can manually run metadata_handler.py later.")
            else:
                print(f"⚠️  Excel file not updated (pandas not available)")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to save metadata: {e}")
            return False
    
    def save_operation_log(self) -> None:
        """Save operation log for review."""
        if not self.operation_log:
            return
        
        log_filename = f"posthoc_update_log_{datetime.now(SGT).strftime('%Y%m%d_%H%M%S')}.json"
        log_path = self.script_dir / log_filename
        
        log_data = {
            'timestamp': datetime.now(SGT).isoformat(),
            'mode': 'live',
            'statistics': {
                'total_files': self.total_files,
                'matched_files': self.matched_files,
                'renamed_files': self.renamed_files,
                'deleted_duplicates': self.deleted_duplicates,
                'conflict_files': self.conflict_files,
                'orphaned_files': self.orphaned_files,
                'updated_metadata': self.updated_metadata,
                'skipped_files': self.skipped_files
            },
            'operations': self.operation_log
        }
        
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Operation log saved: {log_path}")
        except Exception as e:
            print(f"\n⚠️  Failed to save log: {e}")
    
    def print_summary(self) -> None:
        """Print operation summary."""
        print(f"\n📊 Operation Summary:")
        print(f"=" * 50)
        print(f"📁 Total files processed: {self.total_files}")
        print(f"✅ Files matched to metadata: {self.matched_files}")
        print(f"🔄 Files renamed: {self.renamed_files}")
        print(f"🗑️  Duplicate files deleted: {self.deleted_duplicates}")
        print(f"⚠️  Size conflicts: {self.conflict_files}")
        print(f"🆕 Orphaned entries created: {self.orphaned_files}")
        print(f"📝 Metadata records updated: {self.updated_metadata}")
        print(f"⏭️  Files skipped (already correct): {self.skipped_files}")
        
        if self.conflict_files > 0:
            print(f"\n⚠️  WARNING: {self.conflict_files} size conflicts require manual review!")
        
        print(f"\n✅ Changes have been applied to files and metadata")
    
    def run(self) -> bool:
        """Run the complete post-hoc update process."""
        try:
            # Load metadata and create size lookup
            json_data, size_lookup = self.load_metadata()
            
            # Scan downloads folder
            file_info = self.scan_downloads_folder()
            
            if not file_info:
                print("⚠️  No ZIP files found in downloads folder")
                return False
            
            # Process each file
            print(f"\n🔄 Processing files...")
            for file_path, file_size in file_info:
                self.process_single_file(file_path, file_size, size_lookup, json_data)
            
            # Save updated metadata
            if self.save_updated_metadata(json_data):
                print(f"✅ Metadata update completed")
            else:
                print(f"❌ Failed to save metadata updates")
                return False
            
            # Save operation log and print summary
            self.save_operation_log()
            self.print_summary()
            
            return True
            
        except Exception as e:
            print(f"❌ Error during post-hoc update: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """Main entry point."""
    try:
        updater = PosthocMetadataUpdater()
        updater.run()
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
