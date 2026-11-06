import json
import csv
from pathlib import Path

# ---------------- PATHS ----------------

BASE_DIR = Path(__file__).parent

TT_DIR = BASE_DIR / "tt"
YT_DIR = BASE_DIR / "yt"

TT_HISTORY = TT_DIR / "video_history.json"
YT_HISTORY = YT_DIR / "video_history.json"

TT_LOG = TT_DIR / "views_log.json"
YT_LOG = YT_DIR / "views_log.json"

TT_CSV = TT_DIR / "tiktok_data.csv"
YT_CSV = YT_DIR / "youtube_data.csv"

CSV_FILE = BASE_DIR / "total_views.csv"

# ---------------- HELPERS ----------------

def parse_views(v):
    if not v:
        return 0
    return int(str(v).replace(",", ""))

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))

def sum_views(history_path, include_missing=False):
    history = load_json(history_path, {})
    total = 0
    for video in history.values():
        status = video.get("status")
        if status == "ACTIVE" or (include_missing and status == "MISSING"):
            total += parse_views(video.get("views"))
    return total

def get_delta(log_path, current_total):
    log = load_json(log_path, {"last_total": 0})
    last = log.get("last_total", 0)
    delta = current_total - last
    save_json(log_path, {"last_total": current_total})
    return delta

def fmt(n):
    return f"{n:,}"

def fmt_dollars(n):
    return f"${n:,.2f}"

def read_csv_rows(path, reverse=False):
    rows = []
    if not path.exists():
        return rows

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append([
                row.get("number", ""),
                row.get("caption", ""),
                row.get("views", ""),
                row.get("views_since_last_checked", ""),
                row.get("status", "")
            ])

    if reverse:
        rows.reverse()

    return rows

# ---------------- MAIN ----------------

def main():
    # ---- SUMMARY CALCULATIONS ----
    tt_total = sum_views(TT_HISTORY, include_missing=True)
    yt_total = sum_views(YT_HISTORY, include_missing=False)

    tt_delta = get_delta(TT_LOG, tt_total)
    yt_delta = get_delta(YT_LOG, yt_total)

    combined_total = tt_total + yt_total
    combined_delta = tt_delta + yt_delta

    # Historical RPM split:
    # - Views through Feb 28, 2026 use $0.10 RPM
    # - Views from Mar 1, 2026 onward use $0.12 RPM
    pre_march_views = 3190000

    old_rate_views = min(combined_total, pre_march_views)
    new_rate_views = max(0, combined_total - pre_march_views)

    earnings_total = (old_rate_views * 0.10 / 1000) + (new_rate_views * 0.12 / 1000)

    # Current growth is assumed to be post-March traffic
    earnings_delta = combined_delta * 0.12 / 1000

    summary_rows = [
        ["TikTok Total Views", fmt(tt_total)],
        ["New TikTok Views", fmt(tt_delta)],
        ["YouTube Total Views", fmt(yt_total)],
        ["New YouTube Views", fmt(yt_delta)],
        ["Combined Total Views", fmt(combined_total)],
        ["Combined New Views", fmt(combined_delta)],
        ["Earnings", fmt_dollars(earnings_total)],
        ["Increase in Earnings", fmt_dollars(earnings_delta)],
    ]

    # ---- VIDEO TABLES ----
    tt_rows = read_csv_rows(TT_CSV, reverse=False)
    yt_rows = read_csv_rows(YT_CSV, reverse=True)

    max_rows = max(len(summary_rows), len(tt_rows), len(yt_rows))

    while len(summary_rows) < max_rows:
        summary_rows.append(["", ""])

    while len(tt_rows) < max_rows:
        tt_rows.append(["", "", "", "", ""])

    while len(yt_rows) < max_rows:
        yt_rows.append(["", "", "", "", ""])

    # ---- WRITE SIDE-BY-SIDE DASHBOARD ----
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow([
            "View Summary", "",
            "",
            "TikTok", "", "", "", "",
            "",
            "YouTube", "", "", "", ""
        ])

        writer.writerow([
            "Metric", "Value",
            "",
            "#", "Caption", "Views", "New Views", "Status",
            "",
            "#", "Caption", "Views", "New Views", "Status"
        ])

        for i in range(max_rows):
            writer.writerow(
                summary_rows[i] +
                [""] +
                tt_rows[i] +
                [""] +
                yt_rows[i]
            )

    print("✅ Total dashboard spreadsheet updated.")

if __name__ == "__main__":
    main()