from datetime import datetime, timezone

import anthropic

from config import load_config
from extractor import extract_text
from github_client import connect, create_pr, get_existing_metafiles
from inference import infer_metadata, produce_metafile
from onedrive import authenticate, download_file, list_folder_recursive
from scraper import browser_context, load_url_sources, scrape
from state import diff_files, diff_urls, load_state, save_state


def _repo_summary(metafile: dict) -> dict:
    return {
        "id": metafile.get("id", ""),
        "title": metafile.get("title", ""),
        "category": metafile.get("category", ""),
        "tags": metafile.get("tags", []),
        "description": metafile.get("description", ""),
        "source_url": metafile.get("source_url"),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def main() -> None:
    cfg = load_config()
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    repo = connect(cfg["github_token"], cfg["github_repo"])

    state = load_state(cfg["state_file"])

    total_prs = 0
    total_unchanged = 0

    # -------------------------------------------------------------------------
    # OneDrive flow
    # -------------------------------------------------------------------------
    print("--- OneDrive flow ---")
    print("Authenticating with Microsoft Graph...")
    try:
        od_client = authenticate(cfg["azure_client_id"], cfg["azure_tenant_id"])
    except Exception as exc:
        print(f"  ERROR: authentication failed: {exc}")
        print("  Skipping OneDrive flow.\n")
        od_client = None

    if od_client:
        folder = cfg["onedrive_watch_folder"]
        print(f"Scanning OneDrive folder: {folder}/")
        try:
            current_files = list_folder_recursive(od_client, folder)
        except Exception as exc:
            print(f"  ERROR: could not list OneDrive folder: {exc}")
            current_files = []

        print(f"Found {_plural(len(current_files), 'file')}. Comparing against state...")
        new_files, modified_files = diff_files(current_files, state)

        for f in current_files:
            if f in new_files:
                print(f"  NEW:       {f.path}")
            elif f in modified_files:
                print(f"  MODIFIED:  {f.path}")
            else:
                print(f"  UNCHANGED: {f.path}")
                total_unchanged += 1

        # Fetch repo summaries once — shared with URL flow later in this run
        repo_summaries = [
            _repo_summary(
                {"id": s.id, "title": s.title, "category": s.category,
                 "tags": s.tags, "description": s.description,
                 "source_url": s.source_url}
            )
            for s in get_existing_metafiles(repo)
        ]

        od_to_process = [(r, False) for r in new_files] + [(r, True) for r in modified_files]

        for record, is_update in od_to_process:
            status_label = "UPDATE" if is_update else "NEW"
            print(f"\nProcessing ({status_label}): {record.path}")

            try:
                print("  Downloading file...", end=" ", flush=True)
                content = download_file(od_client, record.item_id)
                print("done")
            except Exception as exc:
                print(f"FAILED\n  ERROR: {exc} — skipping")
                continue

            extracted = extract_text(record.name, content)
            if extracted:
                print(f"  Extracting text... done ({_plural(len(extracted.split()), 'word')})")
            else:
                print("  Extracting text... not supported — inferring from filename only")

            source_input = {
                "source_type": "onedrive",
                "filename": record.name,
                "path": record.path,
                "mime_type": record.mime_type,
                "last_modified": record.last_modified,
                "created_by": record.created_by,
                "extracted_text": extracted,
            }

            try:
                print("  Inferring metadata (Claude call 1)...", end=" ", flush=True)
                draft = infer_metadata(source_input, client, cfg["claude_model"])
                print("done")
            except Exception as exc:
                print(f"FAILED\n  ERROR (Claude call 1): {exc} — skipping")
                continue

            source_identifiers = {
                "onedrive_item_id": record.item_id,
                "url": record.web_url,
                "filename": record.name,
                "last_modified": record.last_modified,
            }

            try:
                print("  Comparing against repo (Claude call 2)...", end=" ", flush=True)
                metafile = produce_metafile(
                    draft_metadata=draft,
                    source_type="onedrive",
                    source_identifiers=source_identifiers,
                    repo_summaries=repo_summaries,
                    is_update=is_update,
                    client=client,
                    model=cfg["claude_model"],
                )
                print("done")
            except Exception as exc:
                print(f"FAILED\n  ERROR (Claude call 2): {exc} — skipping")
                continue

            print(f"  Assigned ID: {metafile.get('id')}")

            source_ref = {
                "filename": record.name,
                "onedrive_path": record.path,
            }

            try:
                print("  Creating GitHub PR...", end=" ", flush=True)
                pr_url = create_pr(
                    repo=repo,
                    metafile=metafile,
                    source_type="onedrive",
                    source_ref=source_ref,
                    is_update=is_update,
                    base_branch=cfg["github_base_branch"],
                )
                print("done")
                print(f"  PR: {pr_url}")
            except Exception as exc:
                print(f"FAILED\n  ERROR (GitHub PR): {exc} — skipping")
                continue

            state["onedrive"][record.item_id] = {
                "item_id": record.item_id,
                "name": record.name,
                "path": record.path,
                "last_modified": record.last_modified,
                "guardrail_id": metafile.get("id"),
                "pr_url": pr_url,
                "last_processed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            repo_summaries.append(_repo_summary(metafile))
            total_prs += 1

        od_client.close()
        print()

    # -------------------------------------------------------------------------
    # URL flow
    # -------------------------------------------------------------------------
    print("--- URL flow ---")
    url_sources = load_url_sources(cfg["url_sources_file"])
    print(f"Loading {cfg['url_sources_file']}... {_plural(len(url_sources), 'URL')} found.")

    # Scrape all URLs first so we can diff the whole batch against state
    scraped: list = []
    with browser_context() as page:
        for entry in url_sources:
            url = entry["url"]
            print(f"\nScraping: {url}")
            record, failure_reason = scrape(entry, page)
            if record is None:
                state["scrape_failures"][url] = {
                    "url": url,
                    "reason": failure_reason,
                    "last_attempted": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                continue
            # Successfully scraped — clear any prior failure record
            state["scrape_failures"].pop(url, None)
            print(f"  done ({_plural(len(record.extracted_text.split()), 'word')} extracted)")
            scraped.append(record)

    new_urls, changed_urls = diff_urls(scraped, state)
    url_to_process = [(r, False) for r in new_urls] + [(r, True) for r in changed_urls]
    total_unchanged += len(scraped) - len(url_to_process)

    if url_to_process:
        # repo_summaries is initialised in the OneDrive flow above; fetch it here
        # only when OneDrive auth was skipped entirely.
        if not od_client:
            repo_summaries = [
                _repo_summary(
                    {"id": s.id, "title": s.title, "category": s.category,
                     "tags": s.tags, "description": s.description,
                     "source_url": s.source_url}
                )
                for s in get_existing_metafiles(repo)
            ]

        # Build a URL → existing guardrail ID map for deterministic dedup.
        # This catches the case where the local state was reset but the guardrail
        # already exists in the repo — without this, diff_urls would mark the URL
        # as NEW and Claude would assign a fresh ID instead of reusing the existing one.
        url_to_existing_id = {
            s["source_url"]: s["id"]
            for s in repo_summaries
            if s.get("source_url")
        }

        for record, is_update in url_to_process:
            # If the URL already has a guardrail in the repo, treat this as an
            # update regardless of what the local state file says.
            existing_id = url_to_existing_id.get(record.url)
            if existing_id and not is_update:
                is_update = True
                print(f"\nProcessing: {record.url}")
                print(f"  Content hash: {record.content_hash[:16]}... (REPO-MATCH → treating as UPDATE of {existing_id})")
            else:
                status_label = "CHANGED" if is_update else "NEW"
                print(f"\nProcessing: {record.url}")
                print(f"  Content hash: {record.content_hash[:16]}... ({status_label})")

            source_input = {
                "source_type": "external_url",
                "url": record.url,
                "page_title": record.page_title,
                "label": record.label,
                "category": record.category,
                "hint": record.hint,
                "extracted_text": record.extracted_text,
                "fetched_at": record.fetched_at,
            }

            try:
                print("  Inferring metadata (Claude call 1)...", end=" ", flush=True)
                draft = infer_metadata(source_input, client, cfg["claude_model"])
                print("done")
            except Exception as exc:
                print(f"FAILED\n  ERROR (Claude call 1): {exc} — skipping")
                continue

            source_identifiers = {
                "url": record.url,
                "content_hash": record.content_hash,
                "fetched_at": record.fetched_at,
                "page_title": record.page_title,
            }
            if existing_id:
                source_identifiers["existing_guardrail_id"] = existing_id

            try:
                print("  Comparing against repo (Claude call 2)...", end=" ", flush=True)
                metafile = produce_metafile(
                    draft_metadata=draft,
                    source_type="external_url",
                    source_identifiers=source_identifiers,
                    repo_summaries=repo_summaries,
                    is_update=is_update,
                    client=client,
                    model=cfg["claude_model"],
                )
                print("done")
            except Exception as exc:
                print(f"FAILED\n  ERROR (Claude call 2): {exc} — skipping")
                continue

            print(f"  Assigned ID: {metafile.get('id')}")

            source_ref = {
                "url": record.url,
                "page_title": record.page_title,
                "fetched_at": record.fetched_at,
            }

            try:
                print("  Creating GitHub PR...", end=" ", flush=True)
                pr_url = create_pr(
                    repo=repo,
                    metafile=metafile,
                    source_type="external_url",
                    source_ref=source_ref,
                    is_update=is_update,
                    base_branch=cfg["github_base_branch"],
                )
                print("done")
                print(f"  PR: {pr_url}")
            except Exception as exc:
                print(f"FAILED\n  ERROR (GitHub PR): {exc} — skipping")
                continue

            # Only update state after a fully successful run for this item
            state["urls"][record.url] = {
                "url": record.url,
                "content_hash": record.content_hash,
                "guardrail_id": metafile.get("id"),
                "pr_url": pr_url,
                "last_processed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            # Make this guardrail visible to subsequent Call 2 invocations in the same run
            repo_summaries.append(_repo_summary(metafile))
            total_prs += 1

    # -------------------------------------------------------------------------
    # Final summary
    # -------------------------------------------------------------------------
    save_state(state, cfg["state_file"])

    if total_prs == 0 and total_unchanged > 0:
        print("\nNo changes detected since last run.")

    failure_count = len(state["scrape_failures"])
    parts = [
        f"\nState saved.",
        f"{_plural(total_prs, 'PR')} created.",
        f"{_plural(total_unchanged, 'item')} unchanged.",
    ]
    if failure_count:
        parts.append(
            f"{_plural(failure_count, 'URL')} failed to scrape"
            f" — see scrape_failures in {cfg['state_file']}."
        )
    print(" ".join(parts))


if __name__ == "__main__":
    main()
