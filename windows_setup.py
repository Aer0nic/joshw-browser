"""
Windows dependency setup.

Almost everything is bundled in assets/libraries/windows/. This module fetches
the few things that are not, with one confirmation and a progress window -
no admin rights, no terminal:

  * mpv            - too large for the git repo (>100 MB); downloaded from the
                     shinchiro Windows builds into Documents\\joshw\\mpv\\
                     (PyInstaller builds already contain it)
  * FluidR3_GM.sf2 - General MIDI soundfont for NeoBAE (~140 MB)
  * C64 ROMs       - kernal/basic for sidplayfp, into %APPDATA%\\sidplayfp
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
import urllib.request
from pathlib import Path

import ttkbootstrap as tb

from config import USER_AGENT, get_cache_directory
from platform_support import find_tool, hidden_popen_kwargs

MPV_RELEASES_API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
SOUNDFONT_URL = "https://github.com/pianobooster/fluid-soundfont/releases/download/v3.1/FluidR3_GM.sf2"


def user_tools_dir():
    return Path(get_cache_directory())


def soundfont_path():
    return user_tools_dir() / "FluidR3_GM.sf2"


def missing_items():
    """Human-readable list of what setup would download."""
    items = []
    if not find_tool("mpv"):
        items.append("mpv (MP3/OGG/FLAC/module playback, ~30 MB)")
    if not soundfont_path().exists():
        items.append("FluidR3 General MIDI soundfont (~140 MB)")
    return items


def _download(url, target, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = Path(str(target) + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    os.replace(tmp, target)


def install_mpv(progress=None):
    if find_tool("mpv"):
        return
    req = urllib.request.Request(MPV_RELEASES_API, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        release = json.load(resp)
    asset = next(a for a in release["assets"]
                 if a["name"].startswith("mpv-x86_64-") and a["name"].endswith(".7z")
                 and "-v3-" not in a["name"])
    seven_zip = find_tool("7z")
    if not seven_zip:
        raise RuntimeError("bundled 7-Zip (7za.exe) not found")
    with tempfile.TemporaryDirectory(prefix="joshw_mpv_") as tmp:
        archive = Path(tmp) / asset["name"]
        _download(asset["browser_download_url"], archive, progress)
        extract_dir = Path(tmp) / "x"
        subprocess.run([seven_zip, "x", "-y", f"-o{extract_dir}", str(archive)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden_popen_kwargs())
        mpv_exe = next(extract_dir.rglob("mpv.exe"))
        dest = user_tools_dir() / "mpv"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mpv_exe, dest / "mpv.exe")


def install_soundfont(progress=None):
    if not soundfont_path().exists():
        _download(SOUNDFONT_URL, soundfont_path(), progress)


def install_sid_roms():
    from downloaders import check_and_download_sid_firmware
    check_and_download_sid_firmware()


class WindowsSetupWindow(tb.Toplevel):
    """Progress window that runs the downloads in a background thread."""

    def __init__(self, parent, steps=None, on_complete=None):
        super().__init__(parent)
        self.title("Setting up playback components")
        self.geometry("460x170")
        self.resizable(False, False)
        self.transient(parent)
        self.on_complete = on_complete
        self.steps = steps or [
            ("Downloading mpv", install_mpv),
            ("Downloading MIDI soundfont", install_soundfont),
            ("Downloading C64 ROMs for SID playback", lambda progress=None: install_sid_roms()),
        ]
        self.errors = []

        frame = tb.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        self.step_var = tk.StringVar(value="Starting...")
        self.detail_var = tk.StringVar(value="")
        tb.Label(frame, textvariable=self.step_var, font=("Arial", 10, "bold")).pack(anchor="w")
        self.bar = tb.Progressbar(frame, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=8)
        tb.Label(frame, textvariable=self.detail_var).pack(anchor="w")

        self.protocol("WM_DELETE_WINDOW", lambda: None)  # finish or fail, don't half-close
        threading.Thread(target=self._worker, daemon=True).start()

    def _ui(self, fn, *args):
        try:
            self.after(0, fn, *args)
        except Exception:
            pass

    def _set_progress(self, done, total):
        if total:
            pct = done * 100.0 / total
            self._ui(self.bar.configure, {"value": pct})
            self._ui(self.detail_var.set, f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            self._ui(self.detail_var.set, f"{done / 1e6:.1f} MB")

    def _worker(self):
        for label, step in self.steps:
            self._ui(self.step_var.set, label + "...")
            self._ui(self.bar.configure, {"value": 0})
            self._ui(self.detail_var.set, "")
            try:
                step(progress=self._set_progress)
            except Exception as e:
                self.errors.append(f"{label}: {e}")
        self._ui(self._finish)

    def _finish(self):
        from tkinter import messagebox
        if self.errors:
            messagebox.showwarning("Setup finished with problems",
                                   "Some components could not be downloaded:\n\n" + "\n".join(self.errors)
                                   + "\n\nYou can retry from Options > Install Dependencies.", parent=self)
        self.destroy()
        if self.on_complete:
            self.on_complete()


def ensure_mpv_interactive(parent):
    """Before playing an mpv format: offer to download mpv if it is missing.
    Returns True if mpv is available now (download runs async -> False)."""
    if find_tool("mpv"):
        return True
    from tkinter import messagebox
    if messagebox.askyesno("mpv needed",
                           "Playing this file type needs mpv, which isn't downloaded yet (~30 MB).\n\n"
                           "Download it now?", parent=parent):
        WindowsSetupWindow(parent, steps=[("Downloading mpv", install_mpv)])
    return False
