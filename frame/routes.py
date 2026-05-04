"""HTTP request handler with all photo frame endpoints."""

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import yaml

from frame.clients import (SynologyPhotosClient, GooglePhotosClient, ImmichClient,
                            ICloudSharedAlbumClient, NextcloudClient)

logger = logging.getLogger(__name__)


class PhotoFrameHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler for photo frame.

    The class attribute `app` is an AppState instance, set by the handler
    factory in frame.server.create_handler().
    """

    app = None  # Set by create_handler()


    def __init__(self, *args, photos_dir='/srv/frame/photos', viewer_dir='/srv/frame/viewer', slideshow_config=None, **kwargs):
        self.photos_dir = Path(photos_dir)
        self.viewer_dir = Path(viewer_dir)
        self.slideshow_config = slideshow_config or {}
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        elif path == '/' or path == '/index.html':
            if self.app and self.app.wizard_mode:
                self.serve_wizard()
            else:
                self.serve_viewer()
        elif path == '/wizard':
            self.serve_wizard()
        elif path == '/config':
            self.serve_config_json()
        elif path == '/list':
            self.serve_photos_json()
        elif path == '/sysinfo':
            self.serve_sysinfo()
        elif path == '/brightness':
            self.serve_brightness()
        elif path == '/api/monitor_brightness':
            self.serve_monitor_brightness()
        elif path == '/api/ha_brightness_settings':
            self.serve_ha_brightness_settings()
        elif path == '/schedule':
            self.serve_schedule()
        elif path == '/orientation':
            self.serve_orientation()
        elif path == '/sync/status':
            self.serve_sync_status()
        elif path == '/remote':
            self.serve_remote()
        elif path == '/api/synology':
            self.serve_synology_config()
        elif path == '/api/google_photos':
            self.serve_google_photos_config()
        elif path == '/api/immich':
            self.serve_immich_config()
        elif path == '/api/icloud':
            self.serve_icloud_config()
        elif path == '/api/nextcloud':
            self.serve_nextcloud_config()
        elif path == '/api/album_names':
            self.serve_album_names()
        elif path == '/api/frame/settings':
            self.serve_frame_settings()
        elif path == '/api/qrcode':
            self.serve_qrcode(parsed_path)
        elif path == '/setup':
            self.serve_setup()
        elif path == '/api/wifi/status':
            self.serve_wifi_status()
        elif path == '/api/tailscale/status':
            self.serve_tailscale_status()
        elif path == '/api/screen/detect':
            self.serve_screen_detect()
        elif path == '/api/version':
            self.serve_version()
        elif path.startswith('/photos/'):
            photo_name = path[8:]  # Remove '/photos/' prefix
            self.serve_photo(photo_name)
        else:
            self.handle_not_found()

    def do_POST(self):
        """Handle POST requests."""
        if self.path == '/shutdown':
            self.handle_shutdown()
        elif self.path == '/reboot':
            self.handle_reboot()
        elif self.path == '/schedule':
            self.handle_save_schedule()
        elif self.path == '/wake':
            self.handle_wake()
        elif self.path == '/orientation':
            self.handle_save_orientation()
        elif self.path == '/sync/trigger':
            self.handle_sync_trigger()
        elif self.path == '/api/interval':
            self.handle_save_interval()
        elif self.path == '/api/slideshow_settings':
            self.handle_save_slideshow_settings()
        elif self.path == '/api/monitor_brightness':
            self.handle_save_monitor_brightness()
        elif self.path == '/api/ha_brightness_settings':
            self.handle_save_ha_brightness_settings()
        elif self.path == '/api/ha_lux':
            self.handle_ha_lux()
        elif self.path == '/api/synology':
            self.handle_save_synology_config()
        elif self.path == '/api/google_photos':
            self.handle_save_google_photos_config()
        elif self.path == '/api/immich':
            self.handle_save_immich_config()
        elif self.path == '/api/icloud':
            self.handle_save_icloud_config()
        elif self.path == '/api/nextcloud':
            self.handle_save_nextcloud_config()
        elif self.path == '/api/wifi/scan':
            self.handle_wifi_scan()
        elif self.path == '/api/wifi/connect':
            self.handle_wifi_connect()
        elif self.path == '/api/tailscale/install':
            self.handle_tailscale_install()
        elif self.path == '/api/tailscale/up':
            self.handle_tailscale_up()
        elif self.path == '/api/frame/settings':
            self.handle_save_frame_settings()
        elif self.path == '/api/sleep/test':
            self.handle_sleep_test()
        elif self.path == '/api/wifi/reconfigure':
            self.handle_wifi_reconfigure()
        elif self.path == '/api/wizard/complete':
            self.handle_wizard_complete()
        else:
            self.send_error(404, "Not found")

    # --- Page serving ---

    def serve_viewer(self):
        """Serve the viewer HTML."""
        self._serve_html_file('index.html')

    def serve_wizard(self):
        """Serve the first-time setup wizard."""
        self._serve_html_file('wizard.html')

    def serve_remote(self):
        """Serve the remote config HTML page."""
        self._serve_html_file('remote.html')

    def serve_setup(self):
        """Serve the WiFi setup page."""
        self._serve_html_file('setup.html', cache=False)

    def _serve_html_file(self, filename, cache=True):
        """Serve an HTML file from the viewer directory."""
        file_path = self.viewer_dir / filename
        if not file_path.exists():
            self.send_error(404, f"{filename} not found")
            return
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.send_header('Content-Length', len(content))
            if not cache:
                self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error(f"Error serving {filename}: {e}")
            self.send_error(500, str(e))

    # --- JSON data endpoints ---

    def serve_config_json(self):
        """Serve slideshow configuration as JSON."""
        content = json.dumps(self.slideshow_config).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Content-Length', len(content))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(content)

    def _get_orientation(self):
        """Read current orientation from config."""
        try:
            with open(self.app.config_path) as f:
                cfg = yaml.safe_load(f)
            return cfg.get('frame', {}).get('orientation', 'horizontal')
        except Exception:
            return 'horizontal'

    def serve_photos_json(self):
        """Serve list of photos as JSON (orientation-aware, with fallback)."""
        try:
            orientation = self._get_orientation()
            photos_subdir = self.photos_dir / orientation
            fallback_orientation = 'vertical' if orientation == 'horizontal' else 'horizontal'
            fallback_subdir = self.photos_dir / fallback_orientation

            photos = []
            used_orientation = orientation

            if photos_subdir.exists():
                photos = [f'/photos/{orientation}/{p.name}'
                          for p in photos_subdir.glob('*.jpg')]

            # Fall back to other orientation if target folder is empty
            if not photos and fallback_subdir.exists():
                photos = [f'/photos/{fallback_orientation}/{p.name}'
                          for p in fallback_subdir.glob('*.jpg')]
                used_orientation = fallback_orientation
                if photos:
                    logger.info(f"No {orientation} photos, falling back to {fallback_orientation}")

            content = json.dumps(photos).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Content-Length', len(content))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(content)

            logger.info(f"Served {len(photos)} {used_orientation} photos in JSON")

        except Exception as e:
            logger.error(f"Error serving photos JSON: {e}")
            self.send_error(500, str(e))

    def serve_photo(self, photo_name):
        """Serve a photo file."""
        photo_path = self.photos_dir / photo_name

        if not photo_path.exists() or not photo_path.is_file():
            self.send_error(404, "Photo not found")
            return

        # Security check: ensure photo is within photos_dir
        try:
            photo_path.resolve().relative_to(self.photos_dir.resolve())
        except ValueError:
            self.send_error(403, "Forbidden")
            return

        try:
            with open(photo_path, 'rb') as f:
                content = f.read()

            ext = photo_path.suffix.lower()
            content_types = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.bmp': 'image/bmp'
            }
            content_type = content_types.get(ext, 'application/octet-stream')

            self.send_response(200)
            self.send_header('Content-type', content_type)
            self.send_header('Content-Length', len(content))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(content)

        except Exception as e:
            logger.error(f"Error serving photo {photo_name}: {e}")
            self.send_error(500, str(e))

    def serve_sysinfo(self):
        """Serve cached system info as JSON (updated every 30s in background)."""
        info = self.app.sysinfo_cache.get() if self.app and self.app.sysinfo_cache else {}
        self._json_response(info)

    def serve_brightness(self):
        """Serve auto brightness value from ambient light sensor."""
        brightness = None
        try:
            sensor_file = Path('/tmp/frame-brightness')
            if sensor_file.exists():
                val = int(sensor_file.read_text().strip())
                brightness = max(10, min(100, val))
        except Exception:
            pass
        self._json_response({'brightness': brightness})

    def serve_monitor_brightness(self):
        """Serve monitor hardware brightness via DDC/CI VCP 0x10."""
        try:
            result = subprocess.run(
                ['sudo', 'ddcutil', 'getvcp', '10'],
                capture_output=True, text=True, timeout=8
            )
            out = (result.stdout or '') + '\n' + (result.stderr or '')
            m = re.search(r'current value\s*=\s*(\d+),\s*max value\s*=\s*(\d+)', out)
            if result.returncode == 0 and m:
                current = int(m.group(1))
                max_value = max(1, int(m.group(2)))
                percent = int(round((current / max_value) * 100))
                self._json_response({
                    'ok': True,
                    'available': True,
                    'brightness': max(0, min(100, percent)),
                    'raw_current': current,
                    'raw_max': max_value,
                })
                return
        except Exception:
            pass
        self._json_response({'ok': True, 'available': False, 'brightness': None})

    def _get_ha_brightness_settings(self):
        cfg = (self.app.config or {}).get('ha_brightness', {}) if self.app else {}
        enabled = bool(cfg.get('enabled', False))
        try:
            min_b = int(cfg.get('min_brightness', 0))
        except Exception:
            min_b = 0
        try:
            max_b = int(cfg.get('max_brightness', 100))
        except Exception:
            max_b = 100
        min_b = max(0, min(100, min_b))
        max_b = max(0, min(100, max_b))
        if min_b > max_b:
            min_b, max_b = max_b, min_b
        return {
            'enabled': enabled,
            'min_brightness': min_b,
            'max_brightness': max_b,
            'lux_min': 0,
            'lux_max': 1000,
        }

    def _set_monitor_brightness(self, value):
        value = max(0, min(100, int(value)))
        result = subprocess.run(
            ['sudo', 'ddcutil', 'setvcp', '10', str(value)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or 'ddcutil failed').strip()
            return False, err
        return True, value

    def serve_ha_brightness_settings(self):
        self._json_response({'ok': True, **self._get_ha_brightness_settings()})

    def serve_schedule(self):
        """Serve energy save schedule."""
        data = self.app.energy_save.get_schedule() if self.app and self.app.energy_save else {}
        self._json_response(data)

    def serve_orientation(self):
        """Serve current orientation setting."""
        orientation = self._get_orientation()
        self._json_response({'orientation': orientation})

    def serve_frame_settings(self):
        """Serve frame settings (safe subset, no passwords)."""
        cfg = self.app.config
        data = {
            'frame': cfg.get('frame', {}),
            'slideshow': cfg.get('slideshow', {}),
            'energy_save': cfg.get('energy_save', {}),
            'synology': {'share_urls': cfg.get('synology', {}).get('share_urls', [])},
            'google_photos': {'share_urls': cfg.get('google_photos', {}).get('share_urls', [])},
            'immich': {'share_urls': cfg.get('immich', {}).get('share_urls', [])},
            'icloud': {'share_urls': cfg.get('icloud', {}).get('share_urls', [])},
            'nextcloud': {'share_urls': cfg.get('nextcloud', {}).get('share_urls', [])},
            'sleep_method': cfg.get('energy_save', {}).get('method', 'ddcci'),
        }
        self._json_response(data)

    def serve_sync_status(self):
        """Serve photo sync status as JSON."""
        if self.app and self.app.syncer:
            data = self.app.syncer.get_status()
        else:
            data = {'running': False, 'phase': 'idle'}
        self._json_response(data)

    # --- Config endpoints ---

    def serve_synology_config(self):
        """Serve Synology share URLs and passphrases as JSON."""
        synology = (self.app.config or {}).get('synology', {})
        data = {
            'share_urls': synology.get('share_urls', []),
            'share_passphrases': synology.get('share_passphrases', []),
        }
        self._json_response(data)

    def serve_google_photos_config(self):
        """Serve Google Photos share URLs as JSON."""
        gph = (self.app.config or {}).get('google_photos', {})
        data = {'share_urls': gph.get('share_urls', [])}
        self._json_response(data)

    def serve_immich_config(self):
        """Serve Immich share URLs and passphrases as JSON."""
        immich = (self.app.config or {}).get('immich', {})
        data = {
            'share_urls': immich.get('share_urls', []),
            'share_passphrases': immich.get('share_passphrases', []),
        }
        self._json_response(data)

    def serve_icloud_config(self):
        """Serve iCloud share URLs as JSON."""
        icloud = (self.app.config or {}).get('icloud', {})
        data = {'share_urls': icloud.get('share_urls', [])}
        self._json_response(data)

    def serve_nextcloud_config(self):
        """Serve Nextcloud share URLs and passphrases as JSON."""
        nextcloud = (self.app.config or {}).get('nextcloud', {})
        data = {
            'share_urls': nextcloud.get('share_urls', []),
            'share_passphrases': nextcloud.get('share_passphrases', []),
        }
        self._json_response(data)

    def serve_album_names(self):
        """Return album names — cached instantly, refresh in background.

        First call returns cached names immediately and triggers background
        resolution. The client can poll again to pick up updated names.
        """
        import threading

        synology = (self.app.config or {}).get('synology', {})
        google = (self.app.config or {}).get('google_photos', {})
        immich = (self.app.config or {}).get('immich', {})
        icloud = (self.app.config or {}).get('icloud', {})
        nextcloud = (self.app.config or {}).get('nextcloud', {})

        syn_urls = synology.get('share_urls', [])
        syn_passes = list(synology.get('share_passphrases', []))
        gph_urls = google.get('share_urls', [])
        imm_urls = immich.get('share_urls', [])
        imm_passes = list(immich.get('share_passphrases', []))
        icl_urls = icloud.get('share_urls', [])
        nc_urls = nextcloud.get('share_urls', [])
        nc_passes = list(nextcloud.get('share_passphrases', []))
        while len(syn_passes) < len(syn_urls):
            syn_passes.append('')
        while len(imm_passes) < len(imm_urls):
            imm_passes.append('')
        while len(nc_passes) < len(nc_urls):
            nc_passes.append('')

        cache = self.app.album_name_cache

        def _fallback_name(source, index):
            labels = {'synology': 'Synology', 'google': 'Google Photos',
                      'immich': 'Immich', 'icloud': 'iCloud', 'nextcloud': 'Nextcloud'}
            return f'{labels.get(source, source)} Album {index + 1}'

        # Build result from cache immediately
        url_map = {
            'synology': [(url, i) for i, url in enumerate(syn_urls) if url],
            'google': [(url, i) for i, url in enumerate(gph_urls) if url],
            'immich': [(url, i) for i, url in enumerate(imm_urls) if url],
            'icloud': [(url, i) for i, url in enumerate(icl_urls) if url],
            'nextcloud': [(url, i) for i, url in enumerate(nc_urls) if url],
        }

        result = {'synology': {}, 'google': {}, 'immich': {}, 'icloud': {}, 'nextcloud': {}}
        uncached = []  # (source, url, index, passphrase)

        for source, entries in url_map.items():
            for url, idx in entries:
                if url in cache:
                    result[source][url] = cache[url]
                else:
                    result[source][url] = _fallback_name(source, idx)
                    # Collect for background resolution
                    pw = ''
                    if source == 'synology':
                        pw = syn_passes[idx] if idx < len(syn_passes) else ''
                    elif source == 'immich':
                        pw = imm_passes[idx] if idx < len(imm_passes) else ''
                    elif source == 'nextcloud':
                        pw = nc_passes[idx] if idx < len(nc_passes) else ''
                    uncached.append((source, url, idx, pw))

        # Return cached result immediately
        self._json_response(result)

        # Resolve ALL names in background (updates cache for next request)
        def _bg_resolve():
            changed = False
            all_entries = []
            for source, entries in url_map.items():
                for url, idx in entries:
                    pw = ''
                    if source == 'synology':
                        pw = syn_passes[idx] if idx < len(syn_passes) else ''
                    elif source == 'immich':
                        pw = imm_passes[idx] if idx < len(imm_passes) else ''
                    elif source == 'nextcloud':
                        pw = nc_passes[idx] if idx < len(nc_passes) else ''
                    all_entries.append((source, url, idx, pw))

            for source, url, idx, pw in all_entries:
                try:
                    if source == 'synology':
                        name = SynologyPhotosClient.resolve_album_name(url, pw)
                    elif source == 'google':
                        name = GooglePhotosClient.resolve_album_name(url)
                    elif source == 'immich':
                        name = ImmichClient.resolve_album_name(url, pw)
                    elif source == 'icloud':
                        name = ICloudSharedAlbumClient.resolve_album_name(url)
                    elif source == 'nextcloud':
                        name = NextcloudClient.resolve_album_name(url, pw)
                    else:
                        continue

                    # Only cache real names, not empty results
                    if name and cache.get(url) != name:
                        cache[url] = name
                        changed = True
                except Exception as e:
                    logger.debug(f"Failed to resolve {source} album name: {e}")

            if changed:
                self.app.save_album_cache()

        threading.Thread(target=_bg_resolve, daemon=True).start()

    def serve_qrcode(self, parsed_path):
        """Generate QR code modules as JSON for client-side canvas rendering."""
        params = parse_qs(parsed_path.query)
        text = params.get('text', [''])[0]
        if not text:
            self.send_error(400, "Missing text parameter")
            return
        try:
            import segno
            qr = segno.make(text, error='L')
            matrix = qr.matrix
            modules = []
            for row in matrix:
                modules.append([int(bool(cell)) for cell in row])
            self._json_response({'modules': modules})
        except ImportError:
            logger.error("segno not installed — pip install segno")
            self.send_error(500, "QR library not available")
        except Exception as e:
            logger.error(f"QR generation error: {e}")
            self.send_error(500, str(e))

    def serve_wifi_status(self):
        """Serve WiFi manager status as JSON."""
        if self.app and self.app.wifi_manager:
            data = self.app.wifi_manager.get_status()
        else:
            data = {'mode': 'normal', 'ssid': '', 'ip': ''}
        self._json_response(data)

    def serve_tailscale_status(self):
        """Serve Tailscale installation and connection status."""
        status = {'status': 'not_installed', 'ip': ''}
        try:
            result = subprocess.run(
                ['tailscale', 'status', '--json'],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                ts_data = json.loads(result.stdout)
                backend_state = ts_data.get('BackendState', '')
                if backend_state == 'Running':
                    status['status'] = 'connected'
                    # Get Tailscale IP
                    ip_result = subprocess.run(
                        ['tailscale', 'ip', '-4'],
                        capture_output=True, text=True, timeout=5)
                    status['ip'] = ip_result.stdout.strip()
                else:
                    status['status'] = 'not_connected'
            else:
                # tailscale binary exists but returned error
                status['status'] = 'not_connected'
        except FileNotFoundError:
            status['status'] = 'not_installed'
        except Exception:
            pass
        self._json_response(status)

    def serve_version(self):
        """Serve version info (git commit hash and date)."""
        import os
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version = 'unknown'
        date = ''
        try:
            result = subprocess.run(
                ['git', 'log', '-1', '--format=%h %ci'],
                capture_output=True, text=True, timeout=5, cwd=repo_dir)
            if result.returncode == 0:
                parts = result.stdout.strip().split(' ', 1)
                version = parts[0]
                date = parts[1].split(' ')[0] if len(parts) > 1 else ''
        except Exception:
            pass
        self._json_response({'version': version, 'date': date})

    def serve_screen_detect(self):
        """Try to detect connected screen resolution."""
        # Method 1: wlr-randr (Wayland)
        try:
            result = subprocess.run(['wlr-randr'], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                line = line.strip()
                if 'current' in line and 'x' in line:
                    res = line.split()[0]  # e.g. "1920x1200"
                    w, h = res.split('x')
                    self._json_response({'width': int(w), 'height': int(h), 'method': 'wlr-randr'})
                    return
        except Exception:
            pass
        # Method 2: framebuffer
        try:
            with open('/sys/class/graphics/fb0/virtual_size') as f:
                w, h = f.read().strip().split(',')
                self._json_response({'width': int(w), 'height': int(h), 'method': 'framebuffer'})
                return
        except Exception:
            pass
        # Fallback
        self._json_response({'width': 1920, 'height': 1200, 'method': 'default'})

    # --- POST action handlers ---

    def handle_shutdown(self):
        """Handle shutdown request."""
        logger.warning("Shutdown requested via HTTP")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Shutdown initiated')

            def delayed_shutdown():
                time.sleep(2)
                subprocess.run(['sudo', 'shutdown', '-h', 'now'])

            threading.Thread(target=delayed_shutdown, daemon=True).start()
        except Exception as e:
            logger.error(f"Error handling shutdown: {e}")
            self.send_error(500, str(e))

    def handle_reboot(self):
        """Handle reboot request."""
        logger.warning("Reboot requested via HTTP")
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Reboot initiated')

            def delayed_reboot():
                time.sleep(2)
                subprocess.run(['sudo', 'reboot'])

            threading.Thread(target=delayed_reboot, daemon=True).start()
        except Exception as e:
            logger.error(f"Error handling reboot: {e}")
            self.send_error(500, str(e))

    def handle_wake(self):
        """Wake display from DPMS sleep."""
        if self.app and self.app.energy_save:
            self.app.energy_save.wake_display()
        self._json_response({'ok': True})

    def handle_save_schedule(self):
        """Save energy save schedule."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            if self.app and self.app.energy_save:
                self.app.energy_save.update_schedule(data)
            self._json_response({'ok': True})
        except Exception as e:
            logger.error(f"Error saving schedule: {e}")
            self.send_error(400, str(e))

    def handle_save_interval(self):
        """Save slideshow interval to config and update in-memory config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            interval = int(data.get('interval', 300))
            if interval < 10:
                interval = 10
            if interval > 3600:
                interval = 3600

            with open(self.app.config_path) as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault('slideshow', {})['interval'] = interval
            with open(self.app.config_path, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            # Update in-memory slideshow config
            self.slideshow_config['interval'] = interval

            self._json_response({'ok': True, 'interval': interval})
        except Exception as e:
            logger.error(f"Failed to save interval: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_slideshow_settings(self):
        """Save slideshow settings (interval, fade duration, transition)."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)

            interval = int(data.get('interval', self.slideshow_config.get('interval', 300)))
            if interval < 10:
                interval = 10
            if interval > 3600:
                interval = 3600

            fade_duration = float(data.get('fade_duration', self.slideshow_config.get('fade_duration', 1.0)))
            if fade_duration < 0.0:
                fade_duration = 0.0
            if fade_duration > 5.0:
                fade_duration = 5.0
            fade_duration = round(fade_duration, 1)

            transition = str(data.get('transition', self.slideshow_config.get('transition', 'fade'))).lower()
            if transition not in ('fade', 'cut', 'slide', 'zoom', 'kenburns'):
                transition = 'fade'

            with open(self.app.config_path) as f:
                cfg = yaml.safe_load(f)
            slideshow = cfg.setdefault('slideshow', {})
            slideshow['interval'] = interval
            slideshow['fade_duration'] = fade_duration
            slideshow['transition'] = transition
            with open(self.app.config_path, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            # Update in-memory slideshow config
            self.slideshow_config['interval'] = interval
            self.slideshow_config['fade_duration'] = fade_duration
            self.slideshow_config['transition'] = transition

            self._json_response({
                'ok': True,
                'interval': interval,
                'fade_duration': fade_duration,
                'transition': transition,
            })
        except Exception as e:
            logger.error(f"Failed to save slideshow settings: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_monitor_brightness(self):
        """Set monitor hardware brightness via DDC/CI VCP 0x10."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            value = int(data.get('brightness', 100))
            ok, result = self._set_monitor_brightness(value)
            if not ok:
                self._json_response({'ok': False, 'error': result}, 400)
                return
            self._json_response({'ok': True, 'brightness': result})
        except Exception as e:
            logger.error(f"Failed to set monitor brightness: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_ha_brightness_settings(self):
        """Save HA-driven auto brightness settings."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            enabled = bool(data.get('enabled', False))
            min_b = max(0, min(100, int(data.get('min_brightness', 0))))
            max_b = max(0, min(100, int(data.get('max_brightness', 100))))
            if min_b > max_b:
                min_b, max_b = max_b, min_b

            with open(self.app.config_path) as f:
                cfg = yaml.safe_load(f)
            ha_cfg = cfg.setdefault('ha_brightness', {})
            ha_cfg['enabled'] = enabled
            ha_cfg['min_brightness'] = min_b
            ha_cfg['max_brightness'] = max_b
            with open(self.app.config_path, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)
            self.app.config = cfg

            self._json_response({
                'ok': True,
                'enabled': enabled,
                'min_brightness': min_b,
                'max_brightness': max_b,
            })
        except Exception as e:
            logger.error(f"Failed to save HA brightness settings: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_ha_lux(self):
        """Receive ambient lux from Home Assistant and apply mapped monitor brightness."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            lux = float(data.get('lux', 0.0))
            settings = self._get_ha_brightness_settings()
            if not settings['enabled']:
                self._json_response({'ok': True, 'applied': False, 'reason': 'auto_disabled'})
                return

            lux_clamped = max(settings['lux_min'], min(settings['lux_max'], lux))
            span = max(1.0, float(settings['lux_max'] - settings['lux_min']))
            ratio = (lux_clamped - settings['lux_min']) / span
            brightness = int(round(settings['min_brightness'] + ratio * (settings['max_brightness'] - settings['min_brightness'])))
            brightness = max(0, min(100, brightness))

            ok, result = self._set_monitor_brightness(brightness)
            if not ok:
                self._json_response({'ok': False, 'error': result}, 400)
                return

            self._json_response({
                'ok': True,
                'applied': True,
                'lux': lux,
                'lux_clamped': lux_clamped,
                'brightness': result,
                'settings': settings,
            })
        except Exception as e:
            logger.error(f"Failed to apply HA lux: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_orientation(self):
        """Save orientation setting, trigger sync if target folder is empty, restart cage."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            new_orientation = data.get('orientation', 'horizontal')
            if new_orientation not in ('horizontal', 'vertical'):
                raise ValueError('Invalid orientation')

            with open(self.app.config_path) as f:
                cfg = yaml.safe_load(f)
            old_orientation = cfg.get('frame', {}).get('orientation', 'horizontal')
            cfg.setdefault('frame', {})['orientation'] = new_orientation
            with open(self.app.config_path, 'w') as f:
                yaml.dump(cfg, f, default_flow_style=False)

            # Update in-memory config so syncer uses correct orientation
            self.app.config.setdefault('frame', {})['orientation'] = new_orientation

            # Check if the target orientation folder has photos
            photos_config = (self.app.config or {}).get('photos', {})
            base_dir = Path(photos_config.get('base_dir', '/srv/frame/photos'))
            target_dir = base_dir / new_orientation
            has_photos = target_dir.exists() and any(target_dir.glob('*.jpg'))

            self._json_response({'ok': True, 'sync_triggered': not has_photos})

            if new_orientation != old_orientation:
                def restart_cage():
                    time.sleep(1)
                    subprocess.run(['sudo', 'systemctl', 'restart', 'photo_frame_cage'],
                                 capture_output=True, timeout=15)
                    logger.info(f"Orientation changed to {new_orientation}, restarted cage")
                threading.Thread(target=restart_cage, daemon=True).start()
        except Exception as e:
            logger.error(f"Error saving orientation: {e}")
            self.send_error(400, str(e))

    def handle_sync_trigger(self):
        """Trigger a manual photo sync."""
        if self.app and self.app.syncer:
            self.app.syncer.run_sync()
            data = {'ok': True, 'message': 'Sync started'}
        else:
            data = {'ok': False, 'message': 'Syncer not initialized'}
        self._json_response(data)

    def handle_save_synology_config(self):
        """Save Synology share URLs and passphrases to config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            urls = data.get('share_urls', [])
            passphrases = data.get('share_passphrases', [])
            if not isinstance(urls, list) or not isinstance(passphrases, list):
                raise ValueError('share_urls and share_passphrases must be lists')
            if len(urls) != len(passphrases):
                raise ValueError('share_urls and share_passphrases must have the same length')

            self.app.config.setdefault('synology', {})
            self.app.config['synology']['share_urls'] = urls
            self.app.config['synology']['share_passphrases'] = passphrases
            self.app.save_config()

            # Create syncer if it didn't exist before and we now have URLs
            if self.app.syncer is None and any(urls):
                from frame.sync import PhotoSyncer
                self.app.syncer = PhotoSyncer(self.app.config)

            self._json_response({'ok': True})
            logger.info(f"Synology config saved: {len(urls)} album(s)")
        except Exception as e:
            logger.error(f"Error saving synology config: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_google_photos_config(self):
        """Save Google Photos share URLs to config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            urls = data.get('share_urls', [])
            if not isinstance(urls, list):
                raise ValueError('share_urls must be a list')

            self.app.config.setdefault('google_photos', {})
            self.app.config['google_photos']['share_urls'] = urls
            self.app.save_config()

            if self.app.syncer is None and any(urls):
                from frame.sync import PhotoSyncer
                self.app.syncer = PhotoSyncer(self.app.config)

            self._json_response({'ok': True})
            logger.info(f"Google Photos config saved: {len(urls)} album(s)")
        except Exception as e:
            logger.error(f"Error saving Google Photos config: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_immich_config(self):
        """Save Immich share URLs and passphrases to config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            urls = data.get('share_urls', [])
            passphrases = data.get('share_passphrases', [])
            if not isinstance(urls, list) or not isinstance(passphrases, list):
                raise ValueError('share_urls and share_passphrases must be lists')
            if len(urls) != len(passphrases):
                raise ValueError('share_urls and share_passphrases must have the same length')

            self.app.config.setdefault('immich', {})
            self.app.config['immich']['share_urls'] = urls
            self.app.config['immich']['share_passphrases'] = passphrases
            self.app.save_config()

            if self.app.syncer is None and any(urls):
                from frame.sync import PhotoSyncer
                self.app.syncer = PhotoSyncer(self.app.config)

            self._json_response({'ok': True})
            logger.info(f"Immich config saved: {len(urls)} album(s)")
        except Exception as e:
            logger.error(f"Error saving Immich config: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_icloud_config(self):
        """Save iCloud share URLs to config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            urls = data.get('share_urls', [])
            if not isinstance(urls, list):
                raise ValueError('share_urls must be a list')

            self.app.config.setdefault('icloud', {})
            self.app.config['icloud']['share_urls'] = urls
            self.app.save_config()

            if self.app.syncer is None and any(urls):
                from frame.sync import PhotoSyncer
                self.app.syncer = PhotoSyncer(self.app.config)

            self._json_response({'ok': True})
            logger.info(f"iCloud config saved: {len(urls)} album(s)")
        except Exception as e:
            logger.error(f"Error saving iCloud config: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_save_nextcloud_config(self):
        """Save Nextcloud share URLs and passphrases to config."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            urls = data.get('share_urls', [])
            passphrases = data.get('share_passphrases', [])
            if not isinstance(urls, list) or not isinstance(passphrases, list):
                raise ValueError('share_urls and share_passphrases must be lists')
            if len(urls) != len(passphrases):
                raise ValueError('share_urls and share_passphrases must have the same length')

            self.app.config.setdefault('nextcloud', {})
            self.app.config['nextcloud']['share_urls'] = urls
            self.app.config['nextcloud']['share_passphrases'] = passphrases
            self.app.save_config()

            if self.app.syncer is None and any(urls):
                from frame.sync import PhotoSyncer
                self.app.syncer = PhotoSyncer(self.app.config)

            self._json_response({'ok': True})
            logger.info(f"Nextcloud config saved: {len(urls)} album(s)")
        except Exception as e:
            logger.error(f"Error saving Nextcloud config: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_wifi_scan(self):
        """Scan for WiFi networks."""
        if not (self.app and self.app.wifi_manager):
            self._json_response({'networks': []})
            return
        networks = self.app.wifi_manager.scan_networks()
        self._json_response({'networks': networks})

    def handle_wifi_connect(self):
        """Connect to a WiFi network (runs in background thread)."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            ssid = data.get('ssid', '')
            password = data.get('password', '')
            if not ssid:
                self._json_response({'ok': False, 'message': 'SSID required'}, 400)
                return

            # Respond immediately (phone will lose connection when hotspot drops)
            self._json_response({'ok': True, 'message': 'Connecting...'})

            app = self.app

            def connect_background():
                success, message = app.wifi_manager.connect_to_network(ssid, password)
                if success:
                    logger.info(f"WiFi connected to {ssid}, initializing syncer")
                    if app.config and app.syncer is None:
                        if app.has_album_sources():
                            app.init_syncer()
                else:
                    logger.warning(f"WiFi connect failed: {message}")

            threading.Thread(target=connect_background, daemon=True).start()

        except Exception as e:
            logger.error(f"WiFi connect error: {e}")
            self._json_response({'ok': False, 'message': str(e)}, 400)

    def handle_wifi_reconfigure(self):
        """Start hotspot so user can reconfigure WiFi from their phone."""
        if not (self.app and self.app.wifi_manager):
            self._json_response({'ok': False, 'error': 'WiFi manager not available'}, 500)
            return
        try:
            self.app.wifi_manager.start_hotspot()
            self._json_response({
                'ok': True,
                'ssid': 'PhotoFrame-Setup',
                'password': 'photoframe',
            })
            logger.info("WiFi reconfigure: hotspot started")
        except Exception as e:
            logger.error(f"WiFi reconfigure error: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 500)

    # --- Wizard / Tailscale endpoints ---

    def handle_tailscale_install(self):
        """Install Tailscale via official install script."""
        try:
            result = subprocess.run(
                ['sudo', 'sh', '-c', 'curl -fsSL https://tailscale.com/install.sh | sh'],
                capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                self._json_response({'ok': True})
            else:
                self._json_response({'ok': False, 'error': result.stderr[:500]}, 500)
        except Exception as e:
            logger.error(f"Tailscale install error: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 500)

    def handle_tailscale_up(self):
        """Start Tailscale and return auth URL if needed."""
        try:
            # Kill any previous tailscale up, then start fresh
            subprocess.run(['sudo', 'pkill', '-f', 'tailscale up'],
                          capture_output=True, timeout=3)
            time.sleep(1)
            # Start tailscale up with long timeout (background — don't block HTTP)
            subprocess.Popen(
                ['sudo', 'tailscale', 'up', '--timeout=300s'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Wait for auth URL to appear in tailscale status
            auth_url = ''
            for attempt in range(15):
                time.sleep(1)
                result = subprocess.run(
                    ['sudo', 'tailscale', 'status'],
                    capture_output=True, text=True, timeout=5)
                output = result.stdout + result.stderr
                for line in output.split('\n'):
                    if 'https://login.tailscale.com/' in line:
                        for word in line.split():
                            if word.startswith('https://login.tailscale.com/'):
                                auth_url = word
                                break
                if auth_url:
                    break
                # Already connected?
                if 'logged in' in output.lower() or result.returncode == 0 and 'login.tailscale.com' not in output:
                    # Check if actually connected with IP
                    json_result = subprocess.run(
                        ['sudo', 'tailscale', 'status', '--json'],
                        capture_output=True, text=True, timeout=5)
                    if json_result.returncode == 0:
                        status = json.loads(json_result.stdout)
                        if status.get('BackendState') == 'Running':
                            self._json_response({'ok': True, 'auth_url': ''})
                            return
            self._json_response({'ok': True, 'auth_url': auth_url,
                                'message': 'Needs authentication' if auth_url else ''})
        except Exception as e:
            logger.error(f"Tailscale up error: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 500)

    def handle_save_frame_settings(self):
        """Save frame settings (wizard step 4 + expanded settings)."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            name = data.get('name', '')
            orientation = data.get('orientation', 'horizontal')
            if orientation not in ('horizontal', 'vertical'):
                raise ValueError('Invalid orientation')

            old_orientation = self.app.config.get('frame', {}).get('orientation', 'horizontal')
            self.app.config.setdefault('frame', {})
            if name:
                self.app.config['frame']['name'] = name
            self.app.config['frame']['orientation'] = orientation

            # Slideshow interval
            if 'interval' in data:
                interval = max(5, min(3600, int(data['interval'])))
                self.app.config.setdefault('slideshow', {})['interval'] = interval

            # Photo processing settings
            photos = self.app.config.setdefault('photos', {})
            if 'quality' in data:
                photos['quality'] = max(50, min(100, int(data['quality'])))
            if 'blur_radius' in data:
                photos['blur_radius'] = max(10, min(80, int(data['blur_radius'])))
            if 'blur_darken' in data:
                photos['blur_darken'] = round(max(0.0, min(1.0, float(data['blur_darken']))), 2)

            # Screen resolution
            if 'screen_width' in data and 'screen_height' in data:
                sw, sh = int(data['screen_width']), int(data['screen_height'])
                if sw > 0 and sh > 0:
                    # Horizontal uses native w x h, vertical swaps
                    if sw >= sh:
                        photos.setdefault('horizontal', {}).update({'width': sw, 'height': sh})
                        photos.setdefault('vertical', {}).update({'width': sh, 'height': sw})
                    else:
                        photos.setdefault('horizontal', {}).update({'width': sh, 'height': sw})
                        photos.setdefault('vertical', {}).update({'width': sw, 'height': sh})

            # Sleep method
            if 'sleep_method' in data:
                method = data['sleep_method']
                if method in ('hdmi', 'ddcci', 'dpms', 'brightness', 'black_only'):
                    self.app.config.setdefault('energy_save', {})['method'] = method
                    if self.app.energy_save:
                        self.app.energy_save.method = method

            self.app.save_config()
            self._json_response({'ok': True})

            # Restart cage if orientation changed (triggers wlr-randr rotation)
            if orientation != old_orientation:
                def restart_cage():
                    time.sleep(1)
                    subprocess.run(['sudo', 'systemctl', 'restart', 'photo_frame_cage'],
                                 capture_output=True, timeout=15)
                    logger.info(f"Orientation changed to {orientation}, restarted cage")
                threading.Thread(target=restart_cage, daemon=True).start()
        except Exception as e:
            logger.error(f"Error saving frame settings: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 400)

    def handle_sleep_test(self):
        """Test a sleep method for the wizard. Blocks for ~10 seconds."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
            method = data.get('method', 'ddcci')
            duration = min(int(data.get('duration', 10)), 15)
            if method not in ('hdmi', 'ddcci', 'dpms', 'brightness', 'black_only'):
                raise ValueError(f'Invalid method: {method}')
            if self.app and self.app.energy_save:
                self.app.energy_save.test_sleep_method(method, duration)
            self._json_response({'ok': True, 'method': method})
        except Exception as e:
            logger.error(f"Sleep test error: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 500)

    def handle_wizard_complete(self):
        """Mark setup as complete and exit wizard mode."""
        try:
            self.app.config['setup_complete'] = True
            self.app.save_config()
            self.app.wizard_mode = False

            # Initialize syncer if albums were configured
            if self.app.syncer is None and self.app.has_album_sources():
                self.app.init_syncer()

            # Trigger first sync
            if self.app.syncer:
                self.app.syncer.run_sync()

            self._json_response({'ok': True})
            logger.info("Setup wizard completed")
        except Exception as e:
            logger.error(f"Error completing wizard: {e}")
            self._json_response({'ok': False, 'error': str(e)}, 500)

    # --- 404 / captive portal ---

    def handle_not_found(self):
        """Handle 404 — redirect to /setup in hotspot mode (captive portal)."""
        if self.app and self.app.wifi_manager and self.app.wifi_manager.mode == 'hotspot':
            if self.app.wizard_mode:
                self.send_response(302)
                self.send_header('Location', '/wizard')
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header('Location', '/setup')
                self.end_headers()
        else:
            self.send_error(404, "Not found")

    # --- Helpers ---

    def _json_response(self, data, status=200):
        """Helper to send a JSON response."""
        content = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    # Endpoints that are polled frequently — log at DEBUG to reduce noise
    _quiet_paths = frozenset(('/sync/status', '/schedule', '/sysinfo', '/brightness', '/favicon.ico'))
    _quiet_prefixes = ('/photos/',)

    def log_message(self, format, *args):
        """Override to use Python logging. Suppress noisy polling endpoints."""
        msg = f"{self.address_string()} - {format % args}"
        path = self.path.split('?')[0] if hasattr(self, 'path') else ''
        if path in self._quiet_paths or any(path.startswith(p) for p in self._quiet_prefixes):
            logger.debug(msg)
        else:
            logger.info(msg)
