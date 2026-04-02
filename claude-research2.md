# Guardrails POC — Research Report

_Compiled: 2026-04-02_

---

## 1. What This Project Is

A Python automation tool that builds and governs an **architecture guardrails library** stored as JSON metafiles in a GitHub repository. The core idea: architecture guardrails (standards, policies, patterns) live as documents in OneDrive or as external web pages (e.g. NZ Government standards), and this script automates their discovery, metadata extraction, and governance intake via GitHub pull requests.

The script is manually invoked (`python main.py`). It is not a daemon or scheduler — every run is a fresh scan-and-act cycle.

---

## 2. High-Level Architecture

```
Two source types → shared inference pipeline → GitHub PR
```

```
OneDrive folder ──► extractor.py ──┐
                                    ├──► inference.py (2-stage Claude) ──► github_client.py ──► PR
External URLs ───► scraper.py ─────┘
```

**State tracking** (`state.py`) persists what has already been processed between runs, keyed by OneDrive `item_id` (stable across renames) and URL. The state file lives locally (`.guardrails_state.json`) — a known limitation for production use.

**Approval workflow**: merging the PR is the approval action. Git history is the audit trail. The actual documents stay where they are; the metafile only points to them.

---

## 3. Module-by-Module Analysis

### `main.py` — Orchestrator

Runs both flows sequentially in a single invocation. Key details:

- **OneDrive flow**: authenticates, lists folder recursively, diffs against state, downloads and extracts each changed file, runs 2-stage Claude inference, creates PR, updates state.
- **URL flow**: scrapes all URLs in `url_sources.json` using a single shared Playwright browser context, diffs hashes against state, runs the same 2-stage inference, creates PRs.
- `repo_summaries` is fetched once from GitHub and shared across both flows (and extended in-memory as new guardrails are processed in the same run — so Claude Call 2 for later items can see earlier items from the same run).
- **Graceful degradation**: if OneDrive auth fails, it logs and skips the OneDrive flow entirely, continuing with the URL flow.
- State is saved only after all processing is complete — if the script crashes mid-run, no partial state is persisted.

### `config.py` — Configuration

Loads `.env` via `python-dotenv`. Validates all required keys at startup (fail-fast before any API calls). Supports an optional `CLAUDE_MODEL` override (defaults to `claude-sonnet-4-6`). Masks `anthropic_api_key` and `github_token` in printed output.

### `state.py` — State Tracking

Manages `.guardrails_state.json` — a flat dict with three top-level keys:
- `onedrive` — keyed by `item_id`
- `urls` — keyed by URL
- `scrape_failures` — keyed by URL (added post-plan, not in original design)

The `scrape_failures` section is a pragmatic addition: if a URL scrape fails, the failure reason and timestamp are persisted. On a successful re-scrape, the failure entry is cleared. This makes it possible to inspect what failed without re-running.

Both data classes (`FileRecord`, `UrlRecord`) are defined here and imported by other modules — a slightly unusual placement (they'd more naturally live in `onedrive.py` and `scraper.py` respectively) but keeps the data model in one place.

### `onedrive.py` — Microsoft Graph / OneDrive

Implements a lightweight `OneDriveClient` wrapper using raw **httpx** (not the `msgraph-sdk` package). Note: `msgraph-sdk` appears in `requirements.txt` but is not actually used in the code.

Auth uses **MSAL device code flow** — on first run, the user opens a browser URL and enters a short code. The token is cached in `.graph_token_cache.json` for subsequent runs.

`list_folder_recursive` handles pagination via `@odata.nextLink` — important for large folders. Files and folders are distinguished by presence of `"file"` or `"folder"` keys in the Graph API response.

Stable identifier used throughout is `item_id` (OneDrive's internal ID), not the filename or path — both of which can change if files are renamed or moved.

### `extractor.py` — Document Text Extraction

Handles text extraction from OneDrive files. Only runs on the OneDrive path — URL content extraction is separate (handled by `scraper.py`).

| Extension | Method | Truncation |
|---|---|---|
| `.md`, `.txt` | UTF-8 decode | 1500 words |
| `.docx` | `python-docx` | 1500 words |
| `.pdf` | `pypdf` | 1500 words |
| Everything else (`.drawio`, `.vsdx`, images, etc.) | `None` | — |

When extraction returns `None`, Claude infers metadata from filename and folder path alone — quality is expectedly lower for binary formats.

### `scraper.py` — External URL Fetching

**Key deviation from plan**: the original plan specified `httpx` for URL scraping. The implementation uses **Playwright** (headless Chromium) instead. The commit message confirms this was a deliberate change: _"Change from httpx to playwright"_.

This was a meaningful upgrade: Playwright renders JavaScript, handles redirects robustly, and avoids many bot-protection false positives that a plain HTTP client would trigger. A single browser instance is shared across all URL scrapes in a run via the `browser_context()` context manager (avoids per-URL startup overhead).

Scraping pipeline:
1. Navigate to URL, wait for `networkidle` (30s timeout)
2. Parse rendered HTML with BeautifulSoup
3. Strip `nav`, `header`, `footer`, `script`, `style`, `aside` tags
4. Extract from `<main>` → `<article>` → `<body>` (priority order)
5. Minimum 200 words required — below this, treat as a failure (login wall, JS-only content)
6. Truncate to 2000 words for Claude context
7. SHA-256 hash of extracted text for change detection

`scrape()` returns `(UrlRecord, None)` on success and `(None, reason_string)` on failure, allowing the caller to record the failure in state.

### `inference.py` — Claude API (Two-Stage)

The core intelligence of the pipeline. Two sequential Claude API calls per changed item.

**Call 1 — `infer_metadata()`**

Infers draft metadata (title, category, subcategory, type, description, tags, suggested_owner) from the source content. The user message is built differently for `onedrive` vs `external_url` sources but both use the same system prompt (`SYSTEM_CALL1`). Output is parsed JSON with `_extract_json()`, which tolerates markdown fencing in Claude's response.

**Call 2 — `produce_metafile()`**

Takes Call 1's output plus the existing repo's guardrail summaries and produces the complete metafile JSON. Responsibilities:
- Assign next available ID in the category sequence
- Detect if this is an update to an existing guardrail (by title similarity or stable identifier)
- Link related guardrails (up to 3, by category and tag overlap)
- Set status: `draft` for new, `under-review` for updates
- Populate the correct `source` block based on `source_type`

`_extract_json()` is a resilient parser that strips markdown fences (`\`\`\`json ... \`\`\``) and finds the outermost `{...}` in case Claude adds explanatory text despite the instruction not to.

The module contains a standalone smoke test (`python inference.py`) that exercises both source types against a simulated existing repo, and validates required fields in the output.

### `github_client.py` — GitHub API

Uses `PyGithub`. Key behaviours:

- **`get_existing_metafiles()`** — walks `guardrails/` recursively via GitHub's contents API, excludes `guardrail.schema.json`, returns lightweight summaries for Claude context.
- **`create_pr()`** — creates branch from `main` SHA, commits metafile at `guardrails/{category}/{id}.json`, opens PR with structured body including a reviewer checklist.
- Branch naming: `guardrail/add-{id}` or `guardrail/update-{id}` (slugified).
- PR title format: `[New] Add GUARD-XYZ-001: Title` or `[Update] GUARD-XYZ-001: Title — v2.0`.
- Handles the update case: checks if the metafile already exists on the branch before deciding between `create_file` and `update_file`.

---

## 4. The Metafile Schema

Defined in [guardrails/schema/guardrail.schema.json](guardrails/schema/guardrail.schema.json). JSON Schema draft-07, `additionalProperties: false` (strict).

**Required fields:** `id`, `title`, `category`, `type`, `status`, `version`, `description`, `source`, `change_log`

**ID format:** `GUARD-{CATEGORY_PREFIX}-{THREE_DIGIT_SEQUENCE}` — enforced by regex pattern `^GUARD-[A-Z]+-[0-9]{3}$`

**Status lifecycle:** `draft` → `under-review` → `approved` → `deprecated`

**Source block (discriminated union):**
- `onedrive` source requires: `type`, `onedrive_item_id`, `url`, `filename`, `last_modified`
- `external_url` source requires: `type`, `url`, `page_title`, `content_hash`, `last_fetched`

**Category prefixes:** `INT`, `SEC`, `API`, `DATA`, `CLOUD`, `INFRA`, `EXE`, `GOV`, `ARCH`

---

## 5. Current State of the Library

As of the last commit, the library contains **49 approved/draft metafiles** across five categories:

| Category | Count | Source types |
|---|---|---|
| `governance` | 35 | OneDrive PDFs + external URLs |
| `security` | 7 | OneDrive PDFs + external URLs |
| `api` | 4 | External URLs |
| `cloud` | 2 | External URLs |
| `architecture` | 1 | External URL |

All 49 are `status: "draft"` — no guardrails have been promoted to `approved` yet. This is expected for a POC; promotion happens when a reviewer merges a PR and then (separately, manually) updates the status field.

**Seeded NZ Government sources in `url_sources.json` (11 URLs):**
- Digital.govt.nz: Cloud Adoption Policy, API Guidelines, Digital Service Design Standard, Web Standards, DPUP, Information Sharing Standard, Government Digital Standards Catalogue
- Data.govt.nz: Data and Information Management Principles
- Protective Security Requirements (protectivesecurity.govt.nz): Information Security
- NZISM (nzism.gcsb.govt.nz): NZ Information Security Manual
- docref.digital.govt.nz: NZ API Standard (Draft)

---

## 6. Schema Inconsistencies in Actual Metafiles

Inspection of the generated metafiles reveals two recurring deviations from the schema:

**1. `changelog` vs `change_log`** — The schema mandates `change_log` (with underscore). Some Claude-generated metafiles use `changelog` (no underscore). Examples:
- [GUARD-API-001.json](guardrails/api/GUARD-API-001.json) — uses `changelog`
- [GUARD-SEC-001.json](guardrails/security/GUARD-SEC-001.json) — uses `changelog`
- [GUARD-CLOUD-001.json](guardrails/cloud/GUARD-CLOUD-001.json) — uses `changelog`
- [GUARD-GOV-001.json](guardrails/governance/GUARD-GOV-001.json) — uses `change_log` (correct)

**2. `change` vs `summary` in change log entries** — The schema requires `summary` in each changelog item. Some generated entries use `change` instead. Examples:
- GUARD-SEC-001 and GUARD-CLOUD-001 use `"change": "..."` rather than `"summary": "..."`

These inconsistencies indicate Claude's Call 2 output occasionally drifts from the exact schema, despite the system prompt instructing JSON-only output and the schema being embedded in `SYSTEM_CALL2`. The `_extract_json()` parser does not validate against the schema — it only checks that valid JSON was returned. There is no CI schema validation in place yet (the plan noted this as "optional for POC, worth adding").

---

## 7. Notable Deviations from the Original Plan

| Area | Plan | Actual |
|---|---|---|
| URL scraping | `httpx` + BeautifulSoup | Playwright + BeautifulSoup (upgraded for JS rendering) |
| `msgraph-sdk` | Used for Graph API | Present in requirements.txt but unused; `onedrive.py` uses raw `httpx` calls instead |
| `scrape_failures` in state | Not specified | Added: records failure reason and timestamp per URL |
| `diff_urls` location | Plan specified both `scraper.py` and `state.py` | Implemented only in `state.py`; `scraper.py` only handles fetch/parse |
| `httpx` in requirements | Listed under prerequisites | Absent from `requirements.txt` (replaced by `playwright`); still used by `onedrive.py` — **potential missing dependency** |

**The httpx gap**: `requirements.txt` lists `playwright` but not `httpx`. However, `onedrive.py` imports `httpx` directly. A fresh install from `requirements.txt` would be missing `httpx` for the OneDrive flow. This is likely a residual inconsistency from the scraper migration.

---

## 8. Implementation Phases (All Complete)

Per `claude-plan1.md`, all 6 phases are marked complete:

- **Phase 1** — Scaffold, config, state: schema, requirements, `.env.example`, `config.py`, `state.py`, `url_sources.json`
- **Phase 2** — GitHub client: `github_client.py`, PR creation tested with dummy metafile
- **Phase 3** — Inference: `inference.py`, both source types tested with stubs
- **Phase 4** — URL scraping end-to-end: `scraper.py`, URL flow in `main.py`, verified with real credentials
- **Phase 5** — OneDrive end-to-end: `onedrive.py`, `extractor.py`, OneDrive flow in `main.py`, tested with real files
- **Phase 6** — Integration and cleanup: full orchestration, error handling, console output format, final smoke test

---

## 9. Future Stages (Not Yet Implemented)

Three downstream use cases are designed for but not built:

### Stage 2 — Document Review via Claude Skill

A Claude skill that retrieves approved guardrails from the repo and assesses whether a submitted architecture document (solution design, access control policy, new guardrail proposal) conflicts with any of them.

Mechanism: reads metafiles via GitHub API or pre-built index, filters to `status: approved`, narrows by category/tags relevant to the document type, passes filtered guardrails + document to Claude, returns structured review report (satisfied / violated / uncertain per guardrail).

Design dependency: the `status`, `category`, `tags`, and `description` fields in the metafile schema are specifically chosen to make this filtering efficient and token-economical.

### Stage 3 — Code Review via Claude Code or Claude Skill

Extends Stage 2 to code review. A Claude Code invocation or CI step retrieves relevant approved guardrails and assesses whether a codebase violates architectural intent — e.g. wrong integration pattern, bypassed auth flow, data stored in unapproved region.

**Explicit limitation noted in README:** Claude can assess architectural intent (higher-level reasoning), not replace a linter for fine-grained rule enforcement. For rule-based checks, dedicated static analysis tools are recommended alongside this approach.

Design dependency: `related_guardrails` links and `tags` allow graph traversal across related standards. `source` block provides direct links to full documents if description alone is insufficient.

### Stage 4 — Static Site for Human Browsing

A MkDocs Material-based website generated from the metafiles — searchable, browsable by category/status/tags, with no GitHub access required for end users.

Proposed tooling: MkDocs Material (Python-based, consistent with the rest of the stack), deployed to GitHub Pages or Azure Static Web Apps. A `generate_docs.py` script reads each metafile and writes a corresponding Markdown page before the MkDocs build. CI pipeline runs on every merge to `main`: generate docs → build site → deploy.

The consistent JSON schema makes `generate_docs.py` straightforward — no parsing ambiguity, schema validation catches bad data at PR time.

---

## 10. Known Limitations (from the Plan and Observations)

1. **Binary file content** — DrawIO, Visio, images: metadata inferred from filename/folder only. Lower quality.
2. **Local state file** — Running from a different machine resets state (everything looks new). Production should store state in the repo or a database.
3. **No deletion handling** — Removing a file from OneDrive or a URL from `url_sources.json` does not deprecate the corresponding metafile.
4. **OneDrive URL stability** — `web_url` in the metafile can break if files are moved. `onedrive_item_id` is the stable reference.
5. **No content diff for URL changes** — The script detects that content changed (hash differs) but doesn't show what changed. Reviewers must visit the URL directly.
6. **Rate limits** — Not designed for bulk processing.
7. **`url_sources.json` is manually maintained** — No discovery mechanism.
8. **Schema inconsistency in generated metafiles** — Claude occasionally generates `changelog` instead of `change_log`, and `change` instead of `summary` in log entries. No CI schema validation catches these.
9. **`httpx` missing from requirements.txt** — Used by `onedrive.py` but absent from `requirements.txt` after the scraper migration to Playwright.
10. **`msgraph-sdk` unused** — Listed in `requirements.txt` but never imported; dead dependency.
11. **No approved guardrails** — All 49 existing metafiles are `status: draft`. Stage 2 and 3 use cases require `status: approved` guardrails to be useful.

---

## 11. Production Mapping

The README explicitly notes this POC uses personal SaaS accounts (personal OneDrive, personal GitHub) as a demo before corporate tooling is involved. The direct production equivalents are:

| POC | Production |
|---|---|
| Personal OneDrive | SharePoint / OneDrive for Business |
| Personal GitHub + PAT | Azure DevOps + service principal |
| Manual `python main.py` | Power Automate trigger or Azure Function |
| GitHub Pages (Stage 4) | Azure Static Web Apps |

---

## 12. Summary Assessment

The POC successfully demonstrates the core concept: automated metadata inference from two document sources (internal OneDrive files and external web pages) using Claude, with Git-backed governance via GitHub pull requests. All 6 implementation phases are complete and the library has 49 draft guardrails seeded from real NZ Government sources.

The design is intentionally minimal — no scheduler, no web UI, no background process — which makes it easy to reason about and demo. The two-stage Claude inference (draft metadata → complete metafile with ID assignment and cross-linking) is the most technically interesting part, and works well enough for the POC's purposes.

The main gaps before this is production-ready are: state centralisation, deletion handling, CI schema validation, the `httpx`/`msgraph-sdk` dependency tidying, and — most importantly — getting guardrails promoted from `draft` to `approved` so Stages 2 and 3 have something to work with.
