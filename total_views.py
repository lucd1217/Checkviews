"""Per-channel dashboards. Every channel is fully self-contained in its own folder:

    <channel dir>/video_history.json   current state of each video (caption, upload time, status, latest views)
    <channel dir>/view_log.jsonl       THE history: per-video views at every run. All growth, new-upload,
                                       per-run-total and since-date numbers are derived from this alone
    <channel dir>/run_meta.json        last run / last success
    <channel dir>/<platform>_data.csv  per-video table
    <channel dir>/dashboard.csv        summary + table for that channel only
    <channel dir>/history.csv          per-run totals, derived from view_log.jsonl (not a source)

Nothing here adds channels or platforms together. Earnings are per channel:
views x that channel's RPM (cents per 1,000 views, set in the picker dialog).
"""
import csv
import shutil
from pathlib import Path

import channels as ch_lib
import history
from trackerlib import load_json, parse_views

BASE_DIR = Path(__file__).parent
SHEET_FILE = BASE_DIR / "total_views.csv"   # side-by-side view of the channels you picked
ARCHIVE_DIR = BASE_DIR / "archive"

# ---------------- HELPERS ----------------

def fmt(n):
    return f"{n:,}"


def fmt_dollars(n):
    return f"${n:,.2f}"


def csv_name(ch):
    return "tiktok_data.csv" if ch["platform"] == "tiktok" else "youtube_data.csv"


def channel_stats(ch):
    """Totals for one channel (current state), plus growth/new uploads for its latest run (from the history).
    Growth/new-upload views are zeroed if its last fetch failed."""
    d = BASE_DIR / ch["dir"]
    state = load_json(d / "video_history.json", {})
    meta = load_json(d / "run_meta.json", {})
    fresh = meta.get("ok", True)
    include_missing = history.counts_missing(ch["platform"])

    total = active = missing = 0
    for v in state.values():
        status = v.get("status")
        if status == "ACTIVE":
            active += 1
        elif status == "MISSING":
            missing += 1
        if status == "ACTIVE" or (include_missing and status == "MISSING"):
            total += parse_views(v.get("views")) or 0

    history.ensure_log(d)
    summary = history.run_summary(history.load(d), ch["platform"]) if fresh else None
    growth = summary["growth"] if summary else 0
    new_views = summary["new_views"] if summary else 0
    new_count = len(summary["new_ids"]) if summary else 0

    return {"ch": ch, "total": total, "growth": growth, "new_views": new_views,
            "new_count": new_count, "active": active, "missing": missing,
            "fresh": fresh, "meta": meta, "include_missing": include_missing}


def read_csv_rows(path, reverse=False):
    rows = []
    if not path.exists():
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append([row.get("number", ""), row.get("caption", ""), row.get("views", ""),
                         row.get("views_since_last_checked", ""), row.get("status", "")])
    if reverse:
        rows.reverse()
    return rows


def views_since(ch, since_day):
    """Views gained by `ch` since the start of `since_day`, from the channel's history log."""
    d = BASE_DIR / ch["dir"]
    history.ensure_log(d)
    return history.views_since(history.load(d), load_json(d / "video_history.json", {}),
                               ch["platform"], since_day)


def since_rows(s, since_day):
    """Summary rows for 'views / earnings since <date>' (empty list if no date was chosen)."""
    if not since_day:
        return []
    label = since_day.isoformat()
    info = views_since(s["ch"], since_day)
    if info is None:
        return [[f"Views since {label}", "no history recorded yet"]]
    value = fmt(info["views"])
    if info["partial"]:
        value += f' (records only start {info["base_at"]:%Y-%m-%d %H:%M}; counted since then)'
    rows = [[f"Views since {label}", value]]
    rpm = ch_lib.get_rpm(s["ch"])
    rows.append([f"Earnings since {label}",
                 "set RPM in the picker" if rpm is None else fmt_dollars(ch_lib.earnings_dollars(info["views"], rpm))])
    return rows


def money(s):
    """(total earnings, earnings from this run's new views) as '$x.xx' strings for one channel."""
    rpm = ch_lib.get_rpm(s["ch"])
    if rpm is None:
        return "set RPM in the picker", "set RPM in the picker"
    return (fmt_dollars(ch_lib.earnings_dollars(s["total"], rpm)),
            fmt_dollars(ch_lib.earnings_dollars(s["growth"] + s["new_views"], rpm)))


# ---------------- PER-RUN HISTORY (derived from the log) ----------------

def export_timeline(ch):
    """history.csv: one row per run, regenerated from the channel's log every time."""
    d = BASE_DIR / ch["dir"]
    rows = history.timeline(history.load(d), ch["platform"])
    with open(d / "history.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_time_local", "total_views", "growth_since_previous_run", "new_upload_views"])
        for r in rows:
            w.writerow([f'{r["at"].astimezone():%Y-%m-%d %H:%M:%S}', r["total"], r["growth"], r["new_views"]])


# ---------------- ONE CHANNEL'S DASHBOARD ----------------

def build_dashboard(s, since_day=None):
    ch = s["ch"]
    d = BASE_DIR / ch["dir"]
    d.mkdir(parents=True, exist_ok=True)

    summary = [
        ["Channel", ch_lib.label(ch)],
        ["Total Views", fmt(s["total"])],
        ["Growth (existing videos)", fmt(s["growth"])],
        ["New Uploads (views)", f'{fmt(s["new_views"])} ({s["new_count"]} videos)'],
        ["New Views (growth + new uploads)", fmt(s["growth"] + s["new_views"])],
        ["RPM", ch_lib.fmt_rpm(ch_lib.get_rpm(ch))],
        ["Earnings", money(s)[0]],
        ["Increase in Earnings", money(s)[1]],
        *since_rows(s, since_day),
        ["Videos ACTIVE", fmt(s["active"])],
        ["Videos MISSING", fmt(s["missing"])],
        ["Counts MISSING in total", "yes" if s["include_missing"] else "no"],
        ["Last successful update", (s["meta"].get("last_success") or "never")[:19].replace("T", " ") + " UTC"],
    ]
    if not s["fresh"]:
        summary.append(["⚠️ STALE", f'last fetch failed: {s["meta"].get("error") or "unknown error"}'])

    # TikTok oldest-first, YouTube newest-first (as before)
    table = read_csv_rows(d / csv_name(ch), reverse=ch["platform"] == "youtube")
    n = max(len(summary), len(table))
    summary += [["", ""]] * (n - len(summary))
    table += [["", "", "", "", ""]] * (n - len(table))

    path = d / "dashboard.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Summary", "", "", "Videos", "", "", "", ""])
        w.writerow(["Metric", "Value", "", "#", "Caption", "Views", "New Views", "Status"])
        for i in range(n):
            w.writerow(summary[i] + [""] + table[i])

    export_timeline(ch)
    return path


# ---------------- SIDE-BY-SIDE SHEET (a view only; every channel keeps its own data) ----------------

def grand_total_rows(selected_stats, since_day=None):
    """Grand totals across the selected channels. Computed from each channel's own numbers every time the
    sheet is built; nothing here is stored. (Only shown when more than one channel is selected.)"""
    if len(selected_stats) < 2:
        return []
    G = "GRAND TOTAL"
    total = sum(s["total"] for s in selected_stats)
    growth = sum(s["growth"] for s in selected_stats)
    new_views = sum(s["new_views"] for s in selected_stats)
    new_count = sum(s["new_count"] for s in selected_stats)

    def money_sum(pairs, no_rpm_note=True):
        """Sum views x RPM over channels that have an RPM; say how many were left out."""
        priced = [(v, ch_lib.get_rpm(s["ch"])) for s, v in pairs if ch_lib.get_rpm(s["ch"]) is not None]
        left_out = len(pairs) - len(priced)
        if not priced:
            return "set RPM in the picker"
        text = fmt_dollars(sum(ch_lib.earnings_dollars(v, rpm) for v, rpm in priced))
        return text + (f" (excludes {left_out} channel{'s' if left_out != 1 else ''} with no RPM)" if left_out else "")

    rows = [
        [f"{G} Total Views", fmt(total)],
        [f"{G} Growth (existing videos)", fmt(growth)],
        [f"{G} New Uploads (views)", f"{fmt(new_views)} ({new_count} videos)"],
        [f"{G} Earnings", money_sum([(s, s["total"]) for s in selected_stats])],
        [f"{G} Increase in Earnings", money_sum([(s, s["growth"] + s["new_views"]) for s in selected_stats])],
    ]

    if since_day:
        label = since_day.isoformat()
        infos = [(s, views_since(s["ch"], since_day)) for s in selected_stats]
        have = [(s, i) for s, i in infos if i is not None]
        if not have:
            rows.append([f"{G} Views since {label}", "no history recorded yet"])
        else:
            value = fmt(sum(i["views"] for _, i in have))
            notes = []
            if len(have) < len(infos):
                n = len(infos) - len(have)
                notes.append(f"{n} channel{'s' if n != 1 else ''} with no history left out")
            if any(i["partial"] for _, i in have):
                notes.append("some channels' records start after this date")
            rows.append([f"{G} Views since {label}", value + (f" ({'; '.join(notes)})" if notes else "")])
            rows.append([f"{G} Earnings since {label}", money_sum([(s, i["views"]) for s, i in have])])

    stale = [ch_lib.label(s["ch"]) for s in selected_stats if not s["fresh"]]
    if stale:
        rows.append([f"{G} Note", f"includes last known data for {len(stale)} stale channel{'s' if len(stale) != 1 else ''}; growth for those is 0"])
    return rows


def build_sheet(selected_stats, data, since_day=None):
    """total_views.csv: summary column on the left, then one table per selected channel.
    Each channel's earnings use its own RPM. Grand totals are computed on the fly from the channels shown."""
    summary = []
    for s in selected_stats:
        label = ch_lib.label(s["ch"])
        total_earn, new_earn = money(s)
        summary += [
            [f"{label} Total Views", fmt(s["total"])],
            [f"{label} Growth (existing videos)", fmt(s["growth"])],
            [f"{label} New Uploads (views)", f'{fmt(s["new_views"])} ({s["new_count"]} videos)'],
            [f"{label} RPM", ch_lib.fmt_rpm(ch_lib.get_rpm(s["ch"]))],
            [f"{label} Earnings", total_earn],
            [f"{label} Increase in Earnings", new_earn],
            *[[f"{label} {r[0]}", r[1]] for r in since_rows(s, since_day)],
            ["", ""],                       # blank row: space between each channel's totals
        ]
    grand = grand_total_rows(selected_stats, since_day)
    summary += grand
    if grand:
        summary.append(["", ""])            # space after the grand totals
    for s in selected_stats:
        if not s["fresh"]:
            last_ok = (s["meta"].get("last_success") or "never")[:19].replace("T", " ")
            summary.append([f'⚠️ {ch_lib.label(s["ch"])} STALE',
                            f"update failed; showing last known data from {last_ok} UTC"])

    tables = []
    for s in selected_stats:
        ch = s["ch"]
        # TikTok oldest-first, YouTube newest-first (as before)
        tables.append(read_csv_rows(BASE_DIR / ch["dir"] / csv_name(ch), reverse=ch["platform"] == "youtube"))

    n = max([len(summary)] + [len(t) for t in tables])
    summary += [["", ""]] * (n - len(summary))
    tables = [t + [["", "", "", "", ""]] * (n - len(t)) for t in tables]

    with open(SHEET_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["View Summary", ""] + sum(
            ([""] + [ch_lib.label(s["ch"])] + [""] * 4 for s in selected_stats), []))
        w.writerow(["Metric", "Value"] + sum(
            ([""] + ["#", "Caption", "Views", "New Views", "Status"] for _ in selected_stats), []))
        for i in range(n):
            w.writerow(summary[i] + sum(([""] + t[i] for t in tables), []))
    return SHEET_FILE


# ---------------- ONE-TIME MIGRATION ----------------

def archive_old_files(data):
    """Move retired files into archive/ (never deleted): the old shared snapshots/earnings files, and each
    channel's snapshots.csv, which the history log has replaced."""
    moves = [(BASE_DIR / n, ARCHIVE_DIR / (Path(n).stem + "_retired" + Path(n).suffix))
             for n in ("snapshots.csv", "snapshots_v1.csv", "earnings.csv")]
    for ch in data["channels"]:
        moves.append((BASE_DIR / ch["dir"] / "snapshots.csv",
                      ARCHIVE_DIR / f'{Path(ch["dir"]).name}_snapshots_retired.csv'))
    for src, dest in moves:
        if src.exists():
            ARCHIVE_DIR.mkdir(exist_ok=True)
            shutil.move(str(src), str(dest))


# ---------------- MAIN ----------------

def main(selected=None):
    data = ch_lib.load()
    since_day = ch_lib.get_since(data)
    archive_old_files(data)
    selected = selected or ch_lib.last_selected(data)

    all_stats = []
    for ch in selected:
        stats = channel_stats(ch)
        all_stats.append(stats)
        path = build_dashboard(stats, since_day)
        print(f"✅ {ch_lib.label(ch)} dashboard: {path.relative_to(BASE_DIR)}"
              + ("" if stats["fresh"] else "  (last fetch failed: showing last known data)"))

    build_sheet(all_stats, data, since_day)
    print(f"✅ Side-by-side sheet: {SHEET_FILE.name}")


if __name__ == "__main__":
    main()
