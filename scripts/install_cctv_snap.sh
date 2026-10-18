#!/bin/bash
# CCTV 사진 작업을 이 맥의 launchd 에 5분 주기로 설치한다(한국 IP 맥에서만 의미가 있다).
#   bash scripts/install_cctv_snap.sh            설치·갱신
#   bash scripts/install_cctv_snap.sh --remove   제거
# 전제: ffmpeg(brew install ffmpeg) · ~/.config/ksw/its_key · ~/.config/ksw/cctv_upload (권한 600)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL=kr.jis.cctv-snap
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then rm -f "$PLIST"; echo "removed"; exit 0; fi
for f in its_key cctv_upload; do [ -s "$HOME/.config/ksw/$f" ] || { echo "~/.config/ksw/$f 없음"; exit 2; }; done
command -v ffmpeg >/dev/null || [ -x /opt/homebrew/bin/ffmpeg ] || { echo "ffmpeg 없음 — brew install ffmpeg"; exit 2; }
PY="$(command -v python3)"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$REPO/scripts/cctv_snap.py</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/jis-cctv.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/jis-cctv.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL ($REPO)"
