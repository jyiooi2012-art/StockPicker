#!/bin/bash
D="$HOME/StockPicker"
echo "== Stock Picker status =="
if [ -f "$D/reports/.last_success" ]; then echo "Last successful update: $(date -r "$D/reports/.last_success")"; else echo "Last successful update: report file from $(date -r "$D/reports/latest.html" 2>/dev/null)"; fi
launchctl print "gui/$(id -u)/com.stockpicker.daily" >/dev/null 2>&1 && echo "Automatic updates: ON (checks every hour, updates when the report is 10+ hours old)" || echo "Automatic updates: OFF — double-click 'Run now' once to switch them back on"
echo; echo "Last lines of the log:"; tail -6 "$D/run.log"
read -n 1 -s -r -p "Press any key to close."
