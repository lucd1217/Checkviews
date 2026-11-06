#!/bin/bash

cd "$(dirname "$0")" || exit 1

echo "Running YouTube Shorts updater..."
python3 ytviews.py

echo
echo "Done. Press any key to close."
read -n 1 -s -r
