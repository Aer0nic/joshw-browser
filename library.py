"""
library.py - data shared by the browser and every player process.

  track_key(path)          stable id for a track ("<game folder>/<file>")
  parse_game_folder(name)  joshw folder/archive name -> title/date/dev/publisher/platform
  History                  play history + play counts (SQLite, safe across processes)
  get/set/clear_override   per-track length/fade overrides
  record_new_entries / new_entry_urls   "new since last index" markers
  find_box_art(...)        cover art from thumbnails.libretro.com (cached)

Everything lives in the app's cache directory next to settings.json.
"""
import difflib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from config import get_cache_directory

USER_AGENT = "joshw-browser"


def _cache_file(name):
    d = Path(get_cache_directory())
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _atomic_write_json(path, data):
    tmp = Path(str(path) + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Track identity & game folder names
# ---------------------------------------------------------------------------
def game_folder_name(path):
    """Name of the folder (or archive) a track belongs to."""
    parent = Path(str(path)).parent.name
    if parent.endswith("_extracted"):
        parent = parent[: -len("_extracted")]
    return parent


def track_key(path):
    """Stable, location-independent id for a track: "<game folder>/<file>".

    The same rip streamed twice lands in different temp folders, and local
    copies live anywhere, but the archive/folder name + file name stay put.
    Also accepts URLs.
    """
    s = str(path)
    if "://" in s:
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(s).path.split("/") if p]
        folder = parts[-2] if len(parts) > 1 else ""
        name = parts[-1] if parts else s
        folder = re.sub(r"\.(7z|zip|rar)$", "", folder, flags=re.I)
        return f"{folder}/{name}".lower()
    return f"{game_folder_name(s)}/{Path(s).name}".lower()


_PAREN = re.compile(r"\(([^()]*)\)")
_BRACKET = re.compile(r"\[([^\[\]]*)\]")
_DATEISH = re.compile(r"^(\d{4}|\d{2}XX|\d{3}X|XXXX)(-[\dX?]{2}){0,2}$", re.I)


def parse_game_folder(name):
    """Parse joshw's "Title (tags)(date)(developer)(publisher)[platform]" naming.

    Returns dict(title, date, year, developer, publisher, platform, tags);
    missing parts are "". Groups before the date (e.g. "(conv)", "(ut)") are
    returned as tags. Works for folder names and archive file names.
    """
    name = re.sub(r"\.(7z|zip|rar)$", "", str(name).strip(), flags=re.I)
    if name.endswith("_extracted"):
        name = name[: -len("_extracted")]
    first = len(name)
    m = re.search(r"[(\[]", name)
    if m:
        first = m.start()
    title = name[:first].strip(" -_")
    groups = [g.strip() for g in _PAREN.findall(name[first:])]
    brackets = [b.strip() for b in _BRACKET.findall(name[first:])]
    info = {"title": title or name, "date": "", "year": "", "developer": "", "publisher": "",
            "platform": brackets[-1] if brackets else "", "tags": []}
    date_idx = next((i for i, g in enumerate(groups) if _DATEISH.match(g)), None)
    if date_idx is not None:
        info["tags"] = groups[:date_idx]
        info["date"] = groups[date_idx]
        rest = groups[date_idx + 1:]
    else:
        rest = groups[-2:] if len(groups) >= 2 else groups
        info["tags"] = groups[: len(groups) - len(rest)]
    if rest:
        info["developer"] = "" if rest[0] == "-" else rest[0]
    if len(rest) > 1:
        info["publisher"] = "" if rest[1] == "-" else rest[1]
    year = info["date"][:4]
    info["year"] = year if year.isdigit() else ""
    return info


# ---------------------------------------------------------------------------
# Play history
# ---------------------------------------------------------------------------
class History:
    """Play history in SQLite (WAL mode: several player processes may write).

    Each start of a track is one row; seconds_played is updated while it plays.
    """

    def __init__(self, path=None):
        self.path = str(path or _cache_file("history.sqlite3"))
        self._local = threading.local()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL, path TEXT, url TEXT, title TEXT, game TEXT,
                domain TEXT, player TEXT, started REAL NOT NULL, seconds REAL DEFAULT 0)""")
            db.execute("CREATE INDEX IF NOT EXISTS plays_key ON plays(key)")
            db.execute("CREATE INDEX IF NOT EXISTS plays_started ON plays(started)")
            db.execute("""CREATE TABLE IF NOT EXISTS sources (
                key TEXT PRIMARY KEY, url TEXT, archive_url TEXT, name TEXT, domain TEXT)""")

    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = sqlite3.Row
            self._local.db = db
        return db

    # -- writing ---------------------------------------------------------------
    def remember_source(self, key, url=None, archive_url=None, name=None, domain=None):
        """Where a track came from, so it can be streamed again from History."""
        try:
            self._db().execute(
                "INSERT INTO sources(key,url,archive_url,name,domain) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET url=COALESCE(excluded.url,url), "
                "archive_url=COALESCE(excluded.archive_url,archive_url), "
                "name=COALESCE(excluded.name,name), domain=COALESCE(excluded.domain,domain)",
                (key, url, archive_url, name, domain))
        except sqlite3.Error:
            pass

    def start(self, path, title=None, player=None):
        key = track_key(path)
        game = parse_game_folder(game_folder_name(path))["title"]
        try:
            cur = self._db().execute(
                "INSERT INTO plays(key,path,title,game,player,started) VALUES(?,?,?,?,?,?)",
                (key, str(path), title or Path(str(path)).name, game, player, time.time()))
            return cur.lastrowid
        except sqlite3.Error:
            return None

    def progress(self, play_id, seconds):
        if play_id is None:
            return
        try:
            self._db().execute("UPDATE plays SET seconds=? WHERE id=?", (float(seconds), play_id))
        except sqlite3.Error:
            pass

    def clear(self):
        self._db().execute("DELETE FROM plays")

    # -- reading ---------------------------------------------------------------
    def _rows(self, sql, args=()):
        try:
            return [dict(r) for r in self._db().execute(sql, args)]
        except sqlite3.Error:
            return []

    def recent(self, limit=200):
        """Most recent play of each track, newest first."""
        return self._rows(
            "SELECT p.key, p.path, p.title, p.game, p.player, MAX(p.started) AS last_played, "
            "COUNT(*) AS plays, s.url, s.archive_url, s.domain FROM plays p "
            "LEFT JOIN sources s ON s.key=p.key GROUP BY p.key ORDER BY last_played DESC LIMIT ?",
            (limit,))

    def most_played(self, limit=200):
        return self._rows(
            "SELECT p.key, p.path, p.title, p.game, p.player, MAX(p.started) AS last_played, "
            "COUNT(*) AS plays, SUM(p.seconds) AS seconds, s.url, s.archive_url, s.domain "
            "FROM plays p LEFT JOIN sources s ON s.key=p.key GROUP BY p.key "
            "ORDER BY plays DESC, last_played DESC LIMIT ?", (limit,))

    def play_counts(self):
        return {r["key"]: r["n"] for r in self._rows("SELECT key, COUNT(*) AS n FROM plays GROUP BY key")}

    def recently_played_keys(self, within_days=7):
        since = time.time() - within_days * 86400
        return {r["key"] for r in self._rows("SELECT DISTINCT key FROM plays WHERE started>=?", (since,))}


_history = None


def history():
    global _history
    if _history is None:
        _history = History()
    return _history


# ---------------------------------------------------------------------------
# Per-track length / fade overrides
# ---------------------------------------------------------------------------
_OVERRIDES = "track_overrides.json"


def get_override(path):
    """(length_seconds, fade_seconds) set by the user for this track, or None."""
    key = track_key(path)
    data = _read_json(_cache_file(_OVERRIDES), {})
    o = data.get(key)
    if not o:
        return None
    try:
        return float(o["length"]), float(o.get("fade", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None


def set_override(path, length_seconds, fade_seconds=0.0):
    p = _cache_file(_OVERRIDES)
    data = _read_json(p, {})
    data[track_key(path)] = {"length": float(length_seconds), "fade": float(fade_seconds or 0)}
    _atomic_write_json(p, data)


def clear_override(path):
    p = _cache_file(_OVERRIDES)
    data = _read_json(p, {})
    if data.pop(track_key(path), None) is not None:
        _atomic_write_json(p, data)


def parse_time_text(text):
    """'3:45', '1:02:03', '225', '3:45.5' -> seconds (float) or None."""
    text = str(text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        return total if total >= 0 else None
    except ValueError:
        return None


def format_time_text(seconds):
    seconds = max(0.0, float(seconds or 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    frac = f"{s:05.2f}".rstrip("0").rstrip(".") if s % 1 else f"{int(s):02d}"
    if len(frac.split(".")[0]) < 2:
        frac = "0" + frac
    return f"{h}:{m:02d}:{frac}" if h else f"{m}:{frac}"


# ---------------------------------------------------------------------------
# "New since last index"
# ---------------------------------------------------------------------------
_NEW = "new_entries.json"


def record_new_entries(domain_name, entries):
    """Remember what the last index run added for a domain (replaces older)."""
    p = _cache_file(_NEW)
    data = _read_json(p, {})
    urls = [e["url"] if isinstance(e, dict) else str(e) for e in entries or []]
    if urls:
        data[domain_name] = {"when": time.time(), "urls": urls}
        _atomic_write_json(p, data)


def new_entry_urls(domain_name=None):
    """URLs added by the most recent index run (of one domain, or all)."""
    data = _read_json(_cache_file(_NEW), {})
    if domain_name is not None:
        return set(data.get(domain_name, {}).get("urls", []))
    out = set()
    for v in data.values():
        out.update(v.get("urls", []))
    return out


def new_folder_urls(urls):
    """Every parent folder of the given file URLs (so folders can be marked too)."""
    folders = set()
    for u in urls:
        parsed = urllib.parse.urlparse(u)
        parts = parsed.path.rstrip("/").split("/")[:-1]
        while len(parts) > 1:
            folders.add(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/".join(parts) + "/", "", "", "")))
            parts = parts[:-1]
    return folders


# ---------------------------------------------------------------------------
# Box art (thumbnails.libretro.com)
# ---------------------------------------------------------------------------
LIBRETRO = "https://thumbnails.libretro.com/"

# joshw sub-domain (or the app's domain name) -> libretro system folder(s)
SYSTEMS = {
    "gsf": ["Nintendo - Game Boy Advance"],
    "gbs": ["Nintendo - Game Boy Color", "Nintendo - Game Boy"],
    "nsf": ["Nintendo - Nintendo Entertainment System", "Nintendo - Family Computer Disk System"],
    "spc": ["Nintendo - Super Nintendo Entertainment System"],
    "usf": ["Nintendo - Nintendo 64"],
    "2sf": ["Nintendo - Nintendo DS"],
    "3sf": ["Nintendo - Nintendo 3DS"],
    "gcn": ["Nintendo - GameCube"],
    "wii": ["Nintendo - Wii"],
    "wiiu": ["Nintendo - Wii U"],
    "psf": ["Sony - PlayStation"],
    "psf2": ["Sony - PlayStation 2"],
    "psf3": ["Sony - PlayStation 3"],
    "psf4": ["Sony - PlayStation 4"],
    "psp": ["Sony - PlayStation Portable"],
    "vita": ["Sony - PlayStation Vita"],
    "ssf": ["Sega - Saturn"],
    "dsf": ["Sega - Dreamcast"],
    "smd": ["Sega - Mega Drive - Genesis", "Sega - Mega-CD - Sega CD"],
    "ncd": ["SNK - Neo Geo CD", "SNK - Neo Geo"],
    "hes": ["NEC - PC Engine - TurboGrafx 16", "NEC - PC Engine CD - TurboGrafx-CD"],
    "wsr": ["Bandai - WonderSwan Color", "Bandai - WonderSwan"],
    "xbox": ["Microsoft - Xbox"],
    "x360": ["Microsoft - Xbox 360"],
    "3do": ["The 3DO Company - 3DO"],
    "kss": ["Microsoft - MSX2", "Microsoft - MSX"],
    "cdi": ["Philips - CD-i"],
}

# platform tags in joshw names ([GBA], [PS2], ...) -> sub-domain key above
PLATFORM_TAGS = {"gba": "gsf", "gb": "gbs", "gbc": "gbs", "nes": "nsf", "fds": "nsf", "snes": "spc",
                 "n64": "usf", "nds": "2sf", "ds": "2sf", "3ds": "3sf", "gc": "gcn", "ngc": "gcn",
                 "wii": "wii", "wii u": "wiiu", "ps1": "psf", "psx": "psf", "ps2": "psf2", "ps3": "psf3",
                 "ps4": "psf4", "psp": "psp", "vita": "vita", "ss": "ssf", "sat": "ssf", "dc": "dsf",
                 "md": "smd", "scd": "smd", "xbox": "xbox", "x360": "x360"}


def system_key_for(url_or_path=None, platform_tag=""):
    tag = (platform_tag or "").strip().lower()
    if tag in PLATFORM_TAGS:
        return PLATFORM_TAGS[tag]
    s = str(url_or_path or "")
    if "://" in s:
        host = (urllib.parse.urlparse(s).hostname or "").split(".")[0]
        if host in SYSTEMS:
            return host
    return None


def _norm(title):
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title.lower())
    t = t.replace("&", " and ").replace("'", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"^the |, the\b", " ", t)
    return " ".join(t.split())


_listing_lock = threading.Lock()


def _system_listing(system):
    """Box art file names for a libretro system (cached for 30 days)."""
    cache = _cache_file("art") / "listings"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (re.sub(r"[^A-Za-z0-9]+", "_", system) + ".json")
    with _listing_lock:
        if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
            return _read_json(f, [])
        url = LIBRETRO + urllib.parse.quote(system) + "/Named_Boxarts/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:
            return _read_json(f, [])
        names = sorted({urllib.parse.unquote(h)[:-4] for h in re.findall(r'href="([^"]+\.png)"', html)})
        _atomic_write_json(f, names)
        return names


def _region_rank(name):
    n = name.lower()
    for i, r in enumerate(("(usa", "(world", "(usa, europe", "(europe", "(japan")):
        if r in n:
            return i
    return 9


def find_box_art(game_title, system_key, download=True):
    """Local path of the cover for a game (downloads once), or None.

    Matches the game title against libretro's names (ignoring region and
    punctuation), preferring USA/World/Europe/Japan releases in that order.
    """
    systems = SYSTEMS.get(system_key or "", [])
    if not game_title or not systems:
        return None
    want = _norm(game_title)
    if not want:
        return None
    art_dir = _cache_file("art")
    miss_file = art_dir / "misses.json"
    misses = _read_json(miss_file, {})
    miss_key = f"{system_key}|{want}"
    if miss_key in misses and time.time() - misses[miss_key] < 7 * 86400:
        return None
    for system in systems:
        names = _system_listing(system)
        if not names:
            continue
        exact = [n for n in names if _norm(n) == want]
        if not exact:
            normed = {n: _norm(n) for n in names}
            close = difflib.get_close_matches(want, list(set(normed.values())), n=1, cutoff=0.88)
            exact = [n for n, v in normed.items() if close and v == close[0]]
        if not exact:
            continue
        best = sorted(exact, key=lambda n: (_region_rank(n), n))[0]
        local = art_dir / re.sub(r"[^A-Za-z0-9]+", "_", system) / (re.sub(r'[<>:"/\\|?*]', "_", best) + ".png")
        if local.exists():
            return str(local)
        if not download:
            return None
        url = LIBRETRO + urllib.parse.quote(system) + "/Named_Boxarts/" + urllib.parse.quote(best) + ".png"
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            tmp = Path(str(local) + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, local)
            return str(local)
        except Exception:
            return None
    misses[miss_key] = time.time()
    try:
        _atomic_write_json(miss_file, misses)
    except Exception:
        pass
    return None
