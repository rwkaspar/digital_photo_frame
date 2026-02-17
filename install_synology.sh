#!/bin/bash
# Installation script for Synology Photos Digital Photo Frame on Raspberry Pi

set -e

echo "=== Digital Photo Frame Installation (Synology Photos Version) ==="
echo

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This script is designed for Raspberry Pi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Get installation directory
INSTALL_DIR="${1:-$HOME/digital_photo_frame}"
echo "Installing to: $INSTALL_DIR"

# Create installation directory
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/viewer"
mkdir -p /srv/frame/photos
mkdir -p /srv/frame/viewer

# Copy files
echo "Copying files..."
cp sync_photos.py "$INSTALL_DIR/"
cp viewer_server.py "$INSTALL_DIR/"
cp config_synology.yaml "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"
cp viewer/index.html "$INSTALL_DIR/viewer/"

# Also copy to /srv/frame for systemd services
sudo cp -r "$INSTALL_DIR/viewer" /srv/frame/
sudo chown -R $USER:$USER /srv/frame

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv chromium-browser

# Create Python virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"

# Install Python dependencies in venv
echo "Installing Python dependencies in venv..."
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Set config file permissions
chmod 600 "$INSTALL_DIR/config_synology.yaml"

# Setup systemd services
echo "Setting up systemd services..."

# Update paths in service files
for service in photo_frame_sync.service photo_frame_sync.timer photo_frame_server.service photo_frame_viewer.service; do
    sudo cp "$service" /etc/systemd/system/
    sudo sed -i "s|/home/pi/digital_photo_frame|$INSTALL_DIR|g" "/etc/systemd/system/$service"
    sudo sed -i "s|User=pi|User=$USER|g" "/etc/systemd/system/$service"
    sudo sed -i "s|/home/pi/.Xauthority|$HOME/.Xauthority|g" "/etc/systemd/system/$service"
done

# Reload systemd
sudo systemctl daemon-reload

echo
echo "=== Configuration Required ==="
echo
echo "Edit the configuration file:"
echo "  nano $INSTALL_DIR/config_synology.yaml"
echo
echo "You need to set:"
echo "  - synology.share_url: Your Synology Photos share URL"
echo "  - synology.share_passphrase: Password for the share"
echo "  - synology.base_url: Your Synology Photos base URL"
echo
echo "=== Initial Sync ==="
echo
echo "After configuring, run the initial sync:"
echo "  cd $INSTALL_DIR"
echo "  ./venv/bin/python sync_photos.py config_synology.yaml"
echo
echo "=== Enable Services ==="
echo
echo "To enable auto-start on boot:"
echo "  sudo systemctl enable photo_frame_sync.timer"
echo "  sudo systemctl enable photo_frame_server.service"
echo "  sudo systemctl enable photo_frame_viewer.service"
echo
echo "To start services now:"
echo "  sudo systemctl start photo_frame_sync.timer"
echo "  sudo systemctl start photo_frame_server.service"
echo "  sudo systemctl start photo_frame_viewer.service"
echo
echo "=== Check Status ==="
echo
echo "  sudo systemctl status photo_frame_sync.timer"
echo "  sudo systemctl status photo_frame_server.service"
echo "  sudo systemctl status photo_frame_viewer.service"
echo
echo "=== Logs ==="
echo
echo "  journalctl -u photo_frame_sync.service -f"
echo "  journalctl -u photo_frame_server.service -f"
echo "  journalctl -u photo_frame_viewer.service -f"
echo
echo "=== Manual Testing ==="
echo
echo "Test the sync:"
echo "  cd $INSTALL_DIR && ./venv/bin/python sync_photos.py config_synology.yaml"
echo
echo "Test the server:"
echo "  cd $INSTALL_DIR && ./venv/bin/python viewer_server.py config_synology.yaml"
echo "  Then open http://localhost:8000 in a browser"
echo
echo "=== Installation Complete ==="
