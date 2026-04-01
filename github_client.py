import json
import re
from dataclasses import dataclass

from github import Auth, Github
from github.Repository import Repository


@dataclass
class MetafileSummary:
    id: str
    title: str
    category: str
    tags: list[str]
    description: str


def get_existing_metafiles(repo: Repository) -> list[MetafileSummary]:
    """Fetch all .json metafiles under guardrails/ and return lightweight summaries."""
    summaries = []
    try:
        contents = repo.get_contents("guardrails")
    except Exception:
        return summaries

    # Walk the tree — get_contents returns a flat list when called on a folder,
    # but subfolders are returned as directory entries; recurse into them.
    queue = list(contents)
    while queue:
        item = queue.pop(0)
        if item.type == "dir":
            try:
                queue.extend(repo.get_contents(item.path))
            except Exception:
                continue
        elif item.name.endswith(".json") and item.name != "guardrail.schema.json":
            try:
                raw = item.decoded_content.decode("utf-8")
                data = json.loads(raw)
                summaries.append(
                    MetafileSummary(
                        id=data.get("id", ""),
                        title=data.get("title", ""),
                        category=data.get("category", ""),
                        tags=data.get("tags", []),
                        description=data.get("description", ""),
                    )
                )
            except Exception:
                continue

    return summaries


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")


def _pr_branch_name(guardrail_id: str, is_update: bool) -> str:
    action = "update" if is_update else "add"
    return f"guardrail/{action}-{_slugify(guardrail_id)}"


def _pr_title(metafile: dict, is_update: bool) -> str:
    gid = metafile.get("id", "")
    title = metafile.get("title", "")
    version = metafile.get("version", "")
    if is_update:
        return f"[Update] {gid}: {title} — v{version}"
    return f"[New] Add {gid}: {title}"


def _pr_body_onedrive(metafile: dict, source_ref: dict, is_update: bool) -> str:
    change_type = "Update" if is_update else "New"
    category = metafile.get("category", "")
    subcategory = metafile.get("subcategory") or ""
    cat_display = f"{category} / {subcategory}" if subcategory else category
    tags = ", ".join(metafile.get("tags", []))
    description = metafile.get("description", "")

    return f"""## Guardrail Metadata PR

**Source:** OneDrive file
**File:** {source_ref.get('filename', '')}
**OneDrive path:** {source_ref.get('onedrive_path', '')}
**Change type:** {change_type}

### Inferred Metadata
- **Category:** {cat_display}
- **Tags:** {tags}
- **Description:** {description}

### Reviewer Checklist
- [ ] Category and subcategory are correct
- [ ] Description accurately reflects the document
- [ ] Tags are appropriate
- [ ] Related guardrails are correctly identified
- [ ] Owner is correct
- [ ] Approve and merge to mark as approved"""


def _pr_body_url(metafile: dict, source_ref: dict, is_update: bool) -> str:
    change_type = "Updated content detected (hash changed)" if is_update else "New"
    category = metafile.get("category", "")
    subcategory = metafile.get("subcategory") or ""
    cat_display = f"{category} / {subcategory}" if subcategory else category
    tags = ", ".join(metafile.get("tags", []))
    description = metafile.get("description", "")

    return f"""## Guardrail Metadata PR

**Source:** External URL
**URL:** {source_ref.get('url', '')}
**Page title:** {source_ref.get('page_title', '')}
**Fetched at:** {source_ref.get('fetched_at', '')}
**Change type:** {change_type}

### Inferred Metadata
- **Category:** {cat_display}
- **Tags:** {tags}
- **Description:** {description}

### Reviewer Checklist
- [ ] URL is still the canonical source (check for newer versions or superseding policy)
- [ ] Category and subcategory are correct
- [ ] Description accurately reflects the current page content
- [ ] Tags are appropriate
- [ ] Related guardrails are correctly identified
- [ ] Owner is correct
- [ ] Approve and merge to mark as approved"""


def _metafile_path(metafile: dict) -> str:
    """Derive the repo path for this metafile: guardrails/{category}/{id}.json"""
    category = _slugify(metafile.get("category", "uncategorised"))
    gid = metafile.get("id", "unknown")
    return f"guardrails/{category}/{gid}.json"


def create_pr(
    repo: Repository,
    metafile: dict,
    source_type: str,
    source_ref: dict,
    is_update: bool,
    base_branch: str = "main",
) -> str:
    """Create a branch, commit the metafile, open a PR, and return the PR URL."""
    branch_name = _pr_branch_name(metafile["id"], is_update)
    title = _pr_title(metafile, is_update)

    if source_type == "onedrive":
        body = _pr_body_onedrive(metafile, source_ref, is_update)
    else:
        body = _pr_body_url(metafile, source_ref, is_update)

    file_path = _metafile_path(metafile)
    file_content = json.dumps(metafile, indent=2)
    commit_message = title

    # Get the SHA of the base branch to branch from
    base_ref = repo.get_branch(base_branch)
    base_sha = base_ref.commit.sha

    # Create the branch
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)

    # Check if the file already exists on the branch (needed for update)
    existing_sha = None
    try:
        existing_file = repo.get_contents(file_path, ref=branch_name)
        existing_sha = existing_file.sha
    except Exception:
        pass

    if existing_sha:
        repo.update_file(
            path=file_path,
            message=commit_message,
            content=file_content,
            sha=existing_sha,
            branch=branch_name,
        )
    else:
        repo.create_file(
            path=file_path,
            message=commit_message,
            content=file_content,
            branch=branch_name,
        )

    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
    )

    return pr.html_url


def connect(github_token: str, repo_name: str) -> Repository:
    """Return an authenticated Repository object."""
    g = Github(auth=Auth.Token(github_token))
    return g.get_repo(repo_name)


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPO")
    base_branch = os.environ.get("GITHUB_BASE_BRANCH", "main")

    if not token or not repo_name:
        print("GITHUB_TOKEN and GITHUB_REPO must be set in .env to run this test.")
        raise SystemExit(1)

    repo = connect(token, repo_name)

    # --- Test get_existing_metafiles ---
    print("Fetching existing metafiles...")
    summaries = get_existing_metafiles(repo)
    print(f"  Found {len(summaries)} existing metafile(s).")
    for s in summaries:
        print(f"  {s.id}: {s.title} [{s.category}]")

    # --- Test create_pr with a dummy URL metafile ---
    dummy_metafile = {
        "id": "GUARD-TEST-001",
        "title": "Test Guardrail — Phase 2 Smoke Test",
        "category": "test",
        "subcategory": "smoke",
        "type": "policy",
        "format": "web",
        "status": "draft",
        "version": "1.0",
        "owner": None,
        "approved_by": None,
        "approved_date": None,
        "review_due": None,
        "tags": ["test", "smoke"],
        "description": "Dummy guardrail created during Phase 2 smoke test. Safe to close and delete.",
        "source": {
            "type": "external_url",
            "url": "https://example.com",
            "page_title": "Example Domain",
            "content_hash": "abc123",
            "last_fetched": "2025-01-01T00:00:00Z",
        },
        "related_guardrails": [],
        "change_log": [
            {
                "version": "1.0",
                "date": "2025-01-01",
                "author": "auto-generated",
                "summary": "Phase 2 smoke test — safe to close",
            }
        ],
    }

    source_ref = {
        "url": "https://example.com",
        "page_title": "Example Domain",
        "fetched_at": "2025-01-01T00:00:00Z",
    }

    print("\nCreating test PR (external_url, new)...")
    pr_url = create_pr(
        repo=repo,
        metafile=dummy_metafile,
        source_type="external_url",
        source_ref=source_ref,
        is_update=False,
        base_branch=base_branch,
    )
    print(f"  PR created: {pr_url}")
    print("\nPhase 2 smoke test passed.")
