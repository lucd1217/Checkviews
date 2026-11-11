#!/bin/bash

cd "$(dirname "$0")"

# The picker dialog needs Tk 8.6+. Apple's /usr/bin/python3 ships Tk 8.5, which
# hangs without drawing anything, and a Finder-launched shell can pick it up first.
# So choose the first interpreter that has a modern Tk.
PY=""
for cand in \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /usr/local/bin/python3 \
    /opt/homebrew/bin/python3 \
    "$(command -v python3)"; do
  if [ -x "$cand" ] && "$cand" -c 'import tkinter,sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)' 2>/dev/null; then
    PY="$cand"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "No Python with Tk 8.6+ found (the channel picker needs it)."
  echo "Falling back to the last selection with the default python3."
  PY="$(command -v python3)"
  EXTRA="--last"
fi

echo "Using $PY"
# Asks which channels to check (defaults to last time), updates them, rebuilds the dashboard
"$PY" run.py $EXTRA

echo ""
echo "Opening quick preview..."

# total_views.csv shows every channel you picked side by side
# Replace the shell with Quick Look (NO terminal prompt possible)
exec qlmanage -p total_views.csv >/dev/null 2>&1
