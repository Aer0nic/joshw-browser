"""Configuration and path helpers for JoshW Archive Browser."""

import os
import platform
import re
import shutil
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    # PyInstaller's bundled-resource directory.
    app_dir = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
    )
else:
    # Directory containing config.py while developing.
    app_dir = Path(__file__).resolve().parent


DOMAINS = {
    "PC music archives": "http://pc.joshw.info/",
    "FM Towns music archive": "http://fmtowns.joshw.info/",
    "Hoot music archives": "http://hoot.joshw.info/",
    "S98 music archives": "http://s98.joshw.info/",
    "MSX music archives": "http://kss.joshw.info/",
    "NES music archives": "http://nsf.joshw.info/",
    "SNES music archives": "http://spc.joshw.info/",
    "c64 music archives": "https://hvsc.brona.dk/HVSC/C64Music/",
    "Nintendo 64": "http://usf.joshw.info/",
    "GameCube": "http://gcn.joshw.info/",
    "Nintendo Wii": "http://wii.joshw.info/",
    "Nintendo Wii U": "http://wiiu.joshw.info/",
    "Sega CD / Mega Drive": "http://smd.joshw.info/",
    "Saturn": "http://ssf.joshw.info/",
    "Dreamcast": "http://dsf.joshw.info/",
    "PC Engine": "http://hes.joshw.info/",
    "Neo Geo": "http://ncd.joshw.info/",
    "PlayStation": "http://psf.joshw.info/",
    "PlayStation 2": "http://psf2.joshw.info/",
    "PlayStation 3": "http://psf3.joshw.info/",
    "Playstation 4": "http://psf4.joshw.info/",
    "Playstation 5": "http://psf5.joshw.info/",
    "Xbox": "http://xbox.joshw.info/",
    "Xbox 360": "http://x360.joshw.info/",
    "3DO": "http://3do.joshw.info/",
    "Game Boy": "http://gbs.joshw.info/",
    "Game Boy Advance": "http://gsf.joshw.info/",
    "Nintendo DS": "http://2sf.joshw.info/",
    "Nintendo 3DS": "http://3sf.joshw.info/",
    "WonderSwan": "http://wsr.joshw.info/",
    "PlayStation Portable": "http://psp.joshw.info/",
    "PlayStation Vita": "http://vita.joshw.info/",
    "Other Assorted Systems": "https://cdi.joshw.info/",
}

USER_AGENT = "JoshW-Archive-Browser/1.0"
DATE_PATTERN = re.compile(r"[\(\[](\d{4}(?:-\d{2}(?:-\d{2})?)?)[\)\]]")


def get_ffmpeg_path():
    """Find the bundled, development, or system FFmpeg executable."""
    bundled_path = Path(resource_path("ffmpeg"))
    if bundled_path.exists():
        return str(bundled_path)

    dev_ffmpeg = Path(__file__).resolve().parent / "ffmpeg"
    if dev_ffmpeg.exists():
        return str(dev_ffmpeg)

    return shutil.which("ffmpeg") or "ffmpeg"


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


def resource_path(relative_path):
    """Return an absolute path to a bundled or project resource."""
    return str(app_dir / relative_path)


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
        ".ay",
        ".gbs",
        ".gym",
        ".hes",
        ".kss",
        ".nsf",
        ".nsfe",
        ".sap",
        ".spc",
        ".vgm",
        ".vgz",
        ".usf",
        ".miniusf",
        ".gsf",
        ".minigsf",
        ".sid",
        ".2sf",
        ".mini2sf",
        ".psf",
        ".minipsf",
        ".psf2",
        ".minipsf2",
    }
    return extension in emulated_extensions
