import json
import re

import anthropic

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_CALL1 = """\
You are a technical architecture governance assistant. Your job is to infer \
structured metadata for an architecture guardrails library from information \
about a guardrail source.

The source is either an internal document (from OneDrive) or an external web \
page. You will be given whatever context is available — filename, folder path, \
page title, extracted text, and optional human-supplied hints.

Respond ONLY with a single JSON object matching this exact schema. Do not \
include any explanation, markdown fencing, or text outside the JSON.

{
  "title": "string — concise, human-readable title for this guardrail",
  "category": "string — primary category (e.g. cloud, security, api, data, integration, architecture)",
  "subcategory": "string or null — more specific classification within category",
  "type": "one of: diagram | document | spreadsheet | presentation | policy | standard | guideline | other",
  "description": "string — 1-3 sentence description of what this guardrail covers and why it matters",
  "tags": ["array", "of", "lowercase", "keyword", "strings"],
  "suggested_owner": "string or null — team or role best placed to own this guardrail (null if unclear)",
  "source_type": "onedrive | external_url"
}

Rules:
- Use null for fields you cannot reasonably infer. Do not guess.
- Keep the title short and specific — it will appear as a PR title and in search results.
- Tags should be lowercase, single words or hyphenated phrases. 3–8 tags is ideal.
- Category should be one of: cloud, security, api, data, integration, architecture, governance, execution — \
or a clear alternative if none fit.
- description should be written for a technical audience. Avoid marketing language.\
"""

SYSTEM_CALL2 = """\
You are a technical architecture governance assistant managing a guardrails library \
stored as JSON metafiles in a GitHub repository.

You will be given:
1. Draft metadata inferred from a guardrail source (Call 1 output)
2. The source type ("onedrive" or "external_url") and source-specific identifiers
3. A list of existing guardrails in the repo (id, title, category, tags, description)
4. Whether this item is new or an update to an existing entry

Your job is to produce a complete, valid metafile JSON object.

ID scheme: GUARD-{CATEGORY_PREFIX}-{THREE_DIGIT_SEQUENCE}
Category prefixes:
  INT   — integration
  SEC   — security
  API   — API design
  DATA  — data
  CLOUD — cloud services and platforms
  INFRA — on-premise infrastructure
  EXE   — work, project, design execution
  GOV   — general governance
  ARCH  — general architecture

Rules:
- Assign the next available sequence number in the correct category by inspecting \
the existing guardrail IDs provided. If no guardrails exist in that category yet, \
start at 001.
- If this item appears to be an update to an existing guardrail (matched by title \
similarity or stable source identifier), carry forward the existing ID, increment \
the version, set status to "under-review", and add a new changelog entry. Otherwise \
set status to "draft" and version to "1.0".
- Identify related guardrails from the existing list by category and tag overlap. \
Include up to 3 most relevant IDs in related_guardrails.
- Populate the source block correctly based on source_type:
    onedrive     → { type, onedrive_item_id, url, filename, last_modified }
    external_url → { type, url, page_title, content_hash, last_fetched }
- The change_log entry for a new item should use author "auto-generated" and a \
brief summary. For an update, note what changed.
- approved_by, approved_date, and review_due should be null.
- Respond ONLY with the JSON object. No explanation, no markdown fencing.\
"""

# ---------------------------------------------------------------------------
# User message builders
# ---------------------------------------------------------------------------

def _user_msg_call1_onedrive(source_input: dict) -> str:
    lines = [
        "Source type: OneDrive document",
        f"Filename: {source_input.get('filename', 'unknown')}",
        f"Folder path: {source_input.get('path', 'unknown')}",
        f"MIME type: {source_input.get('mime_type', 'unknown')}",
        f"Last modified: {source_input.get('last_modified', 'unknown')}",
        f"Created by: {source_input.get('created_by', 'unknown')}",
    ]
    text = source_input.get("extracted_text")
    if text:
        lines.append(f"\nExtracted text (first 1500 words):\n{text}")
    else:
        lines.append("\nExtracted text: (not available — infer from filename and path only)")
    return "\n".join(lines)


def _user_msg_call1_url(source_input: dict) -> str:
    lines = [
        "Source type: External URL",
        f"URL: {source_input.get('url', 'unknown')}",
        f"Page title: {source_input.get('page_title', 'unknown')}",
        f"Fetched at: {source_input.get('fetched_at', 'unknown')}",
    ]
    if source_input.get("label"):
        lines.append(f"Human-supplied label: {source_input['label']}")
    if source_input.get("category"):
        lines.append(f"Human-supplied category hint: {source_input['category']}")
    if source_input.get("hint"):
        lines.append(f"Human-supplied description hint: {source_input['hint']}")
    text = source_input.get("extracted_text")
    if text:
        lines.append(f"\nExtracted page text (up to 2000 words):\n{text}")
    else:
        lines.append("\nExtracted page text: (not available)")
    return "\n".join(lines)


def _user_msg_call2(
    draft: dict,
    source_type: str,
    source_identifiers: dict,
    repo_summaries: list,
    is_update: bool,
) -> str:
    existing = "\n".join(
        f"  {s['id']}: {s['title']} [{s['category']}] tags={s['tags']} — {s['description']}"
        for s in repo_summaries
    ) or "  (none — this will be the first guardrail in the repo)"

    lines = [
        f"Change type: {'UPDATE to existing guardrail' if is_update else 'NEW guardrail'}",
        "",
        "Draft metadata from Call 1:",
        json.dumps(draft, indent=2),
        "",
        f"Source type: {source_type}",
        "Source identifiers:",
    ]
    for k, v in source_identifiers.items():
        lines.append(f"  {k}: {v}")

    lines += [
        "",
        "Existing guardrails in the repo:",
        existing,
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract a JSON object from Claude's response, tolerating markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` fences if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    # Find the outermost { ... }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON object found in Claude response:\n{text}")
    return json.loads(text[start:end])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_metadata(source_input: dict, client: anthropic.Anthropic, model: str = "claude-sonnet-4-6") -> dict:
    """
    Call 1 — Infer draft metadata from source content.

    source_input must contain 'source_type': 'onedrive' | 'external_url'
    plus the fields documented in the plan.
    """
    source_type = source_input.get("source_type")
    if source_type == "onedrive":
        user_msg = _user_msg_call1_onedrive(source_input)
    elif source_type == "external_url":
        user_msg = _user_msg_call1_url(source_input)
    else:
        raise ValueError(f"Unknown source_type: {source_type!r}")

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_CALL1,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _extract_json(response.content[0].text)


def produce_metafile(
    draft_metadata: dict,
    source_type: str,
    source_identifiers: dict,
    repo_summaries: list,
    is_update: bool,
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    Call 2 — Produce the complete metafile JSON.

    repo_summaries: list of dicts with keys id, title, category, tags, description.
    source_identifiers: source-specific fields as documented in the plan.
    """
    user_msg = _user_msg_call2(
        draft=draft_metadata,
        source_type=source_type,
        source_identifiers=source_identifiers,
        repo_summaries=repo_summaries,
        is_update=is_update,
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_CALL2,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _extract_json(response.content[0].text)


# ---------------------------------------------------------------------------
# Smoke test — run with: python inference.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY must be set in .env")
        raise SystemExit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Existing guardrails for context (simulate a repo with one entry)
    existing = [
        {
            "id": "GUARD-SEC-001",
            "title": "Identity and Access Management Standards",
            "category": "security",
            "tags": ["iam", "identity", "access", "authentication"],
            "description": "Baseline requirements for identity, authentication, and access control across all systems.",
        }
    ]

    # ------------------------------------------------------------------
    # Test A — external_url stub
    # ------------------------------------------------------------------
    print("=" * 60)
    print("TEST A: external_url source")
    print("=" * 60)

    url_input = {
        "source_type": "external_url",
        "url": "https://www.digital.govt.nz/standards-and-guidance/technology-and-architecture/cloud-services/cloud-adoption-policy-and-strategy",
        "page_title": "Cloud Adoption Policy and Strategy — Digital.govt.nz",
        "label": "Cloud Adoption Policy and Strategy",
        "category": "cloud",
        "hint": "NZ Government Cloud First Policy — mandates cloud-first approach for agency IT investment",
        "extracted_text": (
            "The New Zealand Government has adopted a cloud first policy. "
            "Agencies must consider cloud services before investing in on-premise infrastructure. "
            "This policy applies to all-of-government ICT investment decisions. "
            "Agencies are required to consider Software as a Service (SaaS) before Platform as a Service (PaaS) "
            "and Infrastructure as a Service (IaaS) before on-premise solutions. "
            "Cabinet has mandated this approach to drive cost efficiency, resilience, and agility."
        ),
        "fetched_at": "2026-04-01T00:00:00Z",
    }

    print("\n--- Call 1: infer_metadata ---")
    draft_url = infer_metadata(url_input, client)
    print(json.dumps(draft_url, indent=2))

    source_ids_url = {
        "url": url_input["url"],
        "content_hash": "a3f9c2b1d4e5f6a7b8c9d0e1f2a3b4c5",
        "fetched_at": url_input["fetched_at"],
        "page_title": url_input["page_title"],
    }

    print("\n--- Call 2: produce_metafile ---")
    metafile_url = produce_metafile(
        draft_metadata=draft_url,
        source_type="external_url",
        source_identifiers=source_ids_url,
        repo_summaries=existing,
        is_update=False,
        client=client,
    )
    print(json.dumps(metafile_url, indent=2))

    # ------------------------------------------------------------------
    # Test B — onedrive stub
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("TEST B: onedrive source")
    print("=" * 60)

    onedrive_input = {
        "source_type": "onedrive",
        "filename": "cloud-integration-patterns.docx",
        "path": "Guardrails/integration/cloud-integration-patterns.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "last_modified": "2026-03-15T09:00:00Z",
        "created_by": "mark.tan@example.com",
        "extracted_text": (
            "This document defines the approved integration patterns for cloud services. "
            "All system integrations must use asynchronous messaging via the approved event broker. "
            "Direct synchronous REST calls between services are permitted only for read operations "
            "with latency requirements under 200ms. "
            "API Gateway must be used for all external-facing APIs. "
            "Event-driven architecture is the preferred pattern for data synchronisation across domains."
        ),
    }

    print("\n--- Call 1: infer_metadata ---")
    draft_od = infer_metadata(onedrive_input, client)
    print(json.dumps(draft_od, indent=2))

    source_ids_od = {
        "onedrive_item_id": "ITEM_ABC123XYZ",
        "url": "https://onedrive.live.com/edit?id=ITEM_ABC123XYZ",
        "filename": onedrive_input["filename"],
        "last_modified": onedrive_input["last_modified"],
    }

    # Add the URL guardrail we just produced to the existing list so Call 2
    # can detect related guardrails across both tests
    existing_with_url = existing + [
        {
            "id": metafile_url.get("id", "GUARD-CLOUD-001"),
            "title": metafile_url.get("title", ""),
            "category": metafile_url.get("category", "cloud"),
            "tags": metafile_url.get("tags", []),
            "description": metafile_url.get("description", ""),
        }
    ]

    print("\n--- Call 2: produce_metafile ---")
    metafile_od = produce_metafile(
        draft_metadata=draft_od,
        source_type="onedrive",
        source_identifiers=source_ids_od,
        repo_summaries=existing_with_url,
        is_update=False,
        client=client,
    )
    print(json.dumps(metafile_od, indent=2))

    # ------------------------------------------------------------------
    # Validate both outputs have required fields
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)

    required = ["id", "title", "category", "type", "status", "version",
                "description", "source", "change_log"]
    for label, metafile in [("URL metafile", metafile_url), ("OneDrive metafile", metafile_od)]:
        missing = [f for f in required if f not in metafile]
        if missing:
            print(f"  FAIL {label} — missing fields: {missing}")
        else:
            print(f"  OK   {label} — all required fields present (id={metafile['id']})")

    print("\nPhase 3 smoke test complete.")
