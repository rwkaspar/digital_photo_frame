"""WiFi management: connectivity check, hotspot fallback, captive portal support."""

import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

HOTSPOT_SSID = 'PhotoFrame-Setup'
HOTSPOT_PASSWORD = 'photoframe'
HOTSPOT_CON_NAME = 'PhotoFrame-Hotspot'


class WiFiManager:
    """Manages WiFi connectivity and hotspot fallback."""

    def __init__(self):
        self.mode = 'normal'  # 'normal' or 'hotspot'
        self._lock = threading.Lock()

    def check_connectivity(self):
        """Check if wlan0 has a working non-hotspot WiFi connection."""
        try:
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device'],
                capture_output=True, text=True, timeout=10)
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 4 and parts[0] == 'wlan0' and parts[2] == 'connected':
                    if parts[3] != HOTSPOT_CON_NAME:
                        return True
            return False
        except Exception as e:
            logger.error(f"Connectivity check failed: {e}")
            return False

    def start_hotspot(self):
        """Start WiFi hotspot with captive portal redirect.

        Returns True on success, False otherwise.
        """
        with self._lock:
            if self.mode == 'hotspot':
                return True
            logger.info(f"Starting hotspot: {HOTSPOT_SSID}")
            try:
                # Remove any leftover hotspot connection from a previous attempt
                # — otherwise re-creating `con-name` fails ("already exists") and
                # the hotspot never comes up, so reconfigure silently reverts.
                subprocess.run(
                    ['nmcli', 'connection', 'delete', HOTSPOT_CON_NAME],
                    capture_output=True, timeout=10)

                # Disconnect any existing WiFi first
                subprocess.run(
                    ['nmcli', 'device', 'disconnect', 'wlan0'],
                    capture_output=True, timeout=10)
                time.sleep(1)

                # Start hotspot via NetworkManager
                result = subprocess.run(
                    ['nmcli', 'device', 'wifi', 'hotspot',
                     'ifname', 'wlan0',
                     'con-name', HOTSPOT_CON_NAME,
                     'ssid', HOTSPOT_SSID,
                     'password', HOTSPOT_PASSWORD],
                    capture_output=True, text=True, timeout=15)
                if result.returncode != 0:
                    logger.error(f"Hotspot start failed: {result.stderr.strip()}")
                    # Don't leave wlan0 stranded (disconnected + no autoconnect).
                    # Reconnect it so the frame stays reachable on its network.
                    subprocess.run(['nmcli', 'device', 'connect', 'wlan0'],
                                   capture_output=True, timeout=15)
                    return False

                # Keep the hotspot active instead of letting NetworkManager
                # auto-reconnect wlan0 to a known network a few seconds later.
                subprocess.run(
                    ['nmcli', 'connection', 'modify', HOTSPOT_CON_NAME,
                     'connection.autoconnect', 'yes',
                     'connection.autoconnect-priority', '100'],
                    capture_output=True, timeout=10)

                # Redirect port 80 -> 8080 for captive portal detection
                subprocess.run(
                    ['sudo', 'iptables', '-t', 'nat', '-A', 'PREROUTING',
                     '-i', 'wlan0', '-p', 'tcp', '--dport', '80',
                     '-j', 'REDIRECT', '--to-port', '8080'],
                    capture_output=True, timeout=5)

                self.mode = 'hotspot'
                logger.info("Hotspot started successfully")
                return True
            except Exception as e:
                logger.error(f"Failed to start hotspot: {e}")
                return False

    def stop_hotspot(self):
        """Tear down hotspot and iptables rule."""
        with self._lock:
            if self.mode != 'hotspot':
                return
            logger.info("Stopping hotspot")
            try:
                # Remove iptables redirect
                subprocess.run(
                    ['sudo', 'iptables', '-t', 'nat', '-D', 'PREROUTING',
                     '-i', 'wlan0', '-p', 'tcp', '--dport', '80',
                     '-j', 'REDIRECT', '--to-port', '8080'],
                    capture_output=True, timeout=5)
                # Bring down and delete hotspot connection
                subprocess.run(
                    ['nmcli', 'connection', 'down', HOTSPOT_CON_NAME],
                    capture_output=True, timeout=10)
                subprocess.run(
                    ['nmcli', 'connection', 'delete', HOTSPOT_CON_NAME],
                    capture_output=True, timeout=10)
                self.mode = 'normal'
                logger.info("Hotspot stopped")
            except Exception as e:
                logger.error(f"Failed to stop hotspot: {e}")

    def scan_networks(self):
        """Scan for available WiFi networks. Returns list of dicts."""
        try:
            # Trigger rescan
            subprocess.run(
                ['nmcli', 'device', 'wifi', 'rescan'],
                capture_output=True, timeout=15)
            time.sleep(2)
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list'],
                capture_output=True, text=True, timeout=10)
            networks = []
            seen = set()
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                # nmcli -t uses : as separator; SSID may contain escaped colons
                parts = line.split(':')
                if len(parts) < 3:
                    continue
                ssid = parts[0].replace('\\:', ':')
                if not ssid or ssid in seen:
                    continue
                seen.add(ssid)
                signal = 0
                try:
                    signal = int(parts[1])
                except ValueError:
                    pass
                security = parts[2] if len(parts) > 2 else ''
                networks.append({
                    'ssid': ssid,
                    'signal': signal,
                    'security': security,
                    'secured': bool(security and security != '--'),
                })
            # Sort by signal strength descending
            networks.sort(key=lambda n: n['signal'], reverse=True)
            return networks
        except Exception as e:
            logger.error(f"WiFi scan failed: {e}")
            return []

    def connect_to_network(self, ssid, password=''):
        """Stop hotspot, connect to network, verify. Restart hotspot on failure.

        Returns (success: bool, message: str).
        This is meant to be called from a background thread.
        """
        logger.info(f"Attempting to connect to: {ssid}")
        was_hotspot = self.mode == 'hotspot'

        # Stop hotspot if active
        if was_hotspot:
            self.stop_hotspot()
            time.sleep(2)

        try:
            # Try connecting
            cmd = ['nmcli', 'device', 'wifi', 'connect', ssid]
            if password:
                cmd += ['password', password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.warning(f"nmcli connect failed: {result.stderr}")
                if was_hotspot:
                    self.start_hotspot()
                return False, result.stderr.strip() or 'Connection failed'

            # Verify connectivity (wait for DHCP)
            for attempt in range(10):
                time.sleep(2)
                if self.check_connectivity():
                    logger.info(f"Connected to {ssid} successfully")
                    self.mode = 'normal'
                    return True, 'Connected'

            # Connection established but no IP / connectivity
            logger.warning(f"Connected to {ssid} but no connectivity")
            if was_hotspot:
                subprocess.run(
                    ['nmcli', 'connection', 'down', ssid],
                    capture_output=True, timeout=10)
                self.start_hotspot()
            return False, 'Connected but no internet access'

        except Exception as e:
            logger.error(f"Connect error: {e}")
            if was_hotspot:
                self.start_hotspot()
            return False, str(e)

    def get_status(self):
        """Return current WiFi status."""
        status = {
            'mode': self.mode,
            'ssid': '',
            'ip': '',
            'hotspot_ssid': HOTSPOT_SSID,
            'hotspot_password': HOTSPOT_PASSWORD,
        }
        try:
            if self.mode == 'normal':
                result = subprocess.run(
                    ['iwgetid', '-r'], capture_output=True, text=True, timeout=5)
                status['ssid'] = result.stdout.strip()
                result = subprocess.run(
                    ['hostname', '-I'], capture_output=True, text=True, timeout=5)
                ips = result.stdout.strip().split()
                status['ip'] = ips[0] if ips else ''
        except Exception:
            pass
        return status
