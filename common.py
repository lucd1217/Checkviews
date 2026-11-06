"""Shared logic for the TikTok / YouTube view trackers."""
import csv
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
LOG_FILE = ROOT / "checkviews.log"

log = logging.getLogger("checkviews")

CSV_FIELDS = ["number", "caption", "views", "views_since_last_checked", "status"]


class FetchError(Exception):
    pass


# ---------------- setup ----------------

def setup_logging():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_FILE, encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


# ---------------- helpers ----------------

def clean_caption(text):
    if not text:
        return ""
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_views(v):
    """Return an int, or None if v is missing/unparseable. Accepts 1234 or '1,234'."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def format_views(n):
    n = parse_views(n)
    return f"{n:,}" if n is not None else ""


def load_json(path, default):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    """Atomic write so a crash can't leave a corrupt file."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_history(path):
    history = load_json(path, {})
    for rec in history.values():  # older files stored views as "1,234" strings
        rec["views"] = parse_views(rec.get("views"))
    return history


# ---------------- fetching ----------------

def read_jsonl(path):
    entries = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("skipping malformed JSON on line %d of %s", n, Path(path).name)
    return entries


def fetch_entries(cmd, raw_path, retries, backoff, cwd):
    """Run yt-dlp. Returns (entries, complete). Raises FetchError if nothing came back.

    complete is False when yt-dlp exited non-zero but still returned some videos
    (a partial result), so callers must not conclude that other videos are gone.
    The previous raw file is only replaced after a successful fetch.
    """
    raw_path = Path(raw_path)
    tmp = raw_path.with_suffix(".tmp")
    for attempt in range(1, retries + 2):
        try:
            with open(tmp, "w", encoding="utf-8") as out:
                proc = subprocess.run(cmd, stdout=out, cwd=cwd)
        except FileNotFoundError:
            raise FetchError(f"'{cmd[0]}' not found - install it (brew install yt-dlp)")
        entries = read_jsonl(tmp)
        if entries:
            os.replace(tmp, raw_path)
            if proc.returncode != 0:
                log.warning("yt-dlp exited %d but returned %d videos (partial result)",
                            proc.returncode, len(entries))
            return entries, proc.returncode == 0
        log.warning("attempt %d/%d returned no videos (exit %d)", attempt, retries + 1, proc.returncode)
        if attempt <= retries:
            time.sleep(backoff * attempt)
    tmp.unlink(missing_ok=True)
    raise FetchError("no videos returned - blocked, offline, or wrong URL")


# ---------------- history ----------------

def update_history(history, entries, complete=True):
    """entries: dicts with id, timestamp, caption, views (int|None)."""
    by_id = {}
    for e in entries:  # de-duplicate: a video can show up twice in one fetch
        by_id[e["id"]] = e

    for vid, e in by_id.items():
        new = e["views"]
        rec = history.get(vid)
        if rec is None:
            history[vid] = {
                "video_id": vid,
                "timestamp": e["timestamp"],
                "caption": e["caption"],
                "views": new,
                "views_delta": None,
                "status": "ACTIVE",
            }
            continue

        rec["status"] = "ACTIVE"
        if new is not None:
            old = rec.get("views")
            # unchanged count keeps the previous delta so re-running doesn't zero it
            if old is None:
                rec["views_delta"] = None
            elif new != old:
                rec["views_delta"] = new - old
            rec["views"] = new
        if e["caption"]:
            rec["caption"] = e["caption"]
        if rec.get("timestamp") is None:
            rec["timestamp"] = e["timestamp"]

    # Only declare videos MISSING after a complete fetch.
    if complete:
        for vid, rec in history.items():
            if vid not in by_id:
                rec["status"] = "MISSING"
                rec["views_delta"] = None
    return history


def append_timeseries(path, entries, ts=None):
    ts = ts or int(time.time())
    with open(path, "a", encoding="utf-8") as f:
        for e in {e["id"]: e for e in entries}.values():
            if e["views"] is not None:
                f.write(json.dumps({"ts": ts, "id": e["id"], "views": e["views"]}) + "\n")


def write_csv(history, path):
    ordered = sorted(history.values(), key=lambda x: x.get("timestamp") or 0)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for i, v in enumerate(ordered, start=1):
            delta = v.get("views_delta")
            writer.writerow({
                "number": i,
                "caption": v.get("caption", ""),
                "views": format_views(v.get("views")),
                "views_since_last_checked": format_views(delta) if isinstance(delta, int) else "",
                "status": v.get("status", ""),
            })


def run_platform(name, cmd, parse_entry, platform_dir, csv_name):
    """Fetch -> update history -> CSV. Returns a process exit code."""
    setup_logging()
    platform_dir = Path(platform_dir)
    cfg = load_config()["fetch"]
    try:
        raw, complete = fetch_entries(
            cmd, platform_dir / f"{name}_raw.jsonl",
            cfg["retries"], cfg["backoff_seconds"], platform_dir)
    except FetchError as e:
        log.error("%s fetch failed: %s. History left untouched.", name, e)
        return 1

    entries = [p for p in (parse_entry(d) for d in raw) if p]
    if not entries:
        log.error("%s: fetched data had no usable videos. History left untouched.", name)
        return 1

    history_path = platform_dir / "video_history.json"
    history = update_history(load_history(history_path), entries, complete)
    save_json(history_path, history)
    append_timeseries(platform_dir / "views_timeseries.jsonl", entries)
    write_csv(history, platform_dir / csv_name)
    log.info("%s history updated (%d videos%s)", name, len(entries),
             "" if complete else ", partial fetch - missing-check skipped")
    return 0
