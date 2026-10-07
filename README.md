# Digital Photo Frame

A self-contained digital photo frame system for Raspberry Pi Zero 2 W. Each Pi connects directly to photo sources (Synology Photos, Google Photos, Immich) via Tailscale or HTTPS, downloads and processes photos on-device, and displays them as a full-screen slideshow in a Chromium kiosk.

## How It Works

```
Photo Sources
  ├── Synology Photos (share links via Tailscale)
  ├── Google Photos  (public shared album URLs)
  └── Immich         (share links)
        │
        ▼
Raspberry Pi Zero 2 W
  frame.sync
    ├── Connects to all configured albums
    ├── Downloads new photos, removes deleted ones
    ├── Processes into current orientation (blur-fill backgrounds)
    └── Keeps ≤100 photos in alternate orientation as buffer

  frame.server (HTTP :8080)
    ├── Serves slideshow viewer (labwc + Chromium kiosk)
    ├── Settings overlay (touch hot corner)
    ├── Remote config page (/remote)
    ├── First-time setup wizard
    ├── WiFi hotspot fallback (captive portal)
    └── Sleep schedule with touch-to-wake

  viewer/index.html
    └── Full-screen slideshow with fade transitions + swipe nav
```

## Features

- **Multi-source sync** — pull from Synology Photos, Google Photos, and Immich shared albums simultaneously
- **Self-contained Pi** — no NAS processing needed, everything runs on the Pi
- **First-time setup wizard** — guided 7-step setup (language, WiFi, Tailscale, settings, sleep test, albums, done)
- **WiFi hotspot fallback** — creates `PhotoFrame-Setup` hotspot with captive portal when no WiFi is available
- **Auto-update on boot** — pulls latest code from GitHub before starting
- **Orientation-aware** — processes photos only for the active orientation (horizontal/vertical), keeps 100 in the other as buffer
- **Smart fill** — same-orientation photos crop to fill, cross-orientation get blur-fill backgrounds
- **Screen resolution detection** — auto-detects display resolution via wlr-randr or framebuffer
- **HEIC support** — handles iPhone photos natively via pillow-heif
- **Touch UI** — swipe navigation, hot corner settings overlay, touch-to-wake from sleep
- **Remote config** — phone-friendly web UI at `/remote` to manage albums, trigger sync (QR code from settings)
- **Album names** — resolves and displays actual album names, sorted alphabetically
- **Sleep schedule** — configurable sleep/wake times per weekday, stops Chromium to free RAM, configurable backlight control (DDC/CI, DPMS, brightness, or black-only fallback)
- **Stale cleanup** — photos removed from source albums are automatically deleted locally
- **Bilingual** — English and German (i18n)

## Requirements

### Hardware
- Raspberry Pi Zero 2 W (or any Pi)
- Display with HDMI input (tested: ANMITE 14" 1920x1200 touchscreen)
- Optional: touchscreen for swipe/settings

### Software
- Raspberry Pi OS Trixie (Debian 13)
- Python 3.11+, labwc, Chromium, seatd
- Tailscale (for Synology Photos access over VPN)

## Setup

Flashing the ready-made image (Option A) is the easiest path. Use Option B/C
only if you want to build from source.

> 🇩🇪 Eine deutsche Kurzanleitung gibt es in [ANLEITUNG.md](ANLEITUNG.md).

### Option A — Flash the ready-made image (recommended)

1. Download the latest `digital-photo-frame-*.img.xz` from the
   [Releases page](https://github.com/rwkaspar/digital_photo_frame/releases).
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
   *Choose OS → Use custom* → pick the `.img.xz`. In the customization dialog
   set your **WiFi**, **locale/timezone**, and enable **SSH** if you want
   remote access — these still apply on top of the image.
3. Insert the card and power on. The first boot provisions itself (a few
   minutes) and then shows the setup wizard.
4. Configure your photo albums via the on-screen wizard — or, if no network
   was set, connect to the **`PhotoFrame-Setup`** WiFi hotspot it creates and
   follow the captive portal.

- **Target:** 64-bit (arm64), **Raspberry Pi Zero 2 W or newer**.
- **Login:** user `frame`, password `photoframe` — change it after first login
  (`passwd`). If SSH is enabled, reach it at `photoframe.local`.

### Option B — Prepare an SD card from source

Works on **Windows**, **macOS**, and **Linux** — no ext4 access needed.

1. **Flash** Raspberry Pi OS with [Pi Imager](https://www.raspberrypi.com/software/):
   - User: `frame_user` (set a password you'll remember)
   - SSH: enabled
   - WiFi: enter your network (or skip — the frame creates a setup hotspot)

2. **Re-insert** the SD card so the boot partition (FAT32) mounts

3. **Run the prepare script:**

   **Windows** (no git required):
   1. Download [`prepare_sd.bat`](https://raw.githubusercontent.com/rwkaspar/digital_photo_frame/main/scripts/prepare_sd.bat) (right-click → Save as)
   2. Open a terminal where you saved the file
   3. Run:
      ```cmd
      prepare_sd.bat F:
      ```
      Replace `F:` with the drive letter of the boot partition.
      The script downloads everything from GitHub automatically.

   **Linux / macOS:**
   ```bash
   git clone https://github.com/rwkaspar/digital_photo_frame.git
   cd digital_photo_frame
   bash scripts/prepare_sd.sh
   ```
   The script auto-detects the boot partition. Pass the path manually if needed:
   ```bash
   bash scripts/prepare_sd.sh /media/$USER/bootfs
   ```

4. **Eject** the SD card and insert into the Pi

5. **Wait for 3 boots** (takes ~10 minutes total):
   - **Boot 1:** Pi Imager settings apply (user, WiFi, SSH), then the bootstrap script copies the repo to the home directory and reboots
   - **Boot 2:** Full setup runs (packages, Python venv, systemd services) and reboots
   - **Boot 3:** Photo frame starts — the setup wizard appears on screen

6. **Monitor progress** (optional):
   ```bash
   ssh frame_user@<pi-ip> journalctl -fu photo-frame-firstboot
   ```

### Option C — Manual setup on the Pi

If you prefer to set up directly on the Pi:

```bash
git clone https://github.com/rwkaspar/digital_photo_frame.git
cd digital_photo_frame
sudo bash scripts/setup_pi.sh
```

### Updating an Existing Frame

```bash
bash ~/digital_photo_frame/scripts/deploy_update.sh
```

### Configuration

Edit `config_frame.yaml` or configure via the setup wizard / remote UI:

```yaml
setup_complete: false  # Set to true after wizard completes

frame:
  name: "living-room"
  orientation: "horizontal"  # or "vertical"

synology:
  share_urls:
    # Use Tailscale IP directly in the URL
    - "https://100.101.43.67:5443/mo/sharing/AbCdEfG"
  share_passphrases:
    - "your-passphrase"

google_photos:
  share_urls:
    - "https://photos.app.goo.gl/yourAlbumLink"

immich:
  share_urls: []
  share_passphrases: []

photos:
  base_dir: "/srv/frame/photos"
  quality: 85
  blur_radius: 40
  blur_darken: 0.6
  horizontal:
    width: 1920
    height: 1200
  vertical:
    width: 1200
    height: 1920

slideshow:
  interval: 10
  fade_duration: 1.0

energy_save:
  method: "ddcci"  # ddcci | dpms | brightness | black_only
```

### Enable Services

```bash
sudo systemctl enable --now photo_frame_server photo_frame_cage
```

Sync runs manually ("Sync Now" button in settings) or automatically during sleep mode.

## Project Structure

```
digital_photo_frame/
├── frame/                     # Python package
│   ├── server.py              # HTTP server startup + handler factory
│   ├── routes.py              # All HTTP endpoints
│   ├── config.py              # AppState (replaces global singletons)
│   ├── energy.py              # EnergySaveManager + SysinfoCache
│   ├── sync.py                # PhotoSyncer (multi-source sync engine)
│   ├── database.py            # SQLite photo state DB
│   ├── processing.py          # Blur-fill photo processing (subprocess)
│   ├── wifi.py                # WiFi hotspot fallback manager
│   └── clients/
│       ├── synology.py        # Synology Photos API client
│       ├── google_photos.py   # Google Photos scraper
│       └── immich.py          # Immich API client
├── viewer/
│   ├── index.html             # Slideshow viewer (Chromium kiosk)
│   ├── remote.html            # Phone-friendly remote config UI
│   ├── setup.html             # WiFi setup captive portal page
│   └── wizard.html            # First-time setup wizard (7 steps)
├── systemd/                   # Systemd unit file references
├── scripts/
│   ├── prepare_sd.sh          # SD card prep (Linux/macOS)
│   ├── prepare_sd.bat         # SD card prep (Windows)
│   ├── photo_frame_bootstrap.sh # First-boot bootstrap (copies repo from boot partition)
│   ├── setup_pi.sh            # Full Pi setup (packages, venv, services)
│   ├── deploy_update.sh       # Update existing frame to latest version
│   ├── update.sh              # Auto-update script (git pull + pip)
│   └── generate_defaults.py   # Generate placeholder photos
├── config_frame.yaml          # Per-frame configuration template
├── photo_sync.py              # Backward-compat shim → frame.sync
├── viewer_server.py           # Backward-compat shim → frame.server
└── requirements.txt
```

## Boot Flow

```
network-online.target
  → photo_frame_update.service  (git pull + pip install, fail-ok)
    → photo_frame_server.service  (frame.server)
        IF no WiFi → hotspot "PhotoFrame-Setup" + captive portal
        IF setup_complete=false → setup wizard
        IF normal → slideshow (no auto-sync)
      → photo_frame_cage.service  (labwc + Chromium kiosk)
```

## Remote Config (`/remote`)

Access from your phone by scanning the QR code shown in Settings > Photo Albums > Edit:

- **Synology Photos** — add/remove albums with share URL + passphrase
- **Google Photos** — add/remove shared album URLs (no auth needed)
- **Immich** — add/remove shared album links
- Album names are resolved automatically and displayed alphabetically
- Save persists to `config_frame.yaml`
- "Sync Now" triggers an immediate sync run

## Sleep Mode

Sleep mode stops Chromium (frees ~125MB RAM) and displays a black screen:

1. Stops `photo_frame_cage` service
2. Fills framebuffer with black (keeps HDMI signal active — no blue "no signal")
3. Turns off backlight via configured method:
   - `ddcci` — DDC/CI power mode standby (`ddcutil setvcp d6 4/1`)
   - `dpms` — Wayland DPMS (`wlopm --off/--on`)
   - `brightness` — DDC/CI brightness to 0 (`ddcutil setvcp 10 0/100`)
   - `black_only` — framebuffer only, no backlight control (fallback)
4. Triggers photo sync (more RAM available with Chromium stopped)
5. Listens for touch input to wake

Wake starts cage first, waits 5s for Chromium to render, then restores backlight. The setup wizard tests each method interactively to find the one that works for your monitor.

## Troubleshooting

### Sync not working

```bash
# Check sync status
curl http://localhost:8080/sync/status

# Check logs
journalctl -u photo_frame_server -n 50

# Trigger manual sync
curl -X POST http://localhost:8080/sync/trigger
```

### Blue screen during sleep

If the monitor shows a blue "no signal" screen during sleep, try a different sleep method in the config:

```bash
# Test DDC/CI power mode (default)
sudo ddcutil setvcp d6 4   # standby
sudo ddcutil setvcp d6 1   # wake

# Test DPMS
wlopm --off '*'
wlopm --on '*'

# Test brightness
sudo ddcutil setvcp 10 0    # off
sudo ddcutil setvcp 10 80   # restore
```

### No photos displaying

```bash
# Check photos exist
ls /srv/frame/photos/horizontal/ | wc -l
ls /srv/frame/photos/vertical/ | wc -l

# Check orientation
grep orientation config_frame.yaml

# Restart display
sudo systemctl restart photo_frame_cage
```

## License

This project is open source and available under the MIT License.

## Building the image (maintainers)

A GitHub Actions workflow builds a 64-bit Raspberry Pi OS image with the photo
frame pre-installed, so you can flash it straight from Raspberry Pi Imager
instead of running the SD-prep scripts.

- **Target:** arm64 only, Raspberry Pi Zero 2 W and newer.
- **Build:** push a `vX.Y.Z` tag (the image is attached to the GitHub release),
  or trigger *Build Pi image* manually under the Actions tab.
- **What it bakes:** all apt dependencies pre-installed (so first boot is fast),
  the app on the boot partition, and a first-boot service that runs the normal
  `photo_frame_bootstrap.sh` → `setup_pi.sh` provisioning (venv, services,
  config) for whatever user exists.
- **Flashing:** open the `.img.xz` in Raspberry Pi Imager. Imager's WiFi / SSH /
  locale customization still applies. The appliance user is `frame`
  (password `photoframe`). Configure photo albums via the on-screen setup
  wizard / hotspot captive portal on first boot.

The build definition lives in `.github/workflows/build-image.yml` and the
pi-gen stage in `imaging/stage-photoframe/`.
