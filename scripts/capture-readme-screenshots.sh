#!/usr/bin/env bash
# Capture README screenshots (requires backend + Vite running locally).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
mkdir -p "$OUT"

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome not found at $CHROME. Set CHROME to your Chromium/Chrome binary."
  exit 1
fi

PORT="${DISPATCHIQ_VITE_PORT:-5173}"
BASE="http://127.0.0.1:${PORT}"

shot() {
  local path="$1"
  local url="$2"
  echo "Screenshot: $url -> $path"
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
    --window-size=1440,900 \
    --virtual-time-budget=8000 \
    --screenshot="$path" \
    "$url"
}

shot "$OUT/dashboard.png" "${BASE}/?tab=dashboard"
shot "$OUT/cs-queue.png" "${BASE}/?tab=cs-queue"
shot "$OUT/shift-summary.png" "${BASE}/?tab=shift-summary"
echo "Done. Files in $OUT"
