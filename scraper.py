import hashlib
import json
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from state import UrlRecord

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def load_url_sources(path: str) -> list[dict]:
    """Read url_sources.json and return the list of source entries."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def scrape(entry: dict) -> tuple[UrlRecord | None, str | None]:
    """
    Fetch and parse one URL entry from url_sources.json.

    Returns (UrlRecord, None) on success, or (None, reason) on failure so the
    caller can record the failure reason in the state file.
    """
    url = entry["url"]

    try:
        response = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=20)
    except Exception as exc:
        reason = f"fetch error: {exc}"
        print(f"  WARNING: {reason}")
        return None, reason

    if response.status_code != 200:
        reason = f"HTTP {response.status_code}"
        print(f"  WARNING: {url} returned {reason} — skipping")
        return None, reason

    soup = BeautifulSoup(response.text, "html.parser")

    # Page title
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else url

    # Remove navigation noise before extracting body text
    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    # Extract from the most specific content container available
    content_el = soup.find("main") or soup.find("article") or soup.find("body")
    raw_text = content_el.get_text(separator=" ", strip=True) if content_el else ""

    words = raw_text.split()
    if len(words) < 200:
        reason = f"only {len(words)} words extracted — likely JS-rendered or bot-blocked"
        print(f"  WARNING: {url}: {reason}; skipping")
        return None, reason

    # Truncate to 2000 words for Claude context
    extracted_text = " ".join(words[:2000])
    content_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return UrlRecord(
        url=url,
        label=entry.get("label"),
        category=entry.get("category"),
        hint=entry.get("hint"),
        page_title=page_title,
        extracted_text=extracted_text,
        content_hash=content_hash,
        fetched_at=fetched_at,
    ), None
