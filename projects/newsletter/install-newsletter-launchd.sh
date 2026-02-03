#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/org.current.newsletter.slack.plist"
LOG_DIR="$SCRIPT_DIR/output"
mkdir -p "$LOG_DIR"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>org.current.newsletter.slack</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>$SCRIPT_DIR/run-newsletter-slack.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>TZ</key>
      <string>America/New_York</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
      <dict>
        <key>Weekday</key>
        <integer>2</integer>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
      <dict>
        <key>Weekday</key>
        <integer>5</integer>
        <key>Hour</key>
        <integer>10</integer>
        <key>Minute</key>
        <integer>0</integer>
      </dict>
    </array>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/newsletter-launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/newsletter-launchd.err.log</string>
  </dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
echo "Installed launchd job at $PLIST_PATH"
echo "Runs Mondays and Thursdays at 10:00 AM America/New_York."
