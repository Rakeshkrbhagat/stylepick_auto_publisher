"""Application configuration.

No secrets live in source code. OAuth client config is a separate, git-ignored file
(Google) or comes from the environment / OS credential vault (Meta app secret).
User tokens are stored only in the OS credential vault (see ``src/secure_store.py``).
"""

from dataclasses import dataclass, field
from pathlib import Path
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _meta_app_secret() -> str:
    """Env/.env first, otherwise the OS vault (``python -m src.secure_store set meta_app_secret``)."""
    value = os.getenv("META_APP_SECRET", "")
    if value:
        return value
    from src.secure_store import get_secret

    return get_secret("meta_app_secret")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "StylePick Finds Auto Publisher")
    google_client_secrets_file: Path = Path(
        os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json")
    )
    work_dir: Path = Path(os.getenv("WORK_DIR", "data/downloads"))
    download_retention_hours: float = float(os.getenv("DOWNLOAD_RETENTION_HOURS", "24"))
    # ---- Meta / Facebook / Instagram ----
    meta_app_id: str = os.getenv("META_APP_ID", "")
    meta_app_secret: str = field(default_factory=_meta_app_secret, repr=False)
    # Only request permissions for these platforms (least privilege).
    meta_platforms: tuple = tuple(
        p.strip().lower() for p in os.getenv("META_PLATFORMS", "facebook,instagram").split(",")
        if p.strip())
    # Verify the current version at https://developers.facebook.com/docs/graph-api/changelog
    meta_graph_version: str = os.getenv("META_GRAPH_VERSION", "v23.0")
    meta_redirect_port: int = int(os.getenv("META_REDIRECT_PORT", "8765"))
    job_db_path: Path = Path(os.getenv("JOB_DB_PATH", "data/publishing.db"))
    fb_poll_timeout_s: float = float(os.getenv("FB_POLL_TIMEOUT_SECONDS", "300"))


settings = Settings()

