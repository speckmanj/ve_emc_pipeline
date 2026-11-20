"""Dark theme for the emcscan app: ttk widgets, classic tk widgets (log, lists, simple
dialogs, the matplotlib toolbar), matplotlib figures, and dark Windows title bars.

Colors live here only; app.py uses DIM / OK / BAD for its hint and status text.
Native Windows message boxes and file dialogs are drawn by Windows and stay as they are.
Printed output (marker sheet, reports) must not use these colors: reports run in a
subprocess, and app.py wraps the marker sheet in matplotlib.style.context("default").
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

BG = "#1e1f22"          # window and panel background
SURFACE = "#2b2d31"     # buttons, tabs, headings
HOVER = "#363a40"
PRESSED = "#41464e"
FIELD = "#16171a"       # entries, lists, tables, log
BORDER = "#3c4048"
TEXT = "#e3e5e8"
DIM = "#9aa0a8"         # hints and notes
DISABLED = "#62666e"
ACCENT = "#3d7bd9"      # focus, selection, progress, main action buttons
ACCENT_HOVER = "#4f8ae6"
SELECT = "#2c4a7c"
OK = "#6cc070"
BAD = "#ef6461"
FONT = ("Segoe UI", 9)


def apply(root: tk.Tk) -> ttk.Style:
    st = ttk.Style(root)
    st.theme_use("clam")                    # the only built-in theme whose colors can all be set
    st.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=BORDER,
                 lightcolor=BG, darkcolor=BG, troughcolor=FIELD, focuscolor=ACCENT,
                 selectbackground=SELECT, selectforeground=TEXT, insertcolor=TEXT,
                 arrowcolor=TEXT, font=FONT)
    st.map(".", foreground=[("disabled", DISABLED)])

    st.configure("TButton", background=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                 bordercolor=BORDER, padding=(8, 3), focusthickness=1)
    st.map("TButton",
           background=[("disabled", BG), ("pressed", PRESSED), ("active", HOVER)],
           lightcolor=[("pressed", PRESSED), ("active", HOVER)],
           darkcolor=[("pressed", PRESSED), ("active", HOVER)],
           bordercolor=[("focus", ACCENT)])
    st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=6, background=ACCENT,
                 lightcolor=ACCENT, darkcolor=ACCENT, foreground="#ffffff")
    st.map("Big.TButton",
           background=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
           lightcolor=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
           darkcolor=[("disabled", SURFACE), ("pressed", SELECT), ("active", ACCENT_HOVER)],
           foreground=[("disabled", DISABLED)])
    st.configure("Head.TLabel", font=("Segoe UI", 10, "bold"))

    for w in ("TEntry", "TSpinbox", "TCombobox"):
        st.configure(w, fieldbackground=FIELD, background=SURFACE, insertcolor=TEXT,
                     lightcolor=FIELD, darkcolor=FIELD, bordercolor=BORDER, arrowcolor=TEXT)
        st.map(w, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
               fieldbackground=[("readonly", FIELD), ("disabled", BG)],
               background=[("active", HOVER)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=BG, indicatorbackground=FIELD, indicatorforeground=TEXT,
                     upperbordercolor=BORDER, lowerbordercolor=BORDER)
        st.map(w, background=[("active", BG)],
               indicatorbackground=[("pressed", HOVER), ("selected", ACCENT)],
               indicatorforeground=[("selected", "#ffffff")])

    st.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(2, 4, 2, 0))
    st.configure("TNotebook.Tab", background=SURFACE, lightcolor=SURFACE, bordercolor=BORDER,
                 padding=(10, 4))
    st.map("TNotebook.Tab",
           background=[("selected", BG), ("active", HOVER)],
           lightcolor=[("selected", ACCENT)],
           expand=[("selected", (1, 1, 1, 0))])

    st.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG)
    st.configure("TLabelframe.Label", background=BG, foreground=TEXT)

    st.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                 bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD)
    st.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    st.configure("Treeview.Heading", background=SURFACE, foreground=TEXT, bordercolor=BORDER,
                 lightcolor=SURFACE, darkcolor=SURFACE, relief="flat")
    st.map("Treeview.Heading", background=[("active", HOVER)])

    st.configure("TScrollbar", background=SURFACE, troughcolor=BG, bordercolor=BG,
                 lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=DIM, gripcount=0)
    st.map("TScrollbar", background=[("active", HOVER), ("disabled", BG)])
    st.configure("TProgressbar", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER,
                 lightcolor=ACCENT, darkcolor=ACCENT)
    st.configure("TScale", background=SURFACE, troughcolor=FIELD)
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", sashthickness=6, gripcount=0, background=BORDER, lightcolor=BORDER,
                 bordercolor=BORDER)
    st.configure("TSeparator", background=BORDER)

    # classic tk widgets: log, lists, simple dialogs, the matplotlib toolbar
    for k, v in {"background": BG, "foreground": TEXT, "activeBackground": HOVER,
                 "activeForeground": TEXT, "selectBackground": SELECT, "selectForeground": TEXT,
                 "insertBackground": TEXT, "highlightBackground": BG, "highlightColor": ACCENT,
                 "disabledForeground": DISABLED, "troughColor": FIELD, "selectColor": FIELD,
                 "Entry.background": FIELD, "Listbox.background": FIELD,
                 "Text.background": FIELD, "Spinbox.background": FIELD}.items():
        root.option_add(f"*{k}", v)
    root.configure(background=BG)

    _matplotlib()
    if sys.platform == "win32":
        root.bind_class("Tk", "<Map>", lambda e: dark_titlebar(e.widget), add="+")
        root.bind_class("Toplevel", "<Map>", lambda e: dark_titlebar(e.widget), add="+")
    return st


def _matplotlib():
    import matplotlib
    matplotlib.rcParams.update({
        "figure.facecolor": BG, "figure.edgecolor": BG, "savefig.facecolor": BG,
        "axes.facecolor": FIELD, "axes.edgecolor": BORDER, "axes.labelcolor": TEXT,
        "axes.titlecolor": TEXT, "text.color": TEXT, "xtick.color": DIM, "ytick.color": DIM,
        "xtick.labelcolor": TEXT, "ytick.labelcolor": TEXT, "grid.color": BORDER,
        "legend.facecolor": SURFACE, "legend.edgecolor": BORDER, "legend.labelcolor": TEXT,
    })


_done: set = set()


def dark_titlebar(win):
    """Ask Windows 10/11 for a dark title bar on this window (no effect elsewhere)."""
    if sys.platform != "win32" or not isinstance(win, (tk.Tk, tk.Toplevel)):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd or hwnd in _done:
            return
        on = ctypes.c_int(1)
        for attr in (20, 19):           # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        _done.add(hwnd)
        win.attributes("-alpha", 0.999)     # nudge Windows to repaint the frame now
        win.after(10, lambda: win.attributes("-alpha", 1.0))
    except Exception:                                           # noqa: BLE001
        pass
