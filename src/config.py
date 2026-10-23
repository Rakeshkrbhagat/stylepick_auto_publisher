"""Application configuration (environment driven, no secrets in source code)."""

from dataclasses import dataclass
from pathlib import Path
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    # ---- branding ----
    app_name: str = os.getenv("APP_NAME", "ReelFlow Studio")
    app_icon: str = os.getenv("APP_ICON", "🎬")
    # ---- Google Drive (read-only OAuth) ----
    # On Render: upload the OAuth client JSON as a Secret File and point this at it.
    google_client_secrets_file: Path = Path(
        os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json"))
    # Hosted deployments: the app's public URL, e.g. https://your-app.onrender.com/
    # Leave empty locally to use the desktop (local browser) sign-in flow.
    google_redirect_uri: str = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
    # Temporary download folder (cleaned automatically; nothing is kept permanently).
    work_dir: Path = Path(os.getenv("WORK_DIR", "/tmp/reelflow_downloads"))
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")


settings = Settings()

