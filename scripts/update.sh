#!/bin/bash
# Auto-update: pull latest code from GitHub on boot.
# Designed to be run as a systemd oneshot before the server starts.
# Exits cleanly on any failure so boot continues.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

# Resolve tracked upstream (fallback origin/main)
UPSTREAM_REF="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo origin/main)"
REMOTE="${UPSTREAM_REF%%/*}"
BRANCH="${UPSTREAM_REF#*/}"

# Skip if no network
if ! timeout 5 ping -c1 github.com &>/dev/null; then
    echo "No network - skipping update"
    exit 0
fi

# Discard local modifications (CRLF diffs from FAT32 copy, etc.)
git fetch "$REMOTE" "$BRANCH" --quiet || true
git reset --hard "$UPSTREAM_REF" 2>/dev/null || true

# Pull latest (fast-forward only to avoid merge conflicts)
echo "Pulling latest from GitHub ($UPSTREAM_REF)..."
git pull --ff-only "$REMOTE" "$BRANCH" || { echo "git pull failed - skipping"; exit 0; }

# Only reinstall Python deps if requirements.txt changed
REQ_HASH_FILE="/tmp/frame-requirements-hash"
CURRENT_HASH=$(md5sum "$REPO_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1)
PREV_HASH=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")

if [ "$CURRENT_HASH" != "$PREV_HASH" ]; then
    echo "requirements.txt changed - installing dependencies..."
    "$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt" --quiet
    echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
else
    echo "requirements.txt unchanged - skipping pip install"
fi

echo "Update complete"
