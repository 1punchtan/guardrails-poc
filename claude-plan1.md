# Guardrails Library POC — Implementation Plan

## Overview

A Python script that processes two types of guardrail sources and creates pull
requests in a GitHub repository containing the inferred metafile:

- **OneDrive files** — detects new and modified documents in a watched OneDrive
  folder (Word, PDF, DrawIO, etc.), extracts content where possible, and infers
  metadata via Claude
- **External URLs** — scrapes publicly accessible web pages (e.g. government
  policy pages, standards bodies) via Claude's web reading capability, and
  infers metadata from the scraped content

Manually triggered from the command line (`python main.py`). No daemon, no
scheduler, no background process. Both the OneDrive and URL pipelines run in
the same single invocation.

The two source types share the same inference and GitHub PR logic. Only the
input pipeline differs.

**Target repository:** This `guardrails-poc` repo is the target for the POC.
A `guardrails/` subfolder will be created here to hold all metafiles. The
`guardrails/schema/` subfolder will hold `guardrail.schema.json`.

---

## Prerequisites

- Python 3.11+
- `uv` for dependency management (recommended) or `venv`
- Azure app registration (personal account) for Microsoft Graph API access
- Anthropic API key
- GitHub personal access token (with `repo` scope)
- A target GitHub repo with an empty or seeded `guardrails/` folder structure
  *(for the POC this is the `guardrails-poc` repo itself)*

### Azure App Registration (manual setup required)

You must create an Azure app registration in the Azure portal before the
OneDrive flow will work. Steps:

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active
   Directory** → **App registrations** → **New registration**
2. Name: anything (e.g. `guardrails-poc`)
3. Supported account types: **Personal Microsoft accounts only**
   (if your OneDrive is a personal account)
4. Redirect URI: **Public client/native (mobile & desktop)** →
   `http://localhost:8400`
5. After creation, copy the **Application (client) ID** → `AZURE_CLIENT_ID`
6. Under **Authentication**, enable **Allow public client flows** (device code
   flow requires this)
7. Under **API permissions**, add **Microsoft Graph** → **Delegated** →
   `Files.Read.All` (or `Files.Read` for narrower scope). Grant admin consent
   if prompted.
8. No client secret is needed — device code flow is used.

`AZURE_TENANT_ID` should be set to `consumers` for personal Microsoft accounts.

### Python dependencies

```
msal                  # Microsoft auth (OAuth2 token acquisition)
msgraph-sdk           # Microsoft Graph API client
python-docx           # Word document text extraction
anthropic             # Claude API
PyGithub              # GitHub API client
python-dotenv         # Local .env config
httpx                 # HTTP client for URL scraping
beautifulsoup4        # HTML parsing for URL scraping
pypdf                 # PDF text extraction (optional)
```

---

## Configuration

All secrets and configuration via a `.env` file. Never committed to Git.

```
# Microsoft Graph
AZURE_CLIENT_ID=
AZURE_TENANT_ID=consumers
AZURE_REDIRECT_URI=http://localhost:8400

# OneDrive
ONEDRIVE_WATCH_FOLDER=Guardrails   # Root folder name in personal OneDrive

# Anthropic
ANTHROPIC_API_KEY=

# GitHub
GITHUB_TOKEN=
GITHUB_REPO=your-username/guardrails-library   # e.g. owner/repo
GITHUB_BASE_BRANCH=main

# State
STATE_FILE=.guardrails_state.json

# URL sources
URL_SOURCES_FILE=url_sources.json  # List of external URLs to track
```

---

## Script Structure

```
guardrails-poc/
├── main.py                  # Entry point — orchestrates both flows
├── config.py                # Loads and validates .env config
├── onedrive.py              # OneDrive/Graph API interactions
├── extractor.py             # Document text extraction (Word, PDF, fallback)
├── scraper.py               # External URL fetching and content extraction
├── inference.py             # Claude API calls (two-stage, shared by both flows)
├── github_client.py         # GitHub API interactions
├── state.py                 # State file read/write (covers both source types)
├── url_sources.json         # List of external URLs to track (user-maintained)
└── .env                     # Secrets (not committed)
```

---

## Module Detail

### `main.py`

Orchestrates both flows in sequence. Calls each module in order, handles
top-level errors, and prints a summary of what was processed.

```
load config
authenticate with Microsoft Graph
load previous state

--- OneDrive flow ---
fetch current OneDrive folder contents (recursive)
diff against state → identify new and modified files
for each changed file:
    download file content
    extract text (if supported format)
    run Claude inference (stage 1 — metadata)
    run Claude inference (stage 2 — repo comparison)
    create GitHub PR
    update state entry for this file

--- URL flow ---
load url_sources.json
for each URL:
    scrape page content via scraper.py
    hash content → compare against state to detect changes
    if new or changed:
        run Claude inference (stage 1 — metadata)
        run Claude inference (stage 2 — repo comparison)
        create GitHub PR
        update state entry for this URL

save updated state
print summary
```

---

### `config.py`

Loads `.env` via `python-dotenv`. Validates all required keys are present on
startup. Raises a clear error if anything is missing — fail fast before any API
calls are made.

---

### `onedrive.py`

Handles all Microsoft Graph API interactions.

**Auth flow:**
- Uses `msal` with device code flow (user opens a URL, enters a code)
- Caches the token locally so re-runs don't require re-authentication
- Token cache file: `.graph_token_cache.json` (excluded from Git)

**Functions:**
- `authenticate() → GraphClient` — handles MSAL device code flow and token cache
- `list_folder_recursive(client, folder_name) → list[FileRecord]` — walks the
  watch folder and all subfolders, returns a flat list of file records
- `download_file(client, item_id) → bytes` — downloads file content by item ID

**FileRecord shape:**
```python
@dataclass
class FileRecord:
    item_id: str          # OneDrive item ID (stable, survives renames)
    name: str             # Filename
    path: str             # Full path within watch folder
    last_modified: str    # ISO 8601 timestamp
    created_by: str       # Display name of creator
    size_bytes: int
    mime_type: str
    web_url: str          # Direct OneDrive URL (used in metafile)
```

Note: use `item_id` as the stable identifier in state tracking, not filename
or path — both can change.

---

### `extractor.py`

Extracts plain text from downloaded OneDrive document content for passing to
Claude. Used only in the OneDrive flow — URL content extraction is handled by
`scraper.py`.

**Supported formats:**
- `.docx` — extract via `python-docx`, first 1500 words
- `.md` / `.txt` — decode bytes directly
- `.pdf` — extract first 1500 words via `pypdf`
- All others (`.drawio`, `.vsdx`, images) — return `None`; Claude will infer
  from filename and folder context only

**Functions:**
- `extract_text(filename: str, content: bytes) → str | None`

---

### `scraper.py`

Handles the URL-based guardrail flow. Fetches external web pages, extracts
meaningful text content, and produces a `UrlRecord` for the inference pipeline.

**`url_sources.json` format** — maintained manually by the user, committed to
the repo alongside the script. The file is seeded with real NZ all-of-government
sources from the Department of Internal Affairs (DIA) and related agencies:

```json
[
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/technology-and-architecture/cloud-services/cloud-adoption-policy-and-strategy",
    "label": "Cloud Adoption Policy and Strategy",
    "category": "cloud",
    "hint": "NZ Government Cloud First Policy — mandates cloud-first approach for agency IT investment, including cabinet decisions and requirements"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/technology-and-architecture/application-programming-interfaces-apis/api-guidelines",
    "label": "NZ Government API Guidelines",
    "category": "api",
    "hint": "Official API guidelines for NZ government agencies covering design, implementation, and best practices"
  },
  {
    "url": "https://docref.digital.govt.nz/nz/dia/nz-api-standard/draft/en/",
    "label": "New Zealand API Standard (Draft)",
    "category": "api",
    "hint": "Draft NZ API Standard covering design, development, and security phases; open for public consultation"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/privacy-security-and-risk/privacy/data-protection-and-use-policy-dpup",
    "label": "Data Protection and Use Policy (DPUP)",
    "category": "data",
    "hint": "Policy framework for government data collection and use, focused on wellbeing and trust principles"
  },
  {
    "url": "https://www.data.govt.nz/toolkit/policies/new-zealand-data-and-information-management-principles",
    "label": "NZ Data and Information Management Principles",
    "category": "data",
    "hint": "Government data management principles including open data, accessibility, and protection requirements"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/digital-service-design-standard",
    "label": "Digital Service Design Standard",
    "category": "architecture",
    "hint": "Design standard for NZ government digital service delivery"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/nz-government-web-standards",
    "label": "NZ Government Web Standards",
    "category": "architecture",
    "hint": "Web accessibility and usability standards for government agencies; WCAG 2.2 aligned"
  },
  {
    "url": "https://www.protectivesecurity.govt.nz/policy/information-security",
    "label": "Protective Security Requirements — Information Security",
    "category": "security",
    "hint": "Government-wide information security policy framework and requirements from the Protective Security Requirements (PSR)"
  },
  {
    "url": "https://nzism.gcsb.govt.nz/ism-document",
    "label": "New Zealand Information Security Manual (NZISM)",
    "category": "security",
    "hint": "Comprehensive information security manual from GCSB covering controls and processes for protecting NZ government systems"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/information-sharing-standard/purpose-and-scope",
    "label": "Information Sharing Standard",
    "category": "data",
    "hint": "Requirements for public service agencies handling personal information shared with third parties"
  },
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/technology-and-architecture/government-digital-standards-catalogue",
    "label": "Government Digital Standards Catalogue",
    "category": "architecture",
    "hint": "Comprehensive catalogue of NZ government digital standards covering service design, architecture, and transformation"
  }
]
```

`label`, `category`, and `hint` are optional human-supplied hints passed to
Claude to improve inference quality. They do not override Claude's output —
they inform it.

**Scraping approach:**
- Fetch page HTML via `httpx` (follows redirects, sets a browser-like user
  agent to avoid trivial bot blocks)
- Parse via `BeautifulSoup`, extract text from `<main>`, `<article>`, or
  `<body>` — in that priority order
- Strip navigation, headers, footers, and script/style tags
- Truncate to first 2000 words for Claude context

**Content hash for change detection:**
- SHA-256 hash of the raw extracted text
- Stored in state — if hash matches previous run, skip this URL
- If hash differs, treat as modified

**`UrlRecord` shape:**
```python
@dataclass
class UrlRecord:
    url: str              # Canonical URL (used as stable key in state)
    label: str | None     # User-supplied label from url_sources.json
    category: str | None  # User-supplied category hint
    hint: str | None      # User-supplied description hint
    page_title: str       # Extracted from <title> tag
    extracted_text: str   # Cleaned body text, truncated to 2000 words
    content_hash: str     # SHA-256 of extracted_text
    fetched_at: str       # ISO 8601 timestamp of fetch
```

**Functions:**
- `load_url_sources(path: str) → list[dict]` — reads `url_sources.json`
- `scrape(entry: dict) → UrlRecord | None` — fetches and parses one URL;
  returns `None` on fetch failure (logs error, continues to next URL)
- `diff_urls(records: list[UrlRecord], state: dict) → tuple[list, list]`
  — returns `(new_urls, changed_urls)` based on hash comparison

**Known scraping limitations (flag in demo):**
- Pages behind authentication, paywalls, or JavaScript rendering will not
  scrape correctly. `httpx` fetches static HTML only — no JS execution.
- Some government sites use aggressive bot protection. If a fetch returns a
  non-200 status or suspiciously short content, log a warning and skip.
- For the POC, assume all URLs in `url_sources.json` are publicly accessible
  static HTML pages.

---

### `inference.py`

Two sequential Claude API calls per changed item — shared by both the OneDrive
and URL flows. The inputs differ slightly between source types but the call
structure is identical.

#### Call 1 — Infer metadata from source content

**Input to Claude (OneDrive file):**
```
- filename
- folder path within watch folder
- MIME type
- last_modified timestamp
- created_by
- extracted text content (if available, else None)
```

**Input to Claude (external URL):**
```
- url
- page_title
- user-supplied label (if provided)
- user-supplied category hint (if provided)
- user-supplied description hint (if provided)
- extracted page text (truncated to 2000 words)
- fetched_at timestamp
```

**System prompt summary:**
You are a technical architecture governance assistant. Given information about
a guardrail source — either an internal document or an external web page —
infer structured metadata for an architecture guardrails library. Respond only
in JSON matching the provided schema. Do not invent information you cannot
reasonably infer — use null for uncertain fields.

**Output (parsed JSON, same structure for both source types):**
```json
{
  "title": "...",
  "category": "...",
  "subcategory": "...",
  "type": "diagram | document | spreadsheet | presentation | policy | standard | guideline | other",
  "description": "...",
  "tags": ["...", "..."],
  "suggested_owner": "...",
  "source_type": "onedrive | external_url"
}
```

Note: `type` is extended to include `policy`, `standard`, and `guideline` to
cover common external URL content that doesn't map to a file format.

#### Call 2 — Compare against repo and produce final metafile

**Input to Claude:**
```
- output from Call 1
- source_type flag ("onedrive" or "external_url")
- source-specific identifiers:
    OneDrive: item_id, web_url, last_modified, filename
    URL: url, content_hash, fetched_at
- full list of existing metafile summaries from GitHub repo
  (id, title, category, tags, description — one line per guardrail)
- flag: is this item new (not in state) or modified (in state, content changed)
```

**System prompt summary:**
You are a technical architecture governance assistant managing a guardrails
library. Given draft metadata and the existing guardrail registry, produce a
complete metafile. Assign the next available ID in the correct category
sequence. If this appears to be an update to an existing guardrail (match on
title similarity or stable identifier), set status to under-review and carry
forward the existing ID and changelog. Identify related guardrails by category
and tag overlap. Populate `source` block correctly based on source_type.
Respond only in JSON.

**Output:** complete metafile JSON (see schema below)

---

### `github_client.py`

Handles all GitHub API interactions via `PyGithub`.

**Functions:**
- `get_existing_metafiles(repo) → list[MetafileSummary]` — fetches all `.json`
  files under `guardrails/` and returns lightweight summaries for Claude context
- `create_pr(repo, metafile: dict, source_type: str, source_ref: dict, is_update: bool) → str`
  — creates branch, commits metafile, opens PR, returns PR URL

`source_ref` carries the display details for the PR body:
- OneDrive: `{filename, onedrive_path, onedrive_url}`
- URL: `{url, page_title, fetched_at}`

**PR branch naming:** `guardrail/add-{guardrail-id}` or
`guardrail/update-{guardrail-id}`

**PR title:** `[New] Add GUARD-INT-042: Approved Cloud Integration Patterns` or
`[Update] GUARD-INT-042: Approved Cloud Integration Patterns — v2.2`

**PR body (OneDrive source):**
```markdown
## Guardrail Metadata PR

**Source:** OneDrive file
**File:** cloud-integration-patterns.docx
**OneDrive path:** Guardrails/integration/cloud-integration-patterns.docx
**Change type:** New | Update

### Inferred Metadata
- **Category:** integration / cloud
- **Tags:** cloud, integration, api-gateway, event-driven
- **Description:** ...

### Reviewer Checklist
- [ ] Category and subcategory are correct
- [ ] Description accurately reflects the document
- [ ] Tags are appropriate
- [ ] Related guardrails are correctly identified
- [ ] Owner is correct
- [ ] Approve and merge to mark as approved
```

**PR body (external URL source):**
```markdown
## Guardrail Metadata PR

**Source:** External URL
**URL:** https://www.digital.govt.nz/standards/cloud-first-policy/
**Page title:** Cloud First Policy — Digital.govt.nz
**Fetched at:** 2025-11-14T10:23:00Z
**Change type:** New | Updated content detected (hash changed)

### Inferred Metadata
- **Category:** cloud / policy
- **Tags:** cloud, government, policy, mandatory
- **Description:** ...

### Reviewer Checklist
- [ ] URL is still the canonical source (check for newer versions or superseding policy)
- [ ] Category and subcategory are correct
- [ ] Description accurately reflects the current page content
- [ ] Tags are appropriate
- [ ] Related guardrails are correctly identified
- [ ] Owner is correct
- [ ] Approve and merge to mark as approved
```

---

### `state.py`

Reads and writes `.guardrails_state.json` — a flat dict with two top-level
keys: `onedrive` (keyed by OneDrive `item_id`) and `urls` (keyed by URL).

```json
{
  "onedrive": {
    "ABC123XYZ": {
      "item_id": "ABC123XYZ",
      "name": "cloud-integration-patterns.docx",
      "path": "Guardrails/integration/cloud-integration-patterns.docx",
      "last_modified": "2025-11-14T10:23:00Z",
      "guardrail_id": "GUARD-INT-042",
      "pr_url": "https://github.com/owner/repo/pull/7",
      "last_processed": "2025-11-14T10:45:00Z"
    }
  },
  "urls": {
    "https://www.digital.govt.nz/standards/cloud-first-policy/": {
      "url": "https://www.digital.govt.nz/standards/cloud-first-policy/",
      "content_hash": "a3f9c2b1...",
      "guardrail_id": "GUARD-CLOUD-001",
      "pr_url": "https://github.com/owner/repo/pull/9",
      "last_processed": "2025-11-14T11:02:00Z"
    }
  }
}
```

**Functions:**
- `load_state(path: str) → dict`
- `save_state(state: dict, path: str)`
- `diff_files(current: list[FileRecord], state: dict) → tuple[list, list]`
  — returns `(new_files, modified_files)` for OneDrive flow
- `diff_urls(current: list[UrlRecord], state: dict) → tuple[list, list]`
  — returns `(new_urls, changed_urls)` for URL flow; change detected via hash

A file is **new** if its `item_id` is not in `state["onedrive"]`.
A file is **modified** if its `item_id` is present and `last_modified` changed.
A URL is **new** if it is not in `state["urls"]`.
A URL is **changed** if its `content_hash` differs from the stored hash.

---

## Metafile Schema

Stored at `guardrails/schema/guardrail.schema.json` in the GitHub repo.
All metafiles are validated against this schema in CI (optional for POC, worth
adding as a GitHub Action).

The `source` block is the only field that differs between OneDrive and URL
guardrails. All other fields are identical.

**OneDrive-sourced guardrail:**
```json
{
  "id": "GUARD-INT-042",
  "title": "Approved Cloud Integration Patterns",
  "category": "integration",
  "subcategory": "cloud",
  "type": "diagram",
  "format": "drawio",
  "status": "draft | under-review | approved | deprecated",
  "version": "1.0",
  "owner": "platform-architecture-team",
  "approved_by": null,
  "approved_date": null,
  "review_due": null,
  "tags": ["cloud", "integration", "event-driven"],
  "description": "...",
  "source": {
    "type": "onedrive",
    "onedrive_item_id": "ABC123XYZ",
    "url": "https://onedrive.live.com/...",
    "filename": "cloud-integration-patterns.drawio",
    "last_modified": "2025-11-14T10:23:00Z"
  },
  "related_guardrails": ["GUARD-SEC-018"],
  "change_log": [
    {
      "version": "1.0",
      "date": "2025-11-14",
      "author": "bob.jones@example.com",
      "summary": "Initial draft — auto-generated by guardrails POC script"
    }
  ]
}
```

**External URL-sourced guardrail:**
```json
{
  "id": "GUARD-CLOUD-001",
  "title": "NZ Government Cloud First Policy",
  "category": "cloud",
  "subcategory": "policy",
  "type": "policy",
  "format": "web",
  "status": "draft | under-review | approved | deprecated",
  "version": "1.0",
  "owner": null,
  "approved_by": null,
  "approved_date": null,
  "review_due": null,
  "tags": ["cloud", "government", "policy", "mandatory"],
  "description": "All-of-government policy requiring agencies to adopt cloud services by default...",
  "source": {
    "type": "external_url",
    "url": "https://www.digital.govt.nz/standards/cloud-first-policy/",
    "page_title": "Cloud First Policy — Digital.govt.nz",
    "content_hash": "a3f9c2b1...",
    "last_fetched": "2025-11-14T10:23:00Z"
  },
  "related_guardrails": ["GUARD-CLOUD-002"],
  "change_log": [
    {
      "version": "1.0",
      "date": "2025-11-14",
      "author": "auto-generated",
      "summary": "Initial entry — scraped from digital.govt.nz"
    }
  ]
}
```

Note: for external URL guardrails, `owner` refers to the internal team
responsible for monitoring and actioning changes to the policy, not the
external author. It will often be null until manually set during PR review.

---

## ID Scheme

Format: `GUARD-{CATEGORY}-{THREE_DIGIT_SEQUENCE}`

Category prefixes (extend as needed):
- `INT` — integration
- `SEC` — security
- `API` — API design
- `DATA` — data
- `CLOUD` - cloud-provided services, platforms, and infrastructure
- `INFRA` — on-premise infrastructure
- `EXE` - work, project, design execution
- `GOV` - general governance
- `ARCH` — general architecture

Claude assigns the next available sequence number per category by inspecting
existing metafiles in the repo. Claude call 2 handles this.

---

## Error Handling

- **Auth failure** — clear message pointing to Azure app registration steps
- **OneDrive API error** — log and skip the file, continue processing others
- **Scrape failure (non-200, timeout, bot block)** — log URL and status, skip,
  do not update state (retries next run)
- **Scrape returns suspiciously short content (<200 words)** — log a warning,
  skip; likely a login wall or JS-rendered page
- **Claude API error** — log, skip item, do not update state (so it retries
  next run)
- **GitHub API error** — log, skip PR creation, do not update state
- **No changes detected (either flow)** — print "No changes detected since
  last run." and exit cleanly

---

## Known Limitations (to call out in demo)

1. **No content extraction for binary formats** — DrawIO, Visio, images get
   metadata inferred from filename and folder only. Quality is lower.
2. **State file is local** — if you run the script from a different machine,
   state is lost and everything looks new. For production, state would live in
   the repo or a database.
3. **No deletion handling** — if a file is deleted from OneDrive or removed
   from `url_sources.json`, the metafile in Git is not updated. Out of scope
   for POC.
4. **OneDrive URL stability** — web URLs can change if files are moved. The
   `item_id` in the metafile is the stable reference; the URL is for
   convenience only.
5. **Rate limits** — Microsoft Graph, Anthropic, and GitHub all have rate
   limits. Fine for a demo with a handful of files; not designed for bulk
   processing.
6. **URL scraping is static HTML only** — pages requiring authentication,
   JavaScript rendering, or that use aggressive bot protection will not scrape
   correctly. `httpx` fetches raw HTML; no browser simulation.
7. **External URL content changes are not diffed** — the script detects that
   a page changed (via hash) but does not show Claude or the reviewer *what*
   changed. The reviewer must visit the URL themselves to assess the change.
   For production, a proper text diff between the previous and current scrape
   would be more useful.
8. **`url_sources.json` is manually maintained** — adding a new external
   guardrail source requires editing this file and committing it. There is no
   discovery mechanism.

---

## Running the Script

```bash
# First time setup
uv venv
uv pip install -r requirements.txt
cp .env.example .env
# fill in .env values

# Run
python main.py

# Expected output
Authenticating with Microsoft Graph...

--- OneDrive flow ---
Scanning OneDrive folder: Guardrails/
Found 3 files. Comparing against state...
  NEW:      integration/cloud-integration-patterns.docx
  MODIFIED: security/identity-and-access.docx
  UNCHANGED: api/api-design-standards.docx

Processing: cloud-integration-patterns.docx
  Extracting text... done (1240 words)
  Inferring metadata (Claude call 1)... done
  Comparing against repo (Claude call 2)... done
  Assigned ID: GUARD-INT-042
  Creating GitHub PR... done
  PR: https://github.com/owner/repo/pull/7

Processing: identity-and-access.docx
  Extracting text... done (980 words)
  Inferring metadata (Claude call 1)... done
  Comparing against repo (Claude call 2)... done
  Matched existing guardrail: GUARD-SEC-018
  Creating GitHub PR... done
  PR: https://github.com/owner/repo/pull/8

--- URL flow ---
Loading url_sources.json... 2 URLs found.

Processing: https://www.digital.govt.nz/standards/cloud-first-policy/
  Scraping page... done (1840 words extracted)
  Content hash: a3f9c2b1... (NEW — not in state)
  Inferring metadata (Claude call 1)... done
  Comparing against repo (Claude call 2)... done
  Assigned ID: GUARD-CLOUD-001
  Creating GitHub PR... done
  PR: https://github.com/owner/repo/pull/9

Processing: https://example-standards-body.org/api-guidelines
  Scraping page... done (2000 words extracted)
  Content hash: 88bc14f2... (UNCHANGED — matches state)
  Skipping.

State saved. 3 PRs created. 2 items unchanged.
```

---

## Implementation Phases

### Phase 1 — Project scaffold, config, and state

Stand up the repo structure, dependency management, and the two modules with
no external API dependencies. Everything needed for subsequent phases to build
on.

**Todo:**
- [x] Create `guardrails/` and `guardrails/schema/` subfolders in this repo
      (add a `.gitkeep` so folders are tracked)
- [x] Write `guardrail.schema.json` in `guardrails/schema/` (matches metafile
      schema defined above)
- [x] Create `requirements.txt` with all dependencies
      listed under Prerequisites
- [x] Create `.env.example` with all keys stubbed out and comments
- [x] Add `.env`, `.graph_token_cache.json`, `.guardrails_state.json` to
      `.gitignore`
- [x] Implement `config.py` — load `.env`, validate required keys, raise clear
      errors on missing values
- [x] Implement `state.py` — `load_state`, `save_state`, `diff_files`,
      `diff_urls`
- [x] Write `url_sources.json` with the NZ govt URLs from the sample above
- [x] Smoke-test: run `config.py` and `state.py` in isolation with a stub
      `.env`

---

### Phase 2 — GitHub client

Implement the GitHub integration in isolation before wiring it to inference.
This lets you verify PR creation works end-to-end with a hardcoded test
metafile before Claude is involved.

**Todo:**
- [x] Implement `github_client.py`:
  - [x] `get_existing_metafiles(repo) → list[MetafileSummary]`
  - [x] `create_pr(repo, metafile, source_type, source_ref, is_update) → str`
- [x] Test manually: create a dummy metafile JSON and call `create_pr` — verify
      branch, commit, and PR appear correctly in the repo
- [x] Verify PR body formatting matches the templates above for both source
      types

---

### Phase 3 — Inference module (Claude API)

Implement `inference.py` in isolation, using stubbed inputs so you can iterate
on prompts without needing live OneDrive or URL data.

**Todo:**
- [x] Implement `inference.py`:
  - [x] `infer_metadata(source_input: dict) → dict` (Claude call 1)
  - [x] `produce_metafile(draft_metadata: dict, repo_summaries: list, ...) → dict`
        (Claude call 2)
- [x] Define system prompts for both calls (as module-level constants)
- [x] Add input type routing — Call 1 prompt varies slightly between
      `onedrive` and `external_url` source types
- [x] Test with stubbed OneDrive input (hardcoded FileRecord-like dict)
- [x] Test with stubbed URL input (hardcoded UrlRecord-like dict)
- [x] Verify output JSON parses cleanly and matches metafile schema

---

### Phase 4 — URL scraping flow (end-to-end)

Implement the full URL pipeline. This flow has no Microsoft auth dependency,
making it the easier end-to-end path to validate first.

**Todo:**
- [x] Implement `scraper.py`:
  - [x] `load_url_sources(path) → list[dict]`
  - [x] `scrape(entry) → UrlRecord | None`
  - [x] `diff_urls(records, state) → tuple[list, list]` *(delegated to
        `state.py` — consistent with OneDrive flow)*
- [x] Implement `main.py` URL flow section (stubs for OneDrive section)
- [x] Run end-to-end: `python main.py` with real `url_sources.json` and real
      credentials in `.env`
- [x] Verify: state file written correctly, PR created in GitHub with correct
      body and metafile JSON
- [x] Re-run: verify unchanged URLs are skipped, state is stable

---

### Phase 5 — OneDrive flow (end-to-end)

Add Microsoft Graph auth and the OneDrive pipeline. Depends on Azure app
registration being configured manually (see Prerequisites above).

**Todo:**
- [x] Implement `onedrive.py`:
  - [x] `authenticate() → GraphClient` (MSAL device code flow + token cache)
  - [x] `list_folder_recursive(client, folder_name) → list[FileRecord]`
  - [x] `download_file(client, item_id) → bytes`
- [x] Implement `extractor.py`:
  - [x] `extract_text(filename, content) → str | None`
  - [x] Handle `.docx`, `.md`, `.txt`, `.pdf`; return `None` for all others
- [x] Wire OneDrive flow into `main.py`
- [x] Test auth: run `python main.py` — complete device code flow, verify token
      is cached for subsequent runs
- [x] Test with at least one `.docx` file in OneDrive watch folder
- [x] Test with a `.drawio` or image file — verify graceful fallback (infers
      from filename only)
- [x] Verify full end-to-end: state update + PR creation

---

### Phase 6 — Integration and cleanup

Final pass to make the script demo-ready.

**Todo:**
- [x] Verify `main.py` orchestrates both flows correctly in sequence
- [x] Check all error handling paths fire correctly (auth failure, scrape
      failure, Claude error, GitHub error)
- [x] Ensure "No changes detected" case exits cleanly with a clear message
- [x] Review console output — matches the expected output format in
      "Running the Script" above
- [x] Add `.env.example` final review — all keys present, comments accurate
      (removed unused AZURE_REDIRECT_URI)
- [x] Final smoke test: fresh clone, fill `.env`, run `python main.py`,
      confirm PRs appear in repo