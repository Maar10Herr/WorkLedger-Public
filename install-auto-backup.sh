#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
source .env
hour="${1:-${WORKLEDGER_BACKUP_HOUR:-3}}"
minute="${2:-${WORKLEDGER_BACKUP_MINUTE:-0}}"
retention="${WORKLEDGER_BACKUP_RETENTION:-30}"
label="com.workledger.auto-backup"
plist="$HOME/Library/LaunchAgents/$label.plist"
root="${WORKLEDGER_BACKUP_DIR:-./workledger-backups}"
mkdir -p "$(dirname "$plist")" "$root"
repo="$PWD"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$label</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>-lc</string><string>cd '$repo' &amp;&amp; ./backup.sh &gt;&gt; '$HOME/Library/Logs/workledger-auto-backup.log' 2&gt;&amp;1</string></array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
<key>RunAtLoad</key><false/>
<key>StandardOutPath</key><string>$HOME/Library/Logs/workledger-auto-backup.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Logs/workledger-auto-backup.log</string>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
touch "$root/.workledger-auto-backup-installed"
find "$root" -maxdepth 1 -type d -name 'workledger-*' -print0 | while IFS= read -r -d '' dir; do
  [[ -f "$dir/manifest.txt" ]] || continue
  grep -qx 'format=workledger-backup-v2' "$dir/manifest.txt" || continue
  printf '%s\0' "$dir"
done | xargs -0 -r ls -1dt | tail -n +$((retention + 1)) | while IFS= read -r dir; do rm -rf -- "$dir"; done
echo "Installed $label at $hour:$(printf '%02d' "$minute") local; retention=$retention"
