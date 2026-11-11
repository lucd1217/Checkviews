"""Shared logic for the TikTok / YouTube view trackers.

Each platform script only has to supply a fetch() that returns a list of
{"id", "timestamp", "caption", "views", "precise"} dicts. Everything else
(history merge, delta math, guards, backups, CSV, run metadata) lives here.
"""
import csv
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

MIN_KEEP_RATIO = 0.5   # abort if a fetch returns < 50% of the known ACTIVE videos
MIN_GUARD_VIDEOS = 10  # ...but only enforce that once we know at least this many
BACKUPS_TO_KEEP = 10


# ---------------- PROGRESS REPORTING + STREAMING ----------------

PROGRESS_PREFIX = "@@PROGRESS "


def report(stage, done=None, total=None):
    """Tell whoever launched this process how far along it is. The launcher (run.py) picks these
    lines up and shows them in the progress window; they are never shown as normal output."""
    print(PROGRESS_PREFIX + json.dumps({"stage": stage, "done": done, "total": total}), flush=True)


def stream_json(cmd, cwd, on_json, timeout=None):
    """Run a command and hand every JSON object it prints to on_json() as soon as it arrives
    (instead of waiting for the whole run). stderr is merged in. Returns (returncode, other_lines),
    where other_lines are the non-JSON lines (warnings/errors). If `timeout` passes, the process is
    killed but everything it printed so far has already been delivered."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    timer = threading.Timer(timeout, proc.kill) if timeout else None
    if timer:
        timer.start()
    other = []
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    other.append(line)
                continue
            if isinstance(data, dict):
                on_json(data)
    finally:
        if timer:
            timer.cancel()
        proc.wait()
    return proc.returncode, other


import history  # noqa: E402  (single source for everything time-based)


class FetchError(Exception):
    pass


# ---------------- HELPERS ----------------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_caption(text):
    if not text:
        return ""
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def format_views(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return ""


def parse_views(v):
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_json(path, default):
    path = Path(path)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"⚠️  {path} is corrupt; ignoring it.")
    return default


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)  # atomic: a crash can't leave half a file


def backup_history(history_file):
    history_file = Path(history_file)
    if not history_file.exists():
        return
    folder = history_file.parent / "backups"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(history_file, folder / f"video_history.{stamp}.json")
    for old in sorted(folder.glob("video_history.*.json"))[:-BACKUPS_TO_KEEP]:
        old.unlink()


# ---------------- GUARD ----------------

def check_fetch(fetched, history):
    """Raise FetchError if the fetch looks like a failure rather than real data."""
    if not fetched:
        raise FetchError("fetch returned 0 videos")
    prev_active = sum(1 for v in history.values() if v.get("status") == "ACTIVE")
    if prev_active >= MIN_GUARD_VIDEOS and len(fetched) < prev_active * MIN_KEEP_RATIO:
        raise FetchError(
            f"fetch returned only {len(fetched)} videos but {prev_active} were ACTIVE last time"
        )


# ---------------- MERGE ----------------

def apply_fetch(history, fetched, legacy_precise, stamp=None):
    """Merge fetched videos into history in place.

    legacy_precise: whether history entries written before the "precise" flag
    existed hold exact view counts. (YouTube's old flat-playlist counts were
    rounded, so those entries are re-baselined instead of producing fake growth.)
    """
    stamp = stamp or now_iso()
    seen = set()

    for item in fetched:
        vid = item["id"]
        seen.add(vid)
        views = item.get("views")
        precise = item.get("precise", True)
        caption = clean_caption(item.get("caption", ""))
        entry = history.get(vid)

        if entry is None:
            history[vid] = {
                "video_id": vid,
                "timestamp": item.get("timestamp"),
                "caption": caption,
                "views": format_views(views) if views is not None else None,
                "views_delta": None,
                "status": "ACTIVE",
                "first_seen": stamp,
                "new_this_run": True,
                "precise": precise,
            }
            continue

        entry["status"] = "ACTIVE"
        entry["new_this_run"] = False
        entry["views_delta"] = None
        was_precise = entry.get("precise", legacy_precise)

        if views is not None:
            old = parse_views(entry.get("views"))
            if precise and was_precise and old is not None:
                delta = views - old
                entry["views_delta"] = delta if delta >= 0 else None
                entry["views"] = format_views(views)
            elif precise or not was_precise:
                # Re-baseline: counts aren't comparable, so don't report growth.
                entry["views"] = format_views(views)
            # else: imprecise fallback for a precise entry -> keep old numbers
            if precise:
                entry["precise"] = True
            elif not was_precise:
                entry["precise"] = False
        else:
            entry.setdefault("precise", was_precise)

        if caption:
            entry["caption"] = caption
        if entry.get("timestamp") is None and item.get("timestamp") is not None:
            entry["timestamp"] = item["timestamp"]

    # Unseen videos -> MISSING (views untouched, delta cleared)
    for vid, entry in history.items():
        if vid not in seen:
            entry["status"] = "MISSING"
            entry["views_delta"] = None
            entry["new_this_run"] = False

    return history


# ---------------- OUTPUT ----------------

def write_csv(history_dict, csv_file, entries):
    """Per-video table. 'New views' and the NEW flag come from the history log, not from stored fields."""
    deltas = history.last_run_deltas(entries)
    new_ids = history.new_in_last_run(entries)
    ordered = sorted(history_dict.values(), key=lambda x: (x.get("timestamp") or 0))
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["number", "caption", "views", "views_since_last_checked", "status"],
        )
        writer.writeheader()
        for i, v in enumerate(ordered, start=1):
            vid = v.get("video_id")
            delta = deltas.get(vid)
            status = "NEW" if vid in new_ids and v.get("status") == "ACTIVE" else v.get("status", "")
            writer.writerow({
                "number": i,
                "caption": v.get("caption", ""),
                "views": v.get("views") or "",
                "views_since_last_checked": format_views(delta) if isinstance(delta, int) else "",
                "status": status,
            })


def write_meta(base_dir, ok, count=0, error=None):
    path = Path(base_dir) / "run_meta.json"
    meta = load_json(path, {})
    meta["last_run"] = now_iso()
    meta["ok"] = ok
    meta["error"] = error
    if ok:
        meta["last_success"] = meta["last_run"]
        meta["videos_fetched"] = count
    save_json(path, meta)



# ---------------- ENTRY POINT ----------------

def run_platform(label, base_dir, fetch_fn, legacy_precise, csv_name):
    base_dir = Path(base_dir)
    state_file = base_dir / "video_history.json"
    csv_file = base_dir / csv_name
    state = load_json(state_file, {})
    history.ensure_log(base_dir)   # make sure the state *before* this run is on record

    try:
        fetched = fetch_fn()
        check_fetch(fetched, state)
    except Exception as e:  # noqa: BLE001 - any failure must leave data untouched
        write_meta(base_dir, ok=False, error=str(e))
        print(f"❌ {label} fetch failed: {e}")
        print(f"   {label} history was NOT modified.")
        return 1

    report("Saving…")
    backup_history(state_file)
    apply_fetch(state, fetched, legacy_precise)
    save_json(state_file, state)
    write_meta(base_dir, ok=True, count=len(fetched))
    history.record_run(base_dir, state)            # the one history: everything "since" derives from this
    entries = history.load(base_dir)
    write_csv(state, csv_file, entries)

    new = len(history.new_in_last_run(entries))
    print(f"✅ {label} updated: {len(fetched)} videos fetched, {new} new.")
    return 0
