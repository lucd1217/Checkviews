import csv
import sys
from pathlib import Path

import common
from common import load_json, parse_views, save_json

BASE_DIR = Path(__file__).resolve().parent
TT_DIR = BASE_DIR / "tt"
YT_DIR = BASE_DIR / "yt"

TT_HISTORY = TT_DIR / "video_history.json"
YT_HISTORY = YT_DIR / "video_history.json"
TT_LOG = TT_DIR / "views_log.json"
YT_LOG = YT_DIR / "views_log.json"
TT_CSV = TT_DIR / "tiktok_data.csv"
YT_CSV = YT_DIR / "youtube_data.csv"
CSV_FILE = BASE_DIR / "total_views.csv"


def fmt(n):
    return f"{n:,}"


def fmt_dollars(n):
    return f"${n:,.2f}"


def sum_views(history_path, include_missing=False):
    history = load_json(history_path, {})
    total = 0
    for video in history.values():
        status = video.get("status")
        if status == "ACTIVE" or (include_missing and status == "MISSING"):
            total += parse_views(video.get("views")) or 0
    return total


def compute_delta(log_path, current_total):
    """Return (delta, new_log). Nothing is written here.

    Re-running with an unchanged total reports the previous delta instead of 0.
    """
    log = load_json(log_path, {})
    last = log.get("last_total", 0)
    if current_total == last and "last_delta" in log:
        return log["last_delta"], log
    delta = current_total - last
    return delta, {"last_total": current_total, "last_delta": delta}


def earnings(views, tiers):
    """Revenue for a cumulative view count, with RPM (per 1,000 views) in tiers."""
    total, prev = 0.0, 0
    for tier in tiers:
        cap = tier.get("up_to")
        upper = views if cap is None else min(views, cap)
        if upper > prev:
            total += (upper - prev) * tier["rpm"] / 1000
            prev = upper
    return total


def read_csv_rows(path, reverse=False):
    rows = []
    if not path.exists():
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append([
                row.get("number", ""),
                row.get("caption", ""),
                row.get("views", ""),
                row.get("views_since_last_checked", ""),
                row.get("status", ""),
            ])
    if reverse:
        rows.reverse()
    return rows


def main():
    cfg = common.load_config()
    tiers = cfg["rpm_tiers"]

    tt_total = sum_views(TT_HISTORY, cfg["tiktok"]["include_missing_in_total"])
    yt_total = sum_views(YT_HISTORY, cfg["youtube"]["include_missing_in_total"])

    tt_delta, tt_log = compute_delta(TT_LOG, tt_total)
    yt_delta, yt_log = compute_delta(YT_LOG, yt_total)

    combined_total = tt_total + yt_total
    combined_delta = tt_delta + yt_delta

    earnings_total = earnings(combined_total, tiers)
    earnings_delta = earnings_total - earnings(combined_total - combined_delta, tiers)

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

    tt_rows = read_csv_rows(TT_CSV)
    yt_rows = read_csv_rows(YT_CSV, reverse=True)
    max_rows = max(len(summary_rows), len(tt_rows), len(yt_rows))
    summary_rows += [["", ""]] * (max_rows - len(summary_rows))
    tt_rows += [["", "", "", "", ""]] * (max_rows - len(tt_rows))
    yt_rows += [["", "", "", "", ""]] * (max_rows - len(yt_rows))

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["View Summary", "", "", "TikTok", "", "", "", "", "", "YouTube", "", "", "", ""])
        writer.writerow(["Metric", "Value", "",
                         "#", "Caption", "Views", "New Views", "Status", "",
                         "#", "Caption", "Views", "New Views", "Status"])
        for i in range(max_rows):
            writer.writerow(summary_rows[i] + [""] + tt_rows[i] + [""] + yt_rows[i])

    # Commit baselines only once the dashboard was written successfully.
    save_json(TT_LOG, tt_log)
    save_json(YT_LOG, yt_log)
    print("✅ Total dashboard spreadsheet updated.")


if __name__ == "__main__":
    sys.exit(main())
