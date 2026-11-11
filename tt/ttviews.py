import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import channels as ch_lib  # noqa: E402
from trackerlib import load_json, report, run_platform, stream_json  # noqa: E402

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


def fetch_source(source, cwd, expected=None):
    """Fetch one source, reporting progress as each video arrives. `expected` is roughly how many
    videos we saw last time, so the progress bar has something to fill toward."""
    videos = []

    def on_video(data):
        vid = data.get("id")
        if not vid:
            return
        views = data.get("view_count")
        videos.append({
            "id": str(vid),
            "timestamp": data.get("timestamp"),
            "caption": data.get("description") or data.get("title") or "",
            "views": int(views) if views is not None else None,
            "precise": True,
        })
        n = len(videos)
        report("Fetching videos", n, max(expected or 0, n))

    _rc, other = stream_json(
        [
            "yt-dlp",
            "--ignore-errors",
            "--impersonate", IMPERSONATE_TARGET,
            "--dump-json",
            "--flat-playlist",
            source,
        ],
        cwd, on_video,
    )
    return videos, other


def known_video_count(base_dir):
    state = load_json(Path(base_dir) / "video_history.json", {})
    return sum(1 for v in state.values() if v.get("status") == "ACTIVE") or None


def make_fetch(channel, base_dir):
    def fetch():
        expected = known_video_count(base_dir)
        last_err = ""
        report("Fetching videos", 0, expected)
        for source in sources_for(channel):
            videos, other = fetch_source(source, base_dir, expected)
            if videos:
                return videos
            last_err = other[-1] if other else "no output"
        raise RuntimeError(f"all sources failed ({last_err})")

    return fetch


def run(channel):
    base_dir = ch_lib.channel_dir(channel)
    return run_platform(ch_lib.label(channel), base_dir, make_fetch(channel, base_dir),
                        legacy_precise=True, csv_name="tiktok_data.csv")


if __name__ == "__main__":
    sys.exit(run(ch_lib.default_for("tiktok")))
