# Architecture Guardrails Library — POC

A Python script that automates the intake and governance of architecture
guardrails into a Git-backed library.

---

## What this is

Architecture guardrails are the standards, policies, patterns, and rules that
guide how your organisation builds and integrates systems. This POC manages
them as a governed library — searchable, versioned, and auditable.

The library has two layers:

- **Metafiles in Git** — one JSON file per guardrail, stored in a GitHub repo.
  These are the source of truth for classification, versioning, ownership, and
  approval status. Git history is the audit trail.
- **Actual documents** — stored wherever they naturally live (OneDrive,
  external websites). The metafile points to them; Git doesn't store them.

This script is the intake pipeline. It detects new and changed guardrail
sources, asks Claude to infer the metadata, and opens a GitHub pull request
for human review. Merging the PR is the approval action.

---

## What the script does

It handles two types of guardrail sources:

**OneDrive files**
Watches a designated OneDrive folder (and all subfolders) for new or modified
documents — Word files, PDFs, DrawIO diagrams, etc. When something changes,
it extracts what text it can, sends the content to Claude for metadata
inference, and opens a PR in the GitHub repo with a draft metafile.

**External URLs**
Reads a list of URLs from `url_sources.json` — government policies, standards
body guidelines, vendor documentation, etc. It scrapes each page, hashes the
content to detect changes since the last run, and runs the same inference and
PR pipeline as the OneDrive flow.

In both cases, a human reviews the PR, adjusts any inferred metadata as
needed, and merges to approve.

---

## Why Git as the governance layer

A document management system or SharePoint list can store metadata, but it
can't give you a meaningful audit trail, a structured review process, or
machine-readable access without extra tooling. Git gives you all three for
free:

- Every change to a metafile is a commit — who, what, when
- Pull requests are the review and approval gate
- The repo is directly consumable by RAG pipelines, LLMs, and internal APIs

The actual guardrail documents don't need to move. They stay in OneDrive,
SharePoint, Confluence, or wherever they already live. The metafile just
points to them.

---

## Intended audience for this POC

This is a demo for a technical audience. It is scoped to prove the concept
using personal SaaS accounts (personal OneDrive, personal GitHub) before
any corporate tooling is involved. The underlying logic maps directly to a
production implementation using SharePoint, Azure DevOps, and Power Automate.

---

## Repository structure

```
guardrails-poc/
├── guardrails/
│   ├── schema/
│   │   └── guardrail.schema.json     # JSON schema for metafile validation
│   ├── integration/
│   │   └── cloud-integration-patterns.json
│   ├── security/
│   │   └── identity-and-access.json
│   └── ...
├── main.py                           # Entry point — run this
├── config.py                         # Config and .env loading
├── onedrive.py                       # Microsoft Graph / OneDrive interactions
├── extractor.py                      # Document text extraction
├── scraper.py                        # External URL scraping
├── inference.py                      # Claude API calls (two-stage)
├── github_client.py                  # GitHub API interactions
├── state.py                          # State tracking between runs
├── url_sources.json                  # List of external URLs to track
├── claude-plan1.md                   # Full implementation plan
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- `uv` (recommended) or `pip` with `venv`
- An Azure app registration (personal account) — needed for OneDrive access
  via Microsoft Graph. Register at [portal.azure.com](https://portal.azure.com),
  add `http://localhost:8400` as a redirect URI, and enable the
  `Files.Read.All` delegated permission.
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- A GitHub personal access token with `repo` scope

### 2. Install dependencies

```bash
uv venv
uv pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env and fill in all values
```

### 4. GitHub repo

This repo (`guardrails-poc`) is the target for the POC. The `guardrails/`
and `guardrails/schema/` folders already exist and contain
`guardrail.schema.json`. No setup needed.

The script will create category subfolders and metafiles automatically via
the GitHub API as PRs are merged.

### 5. Add URLs to track (optional)

Edit `url_sources.json` to add any external URLs you want the script to
monitor:

```json
[
  {
    "url": "https://www.digital.govt.nz/standards-and-guidance/technology-and-architecture/cloud-services/cloud-adoption-policy-and-strategy",
    "label": "Cloud Adoption Policy and Strategy",
    "category": "cloud",
    "hint": "NZ Government Cloud First Policy — mandates cloud-first approach for agency IT investment"
  }
]
```

`label`, `category`, and `hint` are optional but improve Claude's inference
quality for pages with sparse or ambiguous content.

---

## Running the script

```bash
python main.py
```

On first run, the script will prompt you to authenticate with Microsoft via
a device code (open a URL, enter a short code). The token is cached locally
so subsequent runs don't require re-authentication.

The script processes both the OneDrive folder and the URL list in sequence,
then prints a summary of what was processed and which PRs were created.

---

## The approval workflow

Each detected change produces one GitHub PR containing a draft metafile.
The PR body includes:

- Source details (file path or URL)
- Claude's inferred metadata (category, tags, description, related guardrails)
- A reviewer checklist

The reviewer adjusts any incorrect fields directly in the PR, then merges.
Merging is the approval action — the merge commit records who approved and
when.

---

## Known limitations

This is a POC. The following are known gaps, intentionally out of scope for
the demo:

1. **Binary file content** — DrawIO, Visio, and image files can't have text
   extracted. Claude infers metadata from filename and folder path only.
2. **Local state file** — change tracking state is stored locally. Running
   from a different machine resets it.
3. **No deletion handling** — removing a file from OneDrive or a URL from
   `url_sources.json` does not update or deprecate the corresponding metafile
   in Git.
4. **Static HTML scraping only** — URLs requiring authentication, JavaScript
   rendering, or that use bot protection will not scrape correctly.
5. **No content diff for URLs** — the script detects that a page changed but
   does not show what changed. Reviewers must visit the URL directly.
6. **Rate limits** — not designed for bulk processing. Fine for a demo with
   a small number of files and URLs.
7. **`url_sources.json` is manually maintained** — no discovery mechanism for
   new external sources.
8. **OneDrive URL stability** — the `item_id` is the stable identifier; the
   URL in the metafile is for convenience and may break if files are moved.