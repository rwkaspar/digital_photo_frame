#!/bin/bash -e
# Runs inside the image chroot. Bakes the photo-frame app and a first-boot
# bootstrap into the image. The heavy apt packages are already installed by
# 00-packages, so first boot only has to build the venv and wire up services
# (via the existing scripts/setup_pi.sh), which is fast.
#
# We deliberately reuse the proven provisioning flow instead of hardcoding a
# user at build time: photo_frame_bootstrap.sh detects whatever user Pi Imager
# created on first boot and sets everything up for it.

REPO_URL="${PHOTOFRAME_REPO_URL:-https://github.com/rwkaspar/digital_photo_frame.git}"
REPO_REF="${PHOTOFRAME_REPO_REF:-main}"
BOOT_DIR="/boot/firmware"
BOOT_REPO="${BOOT_DIR}/photo_frame"

echo "Baking photo-frame app from ${REPO_URL}@${REPO_REF}"

# App repo onto the boot partition; the bootstrap copies it to the detected
# user's home on first boot.
rm -rf "${BOOT_REPO}"
git clone --depth 1 --branch "${REPO_REF}" "${REPO_URL}" "${BOOT_REPO}"
# Drop the .git dir to keep the boot (FAT32) partition small; setup_pi.sh
# re-initialises git from origin on first boot for auto-update.
rm -rf "${BOOT_REPO}/.git"

cp "${BOOT_REPO}/scripts/photo_frame_bootstrap.sh" "${BOOT_DIR}/photo_frame_bootstrap.sh"
chmod +x "${BOOT_DIR}/photo_frame_bootstrap.sh"

# i2c-dev is needed for ddcutil (DDC/CI backlight control).
echo "i2c-dev" > /etc/modules-load.d/i2c-dev.conf

# Run the bootstrap once on first boot. It copies the repo to the user's home,
# installs the full setup service (setup_pi.sh), and reboots into the frame.
cat > /etc/systemd/system/photo-frame-bootstrap.service <<'UNIT'
[Unit]
Description=Photo Frame Bootstrap (first boot)
After=multi-user.target
ConditionPathExists=/boot/firmware/photo_frame_bootstrap.sh

[Service]
Type=oneshot
ExecStart=/bin/bash /boot/firmware/photo_frame_bootstrap.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl enable photo-frame-bootstrap.service

echo "photo-frame bake complete"
