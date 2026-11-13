#!/bin/bash
# One-click installer for the Stock & ETF picker on macOS.
# Installs to ~/StockPicker, sets up Python packages, runs once, and schedules automatic updates
# (checks hourly; refreshes whenever the report is 10+ hours old and the Mac is online).
# Also creates a "Stock Picker" app (Cmd+Space -> Stock Picker) that opens your latest report.
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/StockPicker"
PLIST="$HOME/Library/LaunchAgents/com.stockpicker.daily.plist"
echo "== Stock picker installer =="

if ! python3 -c "import venv, sys; assert sys.version_info >= (3, 9)" >/dev/null 2>&1; then
  echo "Python 3.9+ is needed. If a window asks to install Apple's Command Line Tools, click Install, wait, then double-click this installer again. (Or install Python from https://www.python.org/downloads/macos/ )"
  xcode-select --install || true
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
PY="$(command -v python3)"

echo "1/4 Copying files to $DEST"
mkdir -p "$DEST"
rsync -a --exclude "watchlist.txt" --exclude "mac" --exclude "*.zip" --exclude "reports" --exclude "__pycache__" "$SRC/" "$DEST/"
# keep your edited watchlist if you already had one
cp -n "$SRC/watchlist.txt" "$DEST/watchlist.txt" 2>/dev/null || true
cp "$SRC/mac/Run now.command" "$SRC/mac/Open latest report.command" "$SRC/mac/Uninstall.command" "$SRC/mac/Check status.command" "$DEST/" 2>/dev/null || true
chmod +x "$DEST"/*.command
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo "2/4 Installing Python packages (first time takes a minute)"
"$PY" -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

cp "$SRC/mac/run.sh" "$DEST/run.sh"
chmod +x "$DEST/run.sh"

echo "3/4 Scheduling automatic updates (hourly check; updates whenever the report is 10+ hours old)"
echo "4/4 First run (a few minutes: downloading prices, fundamentals, news)…"
"$DEST/run.sh" now
tail -8 "$DEST/run.log"
open "$DEST/reports/latest.html" 2>/dev/null || echo "No report yet; see $DEST/run.log"
open "$DEST"
echo
echo "Done! You only install once."
echo "To see your picks: press Cmd+Space, type 'Stock Picker', press Enter."
echo "It updates by itself about twice a day whenever your Mac is awake and online."
read -n 1 -s -r -p "Press any key to close."
