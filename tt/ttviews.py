import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent


def extract_video_id(url):
    # drop any query string / fragment, e.g. ".../video/123?lang=en"
    return url.split("/video/")[-1].split("?")[0].split("#")[0]


def parse_entry(data):
    url = data.get("webpage_url")
    vid = extract_video_id(url) if url else data.get("id")
    if not vid:
        return None
    return {
        "id": vid,
        "timestamp": data.get("timestamp"),
        "caption": common.clean_caption(data.get("description", "")),
        "views": common.parse_views(data.get("view_count")),
    }


def main():
    cfg = common.load_config()["tiktok"]
    cmd = [
        "yt-dlp",
        "--ignore-errors",
        "--impersonate", cfg["impersonate"],
        "--dump-json",
        cfg["profile_url"],
    ]
    return common.run_platform("tiktok", cmd, parse_entry, BASE_DIR, "tiktok_data.csv")


if __name__ == "__main__":
    sys.exit(main())
