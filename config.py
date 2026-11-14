"""Configuration and path helpers for JoshW Archive Browser."""

import json
import os
import platform
import re
import shutil
import stat
import sys
import tkinter.font as tkfont
import ttkbootstrap as tb
from pathlib import Path


def get_project_root() -> Path:
    """
    Returns absolute Path to the project root.
    Handles both normal execution and PyInstaller --onefile runtime (_MEIPASS).
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


app_dir = get_project_root()


DOMAINS = {
    "  Please Choose a Domain! ": "",
    
    "----------- 3DO COMPANY -----------": "",
    "3DO": "http://3do.joshw.info/",

    "----------- BANDAI -----------": "",
    "WonderSwan": "http://wsr.joshw.info/",

    "----------- COMMODORE -----------": "",
    "c64": "https://hvsc.brona.dk/HVSC/C64Music/",

    "----------- FUJITSU -----------": "",
    "FM Towns": "http://fmtowns.joshw.info/",

    "----------- MICROSOFT -----------": "",
    "Xbox": "http://xbox.joshw.info/",
    "Xbox 360": "http://x360.joshw.info/",

    "----------- NEC -----------": "",
    "PC Engine": "http://hes.joshw.info/",

    "----------- NINTENDO -----------": "",
    "3DS": "http://3sf.joshw.info/",
    "DS": "http://2sf.joshw.info/",
    "Game Boy Advance": "http://gsf.joshw.info/",
    "Game Boy": "http://gbs.joshw.info/",
    "GameCube": "http://gcn.joshw.info/",
    "N64": "http://usf.joshw.info/",
    "NES": "http://nsf.joshw.info/",
    "SNES": "http://spc.joshw.info/",
    "Switch": "https://switch.joshw.info/",
    "Wii": "http://wii.joshw.info/",
    "Wii U": "http://wiiu.joshw.info/",

    "----------- SEGA -----------": "",
    "Dreamcast": "http://dsf.joshw.info/",
    "Saturn": "http://ssf.joshw.info/",
    "Sega CD / Mega Drive": "http://smd.joshw.info/",

    "----------- SNK -----------": "",
    "Neo Geo": "http://ncd.joshw.info/",

    "----------- SONY -----------": "",
    "PlayStation": "http://psf.joshw.info/",
    "PlayStation 2": "http://psf2.joshw.info/",
    "PlayStation 3": "http://psf3.joshw.info/",
    "Playstation 4": "http://psf4.joshw.info/",
    "Playstation 5": "http://psf5.joshw.info/",
    "PlayStation Portable": "http://psp.joshw.info/",
    "PlayStation Vita": "http://vita.joshw.info/",

    "----------- OTHER & ASSORTED -----------": "",
    "Arcade": "https://vgmrips.net/packs/system/",
    "Hoot": "http://hoot.joshw.info/",
    "MSX": "http://kss.joshw.info/",
    "Other Assorted Systems": "https://cdi.joshw.info/",
    "PC": "http://pc.joshw.info/",
    "S98": "http://s98.joshw.info/",
    "VGMusic Siterip": "https://www.vgmusic.com/music/",
}

USER_AGENT = "JoshW-Archive-Browser/1.0"
DATE_PATTERN = re.compile(r"[\(\[](\d{4}(?:-\d{2}(?:-\d{2})?)?)[\)\]]")


def resource_path(relative_path: str) -> Path:
    """
    Return an absolute Path object to a bundled or project resource.
    Always returns a Path object so .exists() works natively.
    """
    return app_dir / relative_path


def get_binary_path(binary_name: str) -> Path:
    """
    Finds helper binary across PyInstaller bundle, repo root, ~/.config, or PATH.
    Ensures execution permissions (+x) are granted if extracted inside PyInstaller.
    """
    bundled_bin = resource_path(f"bin/{binary_name}")
    if not bundled_bin.exists():
        bundled_bin = resource_path(binary_name)

    if bundled_bin.exists():
        try:
            current_mode = bundled_bin.stat().st_mode
            bundled_bin.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass
        return bundled_bin

    config_bin = Path.home() / ".config" / "joshw" / binary_name
    if config_bin.exists():
        return config_bin

    system_bin = shutil.which(binary_name)
    if system_bin:
        return Path(system_bin)

    return Path(binary_name)


def get_ffmpeg_path():
    """Find the bundled, development, or system FFmpeg executable."""
    ffmpeg_bin = get_binary_path("ffmpeg")
    if ffmpeg_bin.exists():
        return str(ffmpeg_bin)
    return "ffmpeg"


def get_cache_directory():
    """Create and return the application's cache directory."""
    system_name = platform.system()
    if system_name == "Linux":
        base_dir = os.path.expanduser("~/.config")
    elif system_name == "Windows":
        base_dir = os.path.expanduser("~/Documents")
    else:
        base_dir = os.path.expanduser("~/.config")

    cache_dir = os.path.join(base_dir, "joshw")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_temp_directory():
    """Create and return the directory used for extracted temporary files."""
    cache_dir = get_cache_directory()
    temp_dir = os.path.join(cache_dir, "temp_extracted")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def get_cache_path(domain_name):
    """Return the cache-file path for a domain name inside the indexes folder."""
    safe_name = "".join(
        character if character.isalnum() else "_"
        for character in domain_name
    )
    indexes_dir = Path(get_cache_directory()) / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    return str(indexes_dir / f"domain_cache_{safe_name}.json")


def format_size(size):
    """Format a byte count as a readable size, or 'Unknown' if absent."""
    if size is None:
        return "Unknown"

    value = float(size)
    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value):,} {unit}"
            return f"{value:,.1f} {unit}"
        value /= 1024

    return "Unknown"


def is_emulated_file(file_path):
    """Return whether the file extension is for an emulated audio file."""
    extension = os.path.splitext(file_path)[1].lower()
    emulated_extensions = {
        ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe",
        ".sap", ".spc", ".vgm", ".vgz", ".usf", ".miniusf",
        ".gsf", ".minigsf", ".sid", ".2sf", ".mini2sf",
        ".psf", ".minipsf", ".psf2", ".minipsf2",
    }
    return extension in emulated_extensions


def load_saved_font_size(default_size: int = 10) -> int:
    """Safely loads the font_size key from settings.json."""
    settings_path = os.path.join(get_cache_directory(), "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return int(data.get("font_size", default_size))
        except Exception:
            pass
    return default_size


def apply_global_font_scale(font_size: int = None):
    """
    Globally configures Tkinter's default named fonts and ttkbootstrap's root style.
    If no font_size is provided, it reads the saved value from settings.json.
    """
    if font_size is None:
        font_size = load_saved_font_size()

    # 1. Update standard Tkinter named fonts
    for font_name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont"):
        try:
            f = tkfont.nametofont(font_name)
            f.configure(size=font_size)
        except Exception:
            pass

    # 2. Update ttkbootstrap root style defaults
    try:
        style = tb.Style()
        style.configure(".", font=("Arial", font_size))
    except Exception:
        pass