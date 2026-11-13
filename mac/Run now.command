#!/bin/bash
echo "Updating your stock picks (usually 3-10 minutes)…"
"$HOME/StockPicker/run.sh" now
tail -4 "$HOME/StockPicker/run.log"
open "$HOME/StockPicker/reports/latest.html"
