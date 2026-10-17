# Troubleshooting
Logs are printed in the `run.bat` console window; tokens are masked as `***`, so logs are safe to share.

| Run tests | `run_tests.bat` (offline; no posts are created). |
| Job stuck in "Processing"/"Uncertain" | Click **Retry**: the app checks the existing upload first and never re-posts a confirmed video. |
| "Publishing limit reached" | Instagram 24-h quota; remaining jobs are deferred — retry later. |
| Instagram account not listed | Must be a Professional (Business/Creator) account linked to a Facebook Page you manage. |
| "Missing permission …" | Reconnect and approve all requested permissions; in Live mode the app needs App Review for non-role users. |
| "Reconnect Facebook" banner / error 190 | Token expired or revoked (password change, removed app). Reconnect, then **Retry Failed**. |
| Facebook login: port in use | Another app uses 8765; set `META_REDIRECT_PORT` and update the redirect URI in Meta. |
| Facebook login: "URL blocked" | Add `http://localhost:8765/` to *Valid OAuth Redirect URIs* (or change `META_REDIRECT_PORT`). |
| Video marked "needs review" | Filename doesn't match a Gemini block or content is incomplete. Confirm/correct mapping in the Mapping panel. |
| Gemini link can't be read | Shared pages are often JavaScript-rendered. Paste the response or import a `.txt/.md/.json` file. |
| Drive folder shows 0 videos | Only MP4/MOV are listed; check the URL is a folder you can open with the connected account. |
| Google: "Reconnect Google" banner | Refresh token expired or revoked (test-mode tokens expire after 7 days). Click **Connect Google account**. |
| Google: "access blocked / app not verified" | Add your Google account as a **test user** on the OAuth consent screen. |
| "OAuth client file not found" | Save the Desktop-app OAuth JSON as `credentials\client_secret.json` or set `GOOGLE_CLIENT_SECRETS_FILE` in `.env`. |
| "No secure OS credential store is available" | Windows Credential Manager must be enabled (default). On corporate PCs ask IT; the app refuses to store tokens in plain files. |
| Browser doesn't open | Open `http://localhost:8501` manually. If the port is busy: `streamlit run app.py --server.port 8502`. |
| `pip install` fails on `keyring`/`google-*` | Upgrade pip (`python -m pip install --upgrade pip`), check proxy/firewall, then re-run `install.bat`. |
| `install.bat`: "Python is not installed" | Install Python 3.10+ from python.org and tick **Add python.exe to PATH**. Re-open the terminal. |
|---|---|
| Symptom | Cause / fix |


