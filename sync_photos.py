#!/usr/bin/env python3
"""
Synology Photos sync script for digital photo frame.
Downloads photos from a shared Synology Photos album to local storage.
"""

import sys
import os
import sqlite3
import logging
import requests
import yaml
import hashlib
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SynologyPhotosClient:
    """Client for Synology Photos API via public share links."""
    
    def __init__(self, base_url: str, share_url: str, passphrase: str):
        self.base_url = base_url.rstrip('/')
        self.share_url = share_url
        self.passphrase = passphrase
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36'
        })
        
        # Extract share token from URL
        self.share_token = self._extract_share_token(share_url)
        self._sharing_id = None
        logger.info(f"Initialized client with share token: {self.share_token}")
    
    def _extract_share_token(self, share_url: str) -> str:
        """Extract the share token from the share URL."""
        parsed = urlparse(share_url)
        path_parts = parsed.path.strip('/').split('/')
        if 'sharing' in path_parts:
            idx = path_parts.index('sharing')
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
        raise ValueError(f"Could not extract share token from: {share_url}")
    
    def initialize_share(self) -> bool:
        """Initialize the share session and authenticate with passphrase."""
        try:
            # Step 1: GET the share page to establish session
            logger.info("Initializing share session...")
            resp = self.session.get(self.share_url, allow_redirects=True)
            resp.raise_for_status()
            
            # The share token might also be used as sharing_id
            self._sharing_id = self.share_token
            
            logger.info(f"Share session initialized. Sharing ID: {self._sharing_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize share: {e}")
            return False
    
    def list_items(self, offset: int = 0, limit: int = 100) -> Optional[Dict[str, Any]]:
        """List items in the shared album."""
        try:
            url = urljoin(self.base_url, '/webapi/entry.cgi')
            
            data = {
                'api': 'SYNO.Foto.Browse.Item',
                'method': 'list',
                'version': '4',
                'offset': offset,
                'limit': limit,
                'sort_by': 'takentime',
                'sort_direction': 'asc',
                'passphrase': self.passphrase,
                '_sharing_id': self._sharing_id,
            }
            
            logger.debug(f"Listing items: offset={offset}, limit={limit}")
            resp = self.session.post(url, data=data)
            resp.raise_for_status()
            
            result = resp.json()
            if not result.get('success'):
                logger.error(f"API returned error: {result}")
                return None
            
            return result.get('data', {})
            
        except Exception as e:
            logger.error(f"Failed to list items: {e}")
            return None
    
    def get_all_items(self, include_videos: bool = False) -> List[Dict[str, Any]]:
        """Get all items from the shared album with pagination."""
        all_items = []
        offset = 0
        limit = 100
        
        while True:
            data = self.list_items(offset=offset, limit=limit)
            if not data:
                break
            
            items = data.get('list', [])
            if not items:
                break
            
            # Filter by type
            for item in items:
                if item.get('type') == 'photo':
                    all_items.append(item)
                elif item.get('type') == 'video' and include_videos:
                    all_items.append(item)
            
            logger.info(f"Fetched {len(items)} items (offset={offset}), total so far: {len(all_items)}")
            
            # Check if we've reached the end
            if len(items) < limit:
                break
            
            offset += limit
            time.sleep(0.5)  # Be nice to the server
        
        logger.info(f"Total items fetched: {len(all_items)}")
        return all_items
    
    def download_item(self, item_id: int, output_path: Path) -> bool:
        """Download a single item to the specified path."""
        try:
            url = urljoin(self.base_url, '/webapi/entry.cgi')
            
            data = {
                'api': 'SYNO.Foto.Download',
                'method': 'download',
                'version': '2',
                'item_id': f'[{item_id}]',
                'passphrase': self.passphrase,
                '_sharing_id': self._sharing_id,
                'download_type': 'source',
                'force_download': 'true',
            }
            
            logger.debug(f"Downloading item {item_id} to {output_path}")
            resp = self.session.post(url, data=data, stream=True)
            resp.raise_for_status()
            
            # Write to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logger.info(f"Downloaded item {item_id} ({output_path.name})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to download item {item_id}: {e}")
            return False


class PhotoDatabase:
    """SQLite database for tracking photo state."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                item_id INTEGER UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                type TEXT NOT NULL,
                filesize INTEGER,
                taken_time INTEGER,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                times_shown INTEGER DEFAULT 0,
                last_shown_week TEXT,
                last_shown_ts INTEGER
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_time TEXT NOT NULL,
                items_fetched INTEGER,
                items_selected INTEGER,
                items_downloaded INTEGER,
                success INTEGER
            )
        ''')
        self.conn.commit()
    
    def update_items(self, items: List[Dict[str, Any]]):
        """Update items in database."""
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()
        
        for item in items:
            cursor.execute('''
                INSERT INTO items (item_id, filename, type, filesize, taken_time, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    filename=excluded.filename,
                    filesize=excluded.filesize
            ''', (
                item['id'],
                item.get('filename', ''),
                item.get('type', ''),
                item.get('filesize', 0),
                item.get('time', 0),
                now,
                now
            ))
        
        self.conn.commit()
        logger.info(f"Updated {len(items)} items in database")
    
    def get_weighted_selection(self, count: int, max_show_count: int = 10) -> List[int]:
        """Get a weighted random selection of items."""
        cursor = self.conn.cursor()
        
        # Get all eligible items with their weights
        current_week = datetime.now().strftime('%Y-W%W')
        cursor.execute('''
            SELECT item_id, times_shown, last_shown_week
            FROM items
            WHERE type = 'photo' AND times_shown < ?
        ''', (max_show_count,))
        
        items = cursor.fetchall()
        if not items:
            logger.warning("No items available for selection")
            return []
        
        # Calculate weights (inverse of times_shown + time since last shown)
        weighted_items = []
        for item in items:
            item_id = item['item_id']
            times_shown = item['times_shown'] or 0
            last_week = item['last_shown_week']
            
            # Base weight inversely proportional to times shown
            weight = max(1, max_show_count - times_shown)
            
            # Boost if not shown this week
            if last_week != current_week:
                weight *= 2
            
            weighted_items.append((item_id, weight))
        
        # Perform weighted random selection without replacement
        selected_ids = []
        total_weight = sum(w for _, w in weighted_items)
        
        for _ in range(min(count, len(weighted_items))):
            if total_weight <= 0:
                break
            
            # Random selection
            r = random.random() * total_weight
            cumsum = 0
            for idx, (item_id, weight) in enumerate(weighted_items):
                cumsum += weight
                if cumsum >= r:
                    selected_ids.append(item_id)
                    total_weight -= weight
                    weighted_items.pop(idx)
                    break
        
        logger.info(f"Selected {len(selected_ids)} items from {len(items)} eligible")
        return selected_ids
    
    def mark_shown(self, item_ids: List[int]):
        """Mark items as shown in current week."""
        cursor = self.conn.cursor()
        current_week = datetime.now().strftime('%Y-W%W')
        now_ts = int(time.time())
        
        for item_id in item_ids:
            cursor.execute('''
                UPDATE items
                SET times_shown = times_shown + 1,
                    last_shown_week = ?,
                    last_shown_ts = ?
                WHERE item_id = ?
            ''', (current_week, now_ts, item_id))
        
        self.conn.commit()
        logger.info(f"Marked {len(item_ids)} items as shown")
    
    def record_sync(self, items_fetched: int, items_selected: int, items_downloaded: int, success: bool):
        """Record sync history."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO sync_history (sync_time, items_fetched, items_selected, items_downloaded, success)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), items_fetched, items_selected, items_downloaded, int(success)))
        self.conn.commit()
    
    def close(self):
        """Close database connection."""
        self.conn.close()


def load_config(config_path: str = 'config_synology.yaml') -> dict:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def sync_photos(config: dict):
    """Main sync function."""
    logger.info("="*80)
    logger.info("Starting photo sync")
    logger.info("="*80)
    
    # Extract config
    synology_config = config['synology']
    sync_config = config['sync']
    
    # Initialize client
    client = SynologyPhotosClient(
        base_url=synology_config['base_url'],
        share_url=synology_config['share_url'],
        passphrase=synology_config['share_passphrase']
    )
    
    # Initialize database
    db = PhotoDatabase(sync_config['state_db'])
    
    try:
        # Initialize share session
        if not client.initialize_share():
            raise Exception("Failed to initialize share session")
        
        # Fetch all items
        items = client.get_all_items(include_videos=sync_config['include_videos'])
        if not items:
            raise Exception("No items fetched")
        
        # Update database
        db.update_items(items)
        
        # Select items for this week
        selected_ids = db.get_weighted_selection(
            count=sync_config['photos_per_week'],
            max_show_count=sync_config['max_show_count']
        )
        
        if not selected_ids:
            logger.warning("No items selected")
            db.record_sync(len(items), 0, 0, False)
            return
        
        # Create photos directory
        photos_dir = Path(sync_config['photos_dir'])
        photos_dir.mkdir(parents=True, exist_ok=True)
        
        # Clear old photos
        logger.info("Clearing old photos...")
        for old_file in photos_dir.glob('*'):
            if old_file.is_file():
                old_file.unlink()
        
        # Download selected items
        downloaded_count = 0
        item_lookup = {item['id']: item for item in items}
        
        for item_id in selected_ids:
            item = item_lookup.get(item_id)
            if not item:
                continue
            
            filename = item.get('filename', f'photo_{item_id}.jpg')
            output_path = photos_dir / filename
            
            if client.download_item(item_id, output_path):
                downloaded_count += 1
            
            time.sleep(0.2)  # Rate limiting
        
        # Mark as shown
        db.mark_shown(selected_ids)
        
        # Record sync
        db.record_sync(len(items), len(selected_ids), downloaded_count, True)
        
        logger.info(f"Sync completed successfully: {downloaded_count}/{len(selected_ids)} downloaded")
        
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        db.record_sync(0, 0, 0, False)
        raise
    
    finally:
        db.close()


def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config_synology.yaml'
    
    try:
        config = load_config(config_path)
        sync_photos(config)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
