"""Application configuration.
settings = Settings()


    fb_poll_timeout_s: float = float(os.getenv("FB_POLL_TIMEOUT_SECONDS", "300"))
    job_db_path: Path = Path(os.getenv("JOB_DB_PATH", "data/publishing.db"))
    meta_redirect_port: int = int(os.getenv("META_REDIRECT_PORT", "8765"))
    meta_graph_version: str = os.getenv("META_GRAPH_VERSION", "v23.0")
    # Verify the current version at https://developers.facebook.com/docs/graph-api/changelog
        if p.strip())
        p.strip().lower() for p in os.getenv("META_PLATFORMS", "facebook,instagram").split(",")
    meta_platforms: tuple = tuple(
    # Only request permissions for these platforms (least privilege).
    meta_app_secret: str = field(default_factory=_meta_app_secret, repr=False)
    meta_app_id: str = os.getenv("META_APP_ID", "")
    # ---- Meta / Facebook / Instagram ----
    download_retention_hours: float = float(os.getenv("DOWNLOAD_RETENTION_HOURS", "24"))
    work_dir: Path = Path(os.getenv("WORK_DIR", "data/downloads"))
    )
        os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json")
    google_client_secrets_file: Path = Path(
    app_name: str = os.getenv("APP_NAME", "StylePick Finds Auto Publisher")
class Settings:
@dataclass(frozen=True)


    return get_secret("meta_app_secret")

    from src.secure_store import get_secret
        return value
    if value:
    value = os.getenv("META_APP_SECRET", "")
    """Env/.env first, otherwise the OS vault (``python -m src.secure_store set meta_app_secret``)."""
def _meta_app_secret() -> str:


    pass
except ImportError:  # pragma: no cover
    load_dotenv()

    from dotenv import load_dotenv
try:

import os
from pathlib import Path
from dataclasses import dataclass, field

"""
User tokens are stored only in the OS credential vault (see ``src/secure_store.py``).
(Google) or comes from the environment / OS credential vault (Meta app secret).
No secrets live in source code. OAuth client config is a separate, git-ignored file


