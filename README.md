# StylePick Finds Auto Publisher

A local tool that:
1. Reads videos from a Google Drive folder URL.
2. Reads captions/hashtags from a Gemini shared conversation URL (with paste/import fallback).
3. Matches content to videos and previews/edits it.
4. Publishes Reels to a Facebook Page and an Instagram Professional account via official Meta APIs.
5. Tracks results and retries without duplicate posts.

## Current status

- Dashboard UI – done.
- Google Drive integration (OAuth, folder listing, selection, verified downloads) – done.
- Gemini content ingestion (shared URL retrieval + paste/file fallback + parser) – done.
- Video ↔ content matching with manual confirmation – done.
- Facebook Page Reels publishing (opt-in "Live" toggle) – done.
- Instagram Reels publishing (opt-in "Live" toggle) – done.

## Google Drive setup (one-time, free)

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen (External, add yourself as a test user).
3. Create an **OAuth client ID → Desktop app** and download the JSON.
4. Save it as `credentials/client_secret.json` (git-ignored) or set `GOOGLE_CLIENT_SECRETS_FILE` in `.env`.
5. Run the app, turn off *Use sample Drive data*, click **Connect Google account** and sign in.

Notes:
- Scope: `drive.readonly` – the minimum that can list/download from an existing folder chosen by URL.
- Private folders work; public sharing is not required.
- Tokens are stored in the OS keyring (Windows Credential Manager / macOS Keychain), never in the repo.
- Supported videos: MP4 and MOV. Downloads are chunked, verified (size + MD5), retried and cleaned up
  after `DOWNLOAD_RETENTION_HOURS`.

## Meta setup (Facebook + Instagram)

> Verify the current Graph API version, permissions, Reels requirements and publishing limits in the
> Meta docs (Graph API changelog, *Reels Publishing API*, *Instagram Content Publishing*) before going
> live. The version is configurable via `META_GRAPH_VERSION`.

1. At [developers.facebook.com](https://developers.facebook.com/) create an app (type *Business*),
   add **Facebook Login** and the **Instagram API (with Facebook Login)** product.
2. In Facebook Login → Settings add the Valid OAuth Redirect URI `http://localhost:8765/`
   (or your `META_REDIRECT_PORT`).
3. Put `META_APP_ID` and `META_APP_SECRET` in `.env` (never commit it).
4. In the sidebar click **Connect Facebook** and approve all permissions. In development mode only app
   roles (admins/developers/testers) can log in; other users need Meta App Review.
5. Facebook: pick your Page → **Use this Page** → turn on **Live Facebook publishing**.
6. Instagram: pick the account → **Use this Instagram account** → turn on **Live Instagram publishing**.

Both toggles are off by default; while off, that platform is simulated.

### Permissions & eligibility checks

| Platform  | Permissions                                                                     |
|-----------|---------------------------------------------------------------------------------|
| Facebook  | `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`                 |
| Instagram | `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` |

Before each live batch the app validates the token via `/debug_token` and checks the granted
permissions. Only Pages where your role includes `CREATE_CONTENT` are publishable. Instagram accounts
are discovered through `instagram_business_account` on your Pages, so only **Professional (Business or
Creator) accounts linked to a Page** are offered. Pages without one are listed with an explanation
(personal accounts can't publish through the API). The 24-hour Instagram publishing quota
(`content_publishing_limit`) is checked before uploading.

### Facebook Reels flow (`src/facebook_publisher.py`)

`POST /{page}/video_reels` (start) → binary upload to `rupload.facebook.com` →
`POST /{page}/video_reels` (finish, `description` = Facebook description + hashtags) →
poll `GET /{video-id}?fields=status,permalink_url`.

### Instagram Reels flow (`src/instagram_publisher.py`)

`POST /{ig-user}/media` (`media_type=REELS`, `upload_type=resumable`, `caption` = Instagram caption +
hashtags) → binary upload to `rupload.facebook.com/ig-api-upload/...` → poll
`GET /{container}?fields=status_code` until `FINISHED` → `POST /{ig-user}/media_publish` →
`GET /{media-id}?fields=permalink`. The media ID and permalink are stored.

Local checks: MP4/MOV, ≤ 300 MB, caption ≤ 2,200 characters, ≤ 30 hashtags and (when known)
3 s – 15 min.

### Duplicate protection

Every (video, platform, account) job is stored in SQLite (`JOB_DB_PATH`) and saved before the
non-idempotent publish call.
- Already published → no API calls at all.
- Facebook: an uncertain or processing job is only re-checked; a new upload happens only after a
  definitive failure.
- Instagram: on retry the existing container's `status_code` decides: `PUBLISHED` → reconcile the
  media ID from recent posts (by caption); `FINISHED` → publish that same container;
  `IN_PROGRESS` → keep polling; `ERROR`/`EXPIRED` → upload again.

Errors are shown in the UI and history: expired/revoked tokens (code 190), missing permissions,
rate limits (retried with backoff), unsupported content and failed processing.

## Gemini content

**Sources (priority order):** imported file (`.txt`, `.md`, `.json`) → pasted text → shared URL.
Only public share links are fetched (anonymous HTTPS GET). If the page is rendered with JavaScript,
the app explains why it couldn't read it and asks you to paste the response or import a file.

```text
=== VIDEO: stylepick_01.mp4 ===        (also "## Video 1: stylepick_01.mp4")
--- YouTube Shorts ---
Hook / Title / Description / Hashtags / YouTube Tags / On-screen Text / Transcript
--- Instagram Reels ---
Reel Cover Title / Caption / Hashtags / On-screen Text / Optimized Transcript
--- Facebook Reels ---
SEO Title / Description / Hashtags
```

Missing fields are flagged. Ambiguous fields are left empty and flagged, never guessed.

## Matching

Matching is deterministic, in this order: manual mapping → exact filename → Drive file ID → filename
without extension. Similar names, "Video N" blocks, duplicate filenames and content claimed by
several videos need you to confirm, correct or skip them. Invalid or unconfirmed records are left
out of publishing. Text is copied exactly as written; your edits are marked and can be restored.

## Run locally

### Windows (recommended)
1. Install Python 3.10+ from python.org (tick *Add python.exe to PATH*).
2. Double-click `install.bat` (creates `.venv`, installs dependencies, creates `.env`).
3. Fill in `.env` and add `credentials\client_secret.json` (see setup sections above).
4. Double-click `run.bat` – the dashboard opens at `http://localhost:8501`.
5. `run_tests.bat` runs the offline test suite.

See `TROUBLESHOOTING.md` for common problems and `RELEASE.md` for permissions, approval
dependencies, known limitations and the test report.

Manual equivalent:
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Tests: `python -m pytest -q tests`. The live tests are opt-in. The Facebook one needs
`META_TEST_PAGE_ID`/`TOKEN`/`VIDEO` and creates a **draft**. The Instagram one needs
`META_TEST_IG_USER_ID`/`TOKEN`, `META_TEST_VIDEO` and `META_TEST_IG_PUBLISH=1`, and creates a
**public** reel, so use a test account.

## Project structure

```text
src/
├── config.py               # settings from env/.env
├── models.py               # VideoRecord, PlatformContent, statuses
├── content_parser.py       # Gemini response parser
├── gemini_parser.py        # Gemini shared-URL retrieval
├── google_auth.py / drive_client.py / drive_errors.py / video_source.py
├── matching.py             # video ↔ content matching
├── validation.py
├── meta_graph.py           # Graph API client
├── meta_auth.py            # Facebook Login, token checks, Page + Instagram account selection
├── meta_errors.py
├── facebook_publisher.py   # FB Reels upload/publish/poll, idempotent
├── instagram_publisher.py  # IG Reels container/upload/publish/poll, idempotent
├── job_store.py            # SQLite publication records
├── live_publisher.py       # routes each platform → live or mock
├── publishing_service.py   # batch runner + mock publisher
└── ui/                     # Streamlit components (drive, facebook, instagram, mapping panels)
```

## Cost and platform notes

Everything runs locally, so there's no paid hosting. The Graph API is free to use, but rate limits,
publishing quotas, App Review and account eligibility apply. Never commit passwords, tokens or app
secrets.

