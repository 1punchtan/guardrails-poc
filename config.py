import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_KEYS = [
    "AZURE_CLIENT_ID",
    "AZURE_TENANT_ID",
    "AZURE_REDIRECT_URI",
    "ONEDRIVE_WATCH_FOLDER",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GITHUB_REPO",
    "GITHUB_BASE_BRANCH",
    "STATE_FILE",
    "URL_SOURCES_FILE",
]


def load_config() -> dict:
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in all values."
        )

    return {
        "azure_client_id": os.environ["AZURE_CLIENT_ID"],
        "azure_tenant_id": os.environ["AZURE_TENANT_ID"],
        "azure_redirect_uri": os.environ["AZURE_REDIRECT_URI"],
        "onedrive_watch_folder": os.environ["ONEDRIVE_WATCH_FOLDER"],
        "anthropic_api_key": os.environ["ANTHROPIC_API_KEY"],
        "claude_model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        "github_token": os.environ["GITHUB_TOKEN"],
        "github_repo": os.environ["GITHUB_REPO"],
        "github_base_branch": os.environ["GITHUB_BASE_BRANCH"],
        "state_file": os.environ["STATE_FILE"],
        "url_sources_file": os.environ["URL_SOURCES_FILE"],
    }


if __name__ == "__main__":
    cfg = load_config()
    print("Config loaded successfully:")
    for k, v in cfg.items():
        masked = v if k not in ("anthropic_api_key", "github_token") else v[:6] + "..."
        print(f"  {k}: {masked}")
