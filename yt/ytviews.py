import subprocess
import json
import csv
from pathlib import Path
import re

# ---------------- CONFIG ----------------

CHANNEL_URL = "https://www.youtube.com/@togiclips646"

BASE_DIR = Path(__file__).parent
RAW_JSON = BASE_DIR / "youtube_raw.jsonl"
HISTORY_FILE = BASE_DIR / "video_history.json"
CSV_FILE = BASE_DIR / "youtube_data.csv"

# ---------------- HELPERS ----------------

def clean_caption(text):
    if not text:
        return ""
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def format_views(n):
    try:
        return f"{int(n):,}"
    except:
        return ""

def parse_views(v):
    try:
        return int(str(v).replace(",", ""))
    except:
        return None

def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}

def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history, indent=2))

# ---------------- FETCH ----------------

def fetch():
    with open(RAW_JSON, "w", encoding="utf-8") as out:
        subprocess.run(
            [
                "yt-dlp",
                "--ignore-errors",
                "--dump-json",
                "--flat-playlist",
                "--match-filter", "duration <= 60",
                f"{CHANNEL_URL}/shorts"
            ],
            stdout=out,
            cwd=BASE_DIR
        )

# ---------------- MAIN LOGIC ----------------

def update_history():
    history = load_history()
    seen_this_run = set()

    with open(RAW_JSON, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)

            vid = data.get("id")
            if not vid:
                continue

            seen_this_run.add(vid)

            ts = data.get("timestamp")
            new_views_int = parse_views(data.get("view_count"))
            new_views_fmt = format_views(new_views_int) if new_views_int is not None else None
            new_caption = clean_caption(data.get("title", ""))

            if vid not in history:
                history[vid] = {
                    "video_id": vid,
                    "timestamp": ts,
                    "caption": new_caption,
                    "views": new_views_fmt,
                    "views_delta": None,
                    "status": "ACTIVE"
                }
            else:
                history[vid]["status"] = "ACTIVE"

                old_views_int = parse_views(history[vid].get("views"))

                if new_views_int is not None and old_views_int is not None:
                    delta = new_views_int - old_views_int
                    history[vid]["views_delta"] = delta if delta >= 0 else None
                    history[vid]["views"] = new_views_fmt

                if new_caption:
                    history[vid]["caption"] = new_caption

                if history[vid].get("timestamp") is None:
                    history[vid]["timestamp"] = ts

    # Mark unseen videos as MISSING (do NOT touch views or delta)
    for vid in history:
        if vid not in seen_this_run:
            history[vid]["status"] = "MISSING"
            history[vid]["views_delta"] = None

    save_history(history)
    return history

def write_csv(history):
    ordered = sorted(
        history.values(),
        key=lambda x: (x.get("timestamp") or 0)
    )

    rows = []
    for i, v in enumerate(ordered, start=1):
        delta = v.get("views_delta")
        rows.append({
            "number": i,
            "caption": v.get("caption", ""),
            "views": v.get("views", ""),
            "views_since_last_checked": format_views(delta) if isinstance(delta, int) else "",
            "status": v.get("status", "")
        })

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "number",
                "caption",
                "views",
                "views_since_last_checked",
                "status"
            ]
        )
        writer.writeheader()
        writer.writerows(rows)

# ---------------- RUN ----------------

def main():
    fetch()
    history = update_history()
    write_csv(history)
    print("✅ YouTube Shorts history updated (per-video deltas added).")

if __name__ == "__main__":
    main()