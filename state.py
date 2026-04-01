import json
import os
from dataclasses import dataclass


@dataclass
class FileRecord:
    item_id: str
    name: str
    path: str
    last_modified: str
    created_by: str
    size_bytes: int
    mime_type: str
    web_url: str


@dataclass
class UrlRecord:
    url: str
    label: str | None
    category: str | None
    hint: str | None
    page_title: str
    extracted_text: str
    content_hash: str
    fetched_at: str


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"onedrive": {}, "urls": {}}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return {"onedrive": {}, "urls": {}}
    return json.loads(content)


def save_state(state: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def diff_files(
    current: list[FileRecord], state: dict
) -> tuple[list[FileRecord], list[FileRecord]]:
    onedrive_state = state.get("onedrive", {})
    new_files = []
    modified_files = []

    for record in current:
        if record.item_id not in onedrive_state:
            new_files.append(record)
        elif record.last_modified != onedrive_state[record.item_id]["last_modified"]:
            modified_files.append(record)

    return new_files, modified_files


def diff_urls(
    current: list[UrlRecord], state: dict
) -> tuple[list[UrlRecord], list[UrlRecord]]:
    url_state = state.get("urls", {})
    new_urls = []
    changed_urls = []

    for record in current:
        if record.url not in url_state:
            new_urls.append(record)
        elif record.content_hash != url_state[record.url]["content_hash"]:
            changed_urls.append(record)

    return new_urls, changed_urls


if __name__ == "__main__":
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    state = load_state(tmp_path)
    assert state == {"onedrive": {}, "urls": {}}, "Empty state should have two empty dicts"

    state["onedrive"]["item001"] = {
        "item_id": "item001",
        "name": "test.docx",
        "last_modified": "2025-01-01T00:00:00Z",
    }
    state["urls"]["https://example.com"] = {
        "url": "https://example.com",
        "content_hash": "abc123",
    }
    save_state(state, tmp_path)

    reloaded = load_state(tmp_path)
    assert reloaded["onedrive"]["item001"]["name"] == "test.docx"
    assert reloaded["urls"]["https://example.com"]["content_hash"] == "abc123"

    records = [
        FileRecord("item001", "test.docx", "/test.docx", "2025-01-01T00:00:00Z", "user", 100, "docx", "http://x"),
        FileRecord("item002", "new.docx", "/new.docx", "2025-02-01T00:00:00Z", "user", 200, "docx", "http://y"),
        FileRecord("item001", "test.docx", "/test.docx", "2025-03-01T00:00:00Z", "user", 100, "docx", "http://x"),
    ]
    new_f, mod_f = diff_files(records, reloaded)
    assert len(new_f) == 1 and new_f[0].item_id == "item002", "item002 should be new"
    assert len(mod_f) == 1 and mod_f[0].item_id == "item001", "item001 should be modified"

    url_records = [
        UrlRecord("https://example.com", None, None, None, "Example", "text", "different_hash", "2025-01-01T00:00:00Z"),
        UrlRecord("https://new.com", None, None, None, "New", "text", "hash999", "2025-01-01T00:00:00Z"),
    ]
    new_u, changed_u = diff_urls(url_records, reloaded)
    assert len(new_u) == 1 and new_u[0].url == "https://new.com", "new.com should be new"
    assert len(changed_u) == 1 and changed_u[0].url == "https://example.com", "example.com should be changed"

    os.unlink(tmp_path)
    print("state.py smoke test passed.")
