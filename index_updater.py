"""
Fast incremental index updates for "Index All".

For each domain this re-lists only the directories that can contain something
new, appends any new entries to the domain's index file in their sorted
position, and leaves the file untouched when nothing was added.

How it stays fast:
  * Directory listings are fetched in parallel and WITHOUT the per-file HEAD
    requests that fetch_directory() makes. Only *new* files get a size lookup.
  * The server's "Last modified" time for every directory is stored in the
    index (``dir_mtimes``). A directory that only holds files is re-listed only
    when its timestamp changed - adding a file to a folder bumps that folder's
    timestamp. Directories that hold sub-directories are always re-listed
    (cheap: usually just the root) because that is where a changed
    sub-directory timestamp shows up.
  * The first run on an index without stored timestamps lists every directory
    once (still without HEAD requests) to record them.
"""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

from config import get_cache_path
from network import fetch_directory_listing, fill_remote_sizes

LISTING_WORKERS = 4  # parallel directory listings per domain


def _norm_dir(url):
    return url.rstrip("/") + "/"


def _parent_dir(url):
    return url.rstrip("/").rsplit("/", 1)[0] + "/"


def _sort_key(entry):
    # Same order fetch_directory() produces: directories first, then by name
    return (not entry.get("directory"), (entry.get("name") or "").lower())


def load_index(domain_name):
    """Return the index dict for a domain (empty structure if missing/corrupt)."""
    path = get_cache_path(domain_name)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    if isinstance(data, list):  # legacy format: bare entry list
        data = {"entries": data}
    data.setdefault("entries", [])
    data.setdefault("dir_sizes", {})
    data.setdefault("scanned_dirs", [])
    data.setdefault("dir_mtimes", {})
    return data


def _save_index(domain_name, data):
    path = get_cache_path(domain_name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written index


def merge_new_entries(existing, new_entries):
    """Insert new entries next to their siblings, in sorted position.

    Existing entries keep their order. A new entry goes before the first
    sibling (same parent folder) that sorts after it, or right after the last
    sibling. Entries whose folder has no existing siblings (contents of a new
    folder) are appended at the end, sorted.
    """
    siblings = {}  # parent dir -> list of (sort_key, index in existing)
    for i, e in enumerate(existing):
        siblings.setdefault(_parent_dir(e["url"]), []).append((_sort_key(e), i))

    insert_before = {}  # index in existing -> [entries]
    insert_after = {}   # index in existing -> [entries]
    tail = []
    for n in sorted(new_entries, key=_sort_key):
        group = siblings.get(_parent_dir(n["url"]))
        if not group:
            tail.append(n)
            continue
        key = _sort_key(n)
        target = next((idx for k, idx in group if k > key), None)
        if target is not None:
            insert_before.setdefault(target, []).append(n)
        else:
            insert_after.setdefault(group[-1][1], []).append(n)

    merged = []
    for i, e in enumerate(existing):
        merged.extend(insert_before.get(i, ()))
        merged.append(e)
        merged.extend(insert_after.get(i, ()))
    # New folders' contents: group by folder, keep discovery order of folders
    tail.sort(key=lambda e: (_parent_dir(e["url"]).count("/"), _parent_dir(e["url"]), _sort_key(e)))
    merged.extend(tail)
    return merged


def update_domain_index(domain_name, root_url, progress_cb=None, stop_event=None):
    """Check a domain for new entries and append them to its index file.

    Returns (status, new_file_entries) where status is one of
    "updated", "unchanged", "stopped".
    Raises on a failure to list the root directory.
    """
    stop_event = stop_event or threading.Event()
    root = _norm_dir(root_url)
    data = load_index(domain_name)
    existing = data["entries"]
    known_urls = {e["url"] for e in existing if isinstance(e, dict) and "url" in e}
    old_mtimes = dict(data["dir_mtimes"])
    new_mtimes = dict(old_mtimes)

    # Folders that (per the current index) contain sub-folders
    has_subdirs = {_parent_dir(e["url"]) for e in existing if e.get("directory")}

    def needs_listing(dir_url, stamp):
        if dir_url not in known_urls and dir_url != root:
            return True                      # brand-new folder
        if dir_url in has_subdirs:
            return True                      # may hide a changed sub-folder
        if stamp is None or old_mtimes.get(dir_url) is None:
            return True                      # no timestamp to compare against
        return stamp != old_mtimes[dir_url]  # files added/removed here

    new_entries = []
    visited = set()
    listed = queued = 0
    root_error = None

    with ThreadPoolExecutor(max_workers=LISTING_WORKERS) as pool:
        pending = {pool.submit(fetch_directory_listing, root): root}
        visited.add(root)
        queued = 1

        while pending:
            if stop_event.is_set():
                for fut in pending:
                    fut.cancel()
                return "stopped", []

            done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for fut in done:
                dir_url = pending.pop(fut)
                listed += 1
                try:
                    entries, mtimes = fut.result()
                except Exception as e:
                    if dir_url == root:
                        root_error = e
                    continue  # skip an unreachable sub-folder; next run retries it

                for entry in entries:
                    url = entry["url"]
                    # Ignore "Parent Directory" links and anything outside the domain root
                    if not url.startswith(root) or (entry["directory"] and dir_url.startswith(_norm_dir(url))):
                        continue
                    stamp = mtimes.get(url)
                    if entry["directory"]:
                        url = _norm_dir(url)
                        if stamp:
                            new_mtimes[url] = stamp
                    if entry["url"] not in known_urls:
                        known_urls.add(entry["url"])
                        new_entries.append(entry)
                    if entry["directory"] and url not in visited and needs_listing(url, stamp):
                        visited.add(url)
                        pending[pool.submit(fetch_directory_listing, url)] = url
                        queued += 1

            if progress_cb:
                progress_cb(listed, queued)

    if root_error is not None:
        raise root_error

    if not new_entries:
        if new_mtimes != old_mtimes:
            # Nothing new, but remember the timestamps so next run can skip more
            data["dir_mtimes"] = new_mtimes
            _save_index(domain_name, data)
        return "unchanged", []

    # Exact sizes for new files only
    new_files = [e for e in new_entries if not e["directory"]]
    fill_remote_sizes(new_files)
    if stop_event.is_set():
        return "stopped", []

    data["entries"] = merge_new_entries(existing, new_entries)
    data["dir_mtimes"] = new_mtimes
    data["scanned_dirs"] = sorted(set(data["scanned_dirs"]) | visited)

    # Cached folder sizes of every ancestor of a new file are now stale
    stale = set()
    for e in new_entries:
        d = _parent_dir(e["url"])
        while d.startswith(root) and d not in stale:
            stale.add(d)
            d = _parent_dir(d)
    data["dir_sizes"] = {k: v for k, v in data["dir_sizes"].items()
                         if _norm_dir(k) not in stale}

    _save_index(domain_name, data)
    return "updated", new_files
