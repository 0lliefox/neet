#!/bin/zsh
# Install/refresh the NEET capture launchd agent on this machine (run on the capture host).
set -e
PLIST_SRC="$(dirname "$0")/launchd/uk.ac.ncl.neet.capture.plist"
PLIST_DST="$HOME/Library/LaunchAgents/uk.ac.ncl.neet.capture.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed "s#__HOME__#$HOME#g" "$PLIST_SRC" > "$PLIST_DST"
launchctl bootout "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/uk.ac.ncl.neet.capture"
echo "installed: $PLIST_DST"; launchctl list | grep neet || true
