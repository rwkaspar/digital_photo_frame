# Digital Photo Frame

A Python-based digital photo frame application designed for Raspberry Pi Zero 2 W. Display your favorite photos in a beautiful slideshow with smooth transitions and easy configuration.

## Features

- 📸 **Automatic Slideshow**: Displays images from a specified directory with configurable intervals
- 🎨 **Smooth Transitions**: Fade effects between images for a professional look
- 🖼️ **Smart Scaling**: Images are automatically scaled to fit the screen while maintaining aspect ratio
- 🔀 **Shuffle Mode**: Random display order for variety
- 📁 **Recursive Scanning**: Supports subdirectories for organized photo collections
- 🎛️ **Easy Configuration**: YAML-based configuration for all settings
- 🚀 **Auto-Start**: Systemd service for automatic startup on boot
- 📝 **Logging**: Comprehensive logging for monitoring and debugging
- ⌨️ **Keyboard Controls**: Manual control and navigation options

## Requirements

### Hardware
- Raspberry Pi Zero 2 W (or any Raspberry Pi)
- Display (HDMI or DSI)
- SD card with Raspberry Pi OS

### Software
- Python 3.7+
- pygame
- Pillow (PIL)
- PyYAML

## Installation

### Quick Install

1. Clone this repository to your Raspberry Pi:
```bash
git clone https://github.com/rwkaspar/digital_photo_frame.git
cd digital_photo_frame
```

2. Run the installation script:
```bash
chmod +x install.sh
./install.sh
```

This will:
- Install system dependencies
- Install Python packages
- Set up the systemd service
- Create the necessary directories

### Manual Installation

If you prefer to install manually:

1. Install system dependencies:
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-pygame
```

2. Install Python dependencies:
```bash
pip3 install -r requirements.txt
```

3. Copy the files to your desired location (e.g., `~/digital_photo_frame`)

## Configuration

Edit the `config.yaml` file to customize your photo frame:

```yaml
display:
  fullscreen: true          # Run in fullscreen mode
  width: 800               # Window width (if fullscreen is false)
  height: 600              # Window height (if fullscreen is false)

slideshow:
  interval: 10             # Seconds between images
  shuffle: true            # Randomize image order
  transition: fade         # Transition effect ('fade' or 'none')
  fade_duration: 1.0       # Fade transition duration in seconds

images:
  directory: ./images      # Path to your photos
  recursive: true          # Include subdirectories
  extensions:              # Supported image formats
    - .jpg
    - .jpeg
    - .png
    - .bmp
    - .gif

logging:
  level: INFO             # Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
  file: photo_frame.log   # Log file path
```

## Usage

### Adding Photos

Place your photos in the `images` directory (or the directory specified in `config.yaml`):

```bash
cp ~/Pictures/*.jpg ~/digital_photo_frame/images/
```

You can organize photos in subdirectories if `recursive: true` is set in the configuration.

### Running Manually

To start the photo frame:

```bash
cd ~/digital_photo_frame
python3 photo_frame.py
```

Or specify a custom config file:

```bash
python3 photo_frame.py /path/to/custom_config.yaml
```

### Keyboard Controls

While running, you can use these keyboard shortcuts:
- **ESC** or **Q**: Quit the application
- **SPACE**: Skip to next image immediately
- **R**: Reload the image list (useful when adding new photos)

### Auto-Start on Boot

To enable automatic startup:

```bash
sudo systemctl enable photo_frame.service
sudo systemctl start photo_frame.service
```

Check the service status:

```bash
sudo systemctl status photo_frame.service
```

View logs:

```bash
tail -f ~/digital_photo_frame/photo_frame.log
# or
journalctl -u photo_frame.service -f
```

Stop the service:

```bash
sudo systemctl stop photo_frame.service
```

Disable auto-start:

```bash
sudo systemctl disable photo_frame.service
```

## Raspberry Pi Zero 2 W Optimization

For optimal performance on Raspberry Pi Zero 2 W:

1. **Reduce Image Resolution**: Large images can slow down the slideshow. Consider resizing your photos to 1920x1080 or smaller before adding them.

2. **Disable Desktop Environment**: For a dedicated photo frame, you can boot directly to the application:
```bash
sudo raspi-config
# System Options > Boot / Auto Login > Console Autologin
```

3. **Memory Split**: Allocate more memory to GPU:
```bash
sudo raspi-config
# Performance Options > GPU Memory > 128
```

4. **Disable Screen Blanking**: Prevent the screen from turning off:
```bash
# Edit /boot/config.txt and add:
hdmi_blanking=1
```

## Troubleshooting

### No Display / Black Screen

1. Check X11 is running:
```bash
echo $DISPLAY
# Should output: :0
```

2. Verify XAUTHORITY:
```bash
ls -la ~/.Xauthority
```

3. Run with DISPLAY environment variable:
```bash
DISPLAY=:0 python3 photo_frame.py
```

### Images Not Loading

1. Check image directory exists and contains images:
```bash
ls -la ~/digital_photo_frame/images/
```

2. Check file permissions:
```bash
chmod -R 755 ~/digital_photo_frame/images/
```

3. Check logs for errors:
```bash
tail -f ~/digital_photo_frame/photo_frame.log
```

### Service Won't Start

1. Check service status for errors:
```bash
sudo systemctl status photo_frame.service
```

2. View full service logs:
```bash
journalctl -u photo_frame.service -n 50
```

3. Verify paths in service file:
```bash
sudo nano /etc/systemd/system/photo_frame.service
```

## Advanced Usage

### Multiple Directories

You can create symbolic links to include photos from multiple locations:

```bash
cd ~/digital_photo_frame/images
ln -s /media/usb/photos vacation_photos
ln -s ~/Dropbox/Photos dropbox_photos
```

### Network Sync

Sync photos from a network location using rsync:

```bash
# Add to crontab for automatic sync
rsync -av user@server:/path/to/photos/ ~/digital_photo_frame/images/
```

### Custom Transitions

You can extend the `PhotoFrame` class to implement custom transition effects by modifying the `photo_frame.py` file.

## Development

### Project Structure

```
digital_photo_frame/
├── photo_frame.py          # Main application
├── config.yaml            # Configuration file
├── requirements.txt       # Python dependencies
├── install.sh            # Installation script
├── photo_frame.service   # Systemd service file
├── images/               # Photo directory
└── README.md            # This file
```

### Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.

## Credits

Created for Raspberry Pi Zero 2 W digital photo frame projects.