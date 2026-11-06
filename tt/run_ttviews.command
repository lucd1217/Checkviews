#!/bin/bash

cd "$(dirname "$0")"

echo "Running TikTok updater..."
python3 ttviews.py

echo ""
echo "Done. You can close this window."
read -n 1 -s -r