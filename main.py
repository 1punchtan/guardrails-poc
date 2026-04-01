from datetime import datetime, timezone

import anthropic

from config import load_config
from github_client import connect, create_pr, get_existing_metafiles
from inference import infer_metadata, produce_metafile
from scraper import load_url_sources, scrape
from state import diff_urls, load_state, save_state


def _repo_summary(metafile: dict) -> dict:
    return {
        "id": metafile.get("id", ""),
        "title": metafile.get("title", ""),
        "category": metafile.get("category", ""),
        "tags": metafile.get("tags", []),
        "description": metafile.get("description", ""),
    }


def main() -> None:
    cfg = load_config()
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    repo = connect(cfg["github_token"], cfg["github_repo"])

    state = load_state(cfg["state_file"])

    total_prs = 0
    total_unchanged = 0

    # -------------------------------------------------------------------------
    # OneDrive flow (not yet implemented)
    # -------------------------------------------------------------------------
    print("--- OneDrive flow ---")
    print("  (OneDrive flow not yet implemented — skipping)\n")

    # -------------------------------------------------------------------------
    # URL flow
    # -------------------------------------------------------------------------
    print("--- URL flow ---")
    url_sources = load_url_sources(cfg["url_sources_file"])
    print(f"Loading {cfg['url_sources_file']}... {len(url_sources)} URL(s) found.")

    # Scrape all URLs first so we can diff the whole batch against state
    scraped: list = []
    for entry in url_sources:
        url = entry["url"]
        print(f"\nScraping: {url}")
        record, failure_reason = scrape(entry)
        if record is None:
            state["scrape_failures"][url] = {
                "url": url,
                "reason": failure_reason,
                "last_attempted": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            continue
        # Successfully scraped — clear any prior failure record
        state["scrape_failures"].pop(url, None)
        word_count = len(record.extracted_text.split())
        print(f"  done ({word_count} words extracted)")
        scraped.append(record)

    new_urls, changed_urls = diff_urls(scraped, state)
    to_process = [(r, False) for r in new_urls] + [(r, True) for r in changed_urls]
    total_unchanged = len(scraped) - len(to_process)

    if not to_process:
        print("\nNo changes detected since last run.")
        save_state(state, cfg["state_file"])
        failure_count = len(state["scrape_failures"])
        msg = "State saved."
        if failure_count:
            msg += f" {failure_count} URL(s) still pending manual attention — see scrape_failures in {cfg['state_file']}."
        print(msg)
        return

    # Fetch existing metafiles once; we'll extend the list as we create new ones
    # so each Call 2 can see guardrails produced earlier in the same run.
    repo_summaries = [
        _repo_summary(
            {"id": s.id, "title": s.title, "category": s.category,
             "tags": s.tags, "description": s.description}
        )
        for s in get_existing_metafiles(repo)
    ]

    for record, is_update in to_process:
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

    save_state(state, cfg["state_file"])
    failure_count = len(state["scrape_failures"])
    summary = f"\nState saved. {total_prs} PR(s) created. {total_unchanged} item(s) unchanged."
    if failure_count:
        summary += f" {failure_count} URL(s) failed to scrape — see scrape_failures in {cfg['state_file']}."
    print(summary)


if __name__ == "__main__":
    main()
