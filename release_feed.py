"""
Startup check for new releases using the vgm.hcs64.com RSS feed.

The feed (https://vgm.hcs64.com/rss.xml) lists the latest ~100 uploads across
the joshw.info sites, e.g.
    <link>https://pc.joshw.info/p/Phantom%20Blade%20Zero%20(...)[PC].7z</link>
Each link is matched to a domain in DOMAINS by host name and looked up in that
domain's local index. Items whose file isn't indexed yet are "new".

Items the user already declined to index are remembered (release_feed_seen.json
in the cache directory) so the prompt only comes back for newer releases.
"""
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import DOMAINS, USER_AGENT, get_cache_directory, get_cache_path

FEED_URL = "https://vgm.hcs64.com/rss.xml"
SEEN_FILE = "release_feed_seen.json"
MAX_SEEN = 2000  # the feed only holds ~100 items, so this is plenty


def _norm(url):
    """Compare URLs ignoring http/https, %-encoding ([ vs %5b), case of the host
    and a trailing slash, since the feed and the indexes spell them differently."""
    p = urllib.parse.urlparse(url.strip())
    return p.netloc.lower() + urllib.parse.unquote(p.path).rstrip("/")


def fetch_feed(timeout=20):
    """Return [{title, link, guid, date}] from the RSS feed."""
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        root = ET.fromstring(r.read())
    items = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "guid": (item.findtext("guid") or link).strip(),
            "date": (item.findtext("pubDate") or "").strip(),
        })
    return items


def _domains_by_host(excluded=()):
    hosts = {}
    for name, root in DOMAINS.items():
        if root and name not in excluded:
            hosts.setdefault(urllib.parse.urlparse(root).netloc.lower(), name)
    return hosts


def _indexed_urls(domain_name):
    """Normalised URLs in a domain's index, or None when there is no index yet."""
    path = get_cache_path(domain_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    entries = data.get("entries", []) if isinstance(data, dict) else data
    return {_norm(e["url"]) for e in entries if isinstance(e, dict) and e.get("url")}


def _seen_path():
    return os.path.join(get_cache_directory(), SEEN_FILE)


def load_seen():
    try:
        with open(_seen_path(), "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def mark_seen(items):
    """Remember these feed items so they don't trigger the prompt again."""
    seen = load_seen() | {i["guid"] for i in items}
    try:
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump(sorted(seen)[-MAX_SEEN:], f)
    except Exception as e:
        print(f"[release feed] could not save seen items: {e}")


def find_unindexed_releases(excluded=()):
    """Return {domain_name: [feed items not in that domain's index]}.

    Domains without a local index, and items the user already dismissed, are
    skipped. Raises if the feed can't be fetched or parsed.
    """
    hosts = _domains_by_host(excluded)
    seen = load_seen()
    by_domain = {}
    for item in fetch_feed():
        if item["guid"] in seen:
            continue
        domain = hosts.get(urllib.parse.urlparse(item["link"]).netloc.lower())
        if domain:
            by_domain.setdefault(domain, []).append(item)

    new = {}
    for domain, items in by_domain.items():
        known = _indexed_urls(domain)
        if known is None:
            continue
        missing = [i for i in items if _norm(i["link"]) not in known]
        if missing:
            new[domain] = missing
    return new
