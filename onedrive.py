import json
import os

import httpx
import msal

from state import FileRecord

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Files.Read.All"]
TOKEN_CACHE_PATH = ".graph_token_cache.json"


class OneDriveClient:
    def __init__(self, access_token: str) -> None:
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {access_token}"},
            follow_redirects=True,
            timeout=30,
        )

    def get(self, path: str, **kwargs) -> dict:
        response = self._http.get(f"{GRAPH_BASE}{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    def get_bytes(self, path: str) -> bytes:
        response = self._http.get(f"{GRAPH_BASE}{path}")
        response.raise_for_status()
        return response.content

    def close(self) -> None:
        self._http.close()


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_PATH):
        with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


def authenticate(client_id: str, tenant_id: str) -> OneDriveClient:
    """
    Acquire an access token using MSAL device code flow, with local token cache.

    On first run the user is prompted to open a URL and enter a short code.
    Subsequent runs use the cached token silently until it expires.
    """
    cache = _load_cache()
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to create device flow: {flow}")
        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result)}"
        )

    return OneDriveClient(result["access_token"])


def list_folder_recursive(client: OneDriveClient, folder_name: str) -> list[FileRecord]:
    """
    Walk the named OneDrive folder and all subfolders recursively.
    Returns a flat list of FileRecord for every file found.
    """
    records: list[FileRecord] = []

    def _walk(api_path: str, display_path: str) -> None:
        url = api_path
        while url:
            if url.startswith(GRAPH_BASE):
                # nextLink is a full URL — strip base for our get() helper
                url = url[len(GRAPH_BASE):]
            data = client.get(url)
            for item in data.get("value", []):
                if "folder" in item:
                    child_api = f"/me/drive/items/{item['id']}/children"
                    child_display = f"{display_path}/{item['name']}"
                    _walk(child_api, child_display)
                elif "file" in item:
                    records.append(
                        FileRecord(
                            item_id=item["id"],
                            name=item["name"],
                            path=f"{display_path}/{item['name']}",
                            last_modified=item.get("lastModifiedDateTime", ""),
                            created_by=(
                                item.get("createdBy", {})
                                .get("user", {})
                                .get("displayName", "")
                            ),
                            size_bytes=item.get("size", 0),
                            mime_type=item.get("file", {}).get("mimeType", ""),
                            web_url=item.get("webUrl", ""),
                        )
                    )
            url = data.get("@odata.nextLink")

    root_api = f"/me/drive/root:/{folder_name}:/children"
    _walk(root_api, folder_name)
    return records


def download_file(client: OneDriveClient, item_id: str) -> bytes:
    """Download file content by OneDrive item ID."""
    return client.get_bytes(f"/me/drive/items/{item_id}/content")
