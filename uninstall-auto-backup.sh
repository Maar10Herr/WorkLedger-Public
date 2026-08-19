#!/usr/bin/env bash
set -Eeuo pipefail
label="com.workledger.auto-backup"
plist="$HOME/Library/LaunchAgents/$label.plist"
launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
rm -f "$plist"
echo "Removed $label"
