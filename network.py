import os
import posixpath
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from config import USER_AGENT, DATE_PATTERN


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if href:
            self.links.append(href)


def safe_filename(name):
    invalid_characters = r'<>:"/\|?*'
    cleaned = "".join("_" if character in invalid_characters else character for character in name)
    return cleaned.strip().rstrip(".") or "unnamed"


def remote_filename(url, default="file"):
    """Local file name for a remote URL.

    Normally the last path segment. Index entries whose URL is a download
    script (e.g. the Mod Archive's downloads.php?moduleid=N) carry the real
    file name in the fragment instead: ...downloads.php?moduleid=N#song.xm.
    The fragment is never sent to the server.
    """
    parsed = urllib.parse.urlparse(url)
    name = ""
    if parsed.fragment:
        name = posixpath.basename(urllib.parse.unquote(parsed.fragment))
    if not name:
        name = urllib.parse.unquote(posixpath.basename(parsed.path))
    return safe_filename(name) if name else default


def remote_folder_name(url):
    """Name of the remote folder holding url ("" for a file at the site root)."""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    return safe_filename(urllib.parse.unquote(posixpath.basename(posixpath.dirname(path)))) \
        if posixpath.basename(posixpath.dirname(path)) else ""


def parse_filename_and_date(display_name):
    matches = list(DATE_PATTERN.finditer(display_name))
    if not matches:
        return display_name, ""

    target_match = None
    for m in matches:
        if m.start() == 0 and display_name.startswith(m.group(1)):
            continue
        target_match = m
        break

    if not target_match and matches:
        target_match = matches[-1]

    if target_match:
        date_str = target_match.group(1)
        clean_name = display_name[:target_match.start()].strip()
        clean_name = re.sub(r'[\s\-]+$', '', clean_name)
        return clean_name or display_name, date_str

    return display_name, ""


def get_remote_file_size(url):
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                return int(content_length)
    except Exception:
        pass
    return None


def get_size_from_domain_entries(dir_url, domain_entries):
    dir_url_norm = dir_url.rstrip("/") + "/"
    total = 0
    for e in domain_entries:
        u = e["url"]
        if not e["directory"] and u.startswith(dir_url_norm):
            sz = e.get("size")
            if sz is not None:
                total += sz
    return total


def get_entries_from_domain_entries(dir_url, domain_entries):
    parsed_dir = urllib.parse.urlparse(dir_url)
    dir_path_norm = parsed_dir.path.rstrip("/")
    target_base = dir_url.rstrip("/") + "/"
    is_root = (parsed_dir.path.rstrip("/") == "")
    
    entries = []
    seen = set()
    is_arcade = "vgmrips.net" in parsed_dir.netloc
    
    for e in domain_entries:
        u = e.get("url", "")
        parsed_u = urllib.parse.urlparse(u)
        u_path = parsed_u.path.rstrip("/")
        
        if is_arcade:
            # Keep existing arcade logic...
            pass
        else:
            if u.rstrip("/") == dir_url.rstrip("/"):
                continue
            
            if u.startswith(target_base):
                rel_path = u[len(target_base):].strip("/")
                if not rel_path:
                    continue
                
                parts = rel_path.split("/")
                if len(parts) == 1:
                    if u not in seen:
                        seen.add(u)
                        entries.append(dict(e))
                else:
                    # Dynamically create/aggregate intermediate subdirectories
                    sub_dir_name = parts[0]
                    sub_dir_url = target_base + sub_dir_name + "/"
                    if sub_dir_url not in seen:
                        seen.add(sub_dir_url)
                        entries.append({
                            "name": sub_dir_name,
                            "raw_name": sub_dir_name,
                            "date": "",
                            "url": sub_dir_url,
                            "directory": True,
                            "size": None
                        })
                        
    entries.sort(key=lambda entry: (not entry["directory"], entry["name"].lower()))
    return entries


# Apache autoindex rows, plain or table style:
#   <a href="X">X</a>   2021-09-16 09:18   162M
#   <td><a href="X">X</a></td><td align="right">2003-05-10 18:09  </td>
_LISTING_ROW_RE = re.compile(
    r'<a\s+href="([^"]+)"[^>]*>[^<]*</a>(?:\s|</td>|<td[^>]*>)*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})',
    re.IGNORECASE,
)


def fetch_directory(url):
    """List a directory, including each file's exact size (one HEAD per file)."""
    entries, _ = fetch_directory_listing(url)
    files = [entry for entry in entries if not entry["directory"]]
    fill_remote_sizes(files)
    return entries


def fill_remote_sizes(file_entries, max_workers=8):
    """Look up exact sizes (HEAD requests, in parallel) for the given file entries."""
    if not file_entries:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(get_remote_file_size, entry["url"]): entry for entry in file_entries}
        for future in as_completed(future_map):
            entry = future_map[future]
            try:
                entry["size"] = future.result()
            except Exception:
                entry["size"] = None


def fetch_directory_listing(url):
    """Fetch one directory listing without per-file size lookups.

    Returns (entries, mtimes) where mtimes maps absolute URL -> the server's
    "Last modified" string (only for servers whose listing shows it).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get_content_type()
        data = response.read()

    if content_type not in ("text/html", "application/xhtml+xml"):
        raise RuntimeError("The selected URL did not return an HTML directory listing.")

    html = data.decode("utf-8", errors="replace")
    parser = LinkParser()
    parser.feed(html)

    entries = []
    seen_urls = set()

    for href in parser.links:
        if href.startswith("#") or href.startswith("?"):
            continue

        absolute_url = urllib.parse.urljoin(url, href)
        parsed = urllib.parse.urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue

        path = parsed.path
        display_name = urllib.parse.unquote(posixpath.basename(path.rstrip("/")))
        if not display_name or display_name in (".", ".."):
            continue

        is_directory = path.endswith("/")
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        clean_name, file_date = parse_filename_and_date(display_name)
        entries.append({
            "name": clean_name,
            "raw_name": display_name,
            "date": file_date,
            "url": absolute_url,
            "directory": is_directory,
            "size": None,
        })

    # Restrict C64 music archives root directory to specified folders only
    if url.rstrip("/") == "https://hvsc.brona.dk/HVSC/C64Music".rstrip("/"):
        allowed_roots = {"DEMOS", "DOCUMENTS", "GAMES", "MUSICIANS"}
        entries = [e for e in entries if e["directory"] and e["raw_name"].rstrip("/").upper() in allowed_roots]

    mtimes = {}
    for href, stamp in _LISTING_ROW_RE.findall(html):
        mtimes[urllib.parse.urljoin(url, href)] = stamp

    entries.sort(key=lambda entry: (not entry["directory"], entry["name"].lower()))
    return entries, mtimes


def unique_path(path):
    """Return a non-existing path without overwriting an existing file."""
    if not path or not os.path.exists(path):
        return path

    folder = os.path.dirname(path)
    filename = os.path.basename(path)
    stem, extension = os.path.splitext(filename)
    counter = 1

    while True:
        candidate = os.path.join(folder, f"{stem} ({counter}){extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1
