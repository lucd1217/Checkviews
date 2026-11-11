"""Progress window shown while channels are being updated.

The work runs in a background thread and sends updates through a queue; this window (the only
thing touching Tk) polls the queue. Because the main thread keeps handling window events the whole
time, macOS never shows the spinning beachball while videos are being fetched.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import channels as ch_lib
from dialog import bring_to_front

ALL = "__all__"   # event key for messages about the whole run rather than one channel


class ProgressWindow:
    def __init__(self, channels):
        self.root = tk.Tk()
        self.root.title("Checking channels")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        frame = ttk.Frame(self.root, padding=18)
        frame.grid()

        ttk.Label(frame, text="Updating your channels…", font=("Helvetica", 14, "bold")).grid(
            row=0, column=0, sticky="w")
        ttk.Label(frame, text="This can take a minute. You can leave this window open.",
                  foreground="#666").grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.rows = {}
        for i, ch in enumerate(channels):
            box = ttk.Frame(frame)
            box.grid(row=2 + i, column=0, sticky="ew", pady=(0, 10))
            ttk.Label(box, text=ch_lib.label(ch), font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w")
            bar = ttk.Progressbar(box, length=380, mode="determinate", maximum=100)
            bar.grid(row=1, column=0, sticky="ew", pady=(4, 2))
            status = ttk.Label(box, text="Waiting…", foreground="#666")
            status.grid(row=2, column=0, sticky="w")
            self.rows[ch["id"]] = {"bar": bar, "status": status, "indeterminate": False, "finished": False}

        self.footer = ttk.Label(frame, text="", foreground="#666")
        self.footer.grid(row=2 + len(channels), column=0, sticky="w", pady=(2, 0))
        self.close_btn = ttk.Button(frame, text="Close", command=self.root.destroy)

        self.q = queue.Queue()
        self.finished = False      # worker thread has ended
        self.result = None
        self.error = None

    # ----- called from the worker threads (thread-safe: only touches the queue) -----
    def post(self, key, payload):
        self.q.put((key, payload))

    # ----- everything below runs on the main (Tk) thread -----
    def _on_close(self):
        if self.finished:
            self.root.destroy()
        # while work is running the window can't be closed: the fetches would keep going unseen

    def _set_progress(self, row, done, total):
        bar = row["bar"]
        if total:
            if row["indeterminate"]:
                bar.stop()
                bar.config(mode="determinate")
                row["indeterminate"] = False
            bar.config(value=min(100, 100 * (done or 0) / total))
        elif not row["indeterminate"]:
            bar.config(mode="indeterminate")
            bar.start(12)
            row["indeterminate"] = True

    def _apply(self, key, p):
        if key == ALL:
            self.footer.config(text=p.get("stage", ""))
            return
        row = self.rows.get(key)
        if row is None or row["finished"]:
            return
        if p.get("finished"):
            row["finished"] = True
            row["ok"] = bool(p.get("ok"))
            if row["indeterminate"]:
                row["bar"].stop()
                row["bar"].config(mode="determinate")
                row["indeterminate"] = False
            if p.get("ok"):
                row["bar"].config(value=100)
                row["status"].config(text=row.get("summary") or "Done ✓")
            else:
                row["status"].config(text=row.get("summary") or "Failed ✗", foreground="#c0392b")
            return
        if "line" in p:                     # a normal output line: keep the final ✅ / ❌ message
            line = p["line"]
            if "✅" in line and "updated:" in line:
                row["summary"] = "✓ " + line.split("updated:", 1)[1].strip()
            elif "❌" in line and "failed:" in line:
                row["summary"] = "✗ " + line.split("failed:", 1)[1].strip()
            return
        stage, done, total = p.get("stage", ""), p.get("done"), p.get("total")
        self._set_progress(row, done, total)
        if done is not None and total:
            text = f"{stage}: {done} of {total}"
        elif done:
            text = f"{stage}… {done} so far"
        else:
            text = stage if stage.endswith("…") else f"{stage}…"
        row["status"].config(text=text)

    def _poll(self):
        try:
            while True:
                self._apply(*self.q.get_nowait())
        except queue.Empty:
            pass
        if self.finished and self.q.empty():
            self._finish()
        else:
            self.root.after(80, self._poll)

    def _finish(self):
        failed = self.error is not None or any(not r.get("ok") for r in self.rows.values())
        if self.error is not None:
            self.footer.config(text=f"Something went wrong: {self.error}", foreground="#c0392b")
        if failed:
            if self.error is None:
                self.footer.config(text="Some channels didn't update. Their last known numbers are kept.",
                                   foreground="#c0392b")
            self.close_btn.grid(row=3 + len(self.rows), column=0, sticky="e", pady=(10, 0))
        else:
            self.footer.config(text="All done ✓")
            self.root.after(900, self.root.destroy)

    def run(self, work):
        """Run work(post) in a background thread while showing the window. Returns work's result."""
        def target():
            try:
                self.result = work(self.post)
            except Exception as e:  # noqa: BLE001 - show it rather than dying silently
                self.error = e
            finally:
                self.finished = True

        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        self.root.geometry(f"+{(self.root.winfo_screenwidth() - w) // 2}+{(self.root.winfo_screenheight() - h) // 3}")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.after(50, bring_to_front)
        threading.Thread(target=target, daemon=True).start()
        self.root.after(80, self._poll)
        self.root.mainloop()
        if self.error is not None:
            raise self.error
        return self.result
