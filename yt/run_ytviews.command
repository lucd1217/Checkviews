#!/bin/bash

echo "Running YouTube Shorts updater..."

cd "$(dirname "$0")/yt"

python3 ytviews.py

echo
echo "Done. You can close this window."