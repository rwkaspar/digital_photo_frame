#!/bin/bash
# Photo Frame — First-boot setup script
# Runs as root on the Pi. Expects the repo in the user's home directory
# (copied there by photo_frame_bootstrap.sh on first boot).
# Don't use set -e — we want to log errors, not silently abort
# (clear, cat to /dev/tty1, git config etc. can fail harmlessly)
trap 'log "ERROR on line $LINENO (exit $?)"' ERR

# Detect user and paths from script location
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
FRAME_HOME="$(dirname "$REPO_DIR")"
FRAME_USER="$(basename "$FRAME_HOME")"
PHOTOS_DIR="/srv/frame/photos"

# Log to boot partition (FAT32) so it's readable from Windows for debugging
SETUP_LOG="/boot/firmware/setup_log.txt"
log() {
    echo "$(date '+%H:%M:%S') $*" | tee -a "$SETUP_LOG"
}

# Helper: abort on critical errors (replaces set -e for important commands)
die() {
    log "FATAL: $*"
    sync
    exit 1
}

log "=== Photo Frame Setup ==="
log "User: ${FRAME_USER}, Repo: ${REPO_DIR}"

# ---- Verify ----
if [ "$(id -u)" -ne 0 ]; then
    die "must run as root"
fi
if [ ! -d "$REPO_DIR" ]; then
    die "repo not found at $REPO_DIR"
fi

# ---- Show setup progress on screen ----
systemctl stop getty@tty1 2>/dev/null || true
setterm --cursor off > /dev/tty1 2>/dev/null || true
# Helper: show status on both log and display
show() {
    log "$1"
    printf '\033[2J\033[H' > /dev/tty1 2>/dev/null || true
    cat > /dev/tty1 2>/dev/null << SCREEN || true

        ==========================================

           Photo Frame  -  Setup

           $1

           Please wait...

        ==========================================
SCREEN
}

# ---- Desktop display manager conflict handling ----
deactivate_display_manager() {
    log "Checking for desktop display manager conflicts..."
    local dm
    local dm_candidates=(
        "display-manager.service"
        "lightdm.service"
        "gdm.service"
        "gdm3.service"
        "sddm.service"
        "lxdm.service"
        "xdm.service"
    )

    for dm in "${dm_candidates[@]}"; do
        if systemctl is-enabled "$dm" >/dev/null 2>&1 || systemctl is-active "$dm" >/dev/null 2>&1; then
            log "Disabling conflicting service: ${dm}"
            systemctl disable --now "$dm" 2>/dev/null || log "WARNING: could not disable ${dm}"
        fi
    done

    if ! systemctl set-default multi-user.target >/dev/null 2>&1; then
        log "WARNING: failed to set default target to multi-user.target"
    else
        log "Default target set to multi-user.target (no desktop login on boot)."
    fi
}

assert_no_display_manager_conflict() {
    local dm
    local active_dms=()
    local dm_candidates=(
        "display-manager.service"
        "lightdm.service"
        "gdm.service"
        "gdm3.service"
        "sddm.service"
        "lxdm.service"
        "xdm.service"
    )

    for dm in "${dm_candidates[@]}"; do
        if systemctl is-active "$dm" >/dev/null 2>&1; then
            active_dms+=("$dm")
        fi
    done

    if [ "${#active_dms[@]}" -gt 0 ]; then
        die "desktop display manager still active (${active_dms[*]}). This blocks DRM/seat0 for photo_frame_cage. Disable it (e.g. sudo systemctl disable --now lightdm) and rerun setup."
    fi
}

# ---- Check internet (needed for apt-get) ----
if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
    log "Internet already available, skipping network wait."
else
    show "Waiting for network..."
    for i in $(seq 1 15); do
        if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
            log "Internet available after ${i} attempts."
            break
        fi
        sleep 2
    done
fi

log "Checking internet connectivity..."
if ! ping -c 1 -W 5 8.8.8.8 &>/dev/null; then
    log "No internet. Preparing WiFi hotspot..."
    # Unblock WiFi (may be soft-blocked on fresh install without WiFi config)
    rfkill unblock wifi 2>/dev/null || true
    nmcli radio wifi on 2>/dev/null || true

    # Wait for WiFi device to appear (brcmfmac can take a while on Pi Zero 2W)
    log "Waiting for WiFi device..."
    WIFI_READY=false
    for i in $(seq 1 30); do
        if nmcli -t -f TYPE device status 2>/dev/null | grep -q wifi; then
            WIFI_READY=true
            break
        fi
        log "  WiFi not ready yet ($i/30)... $(ip link show wlan0 2>&1 | head -1)"
        sleep 2
    done
    log "WiFi ready: ${WIFI_READY}"
    log "Devices: $(nmcli -t -f DEVICE,TYPE,STATE device status 2>&1)"
    log "Starting wifi_setup_server.py..."
    python3 "${REPO_DIR}/scripts/wifi_setup_server.py" 2>&1 | tee -a "$SETUP_LOG"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        log "ERROR: wifi_setup_server.py failed"
        log "Waiting for network (plug in Ethernet or power-cycle with WiFi configured)..."
        while ! ping -c 1 -W 5 8.8.8.8 &>/dev/null; do
            sleep 10
        done
    fi
    log "Internet connected! Continuing setup..."
    sleep 2
fi

# ---- System packages (with retry) ----
PACKAGES="git python3-venv python3-dev labwc wlr-randr seatd chromium ddcutil i2c-tools network-manager libjpeg-dev zlib1g-dev libffi-dev libheif-dev fonts-noto-color-emoji"

MISSING_PKGS=""
for pkg in $PACKAGES; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        MISSING_PKGS="$MISSING_PKGS $pkg"
    fi
done

if [ -n "$MISSING_PKGS" ]; then
    install_packages() {
        apt-get update -qq 2>&1 | tee -a "$SETUP_LOG"
        apt-get install -y --fix-missing $PACKAGES 2>&1 | tee -a "$SETUP_LOG"
    }

    show "Installing packages (this takes ~15 min)..."
    if ! install_packages; then
        log "First apt attempt failed, retrying in 10s..."
        sleep 10
        if ! install_packages; then
            log "Second apt attempt failed, retrying in 30s..."
            sleep 30
            install_packages || log "ERROR: apt-get install failed after 3 attempts"
        fi
    fi
    for pkg in python3-dev labwc chromium seatd libheif-dev wlr-randr fonts-noto-color-emoji ddcutil; do
        if ! dpkg -s "$pkg" &>/dev/null; then
            log "WARNING: $pkg missing, attempting individual install..."
            apt-get install -y "$pkg" 2>&1 | tee -a "$SETUP_LOG" || log "WARNING: could not install $pkg"
        fi
    done
    log "Packages installed."
else
    log "All packages already installed, skipping."
fi

# ---- Timezone & NTP ----
log "Configuring timezone and NTP..."
timedatectl set-timezone Europe/Berlin 2>/dev/null || true
timedatectl set-ntp true 2>/dev/null || true

# ---- i2c-dev module (needed for ddcutil / DDC/CI backlight control) ----
if ! grep -q "i2c-dev" /etc/modules-load.d/i2c-dev.conf 2>/dev/null; then
    echo "i2c-dev" > /etc/modules-load.d/i2c-dev.conf
fi
modprobe i2c-dev 2>/dev/null || true

# ---- User groups ----
echo "Setting up user groups..."
usermod -aG video,input,render,netdev,i2c "${FRAME_USER}"

# ---- Photos directory ----
echo "Creating photos directory..."
mkdir -p "${PHOTOS_DIR}/horizontal" "${PHOTOS_DIR}/vertical"
# Copy default placeholder images only if photos directory is empty (first run)
if [ -d "${REPO_DIR}/viewer/defaults/horizontal" ] && [ -z "$(ls -A "${PHOTOS_DIR}/horizontal/" 2>/dev/null)" ]; then
    cp "${REPO_DIR}/viewer/defaults/horizontal/"*.jpg "${PHOTOS_DIR}/horizontal/" 2>/dev/null || true
    cp "${REPO_DIR}/viewer/defaults/vertical/"*.jpg "${PHOTOS_DIR}/vertical/" 2>/dev/null || true
fi
chown -R "${FRAME_USER}:${FRAME_USER}" "${PHOTOS_DIR}"

# ---- Python venv (with retry) ----
VENV_DIR="${REPO_DIR}/venv"
VENV_NEEDS_UPDATE=false

if [ ! -x "${VENV_DIR}/bin/python" ] || ! "${VENV_DIR}/bin/python" -c "import sys" &>/dev/null; then
    VENV_NEEDS_UPDATE=true
elif [ -f "${VENV_DIR}/.requirements_md5" ]; then
    CURRENT_MD5=$(md5sum "${REPO_DIR}/requirements.txt" 2>/dev/null | awk '{print $1}')
    SAVED_MD5=$(cat "${VENV_DIR}/.requirements_md5" 2>/dev/null)
    if [ "$CURRENT_MD5" != "$SAVED_MD5" ]; then
        VENV_NEEDS_UPDATE=true
    fi
else
    VENV_NEEDS_UPDATE=true
fi

if [ "$VENV_NEEDS_UPDATE" = true ]; then
    show "Installing Python dependencies..."
    cd "$REPO_DIR"
    pip_install() {
        su - "${FRAME_USER}" -c "cd ${REPO_DIR} && python3 -m venv venv && venv/bin/pip install --quiet -r requirements.txt"
    }
    if ! pip_install; then
        log "First pip attempt failed, retrying..."
        sleep 5
        pip_install || log "ERROR: Python venv/pip setup failed after 2 attempts"
    fi
    md5sum "${REPO_DIR}/requirements.txt" 2>/dev/null | awk '{print $1}' > "${VENV_DIR}/.requirements_md5"
    chown "${FRAME_USER}:${FRAME_USER}" "${VENV_DIR}/.requirements_md5"
else
    log "Python venv up to date, skipping."
fi

# ---- Config file ----
if [ ! -f "${REPO_DIR}/config_frame.yaml" ]; then
    echo "Creating default config..."
    cat > "${REPO_DIR}/config_frame.yaml" << 'YAML'
setup_complete: false
frame:
  name: photo-frame
  orientation: horizontal
photos:
  base_dir: /srv/frame/photos
  blur_darken: 0.6
  blur_radius: 40
  horizontal:
    width: 1920
    height: 1200
  vertical:
    width: 1200
    height: 1920
  quality: 85
  state_db: /srv/frame/photos/state.db
  tmp_dir: /tmp/frame_downloads
slideshow:
  interval: 10
  fade_duration: 1.0
  transition: fade
synology:
  share_urls: []
  share_passphrases: []
google_photos:
  share_urls: []
immich:
  share_urls: []
  share_passphrases: []
energy_save:
  method: ddcci
logging:
  level: INFO
  file: /var/log/photo_frame.log
YAML
    chown "${FRAME_USER}:${FRAME_USER}" "${REPO_DIR}/config_frame.yaml"
fi

# ---- labwc config (uses XDG_CONFIG_HOME pointed at repo dir) ----
echo "Configuring labwc..."
# autostart uses XDG_CONFIG_HOME (set by cage service) for dynamic path detection
chmod +x "${REPO_DIR}/labwc/autostart"

# ---- Systemd services ----
show "Configuring services..."

cat > /etc/systemd/system/photo_frame_server.service << EOF
[Unit]
Description=Digital Photo Frame Viewer Server
After=network.target

[Service]
Type=simple
User=${FRAME_USER}
WorkingDirectory=${REPO_DIR}
Environment="PORT=8080"
ExecStart=${REPO_DIR}/venv/bin/python -m frame.server ${REPO_DIR}/config_frame.yaml
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/photo_frame_cage.service << EOF
[Unit]
Description=Digital Photo Frame Viewer (labwc + Chromium)
After=network.target photo_frame_server.service
Wants=photo_frame_server.service
Conflicts=display-manager.service

[Service]
Type=simple
User=${FRAME_USER}
SupplementaryGroups=video input render
TTYPath=/dev/tty1
Environment="XDG_RUNTIME_DIR=/tmp/frame-runtime"
Environment="XDG_CONFIG_HOME=${REPO_DIR}"
Environment="XCURSOR_PATH=${REPO_DIR}/labwc/icons"
Environment="XCURSOR_THEME=empty"
Environment="XCURSOR_SIZE=1"
ExecStartPre=/bin/mkdir -p /tmp/frame-runtime
ExecStartPre=/bin/chmod 700 /tmp/frame-runtime
ExecStartPre=/bin/sleep 5
ExecStartPre=+${REPO_DIR}/scripts/set_touch_cal.sh
ExecStart=/usr/bin/labwc -s /bin/true
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/photo_frame_update.service << EOF
[Unit]
Description=Digital Photo Frame Auto-Update
After=network-online.target
Wants=network-online.target
Before=photo_frame_server.service

[Service]
Type=oneshot
User=${FRAME_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/scripts/update.sh
TimeoutStartSec=120
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/photo_frame_update.timer << 'EOF'
[Unit]
Description=Daily auto-update for photo frame

[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ---- Polkit rule (NetworkManager without sudo for frame_user) ----
echo "Setting up polkit..."
cat > /etc/polkit-1/rules.d/10-photo-frame-wifi.rules << EOF
// Allow photo frame user to manage WiFi via NetworkManager
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.user === "${FRAME_USER}") {
        return polkit.Result.YES;
    }
});
EOF

# ---- Sudoers (iptables for captive portal) ----
echo "Setting up sudoers..."
cat > /etc/sudoers.d/photo-frame << EOF
${FRAME_USER} ALL=(ALL) NOPASSWD: /usr/sbin/iptables
${FRAME_USER} ALL=(ALL) NOPASSWD: /usr/sbin/ddcutil
${FRAME_USER} ALL=(ALL) NOPASSWD: /usr/bin/wlopm
${FRAME_USER} ALL=(ALL) NOPASSWD: /bin/systemctl stop photo_frame_cage
${FRAME_USER} ALL=(ALL) NOPASSWD: /bin/systemctl start photo_frame_cage
${FRAME_USER} ALL=(ALL) NOPASSWD: /bin/systemctl restart photo_frame_cage
${FRAME_USER} ALL=(ALL) NOPASSWD: /bin/dd
${FRAME_USER} ALL=(ALL) NOPASSWD: /usr/bin/setterm
${FRAME_USER} ALL=(ALL) NOPASSWD: /sbin/shutdown
${FRAME_USER} ALL=(ALL) NOPASSWD: /sbin/reboot
${FRAME_USER} ALL=(ALL) NOPASSWD: /usr/bin/tailscale up *
${FRAME_USER} ALL=(ALL) NOPASSWD: /bin/sh -c curl -fsSL https\://tailscale.com/install.sh | sh
EOF
chmod 440 /etc/sudoers.d/photo-frame

# ---- Quiet boot ----
echo "Configuring quiet boot..."
CMDLINE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    # Add quiet boot params if not present
    for param in "quiet" "loglevel=0" "vt.global_cursor_default=0" "logo.nologo"; do
        if ! grep -q "$param" "$CMDLINE"; then
            sed -i "s/$/ $param/" "$CMDLINE"
        fi
    done
fi

# Disable login prompt on tty1 (permanently)
systemctl disable getty@tty1 2>/dev/null || true

# Disable desktop display managers that would otherwise take DRM/seat0.
deactivate_display_manager
assert_no_display_manager_conflict

# ---- Enable services ----
show "Almost done..."
systemctl daemon-reload
systemctl enable seatd photo_frame_server photo_frame_cage photo_frame_update.service photo_frame_update.timer
systemctl start seatd 2>/dev/null || true

# ---- Set repo ownership ----
chown -R "${FRAME_USER}:${FRAME_USER}" "${REPO_DIR}"

# ---- Initialize git repo if missing (e.g. deployed from ZIP download) ----
if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "Initializing git repo for auto-update..."
    su - "${FRAME_USER}" -c "cd ${REPO_DIR} && git init && git remote add origin https://github.com/rwkaspar/digital_photo_frame.git && git fetch origin main && git reset --mixed origin/main" 2>/dev/null || true
fi

# ---- Ensure git branch is 'main' with correct upstream ----
su - "${FRAME_USER}" -c "cd ${REPO_DIR} && git branch -m master main 2>/dev/null; git branch --set-upstream-to=origin/main main 2>/dev/null" || true

# ---- Configure git safe directory (for auto-update as frame_user) ----
su - "${FRAME_USER}" -c "git config --global --add safe.directory ${REPO_DIR}" 2>/dev/null || true

# ---- Touchscreen check (wait if not detected, first run only) ----
TOUCH_ID="27c0:0859"
if ! lsusb | grep -q "$TOUCH_ID"; then
    if [ -f "${FRAME_HOME}/.photo-frame-setup-done" ]; then
        log "No touchscreen detected (re-run, skipping wait)."
    else
        log "No touchscreen detected, waiting for USB replug..."
        cat > /dev/tty1 2>/dev/null << 'SCREEN' || true


        ==========================================

           No touchscreen detected!

           Please unplug and replug the
           screen's USB cable, then wait...

        ==========================================
SCREEN
        while ! lsusb | grep -q "$TOUCH_ID"; do
            sleep 2
        done
        log "Touchscreen found!"
        sleep 1
    fi
fi

show "Setup complete!"
log "=== Setup complete! ==="

FIRST_RUN=false
if [ ! -f "${FRAME_HOME}/.photo-frame-setup-done" ]; then
    FIRST_RUN=true
fi

touch "${FRAME_HOME}/.photo-frame-setup-done"
systemctl disable photo-frame-firstboot.service 2>/dev/null || true

if [ "$FIRST_RUN" = true ]; then
    log "First run — rebooting..."
    sleep 3
    reboot
else
    log "Re-run complete — no reboot needed."
fi
