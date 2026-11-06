#!/bin/bash

cd "$(dirname "$0")"

echo "Running TikTok updater..."
python3 tt/ttviews.py

echo ""
echo "Running YouTube Shorts updater..."
python3 yt/ytviews.py

echo ""
echo "Aggregating total views..."
python3 total_views.py

echo ""
echo "Opening quick preview..."

# Replace the shell with Quick Look (NO terminal prompt possible)
exec qlmanage -p total_views.csv >/dev/null 2>&1