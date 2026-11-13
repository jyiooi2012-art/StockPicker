#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.stockpicker.daily.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null; rm -f "$PLIST"
rm -rf "$HOME/Applications/Stock Picker.app"
echo "Automatic updates stopped and the Stock Picker app removed. Your reports are still in ~/StockPicker (delete that folder to remove everything)."
read -n 1 -s -r -p "Press any key to close."
