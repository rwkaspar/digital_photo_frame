#!/bin/bash
# Update an already-running photo frame to the latest version.
# Run on the Pi: bash ~/digital_photo_frame/scripts/deploy_update.sh
#
# For FIRST-TIME setup, use prepare_sd.sh on the dev machine instead.
set -e

REPO_DIR="$HOME/digital_photo_frame"
cd "$REPO_DIR"

# Resolve tracked upstream (fallback origin/main)
UPSTREAM_REF="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo origin/main)"
REMOTE="${UPSTREAM_REF%%/*}"
BRANCH="${UPSTREAM_REF#*/}"

echo "=== Updating Photo Frame ==="

echo "Tracking: $UPSTREAM_REF"

# 1. Discard any local modifications (CRLF diffs, etc.) and pull latest
git fetch "$REMOTE" "$BRANCH" --quiet || true
git reset --hard "$UPSTREAM_REF" 2>/dev/null || true
git pull --ff-only "$REMOTE" "$BRANCH" || { echo "git pull failed"; exit 1; }
echo "Code updated to $(git log -1 --format='%h %ci')"

# 2. Patch config: add new fields if missing
if [ -f config_frame.yaml ]; then
    if ! grep -q '^setup_complete:' config_frame.yaml; then
        sed -i '1s/^/setup_complete: true\n/' config_frame.yaml
    fi
    if ! grep -q '^energy_save:' config_frame.yaml; then
        echo -e "\nenergy_save:\n  method: ddcci" >> config_frame.yaml
    fi
    if grep -q 'local_api_base' config_frame.yaml; then
        sed -i '/local_api_base/d' config_frame.yaml
    fi
fi

# 3. Update pip deps if changed
REQ_HASH_FILE="/tmp/frame-requirements-hash"
CURRENT_HASH=$(md5sum "$REPO_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1)
PREV_HASH=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")
if [ "$CURRENT_HASH" != "$PREV_HASH" ]; then
    echo "Installing dependencies..."
    "$REPO_DIR/venv/bin/pip" install --quiet -r requirements.txt
    echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
else
    echo "Deps unchanged"
fi

# 4. Restart services
sudo systemctl restart photo_frame_server
sleep 2
sudo systemctl restart photo_frame_cage
echo "Services restarted"

echo "=== Done! ==="
