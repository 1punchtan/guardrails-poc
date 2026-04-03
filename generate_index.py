"""
generate_index.py

Reads all guardrail metafiles from guardrails/**/*.json (excluding schema files),
and writes a flattened guardrails-index.json to the repo root.

Run manually or via GitHub Actions on merge to main.
"""

import json
import sys
from pathlib import Path

GUARDRAILS_DIR = Path("guardrails")
SCHEMA_DIR = GUARDRAILS_DIR / "schema"
OUTPUT_FILE = Path("guardrails-index.json")

# Fields to include in the index (subset of full metafile schema).
# Keeps the index token-efficient while retaining everything Claude needs for a review.
INDEX_FIELDS = [
    "id",
    "title",
    "status",
    "category",
    "tags",
    "description",
    "owner",
    "source",
    "related_guardrails",
    "version",
    "approved_date",
    "review_due",
]


def load_metafiles(guardrails_dir: Path, schema_dir: Path) -> tuple[list[dict], list[tuple]]:
    entries = []
    skipped = []

    metafiles = sorted(guardrails_dir.rglob("*.json"))

    for path in metafiles:
        # Skip schema files
        if path.is_relative_to(schema_dir):
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            skipped.append((str(path), f"JSON parse error: {e}"))
            continue

        # Only include approved or under_review guardrails — skip deprecated/draft
        status = data.get("status", "").lower()
        if status not in ("approved", "under-review"):
            skipped.append((str(path), f"Skipped status: {status!r}"))
            continue

        entry = {field: data[field] for field in INDEX_FIELDS if field in data}
        entry["_source_file"] = str(path)  # useful for debugging
        entries.append(entry)

    return entries, skipped


def main():
    if not GUARDRAILS_DIR.exists():
        print(f"ERROR: '{GUARDRAILS_DIR}' directory not found. Run from repo root.", file=sys.stderr)
        sys.exit(1)

    entries, skipped = load_metafiles(GUARDRAILS_DIR, SCHEMA_DIR)

    index = {
        "generated_by": "generate_index.py",
        "description": (
            "Auto-generated index of approved architecture guardrails. "
            "Do not edit manually — regenerated on every merge to main."
        ),
        "guardrail_count": len(entries),
        "guardrails": entries,
    }

    OUTPUT_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"✓ Written {len(entries)} guardrails to {OUTPUT_FILE}")

    if skipped:
        print(f"\n  Skipped {len(skipped)} file(s):")
        for path, reason in skipped:
            print(f"    {path}: {reason}")


if __name__ == "__main__":
    main()