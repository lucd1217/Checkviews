"""Registry of channels the tracker knows about (channels.json).

Each channel: {id, platform, handle, dir, [url], [channel_id], [rpm_cents]}.
rpm_cents is revenue per 1,000 views in cents (e.g. 12 = $0.12 per 1,000 views); None = not set.
`dir` is relative to this folder and holds that channel's history/CSV/meta.
"""
import re
import subprocess
from datetime import date
from pathlib import Path

from trackerlib import load_json, save_json

ROOT = Path(__file__).parent
CHANNELS_FILE = ROOT / "channels.json"

# The two original accounts keep their existing tt/ and yt/ folders.
SEED = [
    {
        "id": "tiktok:togi..clips",
        "platform": "tiktok",
        "handle": "togi..clips",
        "dir": "tt",
        # yt-dlp can't resolve this dotted handle; the secUid works via tiktokuser:
        "channel_id": "MS4wLjABAAAAOVRauhiMaTukhg1p_t778GWLRhBEjKPZzAB__UYBUZDFz_eb9-dH7kumP-nGQ-Qb",
        "rpm_cents": 12,   # was the old flat post-March rate; edit it in the picker
    },
    {
        "id": "youtube:togiclips646",
        "platform": "youtube",
        "handle": "togiclips646",
        "dir": "yt",
        "url": "https://www.youtube.com/@togiclips646",
        "rpm_cents": 12,
    },
]

PLATFORM_NAMES = {"tiktok": "TikTok", "youtube": "YouTube"}


# ---------------- RPM / EARNINGS ----------------

def get_rpm(ch):
    """RPM in cents per 1,000 views, or None if it hasn't been set."""
    v = ch.get("rpm_cents")
    return float(v) if isinstance(v, (int, float)) else None


def parse_rpm(text):
    """Parse what was typed in an RPM box. Blank -> None. Raises ValueError if invalid."""
    text = (text or "").strip().replace(",", "").replace("¢", "")
    if not text:
        return None
    value = float(text)          # ValueError if not a number
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("RPM must be a regular number.")
    if value < 0:
        raise ValueError("RPM can't be negative.")
    return value


def earnings_dollars(views, rpm_cents):
    """Dollars earned for `views` at `rpm_cents` cents per 1,000 views."""
    return views * rpm_cents / 100 / 1000


def get_since(data):
    """The 'views since' date chosen in the picker, as a date, or None if off/invalid."""
    raw = data.get("since_date")
    try:
        return date.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None


def fmt_rpm(rpm_cents):
    return "not set" if rpm_cents is None else f"{rpm_cents:g}¢ per 1,000 views"


# ---------------- LOAD / SAVE ----------------

def load():
    data = load_json(CHANNELS_FILE, None)
    if not data:
        data = {"channels": SEED, "last_selected": [c["id"] for c in SEED]}
        save_json(CHANNELS_FILE, data)
    # One-time migration: the old "earnings" flag meant the tiered 12c rate; now each channel has its own RPM.
    migrated = False
    for ch in data["channels"]:
        if "earnings" in ch:
            if ch.pop("earnings") and "rpm_cents" not in ch:
                ch["rpm_cents"] = 12
            migrated = True
    if migrated:
        save_json(CHANNELS_FILE, data)
    return data


def save(data):
    save_json(CHANNELS_FILE, data)


def by_id(data, cid):
    return next((c for c in data["channels"] if c["id"] == cid), None)


def label(ch):
    return f"{PLATFORM_NAMES.get(ch['platform'], ch['platform'])} · @{ch['handle']}"


def last_selected(data):
    chosen = [by_id(data, i) for i in data.get("last_selected", [])]
    chosen = [c for c in chosen if c]
    return chosen or data["channels"][:1]


def default_for(platform):
    """First saved channel on a platform (used by the single-platform scripts)."""
    return next(c for c in load()["channels"] if c["platform"] == platform)


def channel_dir(ch):
    path = ROOT / ch["dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------- NEW ACCOUNTS ----------------

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "channel"


def parse_account(platform, raw):
    """Turn what the user typed (handle, @handle, or profile URL) into a channel dict."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Enter a handle or profile URL.")

    if platform == "tiktok":
        m = re.search(r"tiktok\.com/@([^/?#\s]+)", raw)
        handle = (m.group(1) if m else raw).lstrip("@").strip("/")
        if not re.fullmatch(r"[\w.]+", handle):
            raise ValueError(f"'{raw}' doesn't look like a TikTok handle.")
        ch = {"platform": "tiktok", "handle": handle}
    elif platform == "youtube":
        m = re.search(r"youtube\.com/(@[^/?#\s]+)", raw)
        m_id = re.search(r"youtube\.com/channel/([\w-]+)", raw)
        if m:
            handle = m.group(1).lstrip("@")
            url = f"https://www.youtube.com/@{handle}"
        elif m_id:
            handle = m_id.group(1)
            url = f"https://www.youtube.com/channel/{handle}"
        else:
            handle = raw.lstrip("@").strip("/")
            if not re.fullmatch(r"[\w.\-]+", handle):
                raise ValueError(f"'{raw}' doesn't look like a YouTube handle.")
            url = f"https://www.youtube.com/@{handle}"
        ch = {"platform": "youtube", "handle": handle, "url": url}
    else:
        raise ValueError(f"Unknown platform: {platform}")

    ch["id"] = f"{platform}:{ch['handle'].lower()}"
    ch["dir"] = f"channels/{platform}_{_slug(ch['handle'])}"
    return ch


def validate(ch):
    """Quick yt-dlp probe. Returns (ok, error_message)."""
    if ch["platform"] == "tiktok":
        cmd = ["yt-dlp", "--impersonate", "Chrome-124", "--simulate", "--flat-playlist",
               "--playlist-items", "1", "--print", "%(id)s",
               f"https://www.tiktok.com/@{ch['handle']}"]
    else:
        cmd = ["yt-dlp", "--simulate", "--flat-playlist", "--playlist-items", "1",
               "--print", "%(id)s", ch["url"] + "/shorts"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "Timed out looking up that account."
    if proc.stdout.strip():
        return True, ""
    err = proc.stderr.strip()
    if "404" in err or "secondary user ID" in err or "not found" in err.lower():
        return False, "No public account with that handle was found. Check the spelling."
    if "no videos" in err.lower() or not err:
        return False, "That account has no public videos to track."
    return False, err.splitlines()[-1][:200]


def add(data, ch):
    """Add channel if new; return the stored version (existing one wins)."""
    existing = by_id(data, ch["id"])
    if existing:
        return existing
    data["channels"].append(ch)
    return ch
