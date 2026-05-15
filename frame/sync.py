"""Photo sync from multiple sources to local storage."""

import sys
import time
import shutil
import logging
import threading
from pathlib import Path
from typing import Dict, Any

import yaml

from frame.clients import (SynologyPhotosClient, GooglePhotosClient, ImmichClient,
                            ICloudSharedAlbumClient, NextcloudClient)
from frame.database import PhotoDatabase
from frame.processing import process_photo_in_subprocess
from frame.video import transcode_video_in_subprocess

logger = logging.getLogger(__name__)


def _list_media(orient_dir: Path):
    """List all media files (photos + videos) in an orientation directory."""
    if not orient_dir.exists():
        return []
    return list(orient_dir.glob('*.jpg')) + list(orient_dir.glob('*.mp4'))


def _count_media(orient_dir: Path) -> int:
    return len(_list_media(orient_dir))


# Register HEIF opener if available
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


class PhotoSyncer:
    """Manages photo sync from Synology Photos to local storage."""

    def __init__(self, config: dict):
        self._config = config
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._phase = 'idle'
        self._progress = {}
        self._last_error = None
        self._thread = None

    def run_sync(self):
        """Start a sync in a background thread. No-op if already running."""
        with self._lock:
            if self._running:
                logger.info("Sync already running, skipping")
                return
            self._running = True
            self._stop_event.clear()
            self._last_error = None

        self._thread = threading.Thread(target=self._sync_worker, daemon=True)
        self._thread.start()

    def stop(self):
        """Request the running sync to stop."""
        self._stop_event.set()

    def get_status(self) -> dict:
        """Return current sync status, including pending count from DB when idle."""
        with self._lock:
            photos_config = self._config.get('photos', {})
            base_dir = Path(photos_config.get('base_dir', '/srv/frame/photos'))

            h_count = _count_media(base_dir / 'horizontal')
            v_count = _count_media(base_dir / 'vertical')

            status = {
                'running': self._running,
                'phase': self._phase,
                'h_photos': h_count,
                'v_photos': v_count,
                'error': self._last_error,
            }
            status.update(self._progress)

            # When idle, query DB for pending count so settings always shows the queue
            if not self._running and 'pending' not in status:
                try:
                    db_path = photos_config.get('state_db', str(base_dir / 'state.db'))
                    if Path(db_path).exists():
                        db = PhotoDatabase(db_path)
                        counts = db.get_counts()
                        status['pending'] = counts.get('pending', 0)
                        status['total'] = counts.get('total', 0)
                        db.close()
                except Exception:
                    pass

            return status

    def _sync_worker(self):
        """Main sync logic — runs in background thread."""
        synology_config = self._config.get('synology', {})
        photos_config = self._config.get('photos', {})

        base_dir = Path(photos_config.get('base_dir', '/srv/frame/photos'))
        db_path = photos_config.get('state_db', str(base_dir / 'state.db'))
        tmp_dir = Path(photos_config.get('tmp_dir', '/tmp/frame_downloads'))

        h_size = (photos_config.get('horizontal', {}).get('width', 1920),
                  photos_config.get('horizontal', {}).get('height', 1200))
        v_size = (photos_config.get('vertical', {}).get('width', 1200),
                  photos_config.get('vertical', {}).get('height', 1920))
        blur_radius = photos_config.get('blur_radius', 40)
        blur_darken = photos_config.get('blur_darken', 0.6)
        quality = photos_config.get('quality', 85)

        video_config = self._config.get('videos', {})
        video_max_duration = video_config.get('max_duration', 120)
        video_max_filesize_mb = video_config.get('max_filesize_mb', 100)
        videos_enabled = video_config.get('enabled', True)

        # Ensure directories
        (base_dir / 'horizontal').mkdir(parents=True, exist_ok=True)
        (base_dir / 'vertical').mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        db = PhotoDatabase(db_path)

        google_config = self._config.get('google_photos', {})
        immich_config = self._config.get('immich', {})
        icloud_config = self._config.get('icloud', {})
        nextcloud_config = self._config.get('nextcloud', {})

        try:
            # --- Phase 1: Connect to albums and list all photos ---
            self._set_phase('listing')
            share_urls = synology_config.get('share_urls', [])
            share_passphrases = synology_config.get('share_passphrases', [])

            if len(share_urls) != len(share_passphrases):
                raise ValueError("share_urls and share_passphrases must have the same length")

            syn_albums = [(url, pw) for url, pw in zip(share_urls, share_passphrases) if url]
            gph_urls = [u for u in google_config.get('share_urls', []) if u]

            imm_urls = immich_config.get('share_urls', [])
            imm_passes = immich_config.get('share_passphrases', [])
            # Pad passphrases to match URLs length
            while len(imm_passes) < len(imm_urls):
                imm_passes.append('')
            imm_albums = [(url, pw) for url, pw in zip(imm_urls, imm_passes) if url]

            icl_urls = [u for u in icloud_config.get('share_urls', []) if u]

            nc_urls = nextcloud_config.get('share_urls', [])
            nc_passes = nextcloud_config.get('share_passphrases', [])
            while len(nc_passes) < len(nc_urls):
                nc_passes.append('')
            nc_albums = [(url, pw) for url, pw in zip(nc_urls, nc_passes) if url]

            if not syn_albums and not gph_urls and not imm_albums and not icl_urls and not nc_albums:
                logger.info("No share URLs configured — cleaning media, restoring defaults")
                for orient in ('horizontal', 'vertical'):
                    orient_dir = base_dir / orient
                    for f in _list_media(orient_dir):
                        if not f.name.startswith('default_'):
                            f.unlink(missing_ok=True)
                db.clear_all()
                self._restore_defaults(base_dir)
                self._set_phase('idle')
                return

            all_items = []
            item_client_map = {}  # item_id -> (client_type, client_index)
            syn_clients = []
            gph_clients = []
            imm_clients = []
            icl_clients = []
            nc_clients = []

            # -- Synology albums --
            for album_idx, (share_url, passphrase) in enumerate(syn_albums):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    return

                logger.info(f"Synology album {album_idx + 1}/{len(syn_albums)}...")
                client = SynologyPhotosClient(
                    share_url=share_url,
                    passphrase=passphrase,
                )

                if not client.initialize_share():
                    logger.error(f"Failed to initialize Synology album {album_idx + 1}, skipping")
                    continue

                items = client.get_all_items()
                logger.info(f"Synology album {album_idx + 1}: {len(items)} photos")

                for item in items:
                    # Prefix Synology IDs so they don't collide with Google IDs
                    item['id'] = f"syn_{item['id']}"
                    item_client_map[item['id']] = ('synology', len(syn_clients))
                all_items.extend(items)
                syn_clients.append(client)

            # -- Google Photos albums --
            for gph_idx, gph_url in enumerate(gph_urls):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    return

                logger.info(f"Google Photos album {gph_idx + 1}/{len(gph_urls)}...")
                try:
                    client = GooglePhotosClient(share_url=gph_url)
                    items = client.get_all_items()
                    logger.info(f"Google Photos album {gph_idx + 1}: {len(items)} photos")

                    for item in items:
                        item_client_map[item['id']] = ('google', len(gph_clients))
                    all_items.extend(items)
                    gph_clients.append(client)
                except Exception as e:
                    logger.error(f"Failed to fetch Google Photos album {gph_idx + 1}: {e}")

            # -- Immich albums --
            for imm_idx, (imm_url, imm_pass) in enumerate(imm_albums):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    return

                logger.info(f"Immich album {imm_idx + 1}/{len(imm_albums)}...")
                try:
                    client = ImmichClient(share_url=imm_url, passphrase=imm_pass)
                    if not client.initialize_share():
                        logger.error(f"Failed to initialize Immich album {imm_idx + 1}, skipping")
                        continue

                    items = client.get_all_items()
                    logger.info(f"Immich album {imm_idx + 1}: {len(items)} photos")

                    for item in items:
                        item_client_map[item['id']] = ('immich', len(imm_clients))
                    all_items.extend(items)
                    imm_clients.append(client)
                except Exception as e:
                    logger.error(f"Failed to fetch Immich album {imm_idx + 1}: {e}")

            # -- iCloud albums --
            for icl_idx, icl_url in enumerate(icl_urls):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    return

                logger.info(f"iCloud album {icl_idx + 1}/{len(icl_urls)}...")
                try:
                    client = ICloudSharedAlbumClient(share_url=icl_url)
                    if not client.initialize_share():
                        logger.error(f"Failed to initialize iCloud album {icl_idx + 1}, skipping")
                        continue

                    items = client.get_all_items()
                    logger.info(f"iCloud album {icl_idx + 1}: {len(items)} photos")

                    for item in items:
                        item_client_map[item['id']] = ('icloud', len(icl_clients))
                    all_items.extend(items)
                    icl_clients.append(client)
                except Exception as e:
                    logger.error(f"Failed to fetch iCloud album {icl_idx + 1}: {e}")

            # -- Nextcloud albums --
            for nc_idx, (nc_url, nc_pass) in enumerate(nc_albums):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    return

                logger.info(f"Nextcloud album {nc_idx + 1}/{len(nc_albums)}...")
                try:
                    client = NextcloudClient(share_url=nc_url, passphrase=nc_pass)
                    if not client.initialize_share():
                        logger.error(f"Failed to initialize Nextcloud album {nc_idx + 1}, skipping")
                        continue

                    items = client.get_all_items()
                    logger.info(f"Nextcloud album {nc_idx + 1}: {len(items)} photos")

                    for item in items:
                        item_client_map[item['id']] = ('nextcloud', len(nc_clients))
                    all_items.extend(items)
                    nc_clients.append(client)
                except Exception as e:
                    logger.error(f"Failed to fetch Nextcloud album {nc_idx + 1}: {e}")

            if not all_items:
                logger.info("No items found in any album — restoring defaults")
                for orient in ('horizontal', 'vertical'):
                    orient_dir = base_dir / orient
                    for f in _list_media(orient_dir):
                        if not f.name.startswith('default_'):
                            f.unlink(missing_ok=True)
                self._restore_defaults(base_dir)
                self._set_phase('idle')
                return

            logger.info(f"Total photos across all albums: {len(all_items)}")

            # --- Phase 2: Update database, clean stale files ---
            self._set_phase('updating_db')
            stale_files = db.update_items(all_items)

            # Clean up processed files for removed photos
            if stale_files:
                for h_fn, v_fn in stale_files:
                    if h_fn:
                        (base_dir / 'horizontal' / h_fn).unlink(missing_ok=True)
                    if v_fn:
                        (base_dir / 'vertical' / v_fn).unlink(missing_ok=True)
                logger.info(f"Cleaned {len(stale_files)} stale photo files")

            # --- Phase 3: Download and process new photos ---
            self._set_phase('downloading')

            # Only process into the current orientation; keep ≤100 in the other
            orientation = self._config.get('frame', {}).get('orientation', 'horizontal')
            other_orientation = 'vertical' if orientation == 'horizontal' else 'horizontal'
            other_dir = base_dir / other_orientation
            other_count = _count_media(other_dir)
            other_limit = 100

            logger.info(f"Sync orientation: {orientation} (other has {other_count}/{other_limit})")

            unprocessed = db.get_unprocessed(orientation)
            total = len(unprocessed)
            downloaded = 0
            processed = 0
            t_start = time.time()

            if total:
                logger.info(f"Processing {total} pending photos")

            self._progress = {'total': len(all_items), 'pending': total, 'downloaded': 0}

            # Build download_url lookup for Google/iCloud items
            download_urls = {}
            for ai in all_items:
                if '_download_url' in ai:
                    download_urls[ai['id']] = ai['_download_url']

            # Build webdav_path lookup for Nextcloud items
            nc_webdav_paths = {}
            for ai in all_items:
                if '_webdav_path' in ai:
                    nc_webdav_paths[ai['id']] = ai['_webdav_path']

            for idx, item in enumerate(unprocessed):
                if self._stop_event.is_set():
                    logger.info("Sync stopped by request")
                    break

                item_id = item['item_id']
                filename = item['filename']
                download_path = tmp_dir / filename

                client_info = item_client_map.get(item_id)
                if client_info is None:
                    logger.warning(f"Skipping item {item_id}: no client found")
                    db.mark_failed(item_id)
                    continue

                client_type, client_idx = client_info

                # Download using the appropriate client
                if client_type in ('google', 'icloud'):
                    download_url = download_urls.get(item_id)
                    if not download_url:
                        logger.warning(f"Skipping {client_type} item {item_id}: no download URL")
                        db.mark_failed(item_id)
                        continue
                    if client_type == 'google':
                        client = gph_clients[client_idx]
                    else:
                        client = icl_clients[client_idx]
                    ok = client.download_item(download_url, download_path)
                elif client_type == 'nextcloud':
                    webdav_path = nc_webdav_paths.get(item_id)
                    if not webdav_path:
                        logger.warning(f"Skipping Nextcloud item {item_id}: no webdav path")
                        db.mark_failed(item_id)
                        continue
                    client = nc_clients[client_idx]
                    ok = client.download_item(webdav_path, download_path)
                elif client_type == 'immich':
                    client = imm_clients[client_idx]
                    asset_uuid = item_id.replace('imm_', '')
                    ok = client.download_item(asset_uuid, download_path)
                else:
                    client = syn_clients[client_idx]
                    syn_id = int(item_id.replace('syn_', ''))
                    ok = client.download_item(syn_id, download_path)

                if not ok:
                    logger.warning(f"Skipping item {item_id}: download failed")
                    db.mark_failed(item_id)
                    download_path.unlink(missing_ok=True)
                    continue

                downloaded += 1

                # Decide which orientations to process
                process_orientations = [orientation]
                if other_count < other_limit and not item.get('other_filename'):
                    process_orientations.append(other_orientation)
                    other_count += 1

                media_type = item.get('media_type', 'photo')
                if media_type == 'video':
                    if not videos_enabled:
                        logger.info(f"Skipping video {filename}: videos disabled")
                        download_path.unlink(missing_ok=True)
                        db.mark_failed(item_id)
                        continue
                    result = transcode_video_in_subprocess(
                        download_path, base_dir, item_id, filename,
                        h_size, v_size, blur_radius,
                        orientations=tuple(process_orientations),
                        max_duration=video_max_duration,
                        max_filesize_mb=video_max_filesize_mb,
                    )
                else:
                    result = process_photo_in_subprocess(
                        download_path, base_dir, item_id, filename,
                        h_size, v_size, blur_radius, blur_darken, quality,
                        orientations=tuple(process_orientations),
                    )

                download_path.unlink(missing_ok=True)

                if result:
                    h_fn, v_fn = result
                    db.mark_processed(item_id, h_fn, v_fn)
                    processed += 1
                else:
                    db.mark_failed(item_id)

                self._progress = {
                    'total': len(all_items),
                    'pending': total - idx - 1,
                    'downloaded': downloaded,
                }

                if (idx + 1) % 20 == 0:
                    elapsed = time.time() - t_start
                    per_photo = elapsed / (idx + 1)
                    remaining = per_photo * (total - idx - 1)
                    logger.info(
                        f"Progress: {idx + 1}/{total} "
                        f"({processed} OK, ~{remaining:.0f}s remaining)"
                    )

                time.sleep(0.2)

            db.record_run(len(all_items), downloaded, processed, True)
            elapsed = time.time() - t_start
            logger.info(f"Sync done: {processed}/{total} new photos in {elapsed:.1f}s")

            # Remove default placeholder photos once real media exists
            for orient in ('horizontal', 'vertical'):
                orient_dir = base_dir / orient
                real_media = [f for f in _list_media(orient_dir)
                              if not f.name.startswith('default_')]
                if real_media:
                    for default_file in orient_dir.glob('default_*.jpg'):
                        default_file.unlink(missing_ok=True)
                        logger.info(f"Removed default placeholder: {default_file.name}")

            self._set_phase('idle')

        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            self._last_error = str(e)
            db.record_run(0, 0, 0, False)
            self._set_phase('error')
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            db.close()
            with self._lock:
                self._running = False

    def _restore_defaults(self, base_dir: Path):
        """Restore default placeholder photos if no real photos exist."""
        # Find defaults source — check relative to this file's package
        defaults_dir = Path(__file__).resolve().parent.parent / 'viewer' / 'defaults'
        if not defaults_dir.exists():
            logger.warning(f"Defaults directory not found: {defaults_dir}")
            return

        for orient in ('horizontal', 'vertical'):
            orient_dir = base_dir / orient
            orient_dir.mkdir(parents=True, exist_ok=True)

            # Count non-default media
            real_media = [f for f in _list_media(orient_dir)
                          if not f.name.startswith('default_')]
            if real_media:
                continue  # Has real media, skip

            # Check if defaults already present
            existing_defaults = list(orient_dir.glob('default_*.jpg'))
            if existing_defaults:
                continue

            # Copy defaults
            src_dir = defaults_dir / orient
            if not src_dir.exists():
                continue
            count = 0
            for src_file in src_dir.glob('*.jpg'):
                shutil.copy2(src_file, orient_dir / src_file.name)
                count += 1
            if count:
                logger.info(f"Restored {count} default photos to {orient}/")

    def _set_phase(self, phase: str):
        with self._lock:
            self._phase = phase


def main():
    """Standalone sync entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config_frame.yaml'

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO').upper())
    log_file = log_config.get('file', '')

    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
    )

    syncer = PhotoSyncer(config)
    syncer.run_sync()

    # Wait for completion
    while syncer.get_status()['running']:
        time.sleep(2)

    status = syncer.get_status()
    if status.get('error'):
        logger.error(f"Sync finished with error: {status['error']}")
        sys.exit(1)
    else:
        logger.info(f"Sync complete. H: {status['h_photos']}, V: {status['v_photos']}")


if __name__ == '__main__':
    main()
