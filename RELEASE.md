# Release notes, permissions, limitations & test report

## Deliverables
- Source code (`app.py`, `src/`), tests (`tests/`)
- Dependency list: `requirements.txt`
- Configuration template: `.env.example` (copy to `.env`; never commit)
- Windows scripts: `install.bat`, `run.bat`, `run_tests.bat`
- Docs: `README.md` (setup/config), `TROUBLESHOOTING.md`, this file

## Required API permissions
| Service | Scope / permission | Why |
|---|---|---|
| Google Drive | `drive.readonly` | List and download videos from a folder chosen by URL (read-only). |
| Facebook | `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` | Find Pages, publish Reels to the selected Page. |
| Instagram | `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` | Find the linked Professional account, publish Reels. |

Only permissions for platforms listed in `META_PLATFORMS` are requested.

## External approval dependencies
- **Google OAuth consent screen**: in *Testing* mode only listed test users can sign in and refresh
  tokens expire after 7 days. Publishing the app to *Production* for `drive.readonly` (restricted
  scope) requires Google verification.
- **Meta App Review**: in *Development* mode only app admins/developers/testers can log in.
  Other users need Advanced Access for the permissions above plus Business Verification.
- **Instagram**: account must be Professional (Business/Creator) and linked to a Facebook Page.
- Graph API version (`META_GRAPH_VERSION`) must be kept current; Meta deprecates versions ~2 years.

## Known limitations
- Runs locally (Streamlit in the browser); not a signed `.exe`. Python 3.10+ required.
- Gemini share pages rendered via JavaScript cannot be scraped; paste/import fallback is used.
- Instagram: MP4/MOV ≤ 300 MB, 3 s – 15 min, caption ≤ 2,200 chars, ≤ 30 hashtags,
  ~50 API posts per 24 h (`content_publishing_limit`).
- Facebook Reels must meet Meta's Reels specs (aspect ratio, duration); violations fail on Meta's side.
- Publishing is sequential and throttled; a 10-video combined batch takes several minutes.
- The machine must stay awake/online during publishing; interrupted jobs are marked "uncertain" and
  reconciled on retry.
- Scheduling, YouTube publishing and multi-user operation are out of scope.

## Safety: no public test posts without approval
- Live Facebook / Instagram toggles are **off by default**; while off every publish is simulated.
- Automated tests are offline and use fakes — they never call Meta or Google.
- Live tests in `tests/` are skipped unless explicitly enabled via env variables; the Facebook one
  creates a draft, the Instagram one requires `META_TEST_IG_PUBLISH=1` (use a private test account).

## Test coverage (`python -m pytest -q tests`)
| Area | Tests |
|---|---|
| Dashboard, validation, review/edit | `test_dashboard_logic.py`, `test_end_to_end.py` |
| Drive retrieval (10-video batch, downloads, auth errors) | `test_drive_client.py`, `test_end_to_end.py` |
| Gemini parsing (Hindi/emoji, fallback) | `test_gemini_parser.py` |
| Matching to correct videos | `test_matching.py`, `test_end_to_end.py` |
| Facebook-only / Instagram-only / combined | `test_end_to_end.py`, `test_facebook_publisher.py`, `test_instagram_publisher.py` |
| Partial failure, network interruption, expired token | `test_end_to_end.py`, `test_publishing_coordinator.py` |
| Retry & duplicate protection, restart recovery | `test_publishing_coordinator.py`, `test_history_store.py` |
| Credential storage, redaction, least privilege | `test_security.py` |

### Manual acceptance checklist (Windows)
1. `install.bat` → `run.bat` → dashboard opens at `http://localhost:8501`.
2. Connect Google; paste a Drive folder with ~10 videos; list loads.
3. Paste/import Gemini content; every video shows matched content or a review flag.
4. Review/edit captions in the preview.
5. With live toggles **off**: run FB-only, IG-only and combined batches; check history.
6. Disconnect Wi-Fi mid-batch → failures are retryable; reconnect → **Retry Failed** completes
   without duplicates.
7. Disconnect Facebook in the sidebar → publishing asks for reauthorization.
8. Only with the account owner's explicit approval: enable live publishing for **one** video on a
   test Page / test Instagram account and verify the permalink.

