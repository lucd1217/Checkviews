import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import channels as ch_lib  # noqa: E402
from trackerlib import run_platform  # noqa: E402

IMPERSONATE_TARGET = "Chrome-124"


# ---------------- FETCH ----------------

def sources_for(channel):
    """Most reliable first. A stored channel_id (secUid) works for handles yt-dlp
    can't resolve by name; plain profile URLs work for everything else."""
    sources = []
    if channel.get("channel_id"):
        sources.append(f"tiktokuser:{channel['channel_id']}")
    sources.append(f"https://www.tiktok.com/@{channel['handle']}")
    return sources


def fetch_source(source, cwd):
    proc = subprocess.run(
        [
            "yt-dlp",
            "--ignore-errors",
            "--impersonate", IMPERSONATE_TARGET,
            "--dump-json",
            "--flat-playlist",
            source,
        ],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    videos, raw_lines = [], []
    for line in proc.stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = data.get("id")
        if not vid:
            continue
        raw_lines.append(line)
        views = data.get("view_count")
        videos.append({
            "id": str(vid),
            "timestamp": data.get("timestamp"),
            "caption": data.get("description") or data.get("title") or "",
            "views": int(views) if views is not None else None,
            "precise": True,
        })
    return videos, raw_lines, proc.stderr


def make_fetch(channel, base_dir):
    def fetch():
        last_err = ""
        for source in sources_for(channel):
            videos, raw_lines, err = fetch_source(source, base_dir)
            if videos:
                return videos
            last_err = err.strip().splitlines()[-1] if err.strip() else "no output"
        raise RuntimeError(f"all sources failed ({last_err})")

    return fetch


def run(channel):
    base_dir = ch_lib.channel_dir(channel)
    return run_platform(ch_lib.label(channel), base_dir, make_fetch(channel, base_dir),
                        legacy_precise=True, csv_name="tiktok_data.csv")


if __name__ == "__main__":
    sys.exit(run(ch_lib.default_for("tiktok")))
