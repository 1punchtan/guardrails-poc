"""
generate_docs.py

Reads all guardrail metafiles from guardrails/**/*.json and generates a
MkDocs Material static site under docs-site/.

Outputs:
  docs-site/mkdocs.yml                              (full site config + nav)
  docs-site/docs/index.md                           (home page)
  docs-site/docs/guardrails/{category}/index.md     (category overview)
  docs-site/docs/guardrails/{category}/{ID}.md      (individual guardrail page)

Run: python generate_docs.py
"""

import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

GUARDRAILS_DIR = Path("guardrails")
SCHEMA_DIR = GUARDRAILS_DIR / "schema"
DOCS_SITE_DIR = Path("docs-site")
DOCS_DIR = DOCS_SITE_DIR / "docs"
MKDOCS_YML = DOCS_SITE_DIR / "mkdocs.yml"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INCLUDE_STATUSES = {"approved", "under-review"}

# Canonical order for site navigation and tables.
# Unknown categories (if any are added later) are appended alphabetically.
CATEGORY_ORDER = ["governance", "security", "cloud", "api", "architecture"]

CATEGORY_DISPLAY = {
    "governance": "Governance",
    "security": "Security",
    "cloud": "Cloud",
    "api": "API",
    "architecture": "Architecture",
}

STATUS_BADGE = {
    "approved": "✅ Approved",
    "under-review": "⏳ Under Review",
    "deprecated": "❌ Deprecated",
    "draft": "📝 Draft",
}

NAV_TITLE_MAX = 60  # chars before truncation with …

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_date(date_str: str | None) -> date:
    """Parse a date or datetime string, returning date.min on failure."""
    if not date_str:
        return date.min
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return date.min


def get_owner(data: dict) -> str | None:
    """Return owner, falling back to suggested_owner (schema drift)."""
    return data.get("owner") or data.get("suggested_owner") or None


def get_change_log(data: dict) -> list:
    """Return the change log list, handling change_log/changelog key variants."""
    return data.get("change_log") or data.get("changelog") or []


def get_entry_summary(entry: dict) -> str:
    """Return the summary from a change log entry, handling all key variants."""
    return (
        entry.get("summary")
        or entry.get("change")
        or entry.get("change_summary")
        or ""
    )


def nav_title(guardrail_id: str, title: str) -> str:
    full = f"{guardrail_id} — {title}"
    if len(full) > NAV_TITLE_MAX:
        return full[: NAV_TITLE_MAX - 1] + "…"
    return full


def fmt_date(date_str: str | None) -> str:
    return str(date_str)[:10] if date_str else "—"


def last_updated_date(data: dict) -> date:
    """Best available 'last updated' date for sorting."""
    if data.get("approved_date"):
        return parse_date(data["approved_date"])
    change_log = get_change_log(data)
    if change_log:
        return max(parse_date(e.get("date")) for e in change_log)
    return date.min


def escape_pipe(text: str) -> str:
    return text.replace("|", "&#124;")


# ---------------------------------------------------------------------------
# Step 1 — Load and filter metafiles
# ---------------------------------------------------------------------------


def load_metafiles() -> tuple[list[dict], dict[str, tuple[str, str]], list[tuple]]:
    """
    Returns:
      guardrails   — list of dicts for all included metafiles
      id_to_info   — {id: (category, title)} for cross-link resolution
      skipped      — [(path, reason)] for excluded files
    """
    guardrails = []
    skipped = []

    for path in sorted(GUARDRAILS_DIR.rglob("*.json")):
        if path.is_relative_to(SCHEMA_DIR):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            skipped.append((str(path), f"JSON parse error: {e}"))
            continue

        status = data.get("status", "").lower()
        if status not in INCLUDE_STATUSES:
            skipped.append((str(path), f"Skipped status: {status!r}"))
            continue

        guardrails.append(data)

    id_to_info = {g["id"]: (g["category"], g["title"]) for g in guardrails}
    return guardrails, id_to_info, skipped


# ---------------------------------------------------------------------------
# Step 2 — Clear output directory
# ---------------------------------------------------------------------------


def clear_docs_dir() -> None:
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_DIR.mkdir(parents=True)


# ---------------------------------------------------------------------------
# Step 3 — Per-guardrail pages
# ---------------------------------------------------------------------------


def render_guardrail_page(data: dict, id_to_info: dict) -> str:
    gid = data["id"]
    title = data["title"]
    category = data["category"]
    status = data.get("status", "")
    version = data.get("version", "—")
    subcategory = data.get("subcategory") or "—"
    gtype = data.get("type", "—")
    owner = get_owner(data) or "—"
    approved_by = data.get("approved_by") or "—"
    approved_date = fmt_date(data.get("approved_date"))
    review_due = fmt_date(data.get("review_due"))
    description = data.get("description", "")
    tags = data.get("tags") or []
    related = data.get("related_guardrails") or []
    source = data.get("source", {})
    change_log = get_change_log(data)

    lines = []

    # Frontmatter
    lines += ["---"]
    lines.append(f'title: "{gid} — {title.replace(chr(34), chr(39))}"')
    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append(f"  - {tag}")
    lines += ["---", ""]

    # H1
    lines += [f"# {title}", ""]

    # Status + version inline
    badge = STATUS_BADGE.get(status, status)
    lines += [f"**Status:** {badge} &nbsp;&nbsp; **Version:** {version}", ""]

    # Metadata table
    lines += [
        "| Field | Value |",
        "|---|---|",
        f"| **ID** | `{gid}` |",
        f"| **Category** | {CATEGORY_DISPLAY.get(category, category.title())} |",
        f"| **Subcategory** | {subcategory} |",
        f"| **Type** | {gtype} |",
        f"| **Owner** | {escape_pipe(owner)} |",
        f"| **Approved by** | {approved_by} |",
        f"| **Approved date** | {approved_date} |",
        f"| **Review due** | {review_due} |",
        "",
    ]

    # Description
    lines += ["## Description", "", description, ""]

    # Source document
    lines.append("## Source document")
    lines.append("")
    src_type = source.get("type", "")
    if src_type == "onedrive":
        filename = source.get("filename", "View document")
        url = source.get("url", "")
        last_mod = fmt_date(source.get("last_modified"))
        lines.append(f"[{filename}]({url})" if url else filename)
        lines += ["", f"*Last modified: {last_mod}*"]
    elif src_type == "external_url":
        page_title = source.get("page_title", "View source")
        url = source.get("url", "")
        last_fetched = fmt_date(source.get("last_fetched"))
        lines.append(f"[{page_title}]({url})" if url else page_title)
        lines += ["", f"*Last fetched: {last_fetched}*"]
    else:
        lines.append("Source not available.")
    lines.append("")

    # Related guardrails
    if related:
        lines += ["## Related guardrails", ""]
        for rel_id in related:
            if rel_id in id_to_info:
                rel_cat, rel_title = id_to_info[rel_id]
                rel_path = f"../{rel_cat}/{rel_id}.md"
                lines.append(f"- [{rel_id} — {rel_title}]({rel_path})")
            else:
                lines.append(f"- {rel_id}")
        lines.append("")

    # Change history
    if change_log:
        lines += [
            "## Change history",
            "",
            "| Version | Date | Author | Summary |",
            "|---|---|---|---|",
        ]
        for entry in change_log:
            ev = entry.get("version") or "—"
            ed = fmt_date(entry.get("date"))
            ea = escape_pipe(entry.get("author") or "—")
            es = escape_pipe(get_entry_summary(entry) or "—")
            lines.append(f"| {ev} | {ed} | {ea} | {es} |")
        lines.append("")

    return "\n".join(lines)


def write_guardrail_pages(guardrails: list[dict], id_to_info: dict) -> None:
    for data in guardrails:
        out_dir = DOCS_DIR / "guardrails" / data["category"]
        out_dir.mkdir(parents=True, exist_ok=True)
        page = render_guardrail_page(data, id_to_info)
        (out_dir / f"{data['id']}.md").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 4 — Category index pages
# ---------------------------------------------------------------------------


def write_category_index(category: str, guardrails: list[dict]) -> None:
    display = CATEGORY_DISPLAY.get(category, category.title())
    sorted_g = sorted(guardrails, key=lambda x: x["id"])
    n_approved = sum(1 for g in guardrails if g.get("status") == "approved")
    n_under_review = sum(1 for g in guardrails if g.get("status") == "under-review")

    lines = ["---", f'title: "{display} Guardrails"', "---", "", f"# {display} Guardrails", ""]

    parts = []
    if n_approved:
        parts.append(f"{n_approved} approved")
    if n_under_review:
        parts.append(f"{n_under_review} under review")
    lines += [f"{len(guardrails)} guardrail(s) — {', '.join(parts)}.", ""]

    lines += [
        "| ID | Title | Status | Version | Review due |",
        "|---|---|---|---|---|",
    ]
    for g in sorted_g:
        gid = g["id"]
        gtitle = escape_pipe(g["title"])
        badge = STATUS_BADGE.get(g.get("status", ""), g.get("status", ""))
        gver = g.get("version", "—")
        grev = fmt_date(g.get("review_due"))
        lines.append(f"| [{gid}]({gid}.md) | {gtitle} | {badge} | {gver} | {grev} |")
    lines.append("")

    out_dir = DOCS_DIR / "guardrails" / category
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 5 — Home page
# ---------------------------------------------------------------------------


def write_home_page(guardrails: list[dict], by_category: dict) -> None:
    today = date.today().isoformat()
    n_approved = sum(1 for g in guardrails if g.get("status") == "approved")
    n_under_review = sum(1 for g in guardrails if g.get("status") == "under-review")

    lines = [
        "---",
        'title: "Architecture Guardrails Library"',
        "---",
        "",
        "# Architecture Guardrails Library",
        "",
        (
            "This library contains the approved architecture guardrails for the organisation. "
            "Guardrails are standards, policies, patterns, and rules that guide how systems are built "
            "and integrated. Browse by category, search by keyword, or filter by tag."
        ),
        "",
        (
            f"**{len(guardrails)} guardrails** across {len(by_category)} categories — "
            f"{n_approved} approved, {n_under_review} under review. "
            f"Last updated: {today}."
        ),
        "",
        "## Browse by category",
        "",
        "| Category | Total | Approved | Under Review |",
        "|---|---|---|---|",
    ]

    # Ordered categories first, then any unknown ones alphabetically
    ordered = [c for c in CATEGORY_ORDER if c in by_category]
    extras = sorted(c for c in by_category if c not in CATEGORY_ORDER)
    for cat in ordered + extras:
        glist = by_category[cat]
        display = CATEGORY_DISPLAY.get(cat, cat.title())
        na = sum(1 for g in glist if g.get("status") == "approved")
        nur = sum(1 for g in glist if g.get("status") == "under-review")
        lines.append(f"| [{display}](guardrails/{cat}/index.md) | {len(glist)} | {na} | {nur} |")

    lines += ["", "## Recently updated", ""]
    lines += [
        "| ID | Title | Category | Last updated |",
        "|---|---|---|---|",
    ]
    recent = sorted(guardrails, key=last_updated_date, reverse=True)[:10]
    for g in recent:
        gid = g["id"]
        cat = g["category"]
        display_cat = CATEGORY_DISPLAY.get(cat, cat.title())
        d = last_updated_date(g)
        last_date = d.isoformat() if d != date.min else "—"
        lines.append(
            f"| [{gid}](guardrails/{cat}/{gid}.md) | {escape_pipe(g['title'])} "
            f"| {display_cat} | {last_date} |"
        )
    lines.append("")

    (DOCS_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 6 — Tags page
# ---------------------------------------------------------------------------


def write_tags_page() -> None:
    (DOCS_DIR / "tags.md").write_text(
        "---\ntitle: Tags\n---\n\n# Tags\n\n[TAGS]\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Step 7 — mkdocs.yml
# ---------------------------------------------------------------------------


MKDOCS_STATIC = """\
# DO NOT EDIT — regenerated by generate_docs.py on every run.

site_name: Architecture Guardrails Library
site_description: Approved architecture standards, policies, and patterns.
docs_dir: docs
site_dir: ../site

theme:
  name: material
  palette:
    - scheme: default
      primary: indigo
      accent: indigo
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.indexes
    - navigation.top
    - search.suggest
    - search.highlight

plugins:
  - search

markdown_extensions:
  - tables
  - toc:
      permalink: true
  - admonition

"""


def write_mkdocs_yml(by_category: dict) -> None:
    nav_lines = ["nav:"]
    nav_lines.append("  - Home: index.md")

    ordered = [c for c in CATEGORY_ORDER if c in by_category]
    extras = sorted(c for c in by_category if c not in CATEGORY_ORDER)
    for cat in ordered + extras:
        display = CATEGORY_DISPLAY.get(cat, cat.title())
        nav_lines.append(f"  - {display}:")
        nav_lines.append(f"    - guardrails/{cat}/index.md")
        for g in sorted(by_category[cat], key=lambda x: x["id"]):
            title = nav_title(g["id"], g["title"])
            safe = title.replace('"', '\\"')
            nav_lines.append(f'    - "{safe}": guardrails/{cat}/{g["id"]}.md')

    nav_lines.append("")
    MKDOCS_YML.write_text(MKDOCS_STATIC + "\n".join(nav_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not GUARDRAILS_DIR.exists():
        print(f"ERROR: '{GUARDRAILS_DIR}' not found. Run from repo root.", file=sys.stderr)
        sys.exit(1)

    DOCS_SITE_DIR.mkdir(exist_ok=True)

    # Step 1
    guardrails, id_to_info, skipped = load_metafiles()

    if not guardrails:
        print("No guardrails found matching the status filter. Nothing to generate.")
        sys.exit(0)

    # Group by category
    by_category: dict[str, list[dict]] = {}
    for g in guardrails:
        by_category.setdefault(g["category"], []).append(g)

    # Step 2
    clear_docs_dir()

    # Steps 3–7
    write_guardrail_pages(guardrails, id_to_info)
    for cat, glist in by_category.items():
        write_category_index(cat, glist)
    write_home_page(guardrails, by_category)
    write_mkdocs_yml(by_category)

    # Summary
    print(f"✓ Written {len(guardrails)} guardrail pages across {len(by_category)} categories")
    for cat in CATEGORY_ORDER:
        if cat in by_category:
            print(f"  {cat}: {len(by_category[cat])} pages")
    for cat in sorted(c for c in by_category if c not in CATEGORY_ORDER):
        print(f"  {cat}: {len(by_category[cat])} pages")
    print(f"✓ Written {len(by_category)} category index pages")
    print("✓ Written home page (index.md)")
    print("✓ Written mkdocs.yml")

    if skipped:
        print(f"\n  Skipped {len(skipped)} file(s):")
        for path, reason in skipped:
            print(f"    {path}: {reason}")


if __name__ == "__main__":
    main()

