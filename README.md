# ReelFlow Studio

Paste a Google Drive folder link and a Gemini response, and the app shows each video with its
matched **Instagram caption + hashtags**, **Facebook description + hashtags** (copy buttons)
and a **Proposed Schedule Date/Time** (original date **+1 calendar day**, same time).

**The app does not publish or schedule anything.** You upload and schedule each video yourself
in the Instagram / Facebook apps. There's no database, no accounts, and no saved history.
Results exist only in your browser session.

## How it works
1. **Connect Google account** (read-only Drive access) in the sidebar. You can also use
   **Load sample data** to try the app without Google.
2. Paste the Drive folder URL. Private folders work if your Google account can read them.
3. Give the app the Gemini response in one of these ways:
   - a share link (`https://share.gemini.google/...` or `https://gemini.google.com/share/...`)
   - pasted text
   - an uploaded `.txt`, `.md` or `.json` file

   If the app can't read a share link, paste the text instead.
4. The app matches each video by the number at the end of its filename. For example,
   `R-YT-10.mp4` → response 10 and `M-New-1.mov` → response 1. Numbers with several digits
   work. Duplicate numbers, missing responses and videos without a number are flagged so you
   can fix them. The app never guesses a match.
5. The app copies text exactly as Gemini wrote it, keeping Hindi, emojis, hashtags and line
   breaks. It never rewrites or translates. It ignores the YouTube Shorts section.
6. The original date comes from one of these, in priority order:
   - a date you set for that video
   - the sidebar's default date
   - the Drive file's modified time

   The proposed date is one calendar day later at the same time. Month-end, year-end and leap
   years are handled.

## Environment variables
| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_CLIENT_SECRETS_FILE` | yes (for Drive) | Path to the Google OAuth client JSON |
| `GOOGLE_OAUTH_REDIRECT_URI` | hosted only | The app's public URL, e.g. `https://your-app.onrender.com/` |
| `APP_NAME` | no | Display name (default `ReelFlow Studio`) |
| `DEFAULT_TIMEZONE` | no | Used for converting Drive modified times (default `Asia/Kolkata`) |
| `WORK_DIR` | no | Temporary folder (default `/tmp/reelflow_downloads`) |

You don't need a database, a persistent disk, a token-encryption key or any Meta credentials.

## Google OAuth setup
1. In Google Cloud Console, create a project and enable the **Google Drive API**.
2. On the OAuth consent screen, add the scope `https://www.googleapis.com/auth/drive.readonly`.
   While the app is in *Testing* mode, add every user as a test user.
3. Create the OAuth client:
   - **Render:** create a **Web application** client. Add your app URL, e.g.
     `https://your-app.onrender.com/`, as an **Authorized redirect URI**. Use the exact URL,
     including the trailing slash.
   - **Local:** create a **Desktop app** client and save it as
     `credentials/client_secret.json`. Leave `GOOGLE_OAUTH_REDIRECT_URI` empty.
4. On Render, upload the JSON as a **Secret File** (e.g. `client_secret.json` → available at
   `/etc/secrets/client_secret.json`). Then set:
   - `GOOGLE_CLIENT_SECRETS_FILE=/etc/secrets/client_secret.json`
   - `GOOGLE_OAUTH_REDIRECT_URI`
5. Remove the old environment variables. These are no longer used: `META_*`,
   `JOB_DB_PATH`, `DATABASE_PATH`, `TOKEN_ENCRYPTION_KEY`, `ALLOW_SIGNUP`. Also detach the
   persistent disk if one was added only for this app.

How tokens are handled:
- The OAuth `state` is HMAC-signed and expires after 15 minutes.
- The access token is kept only in the current session's memory and is never stored.
- The app never asks Google for long-lived (offline) access. When the token expires, the app
  asks you to reconnect.
- **Disconnect** revokes the token.

Render start command (unchanged):
`streamlit run app.py --server.port $PORT --server.address 0.0.0.0`

## Run & test locally
```
pip install -r requirements.txt
streamlit run app.py
python -m pytest -q
```

## Troubleshooting
| Problem | Fix |
|---|---|
| `redirect_uri_mismatch` | `GOOGLE_OAUTH_REDIRECT_URI` must exactly match an Authorized redirect URI (scheme, host, trailing slash). |
| "Google sign-in is not configured" | The secret file path is wrong, or the file wasn't uploaded. |
| `access_denied` / "access was not granted" | Add yourself as a test user, then allow Drive read-only access. |
| "sign-in link expired or is invalid" | Click **Connect Google account** again and finish within 15 minutes. |
| Folder not found / access denied | Check the URL. Make sure the connected Google account can open the folder. |
| "Authorization expired" | Reconnect Google. Tokens are session-only, so this is expected after about 1 hour or a page reload. |
| No supported videos | Only MP4/MOV files are listed. Other files are shown as ignored. |
| Gemini link can't be read | Paste the response text or upload it as a file. |
| Wrong or missing match | Use the mapping panel to choose the response, confirm it or skip the video. |

