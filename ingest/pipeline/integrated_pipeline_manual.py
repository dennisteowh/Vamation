#!/usr/bin/env python3
"""
VAMA Patreon Integrated Pipeline v1.0

Unified pipeline that combines metadata extraction, ZIP downloading, and profile image downloading
into a single configurable workflow. Processes posts page by page with selective scraping based
on user configuration.

Features:
- Configurable scraping targets (metadata, ZIP files, profile images)
- Single-pass processing with page-by-page workflow
- Automated cookie validation and extraction
- Incremental metadata updates (no duplicates)
- Forward-compatible metadata structure
- ZIP detection validation during metadata scraping
- File existence checking with size validation
- SGT timezone support

Author: AI Assistant
Date: 2025-11-19
"""

import json
import time
import re
import os
import requests
import pytz
import tempfile
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse
import urllib.parse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.path_config import COOKIES_FILE, DOWNLOADS_DIR, POSTS_METADATA_JSON, PROFILE_IMAGES_DIR, ensure_common_directories

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


# Import selenium components
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import TimeoutException, WebDriverException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================

# Creator settings
CREATOR_SLUG = "VAMA"

# Date range settings (set both to None for ALL historical posts)
START_DATE = "2025-11-22" # "YYYY-MM-DD" format, or None for no start limit
END_DATE = None    # "YYYY-MM-DD" format, or None for no end limit

# Scraping configuration - Set to True for what you want to scrape
SCRAPE_METADATA = True      # Extract post metadata (always recommended)
SCRAPE_ZIP_FILES = False    # Download ZIP file attachments (opt-in)
SCRAPE_PROFILE_IMAGES = True # Download profile images

# File paths
OUTPUT_FILENAME = "posts_metadata"  # Fixed filename for incremental updates

# Download settings
MAX_RETRIES = 3
BATCH_SIZE = 2
DELAY_BETWEEN_BATCHES = 3
LOGIN_TIMEOUT = 120

# Image extraction settings
MAX_IMAGES_PER_POST = 3


class IntegratedPatreonPipeline:
    """Unified Patreon pipeline with configurable scraping targets."""
    
    def __init__(self):
        """Initialize the integrated pipeline."""
        ensure_common_directories()
        self.project_root = PROJECT_ROOT
        self.cookies_path = COOKIES_FILE
        self.metadata_path = POSTS_METADATA_JSON.parent
        self.download_path = DOWNLOADS_DIR
        
        self.metadata_path.mkdir(exist_ok=True)
        self.download_path.mkdir(exist_ok=True)
        
        if SCRAPE_PROFILE_IMAGES:
            self.profile_images_path = PROFILE_IMAGES_DIR
            self.profile_images_path.mkdir(exist_ok=True)
        
        # Known working campaign ID for VAMA
        self.campaign_id = "13637777"
        
        # SGT timezone
        self.sgt = pytz.timezone('Asia/Singapore')
        
        # Session will be created after authentication
        self.session = None
        
        print(f"🚀 VAMA Integrated Patreon Pipeline v1.0")
        print(f"=" * 60)
        print(f"📋 Scraping Configuration:")
        print(f"   • Metadata: {'✅' if SCRAPE_METADATA else '❌'}")
        print(f"   • ZIP Files: {'✅' if SCRAPE_ZIP_FILES else '❌'}")
        print(f"   • Profile Images: {'✅' if SCRAPE_PROFILE_IMAGES else '❌'}")
        
    def setup_authentication(self) -> bool:
        """Setup authentication with automatic cookie extraction if needed."""
        print(f"\n🔐 Setting up authentication...")
        
        # Check if cookies exist and are valid
        if self.cookies_path.exists():
            print(f"📁 Found cookies file: {self.cookies_path}")
            try:
                self.session = self._create_session()
                if self._validate_authentication():
                    print("✅ Existing cookies are valid!")
                    return True
                else:
                    print("❌ Existing cookies are invalid")
            except Exception as e:
                print(f"❌ Error with existing cookies: {e}")
        else:
            print(f"📁 No cookies file found")
        
        # Need fresh cookies
        print(f"🌐 Extracting fresh cookies...")
        if self._extract_fresh_cookies():
            self.session = self._create_session()
            if self._validate_authentication():
                print("✅ Fresh cookies extracted and validated!")
                return True
            else:
                print("❌ Fresh cookies failed validation")
                return False
        else:
            print("❌ Failed to extract fresh cookies")
            return False
    
    def _create_session(self) -> requests.Session:
        """Create an authenticated session with browser cookies."""
        session = requests.Session()
        
        # Load cookies from JSON file
        with open(self.cookies_path, 'r') as f:
            cookies_data = json.load(f)
        
        # Add cookies to session
        for cookie in cookies_data:
            session.cookies.set(
                name=cookie['name'],
                value=cookie['value'],
                domain=cookie.get('domain', '.patreon.com')
            )
        
        # Set browser-like headers
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/vnd.api+json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.patreon.com/',
            'Origin': 'https://www.patreon.com'
        })
        
        return session
    
    def _validate_authentication(self) -> bool:
        """Test if current cookies are still valid."""
        try:
            response = self.session.get(f"https://www.patreon.com/api/campaigns/{self.campaign_id}")
            return response.status_code == 200
        except Exception:
            return False
    
    def _extract_fresh_cookies(self) -> bool:
        """Extract fresh cookies using browser automation."""
        if not SELENIUM_AVAILABLE:
            print("❌ Selenium not available for cookie extraction")
            print("💡 Install selenium: pip install selenium")
            return False
        
        try:
            print("🌐 Opening Chrome browser for authentication...")
            
            # Setup Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            
            # Try to use existing Chrome profile
            profile_path = self._get_chrome_profile_path()
            if profile_path:
                print(f"📂 Using existing Chrome profile: {profile_path}")
                chrome_options.add_argument(f'--user-data-dir={profile_path}')
                chrome_options.add_argument('--profile-directory=Default')
            else:
                print("📂 Using temporary Chrome profile")
            
            # Start browser
            driver = webdriver.Chrome(options=chrome_options)
            
            try:
                # Navigate to Patreon
                print("📖 Navigating to Patreon...")
                driver.get("https://www.patreon.com")
                time.sleep(3)
                
                # Check if already logged in
                if ("/login" not in driver.current_url and 
                    (driver.find_elements(By.CSS_SELECTOR, "[data-tag='user-menu']") or
                     driver.find_elements(By.CSS_SELECTOR, ".sc-fznWqX") or
                     "feed" in driver.current_url.lower())):
                    print("✅ Already logged in!")
                else:
                    # Navigate to login and wait for user
                    driver.get("https://www.patreon.com/login")
                    
                    print("\n" + "="*60)
                    print("🔐 PLEASE LOGIN TO PATREON IN THE BROWSER WINDOW")
                    print("="*60)
                    print("1. Complete your login (username/password, 2FA if needed)")
                    print("2. Wait for the main Patreon page to load")
                    print("3. The script will automatically detect when you're logged in")
                    print(f"4. Timeout: {LOGIN_TIMEOUT} seconds")
                    print("="*60)
                    
                    # Wait for login completion
                    wait = WebDriverWait(driver, LOGIN_TIMEOUT)
                    wait.until(lambda d: "/login" not in d.current_url or 
                              d.find_elements(By.CSS_SELECTOR, "[data-tag='user-menu']") or
                              d.find_elements(By.CSS_SELECTOR, ".sc-fznWqX") or
                              "/home" in d.current_url)
                    
                    print("✅ Login detected!")
                
                # Extract cookies
                cookies = driver.get_cookies()
                essential_cookies = []
                required_names = ['session_id', '__cf_bm', 'patreon_device_id']
                
                for cookie in cookies:
                    if cookie['domain'] in ['.patreon.com', 'patreon.com'] or \
                       any(req in cookie['name'] for req in required_names):
                        essential_cookies.append({
                            'name': cookie['name'],
                            'value': cookie['value'],
                            'domain': cookie['domain']
                        })
                
                if len(essential_cookies) < 3:
                    print(f"❌ Only found {len(essential_cookies)} cookies - login may have failed")
                    return False
                
                # Save cookies
                with open(self.cookies_path, 'w') as f:
                    json.dump(essential_cookies, f, indent=2)
                
                print(f"✅ Extracted {len(essential_cookies)} cookies")
                return True
                
            finally:
                driver.quit()
                
        except Exception as e:
            print(f"❌ Cookie extraction failed: {e}")
            return False
    
    def _get_chrome_profile_path(self) -> str:
        """Get Chrome user data directory path."""
        possible_paths = [
            os.path.expanduser(r'~\AppData\Local\Google\Chrome\User Data'),
            os.path.expanduser(r'~\AppData\Local\Chromium\User Data'),
            r'C:\Users\{}\AppData\Local\Google\Chrome\User Data'.format(os.getenv('USERNAME', '')),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                default_profile = os.path.join(path, 'Default')
                if os.path.exists(default_profile):
                    return path
        return None
    
    def load_existing_metadata(self) -> Dict:
        """Load existing metadata file if it exists."""
        existing_file = POSTS_METADATA_JSON
        if existing_file.exists():
            try:
                with open(existing_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                print(f"📖 Found existing metadata: {existing_file}")
                return data
            except Exception as e:
                print(f"⚠️ Error loading existing metadata: {e}")
                print(f"🆕 Starting fresh...")
                return {'summary': {}, 'posts': []}
        else:
            print(f"🆕 No existing metadata found, starting fresh")
            return {'summary': {}, 'posts': []}
    
    def process_posts_integrated(self, start_date: str = None, end_date: str = None) -> Dict:
        """Process posts with integrated scraping based on configuration."""
        
        if start_date and end_date:
            print(f"\n🎯 Processing posts from {CREATOR_SLUG} between {start_date} and {end_date} (SGT)")
        elif start_date:
            print(f"\n🎯 Processing posts from {CREATOR_SLUG} from {start_date} onwards (SGT)")
        elif end_date:
            print(f"\n🎯 Processing posts from {CREATOR_SLUG} up to {end_date} (SGT)")
        else:
            print(f"\n🎯 Processing ALL historical posts from {CREATOR_SLUG} (SGT)")
        
        # Convert date strings to SGT datetime objects if provided
        start_dt = None
        end_dt = None
        
        if start_date:
            start_dt = self.sgt.localize(datetime.strptime(start_date, '%Y-%m-%d'))
        if end_date:
            end_dt = self.sgt.localize(datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1))
        
        # Initialize tracking variables
        all_metadata = []
        posts_processed_in_range = []
        cursor = None
        page_num = 1
        total_posts_processed = 0
        total_posts_in_range = 0
        
        # Load existing metadata if scraping metadata
        existing_posts = {}
        if SCRAPE_METADATA:
            existing_metadata = self.load_existing_metadata()
            existing_posts = {post['post_id']: post for post in existing_metadata.get('posts', [])}
            print(f"📚 Loaded {len(existing_posts)} existing posts from previous runs")
        
        print(f"📄 Starting integrated processing...")
        
        try:
            while True:
                posts_page = self._get_posts_page(cursor)
                if not posts_page:
                    print(f"🔚 No more posts to process")
                    break
                    
                # Process posts on this page
                page_metadata = []
                page_posts_in_range = 0
                oldest_date = None
                newest_date = None
                
                print(f"\n📄 === PAGE {page_num} ===")
                print(f"📊 Found {len(posts_page)} posts on this page")
                
                for i, post in enumerate(posts_page):
                    attrs = post.get('attributes', {})
                    title = attrs.get('title', 'Untitled')[:50] + ('...' if len(attrs.get('title', '')) > 50 else '')
                    published_str = attrs.get('published_at', '')
                    post_id = post.get('id')
                    
                    if published_str:
                        published_dt = datetime.fromisoformat(published_str.replace('Z', '+00:00'))
                        published_sgt = published_dt.astimezone(self.sgt)
                        
                        if oldest_date is None or published_sgt < oldest_date:
                            oldest_date = published_sgt
                        if newest_date is None or published_sgt > newest_date:
                            newest_date = published_sgt
                        
                        # Check if post is in our date range
                        in_range = True
                        
                        if start_dt and published_sgt < start_dt:
                            in_range = False
                        if end_dt and published_sgt >= end_dt:
                            in_range = False
                        
                        if in_range:
                            print(f"  {i+1:2d}. 📝 PROCESSING | {published_sgt.strftime('%Y-%m-%d %H:%M:%S')} | {title}")
                            
                            # Process this post with integrated scraping
                            post_result = self._process_single_post_integrated(
                                post, existing_posts, total_posts_in_range
                            )
                            
                            if post_result:
                                if SCRAPE_METADATA:
                                    existing_posts[post_id] = post_result
                                    page_metadata.append(post_result)
                                    posts_processed_in_range.append(post_result)
                                
                                page_posts_in_range += 1
                                total_posts_in_range += 1
                        else:
                            status = "❌ OUT OF RANGE"
                            print(f"  {i+1:2d}. {status} | {published_sgt.strftime('%Y-%m-%d %H:%M:%S')} | {title}")
                    else:
                        print(f"  {i+1:2d}. ⚠️  NO DATE   | ??? | {title}")
                    
                    total_posts_processed += 1
                
                print(f"📄 Page {page_num} Summary: {page_posts_in_range}/{len(posts_page)} posts processed (Total in range: {total_posts_in_range})")
                
                # Save progress if scraping metadata
                if SCRAPE_METADATA and page_metadata:
                    all_metadata = list(existing_posts.values())
                    # Sort metadata for consistent ordering in progress saves
                    all_metadata.sort(key=lambda p: (
                        p.get('post_date') is None,
                        -(datetime.fromisoformat(p['post_date'].replace('Z', '+00:00')).timestamp() if p.get('post_date') else 0),
                        p.get('post_name', '').lower()
                    ))
                    self._save_progress(all_metadata, page_num, len(all_metadata))
                
                # Stop conditions
                if start_dt and oldest_date and oldest_date < start_dt:
                    print(f"🔚 Reached posts before {start_date}, stopping")
                    break
                
                if len(posts_page) < 20:
                    print(f"🔚 Reached end of posts (page had {len(posts_page)} posts)")
                    break
                
                # Get cursor for next page
                cursor = self._extract_cursor_from_response(posts_page)
                if not cursor:
                    print(f"🔚 No more pages available (no cursor)")
                    break
                    
                page_num += 1
                print(f"➡️  Proceeding to page {page_num} (Processed: {total_posts_in_range} in range, {total_posts_processed} total)")
                time.sleep(1)  # Rate limiting
        
        except KeyboardInterrupt:
            print(f"\n⏸️ Processing interrupted by user at page {page_num}")
            
        except Exception as e:
            print(f"\n❌ Error during processing at page {page_num}: {e}")
        
        # Final save if scraping metadata
        if SCRAPE_METADATA and all_metadata:
            print(f"\n💾 Saving final metadata...")
            # Sort by post_date (descending, None/missing at bottom) then by post_name (ascending)
            all_metadata.sort(key=lambda p: (
                p.get('post_date') is None,  # None values go to bottom
                -(datetime.fromisoformat(p['post_date'].replace('Z', '+00:00')).timestamp() if p.get('post_date') else 0),  # Descending date
                p.get('post_name', '').lower()  # Ascending name for ties
            ))
            self.save_metadata(all_metadata)
            
            # Show statistics
            posts_with_zip = len([p for p in all_metadata if p.get('has_zip_files', False)])
            total_zip_files = sum(len(p.get('zip_files', [])) for p in all_metadata)
            print(f"\n📊 Statistics:")
            print(f"   Posts with ZIP files: {posts_with_zip}/{len(all_metadata)}")
            print(f"   Total ZIP files found: {total_zip_files}")
        
        date_desc = "ALL historical posts"
        if start_date and end_date:
            date_desc = f"{start_date} to {end_date}"
        elif start_date:
            date_desc = f"from {start_date} onwards"
        elif end_date:
            date_desc = f"up to {end_date}"
            
        print(f"\n✅ Completed processing {total_posts_in_range} posts in range: {date_desc}")
        return {
            'total_processed': total_posts_processed,
            'total_in_range': total_posts_in_range,
            'pages_processed': page_num - 1,
            'metadata': all_metadata if SCRAPE_METADATA else None,
            'posts_processed_in_range': posts_processed_in_range
        }
    
    def _process_single_post_integrated(self, post: Dict, existing_posts: Dict, post_number: int) -> Optional[Dict]:
        """Process a single post with integrated scraping based on configuration."""
        attrs = post.get('attributes', {})
        title = attrs.get('title', 'Untitled')
        post_id = post.get('id')
        
        print(f"   🏷️ Post ID: {post_id}")
        
        try:
            post_metadata = None
            
            # 1. Extract/update metadata if enabled
            if SCRAPE_METADATA:
                if post_id in existing_posts:
                    # Update existing post
                    print(f"   🔄 Updating existing post metadata")
                    existing_post = existing_posts[post_id]
                    existing_post['scraped_date'] = datetime.now(self.sgt).isoformat()
                    post_metadata = self._extract_post_metadata(post)
                    
                    # Preserve user-modified fields
                    post_metadata['revised_post_name'] = existing_post.get('revised_post_name', post_metadata['revised_post_name'])
                    post_metadata['display'] = existing_post.get('display', post_metadata['display'])
                    post_metadata['favourite'] = existing_post.get('favourite', False)
                    post_metadata['cascade_metadata'] = existing_post.get('cascade_metadata', {})
                    
                    # Preserve download status for ZIP files
                    if existing_post.get('zip_files') and post_metadata.get('zip_files'):
                        for i, new_zip in enumerate(post_metadata['zip_files']):
                            if i < len(existing_post['zip_files']):
                                existing_zip = existing_post['zip_files'][i]
                                new_zip['downloaded'] = existing_zip.get('downloaded', False)
                                new_zip['extracted'] = existing_zip.get('extracted', False)
                                new_zip['download_date'] = existing_zip.get('download_date', None)
                                new_zip['local_filename'] = existing_zip.get('local_filename', '')
                    
                    post_metadata = self._add_default_custom_fields(post_metadata, existing_posts)
                else:
                    # New post
                    print(f"   ✨ Adding new post to metadata")
                    post_metadata = self._extract_post_metadata(post)
                    post_metadata = self._add_default_custom_fields(post_metadata, existing_posts)
                
                # Validate ZIP detection if enabled
                if SCRAPE_ZIP_FILES and post_metadata.get('has_zip_files'):
                    downloader_attachments = self._get_post_attachments(post_id)
                    downloader_has_zip = len(downloader_attachments) > 0
                    if post_metadata.get('has_zip_files') != downloader_has_zip:
                        print(f"   ⚠️ ZIP detection mismatch - metadata: {post_metadata.get('has_zip_files')}, downloader: {downloader_has_zip}")
            
            # 2. Download ZIP files if enabled
            if SCRAPE_ZIP_FILES:
                zip_attachments = self._get_post_attachments(post_id)
                if zip_attachments:
                    print(f"   📦 Found {len(zip_attachments)} ZIP file(s)")
                    for attachment in zip_attachments:
                        file_name = attachment['file_name']
                        download_url = attachment['download_url']
                        file_size = attachment.get('size_bytes')
                        
                        # Create safe filename
                        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                        local_filename = f"{safe_title}_{post_id}_{file_name}"
                        
                        # Download file
                        result = self._download_single_file(download_url, local_filename, file_size, self.download_path)
                        if result == 'downloaded':
                            print(f"      ✅ Downloaded: {file_name}")
                            
                            # Update metadata if available
                            if post_metadata and post_metadata.get('zip_files'):
                                for zip_file in post_metadata['zip_files']:
                                    if zip_file.get('filename') == file_name:
                                        zip_file['downloaded'] = True
                                        zip_file['download_date'] = datetime.now(self.sgt).isoformat()
                                        zip_file['local_filename'] = local_filename
                                        break
                        elif result == 'failed':
                            print(f"      ❌ Failed to download: {file_name}")
                        
                        time.sleep(1)  # Rate limiting
            
            # 3. Download profile images if enabled
            if SCRAPE_PROFILE_IMAGES and post_metadata:
                profile_images = post_metadata.get('profile_images', [])
                if profile_images:
                    print(f"   🖼️ Found {len(profile_images)} profile image(s)")
                    for image_info in profile_images:
                        image_url = image_info.get('url')
                        filename = image_info.get('filename')
                        
                        if image_url and filename:
                            result = self._download_single_image(image_url, filename)
                            if result == 'downloaded':
                                print(f"      ✅ Downloaded image: {filename}")
                                image_info['downloaded'] = True
                            elif result == 'failed':
                                print(f"      ❌ Failed to download image: {filename}")
            
            return post_metadata
            
        except Exception as e:
            print(f"      ❌ ERROR processing post {post_id}: {e}")
            return None
    
    def _get_posts_page(self, cursor: Optional[str] = None) -> List[Dict]:
        """Get one page of posts from the Patreon API."""
        try:
            url = "https://www.patreon.com/api/posts"
            
            params = {
                'filter[campaign_id]': self.campaign_id,
                'filter[contains_exclusive_posts]': 'true',
                'filter[is_draft]': 'false',
                'sort': '-published_at',
                'include': 'attachments,audio,images,poll.choices,poll.current_user_responses.user,poll.current_user_responses.poll_choice,user,user_defined_tags,ti_checks',
                'fields[post]': 'change_visibility_at,comment_count,content,current_user_can_comment,current_user_can_view,current_user_has_liked,embed,image,is_paid,like_count,min_cents_pledged_to_view,post_file,published_at,patron_count,patreon_url,post_type,pledge_url,preview_asset_type,thumbnail_url,title,upgrade_url,url,was_posted_by_campaign_owner,has_ti_violation',
                'fields[user]': 'image_url,full_name,url',
                'fields[campaign]': 'avatar_photo_url,earnings_visibility,is_nsfw,is_monthly,name,url',
                'fields[attachment]': 'name,url',
                'fields[image]': 'height,width,url,file_name',
                'json-api-use-default-includes': 'false',
                'json-api-version': '1.0'
            }
            
            if cursor:
                params['page[cursor]'] = cursor
            
            response = self.session.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                posts = data.get('data', [])
                self.last_response = data
                return posts
            else:
                print(f"❌ API request failed: HTTP {response.status_code}")
                return []
            
        except Exception as e:
            print(f"❌ Error fetching posts: {e}")
            return []
    
    def _extract_cursor_from_response(self, posts: List[Dict]) -> Optional[str]:
        """Extract next page cursor from the API response."""
        try:
            if hasattr(self, 'last_response') and self.last_response:
                links = self.last_response.get('links', {})
                next_link = links.get('next')
                
                if next_link:
                    if 'page%5Bcursor%5D=' in next_link:
                        cursor_start = next_link.find('page%5Bcursor%5D=') + len('page%5Bcursor%5D=')
                        cursor_end = next_link.find('&', cursor_start)
                        if cursor_end == -1:
                            cursor_end = len(next_link)
                        cursor = next_link[cursor_start:cursor_end]
                        cursor = urllib.parse.unquote(cursor)
                        return cursor
                    elif 'page[cursor]=' in next_link:
                        cursor_start = next_link.find('page[cursor]=') + len('page[cursor]=')
                        cursor_end = next_link.find('&', cursor_start)
                        if cursor_end == -1:
                            cursor_end = len(next_link)
                        cursor = next_link[cursor_start:cursor_end]
                        return cursor
                
                return None
            
            return None
        except Exception as e:
            print(f"❌ Error extracting cursor: {e}")
            return None
    
    def _extract_post_metadata(self, post: Dict) -> Dict:
        """Extract comprehensive metadata from a single post."""
        attrs = post.get('attributes', {})
        post_id = post.get('id')
        
        # Basic post information
        metadata = {
            'post_id': post_id,
            'post_name': attrs.get('title', 'Untitled'),
            'revised_post_name': attrs.get('title', 'Untitled'),
            'display': True,
            'description': self._clean_html_content(attrs.get('content', '')),
            'patreon_url': attrs.get('patreon_url', ''),
            'post_type': attrs.get('post_type', ''),
            'scraped_date': datetime.now(self.sgt).isoformat(),
        }
        
        # Parse and format date
        published_str = attrs.get('published_at', '')
        if published_str:
            published_dt = datetime.fromisoformat(published_str.replace('Z', '+00:00'))
            published_sgt = published_dt.astimezone(self.sgt)
            metadata['post_date'] = published_sgt.isoformat()
        else:
            metadata['post_date'] = None
        
        # Check for ZIP file attachments
        zip_info = self._check_for_zip_files(post)
        metadata.update(zip_info)
        
        # Extract profile images information
        profile_images_info = self._get_profile_images_info(post)
        metadata.update(profile_images_info)
        
        return metadata
    
    def _clean_html_content(self, content: str) -> str:
        """Clean HTML content to extract readable text."""
        if not content or content is None:
            return ""
        
        content = str(content)
        clean_text = re.sub(r'<[^>]+>', '', content)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        if len(clean_text) > 500:
            clean_text = clean_text[:497] + "..."
        
        return clean_text
    
    def _check_for_zip_files(self, post: Dict) -> Dict:
        """Check if post has ZIP file attachments and get details."""
        post_id = post.get('id')
        
        zip_info = {
            'has_zip_files': False,
            'zip_files': []
        }
        
        zip_details = self._get_post_zip_files(post_id)
        if zip_details:
            zip_info['has_zip_files'] = True
            zip_info['zip_files'] = zip_details
        
        return zip_info
    
    def _get_post_zip_files(self, post_id: str) -> List[Dict]:
        """Get ZIP file details by making individual post API call."""
        try:
            url = f"https://www.patreon.com/api/posts/{post_id}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                zip_files = []
                
                if 'included' in data:
                    for item in data['included']:
                        if item.get('type') == 'media':
                            attrs = item.get('attributes', {})
                            file_name = attrs.get('file_name', '')
                            
                            if file_name.lower().endswith('.zip'):
                                zip_file_info = {
                                    'filename': file_name,
                                    'size_bytes': attrs.get('size_bytes'),
                                    'media_id': item.get('id'), 
                                    'download_url': f"https://www.patreon.com/file?h={post_id}&m={item.get('id')}&_rsc=1vnqu",
                                    'mimetype': attrs.get('mimetype'),
                                    'downloaded': False,
                                    'extracted': False,
                                    'download_date': None,
                                    'local_filename': None
                                }
                                
                                # Add human-readable file size
                                if zip_file_info['size_bytes']:
                                    size_mb = zip_file_info['size_bytes'] / (1024 * 1024)
                                    zip_file_info['size_mb'] = round(size_mb, 2)
                                else:
                                    zip_file_info['size_mb'] = None
                                
                                zip_files.append(zip_file_info)
                
                return zip_files
            else:
                return []
                
        except Exception as e:
            return []
    
    def _get_post_attachments(self, post_id: str) -> List[Dict]:
        """Get ZIP file attachments for downloading (matches patreon_downloader format)."""
        try:
            url = f"https://www.patreon.com/api/posts/{post_id}"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                attachments = []
                
                if 'included' in data:
                    for item in data['included']:
                        if item.get('type') == 'media':
                            attrs = item.get('attributes', {})
                            file_name = attrs.get('file_name', '')
                            
                            if file_name.lower().endswith('.zip'):
                                attachments.append({
                                    'media_id': item.get('id'),
                                    'file_name': file_name,
                                    'size_bytes': attrs.get('size_bytes'),
                                    'download_url': f"https://www.patreon.com/file?h={post_id}&m={item.get('id')}&_rsc=1vnqu"
                                })
                
                return attachments
            else:
                return []
                
        except Exception as e:
            return []
    
    def _get_profile_images_info(self, post: Dict) -> Dict:
        """Extract profile image information from post."""
        attrs = post.get('attributes', {})
        post_id = post.get('id')
        
        images_info = {
            'profile_images': [],
            'profile_images_count': 0
        }
        
        # Check for thumbnail_url (primary profile image chosen by creator)
        thumbnail_url = attrs.get('thumbnail_url')
        if thumbnail_url:
            image_data = self._create_image_info(post_id, thumbnail_url, 1, 'thumbnail')
            if image_data:
                images_info['profile_images'].append(image_data)
        else:
            # Fallback to main image if no thumbnail
            main_image = attrs.get('image')
            if main_image and isinstance(main_image, dict):
                main_image_url = main_image.get('url')
                if main_image_url:
                    image_data = self._create_image_info(post_id, main_image_url, 1, 'main')
                    if image_data:
                        images_info['profile_images'].append(image_data)
        
        images_info['profile_images_count'] = len(images_info['profile_images'])
        return images_info
    
    def _create_image_info(self, post_id: str, image_url: str, image_index: int, image_type: str) -> Dict:
        """Create standardized image information dictionary."""
        try:
            if not image_url:
                return None
            
            parsed_url = urlparse(image_url)
            original_filename = Path(parsed_url.path).name
            if not original_filename or '.' not in original_filename:
                extension = '.jpg'
                original_filename = f"image{extension}"
            else:
                extension = Path(original_filename).suffix
            
            safe_filename = f"{post_id}_{image_type}{extension}"
            
            return {
                'url': image_url,
                'filename': safe_filename,
                'index': image_index,
                'type': image_type,
                'downloaded': False
            }
        except Exception as e:
            return None
    
    def _download_single_file(self, url: str, filename: str, file_size: int = None, download_path: Path = None) -> str:
        """Download a single file with retry logic. Returns 'downloaded', 'skipped', or 'failed'."""
        if download_path is None:
            download_path = self.download_path
            
        for attempt in range(MAX_RETRIES):
            try:
                filepath = download_path / filename
                
                # Check if file already exists and is complete
                if filepath.exists():
                    existing_size = filepath.stat().st_size
                    if file_size and existing_size == file_size:
                        print(f"      ⏭️ Skipped: {filename} (already exists, {existing_size // (1024*1024)}MB)")
                        return 'skipped'
                    elif not file_size:
                        # For files without known size (like images), assume existing file is complete
                        print(f"      ⏭️ Skipped: {filename} (already exists)")
                        return 'skipped'
                    elif file_size and existing_size != file_size:
                        # File exists but size mismatch - overwrite
                        print(f"      🔄 Overwriting: {filename} (size mismatch: {existing_size // (1024*1024)}MB vs {file_size // (1024*1024)}MB expected)")
                
                # Download with streaming
                response = self.session.get(url, stream=True, timeout=30)
                response.raise_for_status()
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                return 'downloaded'
                
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    print(f"      ⚠️ Download attempt {attempt + 1} failed: {e}")
                    print(f"      🔄 Retrying in {DELAY_BETWEEN_BATCHES} seconds...")
                    time.sleep(DELAY_BETWEEN_BATCHES)
                else:
                    print(f"      ❌ All {MAX_RETRIES} attempts failed for {filename}: {e}")
                    return 'failed'
        
        return 'failed'
    
    def _download_single_image(self, image_url: str, filename: str) -> bool:
        """Download a single image file."""
        if not image_url or not filename:
            return False
        
        return self._download_single_file(image_url, filename, None, self.profile_images_path)
    
    def _add_default_custom_fields(self, post_metadata: Dict, existing_posts: Dict) -> Dict:
        """Normalize post metadata to the current app schema."""
        normalized = dict(post_metadata)

        normalized['revised_post_name'] = normalized.get('revised_post_name') or normalized.get('post_name', '')
        normalized['display'] = normalized.get('display', True)
        normalized['favourite'] = normalized.get('favourite', False)
        normalized['description'] = normalized.get('description', '') or ''
        normalized['patreon_url'] = normalized.get('patreon_url', '') or ''
        normalized['post_type'] = normalized.get('post_type', '') or ''
        normalized['post_date'] = normalized.get('post_date')
        normalized['scraped_date'] = normalized.get('scraped_date')
        normalized['has_zip_files'] = normalized.get('has_zip_files', False)
        normalized['zip_files'] = normalized.get('zip_files') or []
        normalized['profile_images'] = normalized.get('profile_images') or []
        normalized['profile_images_count'] = len(normalized['profile_images'])
        normalized['cascade_metadata'] = normalized.get('cascade_metadata') or {}

        return normalized
    
    def _save_progress(self, metadata: List[Dict], page_num: int, posts_count: int):
        """Save incremental progress."""
        try:
            progress_filepath = self.metadata_path / f"{OUTPUT_FILENAME}.json"
            
            summary = {
                'extraction_date': datetime.now(self.sgt).isoformat(),
                'status': 'IN_PROGRESS',
                'last_page_processed': page_num,
                'total_posts': len(metadata),
                'posts_with_images': len([m for m in metadata if m.get('profile_images_count', 0) > 0]),
                'total_images_downloaded': sum([m.get('profile_images_count', 0) for m in metadata]),
                'posts_with_zip_files': len([m for m in metadata if m.get('has_zip_files', False)]),
                'date_range': {
                    'earliest': min([m['post_date'] for m in metadata if m.get('post_date')]) if metadata else None,
                    'latest': max([m['post_date'] for m in metadata if m.get('post_date')]) if metadata else None
                }
            }
            
            output_data = {
                'summary': summary,
                'posts': metadata
            }
            
            safe_save_json(output_data, progress_filepath, create_backup=True)
            
        except Exception as e:
            print(f"      ⚠️ Failed to save progress: {e}")
    
    def save_metadata(self, metadata: List[Dict]):
        """Save metadata to JSON file, then generate Excel using MetadataHandler."""
        # Create summary statistics
        summary = {
            'extraction_date': datetime.now(self.sgt).isoformat(),
            'status': 'COMPLETED',
            'total_posts': len(metadata),
            'posts_with_images': len([m for m in metadata if m.get('profile_images_count', 0) > 0]),
            'total_images_downloaded': sum([m.get('profile_images_count', 0) for m in metadata]),
            'posts_with_zip_files': len([m for m in metadata if m.get('has_zip_files', False)]),
            'date_range': {
                'earliest': min([m['post_date'] for m in metadata if m.get('post_date')]) if metadata else None,
                'latest': max([m['post_date'] for m in metadata if m.get('post_date')]) if metadata else None
            }
        }
        
        # Save JSON file (source of truth)
        json_filepath = self.metadata_path / f"{OUTPUT_FILENAME}.json"
        output_data = {
            'summary': summary,
            'posts': metadata
        }
        
        safe_save_json(output_data, json_filepath, create_backup=True)
        print(f"💾 JSON metadata saved to: {json_filepath}")
        
        # Generate Excel from JSON using MetadataHandler
        if PANDAS_AVAILABLE and metadata:
            try:
                # Import MetadataHandler
                import sys
                sys.path.insert(0, str(self.project_root))
                from shared.metadata_handler import MetadataHandler
                
                print(f"🔄 Converting JSON to Excel using MetadataHandler...")
                handler = MetadataHandler()
                success = handler.json_to_excel(create_backup=True)
                
                if not success:
                    print(f"⚠️  Excel generation failed, but JSON was saved successfully")
                    
            except Exception as e:
                print(f"⚠️  Failed to generate Excel using MetadataHandler: {e}")
                print(f"💡 JSON file was saved successfully. You can manually run metadata_handler.py later.")
        
        # Print summary
        print(f"📊 Summary:")
        print(f"   • Total posts: {summary['total_posts']}")
        print(f"   • Posts with images: {summary['posts_with_images']}")
        print(f"   • Total images downloaded: {summary['total_images_downloaded']}")
        print(f"   • Posts with ZIP files: {summary['posts_with_zip_files']}")
    
    def run(self):
        """Run the integrated pipeline."""
        try:
            # Step 1: Authentication
            if not self.setup_authentication():
                print(f"\n❌ Pipeline aborted: Authentication failed")
                return
            
            # Step 2: Process posts with integrated scraping
            result = self.process_posts_integrated(START_DATE, END_DATE)
            
            if result and result.get('total_in_range', 0) > 0:
                print(f"\n✅ Pipeline completed successfully!")
                print(f"   📊 Total posts processed: {result['total_in_range']}")
                print(f"   📄 Pages processed: {result['pages_processed']}")
            else:
                print(f"\n⚠️ No posts found in specified date range")
            
            # Final summary
            print(f"\n🎉 Integrated Pipeline Completed!")
            print(f"📁 Check these folders for results:")
            if SCRAPE_METADATA:
                print(f"   • {self.metadata_path} - Metadata files")
            if SCRAPE_ZIP_FILES:
                print(f"   • {self.download_path} - Downloaded ZIP files")
            if SCRAPE_PROFILE_IMAGES:
                print(f"   • {self.profile_images_path} - Downloaded images")
            
        except KeyboardInterrupt:
            print(f"\n⏸️ Pipeline interrupted by user")
        except Exception as e:
            print(f"\n❌ Pipeline error: {e}")
            import traceback
            traceback.print_exc()


def main():
    """Main entry point."""
    print(f"⚙️  Configuration Summary:")
    print(f"   • Creator: {CREATOR_SLUG}")
    print(f"   • Date Range: {START_DATE or 'No limit'} to {END_DATE or 'No limit'}")
    print(f"   • Scraping: {'Metadata' if SCRAPE_METADATA else ''}{'+ ZIP' if SCRAPE_ZIP_FILES else ''}{'+ Images' if SCRAPE_PROFILE_IMAGES else ''}")
    
    pipeline = IntegratedPatreonPipeline()
    pipeline.run()


if __name__ == "__main__":
    print(f"🚀 Starting VAMA Integrated Pipeline")
    print(f"   • Creator: {CREATOR_SLUG}")
    print(f"   • Date range: {START_DATE or 'all'} to {END_DATE or 'today'}")
    print(f"   • Scraping: {'Metadata' if SCRAPE_METADATA else ''}{'+ ZIP' if SCRAPE_ZIP_FILES else ''}{'+ Images' if SCRAPE_PROFILE_IMAGES else ''}")
    print()
    
    pipeline = IntegratedPatreonPipeline()
    success = pipeline.run()
    sys.exit(0 if success else 1)