import os
import platform
import re

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
DATE_PATTERN = re.compile(r'[\(\[](\d{4}(?:-\d{2}(?:-\d{2})?)?)[\)\]]')

def is_emulated_file(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    return ext in {".spc", ".gbs", ".hes", ".kss", ".vgm", ".vgz", ".gym", ".ay", ".sap", ".nsf", ".nsfe", ".usf", ".miniusf", ".sid"}

def get_cache_directory():
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
    cache_dir = get_cache_directory()
    temp_dir = os.path.join(cache_dir, "temp_extracted")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir

def get_cache_path(domain_name):
    safe_name = "".join(c if c.isalnum() else "_" for c in domain_name)
    cache_dir = get_cache_directory()
    return os.path.join(cache_dir, f"domain_cache_{safe_name}.json")

def format_size(size):
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
