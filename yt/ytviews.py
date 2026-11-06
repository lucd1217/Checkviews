import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent


def parse_entry(data):
    vid = data.get("id")
    if not vid:
        return None
    return {
        "id": vid,
        "timestamp": data.get("timestamp"),
        "caption": common.clean_caption(data.get("title", "")),
        "views": common.parse_views(data.get("view_count")),
    }


def main():
    cfg = common.load_config()["youtube"]
    cmd = [
        "yt-dlp",
        "--ignore-errors",
        "--dump-json",
        "--flat-playlist",
        "--match-filter", "duration <= 60",
        f"{cfg['channel_url']}/shorts",
    ]
    return common.run_platform("youtube", cmd, parse_entry, BASE_DIR, "youtube_data.csv")


if __name__ == "__main__":
    sys.exit(main())
