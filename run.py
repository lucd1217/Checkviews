"""Entry point: ask which channels to check, update them, rebuild the dashboard.

  python3 run.py           # show the picker dialog
  python3 run.py --last    # skip the dialog, re-check whatever was checked last

Platforms run at the same time (e.g. TikTok and YouTube together). Channels on the
same platform run one after another, so several YouTube channels never pile up
requests and trigger YouTube's rate limiting.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tt"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "yt"))

import channels as ch_lib  # noqa: E402
import total_views  # noqa: E402
from trackerlib import PROGRESS_PREFIX  # noqa: E402

RUNNERS = {"tiktok": "ttviews", "youtube": "ytviews"}


def resolve_new(data, platform, raw):
    """Parse + validate a typed-in account and save it to the registry."""
    try:
        ch = ch_lib.parse_account(platform, raw)
    except ValueError as e:
        return None, str(e)
    if not ch_lib.by_id(data, ch["id"]):
        ok, err = ch_lib.validate(ch)
        if not ok:
            return None, f"Couldn't find {ch_lib.label(ch)}.\n\n{err}"
    ch = ch_lib.add(data, ch)
    ch_lib.save(data)
    return ch, ""


def choose(data, use_last):
    """Returns (selected channels or None, whether the picker window worked)."""
    if use_last:
        return ch_lib.last_selected(data), False
    try:
        import dialog
        return dialog.pick_channels(data, lambda p, r: resolve_new(data, p, r)), True
    except Exception as e:  # no display / Tk problem: fall back rather than fail
        last = ", ".join(ch_lib.label(c) for c in ch_lib.last_selected(data))
        print("\n" + "!" * 60)
        print(f"!!  COULD NOT SHOW THE CHANNEL PICKER: {e}")
        print(f"!!  Using the last selection instead: {last}")
        print("!" * 60 + "\n")
        time.sleep(5)  # long enough to read before the output scrolls
        return ch_lib.last_selected(data), False


def run_one(ch):
    """Update a single channel in this process (used by the --only child processes)."""
    module = __import__(RUNNERS[ch["platform"]])
    return module.run(ch)


def child_command(ch):
    return [sys.executable, str(Path(__file__).resolve()), "--only", ch["id"]]


def run_parallel(selected, build_cmd=child_command, on_event=None):
    """Run one worker per platform at once; within a platform, channels go in order.

    Each channel runs in its own process so output can be prefixed per channel. If on_event is given,
    it is called as on_event(channel, payload) with progress updates, output lines and a final
    {"finished": True, "ok": ...}. Progress lines are never printed as normal output.
    Returns the number of channels that failed.
    """
    groups = {}
    for ch in selected:
        groups.setdefault(ch["platform"], []).append(ch)

    print_lock = threading.Lock()
    failures = []
    env = dict(os.environ, PYTHONUNBUFFERED="1")

    def emit(prefix, line):
        with print_lock:
            print(f"{prefix}{line}", flush=True)

    def worker(chs):
        for ch in chs:
            prefix = f"[{ch_lib.label(ch)}] "
            emit(prefix, "starting…")
            if on_event:
                on_event(ch, {"stage": "Starting…"})
            proc = subprocess.Popen(build_cmd(ch), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=env,
                                    cwd=Path(__file__).resolve().parent)
            for line in proc.stdout:
                if line.startswith(PROGRESS_PREFIX):
                    if on_event:
                        try:
                            on_event(ch, json.loads(line[len(PROGRESS_PREFIX):]))
                        except json.JSONDecodeError:
                            pass
                    continue
                if line.strip():
                    emit(prefix, line.rstrip())
                    if on_event:
                        on_event(ch, {"line": line.rstrip()})
            ok = proc.wait() == 0
            if not ok:
                failures.append(ch["id"])
            if on_event:
                on_event(ch, {"finished": True, "ok": ok})

    threads = [threading.Thread(target=worker, args=(chs,)) for chs in groups.values()]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"\nAll channels finished in {time.time() - started:.0f}s.")
    return len(failures)


def main():
    data = ch_lib.load()

    if "--only" in sys.argv:  # child mode: update exactly one channel, nothing else
        ch = ch_lib.by_id(data, sys.argv[sys.argv.index("--only") + 1])
        return run_one(ch) if ch else 2

    selected, gui = choose(data, "--last" in sys.argv)
    if not selected:
        print("Cancelled.")
        return 130

    data["last_selected"] = [c["id"] for c in selected]
    ch_lib.save(data)

    def work(post=None):
        """Update every selected channel, then build the sheet. Runs in a background thread when a
        progress window is showing, so the window stays responsive."""
        on_event = (lambda ch, payload: post(ch["id"], payload)) if post else None
        failed = run_parallel(selected, on_event=on_event)
        if post:
            post("__all__", {"stage": "Building the sheet…"})
        print("\nAggregating total views...")
        total_views.main(selected)
        return failed

    window = None
    if gui:  # the picker was used, so a window system exists; show progress instead of freezing
        try:
            from progress import ProgressWindow
            window = ProgressWindow(selected)
        except Exception as e:  # noqa: BLE001 - never block the run on a cosmetic window
            print(f"(No progress window: {e})")
    failures = window.run(work) if window else work()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
