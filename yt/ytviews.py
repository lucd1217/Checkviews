import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import channels as ch_lib  # noqa: E402
from trackerlib import run_platform  # noqa: E402

WORKERS = 3              # parallel per-video lookups without cookies (kept low: YouTube rate-limits)
WORKERS_WITH_COOKIES = 8  # a logged-in session is throttled far less, so allow a few more
MIN_CHUNK = 5            # never split into batches smaller than this. Loading browser cookies costs ~5s per
                         # process, so each process handles a batch instead of paying that per video, and
                         # batches are sized so every worker gets one (no idle workers waiting on a long tail).
EXTRACTOR_ARGS = ["--extractor-args", "youtube:skip=dash,hls,translated_subs"]  # skips manifests we don't need
BLOCKED_CHUNKS = 2       # this many empty+throttled batches with zero successes => stop, we're blocked
# Browsers to borrow cookies from, in order. Only installed ones are tried; the first that
# yields a working lookup is used for the whole run. If none work we fall back to plain lookups.
COOKIE_BROWSERS = [
    ("chrome", "Google Chrome"),
    ("brave", "Brave Browser"),
    ("edge", "Microsoft Edge"),
    ("firefox", "Firefox"),
    ("safari", "Safari"),
]
PROBE_TIMEOUT = 90       # first cookie use can wait on a macOS Keychain prompt
LOOKUP_RETRIES = 3
BACKOFF_SECONDS = 6      # wait grows with each retry after a throttled lookup
BLOCKED_AFTER = 6        # this many throttled lookups with zero successes => stop, we're blocked
THROTTLE_MARKERS = ("429", "Too Many Requests", "Sign in to confirm", "not a bot")


class Blocked(RuntimeError):
    pass


# ---------------- FETCH ----------------

def list_shorts(channel, cwd):
    """Fast pass: ids + titles. View counts here are rounded, so only used as fallback."""
    proc = subprocess.run(
        [
            "yt-dlp",
            "--ignore-errors",
            "--dump-json",
            "--flat-playlist",
            "--match-filter", "duration <= 60",
            f"{channel['url']}/shorts",
        ],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    items = []
    for line in proc.stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("id"):
            items.append(data)
    return items


def lookup(vid, cwd, browser=None, timeout=120, retries=None):
    """Slow pass: exact view count + upload date for one video.

    browser: if set, borrow that browser's YouTube cookies for this lookup.

    Returns (data, throttled). data is None if the video couldn't be fetched;
    throttled says whether YouTube was blocking us (vs. the video being gone).
    """
    throttled = False
    retries = retries or LOOKUP_RETRIES
    cookie_args = ["--cookies-from-browser", browser] if browser else []
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                [
                    "yt-dlp", "--ignore-errors", "--no-warnings", *cookie_args,
                    "--dump-json", f"https://www.youtube.com/shorts/{vid}",
                ],
                capture_output=True, text=True, cwd=cwd, timeout=timeout,
            )
            if proc.stdout.strip():
                return json.loads(proc.stdout.splitlines()[0]), False
            throttled = any(m in proc.stderr for m in THROTTLE_MARKERS)
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            throttled = False
        if attempt < retries and throttled:
            time.sleep(BACKOFF_SECONDS * attempt)
        elif not throttled:
            break  # video genuinely unavailable: retrying won't help
    return None, throttled


def lookup_batch(vids, cwd, browser=None):
    """One yt-dlp process for many videos. Returns ({video_id: data}, throttled).

    Videos that fail are simply absent from the dict; throttled says whether
    YouTube's block markers showed up on stderr.
    """
    cookie_args = ["--cookies-from-browser", browser] if browser else []
    cmd = ["yt-dlp", "--ignore-errors", "--no-warnings", *EXTRACTOR_ARGS, *cookie_args,
           "--dump-json", *[f"https://www.youtube.com/shorts/{v}" for v in vids]]
    stdout = stderr = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                              timeout=PROBE_TIMEOUT + 30 * len(vids))
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:  # keep whatever finished before the timeout
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
    found = {}
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("id") and data.get("view_count") is not None:
            found[data["id"]] = data
    return found, any(m in stderr for m in THROTTLE_MARKERS)


def installed_cookie_browsers():
    found = []
    for key, app in COOKIE_BROWSERS:
        if any((Path(root) / f"{app}.app").exists() for root in ("/Applications", str(Path.home() / "Applications"))):
            found.append(key)
    return found


def pick_cookie_browser(vid, cwd):
    """Try each installed browser's cookies on one real lookup; return the first that works, else None."""
    for browser in installed_cookie_browsers():
        data, _throttled = lookup(vid, cwd, browser=browser, timeout=PROBE_TIMEOUT, retries=1)
        if data and data.get("view_count") is not None:
            return browser
        print(f"   (couldn't use {browser} cookies; trying next option)")
    return None


def to_timestamp(data):
    if data.get("timestamp"):
        return data["timestamp"]
    d = data.get("upload_date")  # YYYYMMDD
    if d:
        return int(datetime.strptime(d, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
    return None


def make_fetch(channel, base_dir):

    def fetch():
        flat = list_shorts(channel, base_dir)
        if not flat:
            raise RuntimeError("channel listing returned no shorts")

        browser = pick_cookie_browser(flat[0]["id"], base_dir)
        if browser:
            print(f"🍪 Using your {browser} cookies for YouTube lookups.")
        else:
            print("No usable browser cookies; using plain lookups (slower, easier to rate-limit).")

        ids = [f["id"] for f in flat]
        found = {}                      # video id -> yt-dlp data
        lock = threading.Lock()
        state = {"empty_throttled": 0, "throttled": False}
        workers = WORKERS_WITH_COOKIES if browser else WORKERS

        def run_chunk(chunk, mode):
            with lock:
                if not found and state["empty_throttled"] >= BLOCKED_CHUNKS:
                    return  # clearly blocked: stop hammering
            got, throttled = lookup_batch(chunk, base_dir, browser=mode)
            with lock:
                found.update(got)
                if throttled:
                    state["throttled"] = True
                if throttled and not got:
                    state["empty_throttled"] += 1

        def run_pass(todo, mode):
            size = max(MIN_CHUNK, -(-len(todo) // workers))   # ceil(videos / workers)
            chunks = [todo[i:i + size] for i in range(0, len(todo), size)]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda c: run_chunk(c, mode), chunks))

        run_pass(ids, browser)
        missing = [i for i in ids if i not in found]
        if missing and found and state["throttled"]:
            time.sleep(BACKOFF_SECONDS)          # give YouTube a breather before retrying
        if missing and (found or not state["throttled"]):
            run_pass(missing, browser)           # second chance, same method
            missing = [i for i in ids if i not in found]
        if missing and browser and (found or not state["throttled"]):
            run_pass(missing, None)              # cookies didn't get them: plain method

        if not found and state["throttled"]:
            raise Blocked(
                "YouTube is rate-limiting this connection (429 / 'confirm you're not a bot'). "
                "Nothing was changed. Wait a while and retry (browser cookies were "
                + ("used" if browser else "not available") + ")."
            )

        results = [(found.get(i), False) for i in ids]

        videos, raw, failed = [], [], []
        for f, (d, _throttled) in zip(flat, results):
            if d and d.get("view_count") is not None:
                title = d.get("title") or f.get("title", "")
                ts = to_timestamp(d)
                views = int(d["view_count"])
                raw.append({"id": f["id"], "title": title, "view_count": views,
                            "upload_date": d.get("upload_date"), "timestamp": ts})
                videos.append({"id": f["id"], "timestamp": ts, "caption": title,
                               "views": views, "precise": True})
            else:
                # Didn't come up: leave it out so it gets marked MISSING.
                failed.append(f["id"])
                raw.append({"id": f["id"], "title": f.get("title", ""), "lookup_failed": True})

        if failed:
            print(f"⚠️  {len(failed)} video(s) didn't come up and will be marked MISSING this run.")
        return videos

    return fetch


def run(channel):
    base_dir = ch_lib.channel_dir(channel)
    # legacy_precise=False: history written before exact lookups used rounded
    # counts, so those entries are re-baselined once rather than faking growth.
    return run_platform(ch_lib.label(channel), base_dir, make_fetch(channel, base_dir),
                        legacy_precise=False, csv_name="youtube_data.csv")


if __name__ == "__main__":
    sys.exit(run(ch_lib.default_for("youtube")))
