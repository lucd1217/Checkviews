"""The one history every time-based number comes from.

Each channel has an append-only log, <channel dir>/view_log.jsonl. One line per
successful run:

    {"at": "<iso time>", "views": {"<video id>": 1234, ...}, "status": {"<video id>": "A" | "M"}}

("A" = ACTIVE, "M" = MISSING. Older lines written before statuses were recorded
have no "status" and count every video in them.)

Everything that depends on time is derived from this log, nothing else:
  - growth since the last run, and which videos are new       -> run_summary / last_run_deltas
  - per-run totals over time (the old snapshots.csv)           -> timeline
  - views gained since any date                                -> views_since

video_history.json only holds the *current* state (caption, upload time, status,
latest views). It is no longer a source for any "since" or "growth" figure.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

VIEW_LOG_NAME = "view_log.jsonl"


# ---------------- small helpers (kept here so this module has no dependencies) ----------------

def _load_json(path, default):
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def _to_int(v):
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def counts_missing(platform):
    """Whether MISSING videos still count toward a channel's total (TikTok yes, YouTube no)."""
    return platform == "tiktok"


# ---------------- WRITING ----------------

def snapshot_of(history):
    """(views, status) dicts for every video in a video_history.json-style dict."""
    views, status = {}, {}
    for vid, entry in history.items():
        n = _to_int(entry.get("views"))
        if n is None:
            continue
        views[vid] = n
        status[vid] = "M" if entry.get("status") == "MISSING" else "A"
    return views, status


def record_run(base_dir, history, at=None):
    """Append this run's state to the channel's log."""
    views, status = snapshot_of(history)
    line = json.dumps({"at": at or _now_iso(), "views": views, "status": status}, separators=(",", ":"))
    with open(Path(base_dir) / VIEW_LOG_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_log(base_dir):
    """If a channel has history but no log yet, record its current state as the first entry,
    stamped with its last successful update."""
    base_dir = Path(base_dir)
    log_file = base_dir / VIEW_LOG_NAME
    history_file = base_dir / "video_history.json"
    if log_file.exists() or not history_file.exists():
        return
    history = _load_json(history_file, {})
    if not history:
        return
    meta = _load_json(base_dir / "run_meta.json", {})
    at = meta.get("last_success") or datetime.fromtimestamp(
        history_file.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    record_run(base_dir, history, at=at)


# ---------------- READING ----------------

def load(base_dir):
    """All log entries, oldest first, with 'at' as an aware datetime."""
    path = Path(base_dir) / VIEW_LOG_NAME
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                entries.append({"at": datetime.fromisoformat(e["at"]),
                                "views": {k: int(v) for k, v in e["views"].items()},
                                "status": e.get("status")})
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
    return sorted(entries, key=lambda e: e["at"])


def counted_ids(entry, platform):
    """Videos in this entry that count toward the channel total."""
    status = entry.get("status")
    if status is None:                      # old line: no status recorded, count everything
        return set(entry["views"])
    keep_missing = counts_missing(platform)
    return {v for v in entry["views"] if status.get(v, "A") == "A" or (keep_missing and status.get(v) == "M")}


def total_of(entry, platform):
    return sum(entry["views"][v] for v in counted_ids(entry, platform))


def run_summary(entries, platform, index=-1):
    """What entry `index` added compared with the entry before it.

    growth      = views gained by videos that already existed (never negative per video)
    new_views   = views held by videos that first appear in this entry
    new_ids     = those videos
    Returns None if there is no earlier entry to compare with.
    """
    if len(entries) < 2 or index == -len(entries) or index == 0:
        return None
    cur, prev = entries[index], entries[index - 1]
    ids = counted_ids(cur, platform)
    growth = new_views = 0
    new_ids = set()
    for v in ids:
        now = cur["views"][v]
        if v in prev["views"]:
            growth += max(0, now - prev["views"][v])
        else:
            new_views += now
            new_ids.add(v)
    return {"at": cur["at"], "growth": growth, "new_views": new_views, "new_ids": new_ids,
            "total": total_of(cur, platform)}


def last_run_deltas(entries):
    """Per-video change in the latest run: {video_id: delta}. Videos new in that run map to None."""
    if len(entries) < 2:
        return {}
    cur, prev = entries[-1], entries[-2]
    out = {}
    for v, now in cur["views"].items():
        if v in prev["views"]:
            d = now - prev["views"][v]
            out[v] = d if d >= 0 else None
        else:
            out[v] = None
    return out


def new_in_last_run(entries):
    """Video ids that first appeared in the latest run (empty if there is nothing to compare with)."""
    if len(entries) < 2:
        return set()
    return set(entries[-1]["views"]) - set(entries[-2]["views"])


def timeline(entries, platform):
    """One row per run: time, total, growth, new-upload views. (This replaces snapshots.csv.)"""
    rows = []
    for i, e in enumerate(entries):
        s = run_summary(entries, platform, i)
        rows.append({"at": e["at"], "total": total_of(e, platform),
                     "growth": s["growth"] if s else 0, "new_views": s["new_views"] if s else 0})
    return rows


def views_since(entries, current_history, platform, since_day):
    """Views gained since the start of `since_day` (local time).

    Compares each currently-counted video's views now with its views in the last log entry
    at or before that moment; videos that didn't exist then count in full. If the log only
    starts after that moment, the earliest entry is used and partial=True. None if no log.
    """
    if not entries:
        return None
    start = datetime(since_day.year, since_day.month, since_day.day).astimezone().astimezone(timezone.utc)
    before = [e for e in entries if e["at"] <= start]
    base, partial = (before[-1], False) if before else (entries[0], True)

    keep_missing = counts_missing(platform)
    gained = 0
    for vid, v in current_history.items():
        status = v.get("status")
        if not (status == "ACTIVE" or (keep_missing and status == "MISSING")):
            continue
        now = _to_int(v.get("views")) or 0
        gained += max(0, now - base["views"].get(vid, 0))
    return {"views": gained, "base_at": base["at"].astimezone(), "partial": partial}
