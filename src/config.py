"""Application configuration (environment driven, no secrets in source code)."""

from dataclasses import dataclass, field
from pathlib import Path
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _client_secrets_path() -> Path:
    """GOOGLE_CLIENT_SECRETS_JSON (the JSON itself, e.g. a Render env var) takes priority;
    it is written to a private temp file so the OAuth library can read it."""
    raw = os.getenv("GOOGLE_CLIENT_SECRETS_JSON", "").strip()
    if raw:
        import json
        import tempfile
        try:
            json.loads(raw)
        except ValueError:
            pass  # leave invalid JSON unused; the UI reports "not configured"
        else:
            path = Path(tempfile.gettempdir()) / "reelflow_client_secret.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
            return path
    return Path(os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json"))


@dataclass(frozen=True)
class Settings:
    # ---- branding ----
    app_name: str = os.getenv("APP_NAME", "ReelFlow Studio")
    app_icon: str = os.getenv("APP_ICON", "🎬")
    # ---- Google Drive (read-only OAuth) ----
    # Either GOOGLE_CLIENT_SECRETS_JSON (paste the JSON) or GOOGLE_CLIENT_SECRETS_FILE (path,
    # e.g. a Render Secret File at /etc/secrets/client_secret.json).
    google_client_secrets_file: Path = field(default_factory=_client_secrets_path)
    # Hosted deployments: the app's public URL, e.g. https://your-app.onrender.com/
    # Leave empty locally to use the desktop (local browser) sign-in flow.
    google_redirect_uri: str = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "") or (
        os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/") + "/"
        if os.getenv("RENDER_EXTERNAL_URL") else "")
    # Temporary download folder (cleaned automatically; nothing is kept permanently).
    work_dir: Path = Path(os.getenv("WORK_DIR", "/tmp/reelflow_downloads"))
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")


settings = Settings()

