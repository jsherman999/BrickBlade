#!/usr/bin/env bash
# Render the launchd plists with this repo's absolute path and load them.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
UV="$(command -v uv || echo /opt/homebrew/bin/uv)"

mkdir -p "$AGENTS" "$PROJECT_DIR/var/logs"

for src in "$PROJECT_DIR/launchd"/com.brickblade.*.plist; do
  name="$(basename "$src")"
  dest="$AGENTS/$name"
  sed \
    -e "s|PROJECT_DIR|$PROJECT_DIR|g" \
    -e "s|/opt/homebrew/bin/uv|$UV|g" \
    "$src" > "$dest"
  # If an old version is loaded, stop it so bootstrap can re-register cleanly.
  launchctl bootout "gui/$(id -u)/${name%.plist}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "installed $dest"
done

echo
echo "View live logs:  tail -f \"$PROJECT_DIR/var/logs/\"*.stdout"
echo "Trigger manually: launchctl kickstart -k \"gui/$(id -u)/com.brickblade.refresh-prices\""
echo "Uninstall:       launchctl bootout \"gui/$(id -u)/com.brickblade.<label>\""
