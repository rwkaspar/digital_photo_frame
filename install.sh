#!/bin/bash
# Installation script for Digital Photo Frame on Raspberry Pi

set -e

echo "=== Digital Photo Frame Installation ==="
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
mkdir -p "$INSTALL_DIR/images"

# Copy files
echo "Copying files..."
cp photo_frame.py "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"

# Install system dependencies
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt"

# Setup systemd service
echo "Setting up systemd service..."
sudo cp photo_frame.service /etc/systemd/system/
sudo sed -i "s|/home/pi/digital_photo_frame|$INSTALL_DIR|g" /etc/systemd/system/photo_frame.service
sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/photo_frame.service
sudo sed -i "s|/home/pi/.Xauthority|$HOME/.Xauthority|g" /etc/systemd/system/photo_frame.service

# Reload systemd
sudo systemctl daemon-reload

echo
echo "=== Installation Complete ==="
echo
echo "Add your photos to: $INSTALL_DIR/images"
echo
echo "To start the photo frame manually:"
echo "  cd $INSTALL_DIR"
echo "  python3 photo_frame.py"
echo
echo "To enable auto-start on boot:"
echo "  sudo systemctl enable photo_frame.service"
echo "  sudo systemctl start photo_frame.service"
echo
echo "To check status:"
echo "  sudo systemctl status photo_frame.service"
echo
echo "To view logs:"
echo "  tail -f $INSTALL_DIR/photo_frame.log"
echo
