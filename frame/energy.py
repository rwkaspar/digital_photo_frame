"""Energy save management and system info caching."""

import os
import json
import logging
import subprocess
import threading
import time
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


class SysinfoCache:
    """Background cache for system info to avoid slow shell calls on each request."""

    def __init__(self, photos_base_dir):
        self.photos_base = Path(photos_base_dir)
        self.data = {}
        self._update()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            time.sleep(30)
            self._update()

    def _update(self):
        info = {'hostname': socket.gethostname(), 'ips': [], 'tailscale_ip': ''}
        try:
            result = subprocess.run(
                ['tailscale', 'ip', '-4'], capture_output=True, text=True, timeout=5)
            ts = result.stdout.strip()
            if ts and result.returncode == 0:
                info['tailscale_ip'] = ts
        except Exception:
            pass
        try:
            result = subprocess.run(
                ['hostname', '-I'], capture_output=True, text=True, timeout=5)
            all_ips = result.stdout.strip().split()
            ts_ip = info['tailscale_ip']
            for ip in all_ips:
                if ':' in ip:
                    continue  # skip IPv6
                if ip == ts_ip:
                    continue  # exact match
                # CGNAT fallback: if tailscale CLI failed, detect by range
                if not ts_ip and self._is_cgnat(ip):
                    info['tailscale_ip'] = ip
                    continue
                info['ips'].append(ip)
        except Exception:
            pass
        try:
            result = subprocess.run(
                ['iwgetid', '-r'], capture_output=True, text=True, timeout=5)
            info['wifi_ssid'] = result.stdout.strip()
        except Exception:
            info['wifi_ssid'] = ''
        try:
            with open('/sys/class/thermal/thermal_zone0/temp') as f:
                info['cpu_temp'] = round(int(f.read().strip()) / 1000, 1)
        except Exception:
            info['cpu_temp'] = None
        try:
            result = subprocess.run(
                ['df', '-h', '/'], capture_output=True, text=True, timeout=5)
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                info['disk'] = f"{parts[2]}/{parts[1]} ({parts[4]})"
        except Exception:
            info['disk'] = ''
        h_dir = self.photos_base / 'horizontal'
        v_dir = self.photos_base / 'vertical'
        h_count = len(list(h_dir.glob('*.jpg'))) if h_dir.exists() else 0
        v_count = len(list(v_dir.glob('*.jpg'))) if v_dir.exists() else 0
        h_videos = len(list(h_dir.glob('*.mp4'))) if h_dir.exists() else 0
        v_videos = len(list(v_dir.glob('*.mp4'))) if v_dir.exists() else 0
        info['h_photos'] = h_count
        info['v_photos'] = v_count
        info['h_videos'] = h_videos
        info['v_videos'] = v_videos
        info['photo_count'] = f"H: {h_count} / V: {v_count}"
        info['video_count'] = f"H: {h_videos} / V: {v_videos}"
        # Touchscreen detection
        try:
            result = subprocess.run(
                ['lsusb'], capture_output=True, text=True, timeout=5)
            info['touchscreen'] = '27c0:0859' in result.stdout
        except Exception:
            info['touchscreen'] = False
        self.data = info

    @staticmethod
    def _is_cgnat(ip):
        """Check if IP is in Tailscale CGNAT range 100.64.0.0/10."""
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            first, second = int(parts[0]), int(parts[1])
            return first == 100 and 64 <= second <= 127
        except (ValueError, IndexError):
            return False

    def get(self):
        return self.data


class EnergySaveManager:
    """Manages sleep schedule by stopping/starting the display service.

    During sleep: stops cage+Chromium (frees RAM, screen shows black console).
    On wake: restarts the display service.
    Touch-to-wake: reads raw input events during sleep to detect touch/tap.
    """

    SCHEDULE_FILE = '/tmp/frame-schedule.json'

    SLEEP_METHODS = ('hdmi', 'ddcci', 'dpms', 'brightness', 'black_only')

    def __init__(self, app_state=None):
        self.app = app_state
        self.enabled = False
        self.off_time = '22:00'
        self.on_time = '07:00'
        self.weekdays = [0, 1, 2, 3, 4, 5, 6]  # all days (JS getDay: 0=Sun)
        self.sleeping = False
        self._wake_event = threading.Event()
        # Sleep method from config
        config = app_state.config if app_state else {}
        self.method = config.get('energy_save', {}).get('method', 'ddcci')
        self._load()
        self._thread = None

    def start(self):
        """Start the sleep-check loop. Call after syncer is initialized."""
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _load(self):
        try:
            with open(self.SCHEDULE_FILE) as f:
                data = json.load(f)
                self.enabled = data.get('enabled', False)
                self.off_time = data.get('off_time', '22:00')
                self.on_time = data.get('on_time', '07:00')
                self.weekdays = data.get('weekdays', [0, 1, 2, 3, 4, 5, 6])
        except Exception:
            pass

    def _save(self):
        try:
            with open(self.SCHEDULE_FILE, 'w') as f:
                json.dump({
                    'enabled': self.enabled,
                    'off_time': self.off_time,
                    'on_time': self.on_time,
                    'weekdays': self.weekdays,
                }, f)
        except Exception as e:
            logger.error(f"Error saving schedule: {e}")

    def is_sleeping(self):
        return self.sleeping

    def get_schedule(self):
        return {
            'enabled': self.enabled,
            'off_time': self.off_time,
            'on_time': self.on_time,
            'weekdays': self.weekdays,
            'sleeping': self.sleeping,
        }

    def update_schedule(self, data):
        self.enabled = data.get('enabled', self.enabled)
        self.off_time = data.get('off_time', self.off_time)
        self.on_time = data.get('on_time', self.on_time)
        if 'weekdays' in data:
            self.weekdays = data['weekdays']
        self._save()
        self._check()

    def wake_display(self):
        """Wake display immediately."""
        if self.sleeping:
            self._set_sleep(False)

    def _wayland_env(self):
        """Get environment with Wayland display vars for wlr-randr/wlopm."""
        env = os.environ.copy()
        env['XDG_RUNTIME_DIR'] = '/tmp/frame-runtime'
        env['WAYLAND_DISPLAY'] = 'wayland-0'
        return env

    def _backlight_off(self):
        """Turn off backlight using configured method."""
        method = self.method
        if method == 'ddcci':
            subprocess.run(['sudo', 'ddcutil', 'setvcp', 'd6', '4'],
                         capture_output=True, timeout=10)
        elif method == 'dpms':
            subprocess.run(['sudo', 'wlopm', '--off', '*'],
                         capture_output=True, timeout=5, env=self._wayland_env())
        elif method == 'brightness':
            subprocess.run(['sudo', 'ddcutil', 'setvcp', '10', '0'],
                         capture_output=True, timeout=10)
        elif method == 'hdmi':
            subprocess.run(['wlr-randr', '--output', 'HDMI-A-1', '--off'],
                         capture_output=True, timeout=5, env=self._wayland_env())
        # black_only: no additional command (framebuffer already zeroed)

    def _backlight_on(self):
        """Turn on backlight using configured method."""
        method = self.method
        if method == 'ddcci':
            subprocess.run(['sudo', 'ddcutil', 'setvcp', 'd6', '1'],
                         capture_output=True, timeout=10)
        elif method == 'dpms':
            subprocess.run(['sudo', 'wlopm', '--on', '*'],
                         capture_output=True, timeout=5, env=self._wayland_env())
        elif method == 'brightness':
            subprocess.run(['sudo', 'ddcutil', 'setvcp', '10', '100'],
                         capture_output=True, timeout=10)
        elif method == 'hdmi':
            subprocess.run(['wlr-randr', '--output', 'HDMI-A-1', '--on'],
                         capture_output=True, timeout=5, env=self._wayland_env())
        # black_only: no additional command

    def _set_sleep(self, sleep):
        try:
            if sleep and not self.sleeping:
                self.sleeping = True
                # Stop cage to free RAM (returns to Linux console/framebuffer)
                subprocess.run(['sudo', 'systemctl', 'stop', 'photo_frame_cage'],
                             capture_output=True, timeout=15)
                time.sleep(1)
                # Fill framebuffer with black — keeps HDMI signal active
                # (avoids ANMITE "no signal" blue screen that DPMS causes)
                subprocess.run(['sudo', 'dd', 'if=/dev/zero', 'of=/dev/fb0',
                                'bs=1M', 'count=10'],
                             capture_output=True, timeout=5)
                # Hide console cursor and prevent text from appearing
                subprocess.run(['sudo', 'sh', '-c',
                                'setterm --cursor off --blank force --powerdown 0 > /dev/tty1'],
                             capture_output=True, timeout=5)
                # Start touch-to-wake listener BEFORE backlight off
                # (DDC/CI standby may cut USB power, killing the touchscreen)
                self._wake_event.clear()
                threading.Thread(target=self._touch_wake_listener, daemon=True).start()
                time.sleep(0.2)  # let listener open FDs while devices exist
                # Turn off backlight via configured method
                self._backlight_off()
                logger.info(f"Sleep: stopped display ({self.method}), framebuffer black")
                # Auto-update from GitHub (cage stopped = more RAM for git/pip)
                self._run_update()
                # Trigger photo sync during sleep
                syncer = self.app.syncer if self.app else None
                if syncer:
                    logger.info("Sleep: triggering photo sync")
                    syncer.run_sync()
                else:
                    logger.warning("Sleep: syncer not initialized, skipping sync")
            elif not sleep and self.sleeping:
                self._wake_event.set()  # stop touch listener
                # Stop any running sync before starting cage (free RAM for Chromium)
                syncer = self.app.syncer if self.app else None
                if syncer:
                    syncer.stop()
                    # Wait briefly for current subprocess to finish
                    for _ in range(30):
                        if not syncer.get_status().get('running'):
                            break
                        time.sleep(1)
                    logger.info("Wake: stopped sync")
                # Start cage first (Chromium needs time to render)
                subprocess.run(['sudo', 'systemctl', 'start', 'photo_frame_cage'],
                             capture_output=True, timeout=15)
                # Wait for Chromium to render the slideshow before turning on display
                time.sleep(5)
                # Restore backlight via configured method
                self._backlight_on()
                self.sleeping = False
                logger.info(f"Wake: started display service ({self.method})")
        except Exception as e:
            logger.error(f"Sleep control error: {e}")

    def _run_update(self):
        """Run git pull via update.sh during sleep."""
        config = self.app.config if self.app else {}
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        update_script = os.path.join(repo_dir, 'scripts', 'update.sh')
        if not os.path.exists(update_script):
            logger.warning("Sleep: update.sh not found, skipping update")
            return
        try:
            logger.info("Sleep: running git pull (update.sh)")
            result = subprocess.run(
                ['bash', update_script],
                capture_output=True, text=True, timeout=120,
                cwd=repo_dir)
            for line in result.stdout.strip().split('\n'):
                if line:
                    logger.info(f"Sleep update: {line}")
            if result.returncode != 0:
                logger.warning(f"Sleep update: exited {result.returncode}")
                if result.stderr:
                    logger.warning(f"Sleep update stderr: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("Sleep update: timed out after 120s")
        except Exception as e:
            logger.error(f"Sleep update error: {e}")

    def test_sleep_method(self, method, duration=10):
        """Test a sleep method: turn off for duration seconds, then turn on.

        Used by the wizard to let users find the best sleep method.
        """
        saved = self.method
        self.method = method
        try:
            self._backlight_off()
            time.sleep(duration)
            self._backlight_on()
        finally:
            self.method = saved

    def _touch_wake_listener(self):
        """Listen for touch/input events during sleep to trigger wake.

        Opens input devices early (before backlight off) so FDs exist even if
        DDC/CI standby later cuts USB power.  Re-scans periodically in case
        devices disappear and reappear.
        """
        import glob
        import select

        RESCAN_INTERVAL = 5  # seconds

        def open_devices():
            fds = []
            for dev in glob.glob('/dev/input/event*'):
                try:
                    fds.append(os.open(dev, os.O_RDONLY | os.O_NONBLOCK))
                except Exception:
                    pass
            return fds

        def close_fds(fds):
            for fd in fds:
                try:
                    os.close(fd)
                except Exception:
                    pass

        fds = open_devices()
        if fds:
            logger.info(f"Touch wake: listening on {len(fds)} input devices")
        else:
            logger.warning("Touch wake: no input devices found")
        last_rescan = time.time()

        try:
            while not self._wake_event.is_set():
                # Re-scan for devices if we lost them (USB power restored)
                if not fds and time.time() - last_rescan > RESCAN_INTERVAL:
                    fds = open_devices()
                    if fds:
                        logger.info(f"Touch wake: found {len(fds)} devices on rescan")
                    last_rescan = time.time()

                if not fds:
                    self._wake_event.wait(timeout=1.0)
                    continue

                try:
                    readable, _, _ = select.select(fds, [], [], 1.0)
                except (OSError, ValueError):
                    # FDs became invalid (USB disconnected by monitor standby)
                    close_fds(fds)
                    fds = []
                    logger.info("Touch wake: input devices lost, will re-scan")
                    continue

                for fd in readable:
                    try:
                        data = os.read(fd, 4096)
                        if data and self.sleeping:
                            logger.info("Touch wake: input detected, waking display")
                            self.wake_display()
                            return
                    except OSError:
                        # Single device disconnected
                        try:
                            os.close(fd)
                        except Exception:
                            pass
                        fds.remove(fd)
        finally:
            close_fds(fds)

    def _check(self):
        if not self.enabled:
            if self.sleeping:
                self._set_sleep(False)
            return
        should_sleep = self._in_off_period()
        if should_sleep and not self.sleeping:
            self._set_sleep(True)
        elif not should_sleep and self.sleeping:
            self._set_sleep(False)

    def _loop(self):
        while True:
            self._check()
            time.sleep(30)

    def _in_off_period(self):
        from datetime import datetime
        now = datetime.now()
        now_mins = now.hour * 60 + now.minute
        # JS getDay(): 0=Sunday, 1=Monday, ..., 6=Saturday
        # Python weekday(): 0=Monday, ..., 6=Sunday → convert
        js_day = (now.weekday() + 1) % 7
        off_parts = self.off_time.split(':')
        on_parts = self.on_time.split(':')
        off_mins = int(off_parts[0]) * 60 + int(off_parts[1])
        on_mins = int(on_parts[0]) * 60 + int(on_parts[1])

        if off_mins > on_mins:
            # e.g. 22:00 - 07:00 (overnight)
            in_period = now_mins >= off_mins or now_mins < on_mins
            # Check weekday: if after off_time, check today; if before on_time, check yesterday
            if now_mins >= off_mins:
                day_to_check = js_day
            else:
                day_to_check = (js_day - 1) % 7
        else:
            # e.g. 01:00 - 06:00 (same day)
            in_period = off_mins <= now_mins < on_mins
            day_to_check = js_day

        if not in_period:
            return False
        return day_to_check in self.weekdays
