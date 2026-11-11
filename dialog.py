"""Pre-run dialog: pick which channel(s) to check, or add a new account."""
import calendar
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk

import channels as ch_lib
import history


def bring_to_front():
    """Activate this process at the macOS level.

    Tk's lift()/-topmost only reorder windows inside the app; a Python process
    started from Terminal is not the active app, so without this the dialog can
    open hidden behind Terminal. Returns True if the app reports itself active.
    """
    try:
        import ctypes
        import ctypes.util

        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def send(obj, sel, *args, restype=ctypes.c_void_p, argtypes=()):
            fn = objc.objc_msgSend
            fn.restype = restype
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
            return fn(obj, objc.sel_registerName(sel), *args)

        app = send(objc.objc_getClass(b"NSApplication"), b"sharedApplication")
        send(app, b"activateIgnoringOtherApps:", True, restype=None, argtypes=(ctypes.c_bool,))
        return bool(send(app, b"isActive", restype=ctypes.c_bool))
    except Exception:  # never let focus handling break the dialog
        return False


class CalendarPicker(ttk.Frame):
    """A month calendar. Days outside [min_date, max_date] are greyed out and can't be clicked.

    min_date=None means no day is available (nothing recorded yet), so every day is greyed out.
    """
    GREY = "#9a9a9f"
    SELECTED_BG = "#2f6df6"

    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        probe = tk.Label(self)                      # Tk can't reset a colour with "", so remember the theme's own
        self.NORMAL_FG, self.NORMAL_BG = probe.cget("fg"), probe.cget("bg")
        probe.destroy()
        self.min_date = None
        self.max_date = date.today()
        self.selected = None
        self.view = date.today().replace(day=1)
        self.on_change = on_change

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        self.prev_lbl = tk.Label(head, text="◀", width=3)
        self.prev_lbl.grid(row=0, column=0)
        self.title_lbl = ttk.Label(head, text="", anchor="center", width=18, font=("Helvetica", 12, "bold"))
        self.title_lbl.grid(row=0, column=1)
        self.next_lbl = tk.Label(head, text="▶", width=3)
        self.next_lbl.grid(row=0, column=2)
        for lbl, step in ((self.prev_lbl, -1), (self.next_lbl, 1)):
            lbl.bind("<Button-1>", lambda _e, l=lbl, st=step: self._nav(l, st))

        grid = ttk.Frame(self)
        grid.grid(row=1, column=0, pady=(6, 0))
        for i, name in enumerate(calendar.day_abbr[6:] + calendar.day_abbr[:6]):   # Sunday first
            ttk.Label(grid, text=name[:2], foreground="#777", anchor="center", width=4).grid(row=0, column=i)
        self.cells = []
        for r in range(6):
            for c in range(7):
                lbl = tk.Label(grid, text="", width=4, pady=3)
                lbl.grid(row=r + 1, column=c, padx=1, pady=1)
                lbl._date, lbl._enabled = None, False
                lbl.bind("<Button-1>", lambda _e, l=lbl: self._click(l))
                self.cells.append(lbl)

        self.note = ttk.Label(self, text="", foreground="#666", wraplength=300, justify="left")
        self.note.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._render()

    # ----- state -----
    def enabled(self, d):
        return self.min_date is not None and self.min_date <= d <= self.max_date

    def set_range(self, min_date, default=None):
        """Update the first selectable day. Keeps the selection if it is still valid, else moves it
        to the closest valid day (or None if no day is available)."""
        self.min_date = min_date
        if min_date is None:
            self.selected = None
        else:
            want = self.selected or default or self.max_date
            self.selected = min(max(want, min_date), self.max_date)
            self.view = self.selected.replace(day=1)
        self._render()

    def select(self, d):
        if self.enabled(d):
            self.selected = d
            self._render()
            if self.on_change:
                self.on_change(d)

    # ----- drawing -----
    def _nav_allowed(self, step):
        if self.min_date is None:
            return False
        if step < 0:
            return self.view - timedelta(days=1) >= self.min_date
        nxt = (self.view + timedelta(days=32)).replace(day=1)
        return nxt <= self.max_date

    def _nav(self, lbl, step):
        if not self._nav_allowed(step):
            return
        self.view = (self.view - timedelta(days=1)).replace(day=1) if step < 0 else \
            (self.view + timedelta(days=32)).replace(day=1)
        self._render()

    def _click(self, lbl):
        if lbl._enabled and lbl._date:
            self.select(lbl._date)

    def _render(self):
        self.title_lbl.config(text=f"{calendar.month_name[self.view.month]} {self.view.year}")
        for lbl, step in ((self.prev_lbl, -1), (self.next_lbl, 1)):
            ok = self._nav_allowed(step)
            lbl.config(fg=self.NORMAL_FG if ok else self.GREY, cursor="pointinghand" if ok else "")
        weeks = calendar.Calendar(firstweekday=6).monthdayscalendar(self.view.year, self.view.month)
        weeks += [[0] * 7] * (6 - len(weeks))
        for lbl, day in zip(self.cells, [d for w in weeks for d in w]):
            if day == 0:
                lbl._date, lbl._enabled = None, False
                lbl.config(text="", bg=self.NORMAL_BG, fg=self.NORMAL_FG, cursor="")
                continue
            d = self.view.replace(day=day)
            ok = self.enabled(d)
            lbl._date, lbl._enabled = d, ok
            if d == self.selected:
                lbl.config(text=str(day), bg=self.SELECTED_BG, fg="white", cursor="pointinghand")
            elif ok:
                lbl.config(text=str(day), bg=self.NORMAL_BG, fg=self.NORMAL_FG, cursor="pointinghand")
            else:
                lbl.config(text=str(day), bg=self.NORMAL_BG, fg=self.GREY, cursor="")
        if self.min_date is None:
            self.note.config(text="No history recorded yet for the ticked channels, so no dates are available.")
        else:
            self.note.config(text=f"Greyed-out days have no records before them. "
                                  f"Earliest available: {calendar.month_abbr[self.min_date.month]} "
                                  f"{self.min_date.day}, {self.min_date.year}.")


def pick_channels(data, resolve_new):
    """Show the dialog. Returns a list of channel dicts, or None if cancelled.

    Every saved channel is a checkbox (last run's selection pre-ticked) with an RPM
    box beside it (cents per 1,000 views, saved on OK). A date picker chooses the day
    to count "views gained since" from. New accounts can be added in place and show
    up ticked.

    resolve_new(platform, raw) -> (channel_dict | None, error_message)
    validates and registers a new account.
    """
    if tk.TkVersion < 8.6:
        # Apple's bundled Tk 8.5 can hang without ever drawing a window.
        raise RuntimeError(f"Tk {tk.TkVersion} is too old (need 8.6+); run with python.org/Homebrew Python")

    chans = data["channels"]
    last_ids = {c["id"] for c in ch_lib.last_selected(data)}
    result = {"value": None}

    root = tk.Tk()
    root.title("Check channels")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=18)
    frame.grid()

    ttk.Label(frame, text="Which channels do you want to check?",
              font=("Helvetica", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(frame, text="Tick as many as you like. Your last selection is pre-ticked.",
              foreground="#666").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

    # --- channel checkboxes ---
    checks_frame = ttk.Frame(frame)
    checks_frame.grid(row=2, column=0, columnspan=2, sticky="w")
    vars_by_id = {}  # channel id -> BooleanVar
    rpm_vars = {}    # channel id -> StringVar (text in that channel's RPM box)

    ttk.Label(checks_frame, text="RPM  (¢ per 1,000 views)", foreground="#666").grid(
        row=0, column=1, padx=(18, 0), sticky="w")

    go_btn = None

    def update_go(*_):
        if go_btn is not None:
            go_btn.state(["!disabled"] if any(v.get() for v in vars_by_id.values()) else ["disabled"])

    def on_tick(*_):
        update_go()
        refresh_range()      # which dates have data depends on which channels are ticked

    def add_checkbox(c, checked):
        var = tk.BooleanVar(value=checked)
        var.trace_add("write", on_tick)
        vars_by_id[c["id"]] = var
        row = len(vars_by_id)  # row 0 is the RPM header
        ttk.Checkbutton(checks_frame, text=ch_lib.label(c), variable=var).grid(
            row=row, column=0, sticky="w", pady=2)
        rpm = ch_lib.get_rpm(c)
        rpm_var = tk.StringVar(value="" if rpm is None else f"{rpm:g}")
        rpm_vars[c["id"]] = rpm_var
        ttk.Entry(checks_frame, textvariable=rpm_var, width=9, justify="right").grid(
            row=row, column=1, padx=(18, 0), pady=2, sticky="w")
        ttk.Label(checks_frame, text="¢", foreground="#666").grid(row=row, column=2, sticky="w", padx=(3, 0))

    for c in chans:
        add_checkbox(c, c["id"] in last_ids)

    quick = ttk.Frame(frame)
    quick.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
    ttk.Button(quick, text="Select all",
               command=lambda: [v.set(True) for v in vars_by_id.values()]).grid(row=0, column=0)
    ttk.Button(quick, text="Select none",
               command=lambda: [v.set(False) for v in vars_by_id.values()]).grid(row=0, column=1, padx=(6, 0))


    # --- "views gained since" date picker (days with no history before them are greyed out) ---
    saved_since = ch_lib.get_since(data)
    since_on = tk.BooleanVar(value=saved_since is not None)
    since_frame = ttk.Frame(quick)
    since_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 0))
    picker = CalendarPicker(since_frame)
    picker.selected = saved_since

    first_day = {}   # channel id -> first date that has a record before it (None = no history yet)

    def first_valid_day(c):
        folder = ch_lib.ROOT / c["dir"]
        history.ensure_log(folder)
        entries = history.load(folder)
        # a date is usable only if some record exists at or before its start, so the day *after* the first record
        return entries[0]["at"].astimezone().date() + timedelta(days=1) if entries else None

    def refresh_range(*_):
        for c in chans:
            first_day.setdefault(c["id"], first_valid_day(c))
        ticked = [first_day[cid] for cid, v in vars_by_id.items() if v.get() and first_day.get(cid)]
        picker.set_range(min(ticked) if ticked else None, default=date.today() - timedelta(days=7))

    def toggle_since(*_):
        if since_on.get():
            picker.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        else:
            picker.grid_remove()

    ttk.Checkbutton(since_frame, text="Show views gained since a date", variable=since_on,
                    command=toggle_since).grid(row=0, column=0, sticky="w")
    refresh_range()
    toggle_since()

    ttk.Separator(frame).grid(row=4, column=0, columnspan=2, sticky="ew", pady=14)

    # --- add a new account ---
    ttk.Label(frame, text="➕  Add a new account",
              font=("Helvetica", 12, "bold")).grid(row=5, column=0, columnspan=2, sticky="w")
    add_frame = ttk.Frame(frame)
    add_frame.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
    platform_var = tk.StringVar(value="TikTok")
    ttk.Combobox(add_frame, textvariable=platform_var, values=["TikTok", "YouTube"],
                 state="readonly", width=9).grid(row=0, column=0)
    account_var = tk.StringVar()
    account_entry = ttk.Entry(add_frame, textvariable=account_var, width=28)
    account_entry.grid(row=0, column=1, padx=6)
    add_btn = ttk.Button(add_frame, text="Add")
    add_btn.grid(row=0, column=2)
    ttk.Label(frame, text="@handle or profile URL", foreground="#666").grid(
        row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

    status = ttk.Label(frame, text="", foreground="#666")
    status.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

    buttons = ttk.Frame(frame)
    buttons.grid(row=9, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=(0, 8))
    go_btn = ttk.Button(buttons, text="Check")
    go_btn.grid(row=0, column=1)
    update_go()

    def add_account(*_):
        if not account_var.get().strip():
            return
        status.config(text="Looking up account…")
        add_btn.state(["disabled"])
        root.update_idletasks()
        channel, err = resolve_new(platform_var.get().lower(), account_var.get())
        add_btn.state(["!disabled"])
        if channel is None:
            status.config(text="")
            messagebox.showerror("Couldn't add account", err, parent=root)
            return
        if channel["id"] in vars_by_id:
            vars_by_id[channel["id"]].set(True)
        else:
            add_checkbox(channel, True)
        account_var.set("")
        status.config(text=f"Added {ch_lib.label(channel)}")
        update_go()
        refresh_range()

    def read_rpms():
        """Parse every RPM box. Returns {id: value|None}, or None after showing an error."""
        parsed = {}
        for cid, var in rpm_vars.items():
            try:
                parsed[cid] = ch_lib.parse_rpm(var.get())
            except ValueError as e:
                name = ch_lib.label(next(c for c in chans if c["id"] == cid))
                msg = str(e) if "negative" in str(e) else "Enter a number like 12 or 8.5 (cents per 1,000 views), or leave it blank."
                messagebox.showerror("Invalid RPM", f"{name}: {msg}", parent=root)
                return None
        return parsed

    def submit(*_):
        # A half-typed account in the box counts as "add it too".
        if account_var.get().strip():
            add_account()
            if account_var.get().strip():  # lookup failed, stay open
                return
        picked = [c for c in chans if c["id"] in vars_by_id and vars_by_id[c["id"]].get()]
        if not picked:
            return
        chosen_since = None
        if since_on.get():
            chosen_since = picker.selected
            if chosen_since is None:
                messagebox.showerror("No date available",
                                     "None of the ticked channels have any recorded history yet, so there is "
                                     "nothing to compare against. Untick \"Show views gained since a date\", "
                                     "or tick a channel that has been checked before.", parent=root)
                return
        rpms = read_rpms()
        if rpms is None:
            return                      # invalid RPM: stay open so it can be fixed
        for c in chans:                 # saved by run.py right after (these are the registry's own dicts)
            if c["id"] in rpms:
                c["rpm_cents"] = rpms[c["id"]]
        data["since_date"] = chosen_since.isoformat() if chosen_since else None
        result["value"] = picked
        root.destroy()

    add_btn.config(command=add_account)
    go_btn.config(command=submit)
    account_entry.bind("<Return>", add_account)
    root.bind("<Escape>", lambda _e: root.destroy())

    # Come to the front even when launched from a Terminal .command
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.after(50, bring_to_front)    # once the window is mapped...
    root.after(500, bring_to_front)   # ...and again in case the first was too early
    root.mainloop()
    return result["value"]
