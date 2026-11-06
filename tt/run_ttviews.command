#!/bin/bash

cd "$(dirname "$0")" || exit 1

echo "Running TikTok updater..."
python3 ttviews.py

echo ""
echo "Done. Press any key to close."
read -n 1 -s -r
