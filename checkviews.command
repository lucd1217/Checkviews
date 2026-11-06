#!/bin/bash

cd "$(dirname "$0")" || exit 1

pause() { read -n 1 -s -r -p "Press any key to close..."; echo; }

echo "Running TikTok updater..."
python3 tt/ttviews.py || fail=1

echo ""
echo "Running YouTube Shorts updater..."
python3 yt/ytviews.py || fail=1

if [ -n "$fail" ]; then
    echo ""
    echo "❌ An updater failed, so totals were NOT recalculated. See checkviews.log."
    pause
    exit 1
fi

echo ""
echo "Aggregating total views..."
python3 total_views.py || { pause; exit 1; }

echo ""
echo "Opening quick preview..."
qlmanage -p total_views.csv >/dev/null 2>&1
