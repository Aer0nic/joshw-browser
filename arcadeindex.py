import json
import re
import time
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

BASE_URL = "https://vgmrips.net/packs/systems/arcade/"
OUTPUT_FILE = "domain_cache_Arcade.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

def create_robust_session() -> requests.Session:
    """Creates a session that handles network hiccups and follows redirects."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def parse_system_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], parts[0]

def clean_display_name(raw_name: str) -> str:
    # Strip extension
    name = re.sub(r"\.[a-zA-Z0-9]+$", "", raw_name)
    # Replace underscores with spaces for readability
    name = name.replace("_", " ")
    # Strip trailing bracketed data (e.g., "(Neo Geo)")
    name = re.sub(r"\s*[\(\[\{].*$", "", name)
    return name.strip()

def extract_date(text: str) -> str:
    match = re.search(r"\((\d{4}(?:-\d{2})?(?:-\d{2})?)\)", text)
    return match.group(1) if match else ""

def get_arcade_systems(session: requests.Session) -> list[dict]:
    print(f"Fetching main arcade index: {BASE_URL}")
    resp = session.get(BASE_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    systems = []
    
    # Using your exact CSS path for the systems list
    for link in soup.select("div#systems div.system div.name a"):
        href = link.get("href")
        system_name = link.text.strip()
        
        if href and system_name:
            full_url = urllib.parse.urljoin(BASE_URL, href)
            company, system = parse_system_name(system_name)
            
            systems.append({
                "company": company,
                "system": system,
                "full_name": system_name,
                "url": full_url
            })

    unique_systems = {s["url"]: s for s in systems}.values()
    return sorted(unique_systems, key=lambda x: (x["company"].lower(), x["system"].lower()))

def parse_system_pack_files(system_info: dict, session: requests.Session) -> list[dict]:
    base_sys_url = system_info["url"]
    file_entries = []
    page = 1
    
    while True:
        url = f"{base_sys_url}?page={page}" if page > 1 else base_sys_url
        print(f"  -> Scraping page {page}: {url}")
        
        try:
            resp = session.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"  -> Failed to fetch {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Target the download class explicitly as requested
        downloads = soup.select(".download")
        if not downloads:
            break
            
        items_found_on_page = 0
        
        for dl in downloads:
            # class="download" might be on the <a> tag itself or a wrapping <td>/<div> element
            link = dl if dl.name == "a" else dl.find("a", href=True)
            if not link or not link.has_attr("href"):
                continue
                
            href = link["href"]
            initial_url = urllib.parse.urljoin(url, href)
            
            try:
                # Follow the redirect to get the true /files/...zip URL 
                # HEAD request gets the URL without downloading the actual file body
                head_resp = session.head(initial_url, allow_redirects=True, timeout=10)
                final_url = head_resp.url
                
                # Fallback if the server rejects HEAD requests
                if head_resp.status_code == 405:
                    with session.get(initial_url, stream=True, allow_redirects=True, timeout=10) as r:
                        final_url = r.url
            except requests.RequestException:
                final_url = initial_url
                
            # Parse filename cleanly from the resolved final URL
            raw_filename = final_url.split("/")[-1]
            raw_filename = urllib.parse.unquote(raw_filename)

            clean_name = clean_display_name(raw_filename)
            date_str = extract_date(raw_filename)

            file_entries.append({
                "company": system_info["company"],
                "system": system_info["system"],
                "name": clean_name,
                "raw_name": raw_filename,
                "date": date_str,
                "url": final_url,
                "directory": False,
                "size": None
            })
            items_found_on_page += 1

        if items_found_on_page == 0:
            break
            
        # Standardize pagination: look for any link pointing to the next page number
        target_next_page = f"page={page + 1}"
        if not any(target_next_page in a.get("href", "") for a in soup.find_all("a")):
            break
            
        page += 1
        time.sleep(0.5)
        
    unique_files = {f["url"]: f for f in file_entries}.values()
    return list(unique_files)

def main():
    session = create_robust_session()
    
    sorted_systems = get_arcade_systems(session)
    print(f"Found {len(sorted_systems)} arcade systems. Beginning crawl...\n")

    all_indexed_entries = []

    for sys_info in sorted_systems:
        all_indexed_entries.append({
            "company": sys_info["company"],
            "system": sys_info["system"],
            "name": sys_info["full_name"],
            "raw_name": sys_info["full_name"],
            "date": "",
            "url": sys_info["url"],
            "directory": True,
            "size": None
        })

        pack_files = parse_system_pack_files(sys_info, session)
        pack_files_sorted = sorted(pack_files, key=lambda x: x["name"].lower())
        all_indexed_entries.extend(pack_files_sorted)

    output_data = {
        "entries": all_indexed_entries
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\nSuccessfully crawled all subdomains.")
    print(f"Indexed {len(all_indexed_entries)} total entries into {OUTPUT_FILE}.")

if __name__ == "__main__":
    main()
