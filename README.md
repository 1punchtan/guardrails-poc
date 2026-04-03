# Architecture Guardrails Library — POC

A Python script that automates the intake and governance of architecture
guardrails into a Git-backed library.

> **Note:** This project — including its architecture, design decisions, and
> code — was developed with extensive assistance from
> [Claude](https://claude.ai) (Anthropic). The design conversations that
> produced this README, Plan MD files, and the overall approach are available on
> request.

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
│   ├── governance/
│   ├── security/
│   ├── cloud/
│   ├── api/
│   └── architecture/
├── docs-site/                        # Stage 4 — static site sources (generated)
│   ├── mkdocs.yml                    # Generated — do not edit manually
│   └── docs/
│       ├── index.md
│       ├── tags.md
│       └── guardrails/{category}/
├── .github/workflows/
│   ├── generate-guardrails-index.yml # Stage 2 — regenerates guardrails-index.json
│   └── generate-guardrails-docs.yml  # Stage 4 — builds and deploys static site
├── skills/
│   └── review-document-instruction.md  # Stage 2 — Claude reviewer system prompt
├── main.py                           # Entry point — run this
├── config.py                         # Config and .env loading
├── onedrive.py                       # Microsoft Graph / OneDrive interactions
├── extractor.py                      # Document text extraction
├── scraper.py                        # External URL scraping
├── inference.py                      # Claude API calls (two-stage)
├── github_client.py                  # GitHub API interactions
├── state.py                          # State tracking between runs
├── generate_index.py                 # Stage 2 — generates guardrails-index.json
├── generate_docs.py                  # Stage 4 — generates static site pages
├── guardrails-index.json             # Stage 2 — generated index for Claude reviews
├── url_sources.json                  # List of external URLs to track
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
  via Microsoft Graph. Register at [portal.azure.com](https://portal.azure.com) and enable `Files.Read.All` delegated permissions.
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
 
## Stages

This POC establishes the library and its intake pipeline. The metafile
structure and Git-based design are intentionally chosen to support the following
downstream use cases. Stages 1, 2, and 4 are complete; Stages 3 remains planned.
 
---
 
### Stage 2 (Completed) — Document review via Claude skill
 
**Use case:** An architect uploads a solution architecture document, access control policy, or guardrail proposal to a Claude Chat project. Claude assesses whether the document conflicts with any approved guardrails in the library.
 
**How it works:**
 
A Claude Chat project is configured with a system prompt (found in `review-document-instruction.md`) that defines the reviewer role, review scope, and output format. Each review conversation includes two attachments:
 
- `guardrails-index.json` — a flattened index of all approved guardrails, auto-generated from the metafiles in this repo
- The document under review
 
Claude reads both files, identifies which guardrails are relevant to the document's subject matter, and produces a structured review report.
 
**Review scope — conflict detection only:**
 
The reviewer flags where a document explicitly or implicitly contradicts an approved guardrail — for example, proposing a disallowed integration pattern, storing data outside approved regions, or using an excluded authentication mechanism. It does not flag gaps (topics the document omits). Uncertain findings are surfaced with reasoning rather than suppressed.
 
**Output format:**
 
- A short summary: what the document covers, which guardrail categories were assessed, and the overall finding
- A findings table: one row per assessed guardrail, with finding (Satisfied / Conflict / Uncertain / Not applicable), severity, evidence from the document, and reviewer notes
 
**Components:**
 
- `generate_index.py` — reads all metafiles from `guardrails/**/*.json` and writes `guardrails-index.json` to the repo root. Excludes draft and deprecated guardrails.
- `.github/workflows/generate-guardrails-index.yml` — GitHub Action that runs `generate_index.py` and auto-commits the updated index on every merge to `main` that touches `guardrails/**`.
- `skills/review-document-instruction.md` — the system prompt for the Claude Chat project.
 
**Workflow:**
 
1. Merge a guardrail PR — the GitHub Action regenerates `guardrails-index.json` automatically
2. Copy the updated index content into the Claude Chat project instructions (one manual step — no API available to automate this on personal Claude.ai)
3. Start a review conversation: attach `guardrails-index.json` and the document, ask for a review
 
**Why the metafile design supports this:**
 
The `status` field limits the index to approved guardrails only. The `category` and `tags` fields let Claude scope its assessment to relevant guardrails rather than checking everything. The `description` field carries enough context for a confident assessment in most cases; where it doesn't, Claude surfaces this in the findings and directs the reviewer to the source document.
 
**Scope note:** This works best for documents that make explicit architectural decisions in prose. It is not a substitute for policy-as-code tooling or static analysis for fine-grained configuration enforcement.
 
---
 
### Stage 3 — Code review via Claude Code or Claude skill
 
**Use case:** A developer runs Claude Code against a codebase, or a Claude
skill is invoked during a code review. Claude checks whether the implementation
violates approved architecture guardrails — for example, using a disallowed
integration pattern, bypassing a mandated authentication flow, or storing data
outside approved regions.
 
**How it works:**
 
The mechanism is the same as Stage 2 — a skill retrieves relevant approved
guardrails and provides them as context. Claude then reasons over the code
being reviewed against those guardrails.
 
In Claude Code, this could be triggered explicitly ("review this file against
our guardrails") or wired into a broader review workflow. In a CI/CD context,
it could run as a step on pull requests against specific paths or file types.
 
**What Claude can and cannot do here:**
 
Claude can assess whether code *satisfies the intent* of an architectural
guardrail — for example, confirming that an integration uses the approved
async messaging pattern rather than direct synchronous calls, or that secrets
are retrieved from the approved vault rather than hardcoded. This is
higher-level reasoning about architectural decisions in code.
 
Claude is not a linter. It will not reliably catch every instance of a
specific low-level code pattern at scale. For fine-grained, rule-based
enforcement (e.g. "never use library X", "all database calls must go through
this abstraction"), dedicated static analysis tools are more appropriate and
should complement rather than replace this approach.
 
**Why the metafile design supports this:**
 
The `related_guardrails` links and `tags` allow the skill to traverse the
guardrail graph — if a guardrail references a security standard, the skill
can pull that in too. The `source` block provides a direct link to the full
guardrail document so Claude can fetch and read it if the description alone
is insufficient context for a nuanced assessment.
 
---
 
### Stage 4 (Completed) — Static site for human browsing

**Use case:** A searchable, browsable website generated directly from the
metafiles in the Git repo. Architects, engineers, and other stakeholders can
browse guardrails by category, filter by tags, and click through to the source
documents — without needing access to GitHub.

**Live site:** https://1punchtan.github.io/guardrails-poc/

**How it works:**

`generate_docs.py` reads all approved and under-review guardrail metafiles and
writes Markdown pages into `docs-site/docs/`. MkDocs Material builds the static
HTML. A GitHub Action runs both steps automatically on every merge to `main`
that touches `guardrails/**` or `generate_docs.py`, then deploys to GitHub Pages.

**Components:**

- `generate_docs.py` — reads `guardrails/**/*.json`, writes all pages and
  regenerates `docs-site/mkdocs.yml` (including the full nav) from scratch on
  every run. No manual steps after merging a guardrail PR.
- `docs-site/` — generated Markdown sources committed to `main` for
  traceability. `docs-site/mkdocs.yml` is also generated — do not edit manually.
- `.github/workflows/generate-guardrails-docs.yml` — GitHub Action that
  runs the generator, builds the site, commits the updated `docs-site/` back
  to `main`, and deploys the HTML to the `gh-pages` branch.

**Repository structure (Stage 4 additions):**

```
guardrails-poc/
├── generate_docs.py                        # Generator script
├── docs-site/
│   ├── mkdocs.yml                          # Generated — do not edit
│   └── docs/
│       ├── index.md                        # Home page: stats + recently updated
│       ├── tags.md                         # Tag index (rendered by MkDocs Material)
│       └── guardrails/
│           ├── governance/
│           │   ├── index.md                # Category overview
│           │   ├── GUARD-GOV-001.md
│           │   └── ...
│           ├── security/
│           ├── cloud/
│           ├── api/
│           └── architecture/
└── .github/workflows/
    └── generate-guardrails-docs.yml        # CI/CD pipeline
```

**Each guardrail page includes:**

- Status badge and version
- Metadata table (ID, category, subcategory, type, owner, approval dates)
- Full description
- Source document link (OneDrive filename or external URL page title)
- Related guardrails as cross-links
- Change history table

**Site navigation:**

Navigation tabs are generated dynamically by category. The tag index
(`/tags`) is built automatically from tags in each page's YAML frontmatter.
Full-text search requires no backend — the index is built at deploy time.

**CI pipeline:**

Triggers on push to `main` when `guardrails/**` or `generate_docs.py` changes.
The full generate → build → deploy cycle completes in under a minute.

```
generate_docs.py  →  mkdocs build  →  commit docs-site/ to main  →  deploy to gh-pages
```

For a corporate deployment, replace the GitHub Pages deploy step with a push
to Azure Static Web Apps. The rest of the pipeline is identical.

**Why the metafile design supports this:**

The structured JSON schema means `generate_docs.py` needs no special-case
parsing logic — fields are present and typed consistently. The
`related_guardrails` array generates cross-links between pages automatically.
The `status` field drives visual indicators. Tags feed directly into MkDocs
Material's built-in tag index. The `source` block provides the correct link
regardless of whether the guardrail came from OneDrive or an external URL.
 
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