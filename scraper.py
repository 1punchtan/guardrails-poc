import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

from state import UrlRecord


@contextmanager
def browser_context():
    """
    Context manager that launches a single headless Chromium browser for the
    duration of the with-block. Yields a Playwright Page ready for navigation.
    Reusing one browser across all URLs avoids per-URL startup overhead.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        try:
            yield page
        finally:
            browser.close()


def load_url_sources(path: str) -> list[dict]:
    """Read url_sources.json and return the list of source entries."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def scrape(entry: dict, page: Page) -> tuple[UrlRecord | None, str | None]:
    """
    Fetch and parse one URL entry using a headless browser.

    Returns (UrlRecord, None) on success, or (None, reason) on failure so the
    caller can record the failure reason in the state file.
    """
    url = entry["url"]

    try:
        response = page.goto(url, wait_until="networkidle", timeout=30_000)
    except Exception as exc:
        reason = f"navigation error: {exc}"
        print(f"  WARNING: {reason}")
        return None, reason

    if response is None or not response.ok:
        status = response.status if response else "no response"
        reason = f"HTTP {status}"
        print(f"  WARNING: {url} returned {reason} — skipping")
        return None, reason

    page_title = page.title() or url
    html = page.content()

    soup = BeautifulSoup(html, "html.parser")

    # Remove navigation noise before extracting body text
    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    # Extract from the most specific content container available
    content_el = soup.find("main") or soup.find("article") or soup.find("body")
    raw_text = content_el.get_text(separator=" ", strip=True) if content_el else ""

    words = raw_text.split()
    if len(words) < 200:
        reason = f"only {len(words)} words extracted — page may require authentication or further interaction"
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
