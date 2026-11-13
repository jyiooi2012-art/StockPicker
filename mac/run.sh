#!/bin/bash
# Runs the stock picker.
#   run.sh            -> automatic check (hourly): only runs if the last good report is 10+ hours old
#   run.sh now        -> run right away (used by "Run now")
#   run.sh app        -> used by the Stock Picker app: opens the report, refreshes it in the background if old
DEST="$HOME/StockPicker"
cd "$DEST" || exit 1
MODE="${1:-auto}"
STAMP="$DEST/reports/.last_success"
LOCK="$DEST/.running"
PLIST="$HOME/Library/LaunchAgents/com.stockpicker.daily.plist"
notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Stock Picker\"" >/dev/null 2>&1; }

write_plist() {  # hourly check + at login; the 10-hour rule decides whether to actually run
  cat > "$PLIST.new" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.stockpicker.daily</string>
  <key>ProgramArguments</key><array><string>$DEST/run.sh</string><string>auto</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DEST/launchd.log</string>
  <key>StandardErrorPath</key><string>$DEST/launchd.log</string>
</dict></plist>
PL
  if ! cmp -s "$PLIST.new" "$PLIST"; then
    mv "$PLIST.new" "$PLIST"
    # reload only when not started by the schedule itself (reloading would stop this run)
    if [ "$MODE" != "auto" ]; then
      launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
      launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null
    fi
  else
    rm -f "$PLIST.new"
  fi
}

make_app() {  # "Stock Picker" app for Spotlight / Launchpad: opens the report, refreshes it if old
  APP="$HOME/Applications/Stock Picker.app"
  [ -d "$APP" ] && [ "$APP" -nt "$DEST/run.sh" ] && return
  mkdir -p "$HOME/Applications"
  /usr/bin/osacompile -o "$APP" -e "do shell script \"open '$DEST/reports/latest.html'; nohup '$DEST/run.sh' app > /dev/null 2>&1 &\"" >/dev/null 2>&1
}

mkdir -p "$HOME/Library/LaunchAgents"
write_plist
make_app

if [ "$MODE" != "now" ] && [ -f "$STAMP" ]; then
  age=$(( $(date +%s) - $(stat -f %m "$STAMP") ))
  [ "$age" -lt 36000 ] && exit 0          # updated within the last 10 hours: nothing to do
fi

# only one run at a time
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -mmin +60 2>/dev/null)" ]; then rm -rf "$LOCK"; mkdir "$LOCK"; else
    [ "$MODE" = "now" ] && echo "An update is already running. Please wait a few minutes."; exit 0; fi
fi
trap 'rm -rf "$LOCK"' EXIT

# wait for the internet (a Mac that just woke up needs a moment for Wi-Fi)
for i in $(seq 1 20); do
  /usr/bin/curl -s -m 8 -o /dev/null https://query1.finance.yahoo.com && break
  if [ "$i" -eq 20 ]; then
    echo "=== $(date) === no internet; will try again in an hour" >> run.log
    [ "$MODE" != "auto" ] && notify "No internet connection. Will try again automatically."
    exit 0
  fi
  sleep 30
done

[ "$MODE" = "app" ] && notify "Updating your picks in the background (a few minutes)…"
echo "=== $(date) === ($MODE)" >> run.log
/usr/bin/caffeinate -i "$DEST/.venv/bin/python" stock_picker.py --out reports >> run.log 2>&1
rc=$?
if [ $rc -eq 0 ]; then
  touch "$STAMP"
  notify "New picks are ready. Open the Stock Picker app to see them."
elif [ $rc -eq 3 ]; then
  echo "    (offline during the run; will try again in an hour)" >> run.log
else
  notify "Update failed. Tell Claude, or see run.log in your StockPicker folder."
fi
exit 0
