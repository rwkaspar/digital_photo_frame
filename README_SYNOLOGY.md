# Digital Photo Frame - Synology Photos Integration

A digital photo frame solution for Raspberry Pi Zero 2 W that syncs photos from Synology Photos via public share links and displays them in a web-based kiosk viewer.

## Features

- 📸 **Synology Photos Integration**: Syncs photos from shared albums via API
- 🎲 **Smart Selection**: Randomly selects 200 photos weekly with cooldown system
- 🖼️ **Web-Based Viewer**: Chromium kiosk mode with smooth fade transitions  
- 🔄 **Automatic Sync**: Weekly scheduled sync via systemd timer
- 👆 **Touch Controls**: Hot corner for shutdown, swipe/tap navigation
- 📊 **State Tracking**: SQLite database prevents repetitive photo selection
- 🔐 **Password Protected**: Supports password-protected share links
- 🖥️ **Multiple Frames**: Easy configuration for multiple photo frames
- 🛠️ **Tailscale Ready**: Remote management via Tailscale

## Requirements

### Hardware
- Raspberry Pi Zero 2 W (or any Raspberry Pi)
- Display with HDMI (e.g., Anmite 14" IPS)
- Touch screen (optional, for hot corner)
- SD card with Raspberry Pi OS

### Software
- Raspberry Pi OS (Desktop, 32-bit recommended)
- Python 3.7+
- Chromium browser
- Synology Photos with a shared album

## Installation

### Quick Install

1. Clone this repository to your Raspberry Pi:
```bash
git clone https://github.com/rwkaspar/digital_photo_frame.git
cd digital_photo_frame
```

2. Run the installation script:
```bash
chmod +x install_synology.sh
./install_synology.sh
```

This will:
- Install system dependencies (Python, Chromium)
- Install Python packages (requests, PyYAML, Pillow)
- Set up systemd services
- Create necessary directories

### Configuration

Edit the configuration file with your Synology Photos details:

```bash
nano ~/digital_photo_frame/config_synology.yaml
```

Key settings to configure:

```yaml
synology:
  # Your Synology Photos share URL
  share_url: "https://photos.kaspar-family.org/mo/sharing/YOUR_SHARE_TOKEN"
  # Password for the shared album
  share_passphrase: "your_password"
  # Base URL for your Synology Photos instance
  base_url: "https://photos.kaspar-family.org"

sync:
  # Number of photos to select each week
  photos_per_week: 200
  # Local storage path
  photos_dir: "/srv/frame/photos"

slideshow:
  # Time between photos in seconds
  interval: 10
  # Hot corner position
  hot_corner_position: "top-left"
```

### Initial Sync

Run the initial sync to download photos:

```bash
cd ~/digital_photo_frame
python3 sync_photos.py config_synology.yaml
```

This will:
1. Connect to your Synology Photos share
2. Fetch all photos from the shared album
3. Randomly select 200 photos
4. Download them to `/srv/frame/photos`
5. Create a state database to track selections

### Enable Auto-Start

Enable the systemd services to start automatically on boot:

```bash
# Enable weekly sync timer
sudo systemctl enable photo_frame_sync.timer
sudo systemctl start photo_frame_sync.timer

# Enable photo server
sudo systemctl enable photo_frame_server.service
sudo systemctl start photo_frame_server.service

# Enable Chromium kiosk viewer
sudo systemctl enable photo_frame_viewer.service
sudo systemctl start photo_frame_viewer.service
```

## Usage

### Manual Sync

To manually trigger a sync:

```bash
sudo systemctl start photo_frame_sync.service
```

Or run directly:

```bash
cd ~/digital_photo_frame
python3 sync_photos.py config_synology.yaml
```

### View Logs

Check sync logs:
```bash
journalctl -u photo_frame_sync.service -f
```

Check server logs:
```bash
journalctl -u photo_frame_server.service -f
```

Check viewer logs:
```bash
journalctl -u photo_frame_viewer.service -f
```

### Hot Corner Shutdown

- Touch (or click) the top-left corner (by default) to bring up shutdown confirmation
- Confirm to shut down the Raspberry Pi
- Position configurable in `config_synology.yaml`

### Manual Navigation

- **Touch/Click**: Skip to next photo
- **Swipe**: Skip to next photo
- **Keyboard Space**: Skip to next photo (if keyboard connected)
- **Keyboard Esc/Q**: Shutdown prompt

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Synology Photos (Cloud)                    │
│                  Shared Album with Password                  │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTPS API
                             │ (weekly sync)
┌────────────────────────────▼────────────────────────────────┐
│                   Raspberry Pi Zero 2 W                      │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  sync_photos.py (systemd timer, weekly)              │  │
│  │  - Fetch photos from API                             │  │
│  │  - Smart selection (200 random, weighted)            │  │
│  │  - Download to /srv/frame/photos                     │  │
│  │  - Update SQLite state                               │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  viewer_server.py (systemd service)                  │  │
│  │  - HTTP server on port 8000                          │  │
│  │  - Serves viewer HTML                                │  │
│  │  - Generates /list                            │  │
│  │  - Serves photo files                                │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Chromium Browser (kiosk mode)                       │  │
│  │  - Loads viewer at http://localhost:8000             │  │
│  │  - Fullscreen slideshow with fade transitions        │  │
│  │  - Touch controls & hot corner                       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Sync Process

1. **Authentication**: Connects using share URL and passphrase
2. **Fetch**: Paginated API calls to get all photos in album
3. **Database Update**: Records photo metadata in SQLite
4. **Smart Selection**: 
   - Weighted random selection (avoid recent/frequent photos)
   - Photos shown more times have lower selection weight
   - Photos not shown this week get priority boost
5. **Download**: Downloads selected 200 photos to local storage
6. **Cleanup**: Removes old photos from previous week

### Viewer

- Pure HTML/CSS/JavaScript slideshow
- Loads photo list from `/list` endpoint
- Preloads images for smooth transitions
- CSS fade transitions between photos
- Wake Lock API to prevent screen sleep
- Hot corner for power management

## Configuration Reference

### Synology Settings

```yaml
synology:
  share_url: ""           # Full URL to shared album
  share_passphrase: ""    # Password for share
  base_url: ""            # Base URL of Synology Photos
```

### Sync Settings

```yaml
sync:
  photos_per_week: 200           # Number of photos to select
  include_videos: false          # Include videos (not yet supported)
  max_show_count: 10             # Max times before cooldown
  page_limit: 100                # API pagination size
  photos_dir: "/srv/frame/photos"  # Local storage
  state_db: "/srv/frame/state.db"  # SQLite database
```

### Slideshow Settings

```yaml
slideshow:
  interval: 10                   # Seconds between photos
  transition: "fade"             # Transition effect
  fade_duration: 1.0             # Fade duration in seconds
  fullscreen: true               # Fullscreen mode
  hot_corner_size: 50            # Hot corner size in pixels
  hot_corner_position: "top-left"  # top-left, top-right, bottom-left, bottom-right
```

### System Settings

```yaml
system:
  autostart: true                # Enable autostart
  sync_schedule: "weekly"        # Sync frequency
  sync_day: 0                    # Day of week (0=Mon, 6=Sun)
  sync_hour: 3                   # Hour for sync (0-23)
  chromium_path: "/usr/bin/chromium-browser"
  viewer_url: "file:///srv/frame/viewer/index.html"
```

## Multiple Photo Frames

To set up multiple frames:

1. Copy the configuration file for each frame:
```bash
cp config_synology.yaml config_frame2.yaml
```

2. Edit each config with different:
   - Share URLs (different albums or same album)
   - Photos directory (`/srv/frame2/photos`)
   - State database (`/srv/frame2/state.db`)

3. Create separate systemd service files pointing to different configs

4. Each frame can have different selection logic while sharing the same codebase

## Troubleshooting

### No Photos Displayed

1. Check if photos were downloaded:
```bash
ls -la /srv/frame/photos
```

2. Check sync logs:
```bash
journalctl -u photo_frame_sync.service -n 50
```

3. Test sync manually:
```bash
cd ~/digital_photo_frame
python3 sync_photos.py config_synology.yaml
```

### Viewer Won't Start

1. Check server is running:
```bash
sudo systemctl status photo_frame_server.service
```

2. Test server manually:
```bash
cd ~/digital_photo_frame
python3 viewer_server.py config_synology.yaml
```

3. Check if port 8000 is accessible:
```bash
curl http://localhost:8000/list
```

### Chromium Kiosk Issues

1. Check X11 display:
```bash
echo $DISPLAY  # Should be :0
```

2. Test Chromium manually:
```bash
DISPLAY=:0 chromium-browser --kiosk http://localhost:8000
```

3. Check viewer service logs:
```bash
journalctl -u photo_frame_viewer.service -n 50
```

### API Connection Issues

1. Test share URL in browser
2. Verify passphrase is correct
3. Check network connectivity:
```bash
ping photos.kaspar-family.org
```

4. Enable debug logging in `sync_photos.py`

## Raspberry Pi Optimization

### Reduce Boot Time

1. Disable unnecessary services:
```bash
sudo systemctl disable bluetooth
sudo systemctl disable hciuart
```

2. Use lite boot (no desktop until needed):
```bash
sudo raspi-config
# Boot Options -> Console Autologin
```

### GPU Memory

Allocate more memory to GPU for better display performance:

```bash
sudo raspi-config
# Performance Options -> GPU Memory -> 128
```

### Prevent Screen Blanking

Edit `/boot/config.txt`:
```
hdmi_blanking=1
```

Or use systemd to run xset:
```bash
# Add to photo_frame_viewer.service
ExecStartPre=/usr/bin/xset -display :0 s off
ExecStartPre=/usr/bin/xset -display :0 -dpms
ExecStartPre=/usr/bin/xset -display :0 s noblank
```

## Tailscale Integration

For remote management:

1. Install Tailscale:
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

2. Connect via SSH over Tailscale:
```bash
ssh pi@photo-frame-hostname.tailnet
```

3. Access viewer remotely:
```
http://photo-frame-hostname.tailnet:8000
```

## Advanced Usage

### Custom Selection Algorithm

Edit `sync_photos.py`, modify `get_weighted_selection()` method to change how photos are selected.

### Add Videos Support

1. Set `include_videos: true` in config
2. Update viewer to support `<video>` tags
3. Modify download logic to handle video formats

### Integration with Home Assistant

Expose the server API and use Home Assistant to:
- Trigger manual syncs
- Monitor photo count
- Control frame power

## Development

### Project Structure

```
digital_photo_frame/
├── sync_photos.py              # Main sync script
├── viewer_server.py            # HTTP server
├── config_synology.yaml        # Configuration
├── requirements.txt            # Python dependencies
├── install_synology.sh         # Installation script
├── viewer/
│   └── index.html              # Slideshow viewer
├── photo_frame_sync.service    # Systemd sync service
├── photo_frame_sync.timer      # Systemd sync timer
├── photo_frame_server.service  # Systemd server service
└── photo_frame_viewer.service  # Systemd viewer service
```

### Testing

Test sync without affecting production:
```bash
python3 sync_photos.py config_test.yaml
```

Test viewer locally:
```bash
python3 viewer_server.py config_test.yaml
# Open http://localhost:8000 in browser
```

## License

This project is open source and available under the MIT License.

## Credits

Created for Raspberry Pi Zero 2 W digital photo frame projects with Synology Photos integration.

Based on the conversation and requirements discussed in the repository's HTML conversation file.

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Check the conversation HTML for original requirements
- Review systemd logs for troubleshooting
