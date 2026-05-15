"""SQLite database for tracking photo sync state."""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


class PhotoDatabase:
    """SQLite database for tracking photo sync state."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS photos (
                item_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                filesize INTEGER,
                taken_time INTEGER,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                downloaded INTEGER DEFAULT 0,
                download_failed INTEGER DEFAULT 0,
                h_filename TEXT,
                v_filename TEXT,
                media_type TEXT DEFAULT 'photo'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time TEXT NOT NULL,
                photos_scanned INTEGER,
                downloaded INTEGER,
                processed INTEGER,
                success INTEGER
            )
        ''')
        self.conn.commit()
        self._migrate_item_id_to_text()
        self._migrate_add_media_type()

    def _migrate_item_id_to_text(self):
        """Migrate item_id column from INTEGER to TEXT if needed.

        Existing Synology rows get a 'syn_' prefix on their IDs.
        """
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(photos)")
        columns = cursor.fetchall()
        for col in columns:
            # col: (cid, name, type, notnull, default, pk)
            if col[1] == 'item_id' and col[2].upper() == 'INTEGER':
                logger.info("Migrating photos table: item_id INTEGER -> TEXT")
                cursor.execute('''
                    CREATE TABLE photos_new (
                        item_id TEXT PRIMARY KEY,
                        filename TEXT NOT NULL,
                        filesize INTEGER,
                        taken_time INTEGER,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        downloaded INTEGER DEFAULT 0,
                        download_failed INTEGER DEFAULT 0,
                        h_filename TEXT,
                        v_filename TEXT
                    )
                ''')
                cursor.execute('''
                    INSERT INTO photos_new
                    SELECT 'syn_' || CAST(item_id AS TEXT),
                           filename, filesize, taken_time,
                           first_seen, last_seen, downloaded,
                           download_failed, h_filename, v_filename
                    FROM photos
                ''')
                cursor.execute('DROP TABLE photos')
                cursor.execute('ALTER TABLE photos_new RENAME TO photos')
                self.conn.commit()
                logger.info("Migration complete")
                break

    def _migrate_add_media_type(self):
        """Add media_type column to existing photos tables."""
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(photos)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'media_type' not in columns:
            logger.info("Migrating photos table: adding media_type column")
            cursor.execute("ALTER TABLE photos ADD COLUMN media_type TEXT DEFAULT 'photo'")
            self.conn.commit()

    def update_items(self, items: List[Dict[str, Any]]) -> List[Tuple[Optional[str], Optional[str]]]:
        """Update database with items from the API.

        Returns list of (h_filename, v_filename) tuples for removed (stale) items.
        """
        cursor = self.conn.cursor()
        now = datetime.now().isoformat()

        for item in items:
            cursor.execute('''
                INSERT INTO photos (item_id, filename, filesize, taken_time,
                                    first_seen, last_seen, media_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    filename = excluded.filename,
                    filesize = excluded.filesize,
                    media_type = excluded.media_type
            ''', (
                item['id'],
                item.get('filename', ''),
                item.get('filesize', 0),
                item.get('time', 0),
                now,
                now,
                item.get('media_type', 'photo'),
            ))

        # Find stale entries (no longer in any album)
        item_ids = {item['id'] for item in items}
        cursor.execute('SELECT item_id, h_filename, v_filename FROM photos')
        all_rows = cursor.fetchall()
        stale_files = []
        stale_ids = []
        for row in all_rows:
            if row['item_id'] not in item_ids:
                stale_ids.append(row['item_id'])
                stale_files.append((row['h_filename'], row['v_filename']))

        # Delete stale rows
        if stale_ids:
            cursor.executemany(
                'DELETE FROM photos WHERE item_id = ?',
                [(i,) for i in stale_ids],
            )
            logger.info(f"Removed {len(stale_ids)} stale entries from database")

        self.conn.commit()
        logger.info(f"Updated {len(items)} items in database")
        return stale_files

    def get_unprocessed(self, orientation: str) -> List[Dict[str, Any]]:
        """Get items not yet processed for the given orientation (with < 3 failures).

        Also returns 'other_filename' so caller knows if the other orientation exists.
        """
        col = 'h_filename' if orientation == 'horizontal' else 'v_filename'
        other_col = 'v_filename' if orientation == 'horizontal' else 'h_filename'
        cursor = self.conn.cursor()
        cursor.execute(f'''
            SELECT item_id, filename, filesize, media_type,
                   {other_col} as other_filename
            FROM photos
            WHERE {col} IS NULL AND download_failed < 3
        ''')
        return [dict(row) for row in cursor.fetchall()]

    def mark_processed(self, item_id, h_filename: str = None, v_filename: str = None):
        """Mark orientation-specific filenames after processing."""
        cursor = self.conn.cursor()
        updates = ['downloaded = 1']
        params = []
        if h_filename is not None:
            updates.append('h_filename = ?')
            params.append(h_filename)
        if v_filename is not None:
            updates.append('v_filename = ?')
            params.append(v_filename)
        params.append(item_id)
        cursor.execute(
            f'UPDATE photos SET {", ".join(updates)} WHERE item_id = ?', params)
        self.conn.commit()

    def mark_failed(self, item_id):
        """Increment the failure counter for an item."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE photos
            SET download_failed = download_failed + 1
            WHERE item_id = ?
        ''', (item_id,))
        self.conn.commit()

    def clear_all(self):
        """Remove all entries from the photos table."""
        self.conn.execute('DELETE FROM photos')
        self.conn.commit()
        logger.info("Cleared all entries from database")

    def cleanup_orientation(self, orientation: str, keep_count: int, base_dir: Path):
        """Delete processed files for an orientation beyond keep_count, clear DB refs.

        Returns the number of files deleted.
        """
        photo_dir = base_dir / orientation
        if not photo_dir.exists():
            return 0

        media = sorted(list(photo_dir.glob('*.jpg')) + list(photo_dir.glob('*.mp4')))
        if len(media) <= keep_count:
            return 0

        to_delete = media[keep_count:]
        deleted_names = set()
        for p in to_delete:
            deleted_names.add(p.name)
            p.unlink(missing_ok=True)

        # Clear DB references for deleted files
        col = 'h_filename' if orientation == 'horizontal' else 'v_filename'
        cursor = self.conn.cursor()
        for name in deleted_names:
            cursor.execute(f'UPDATE photos SET {col} = NULL WHERE {col} = ?', (name,))
        self.conn.commit()

        logger.info(f"Cleaned {len(to_delete)} {orientation} photos (kept {keep_count})")
        return len(to_delete)

    def get_counts(self) -> Dict[str, int]:
        """Get photo counts for status display."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) as total FROM photos')
        total = cursor.fetchone()['total']
        cursor.execute('SELECT COUNT(*) as done FROM photos WHERE downloaded = 1')
        done = cursor.fetchone()['done']
        cursor.execute('SELECT COUNT(*) as pending FROM photos WHERE downloaded = 0 AND download_failed < 3')
        pending = cursor.fetchone()['pending']
        return {'total': total, 'downloaded': done, 'pending': pending}

    def get_last_run(self) -> Optional[Dict[str, Any]]:
        """Get the most recent sync run info."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1')
        row = cursor.fetchone()
        return dict(row) if row else None

    def record_run(self, scanned: int, downloaded: int, processed: int, success: bool):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO sync_runs (run_time, photos_scanned, downloaded, processed, success)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), scanned, downloaded, processed, int(success)))
        self.conn.commit()

    def close(self):
        self.conn.close()
