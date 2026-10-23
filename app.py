"""ReelFlow Studio – prepare reel captions and proposed schedule dates (no publishing).

Flow: connect Google (read-only) → paste Drive folder URL + Gemini response → videos are
matched by trailing number → Instagram/Facebook text extracted verbatim → proposed date =
original + 1 calendar day (same time). Everything lives in this browser session only: no
database, no accounts, no saved history. Nothing is ever published or scheduled.

Run: streamlit run app.py
"""

from __future__ import annotations

from datetime import date, datetime, time

from src.redaction import install_log_redaction

install_log_redaction()  # before anything else logs

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from src import oauth_state, scheduling  # noqa: E402
from src.config import settings  # noqa: E402
from src.content_parser import parse_gemini_response, parse_uploaded_result  # noqa: E402
from src.drive_client import cleanup_work_dir  # noqa: E402
from src.drive_errors import AuthError, AuthExpiredError, DriveError, NotConnectedError  # noqa: E402
from src.gemini_parser import GeminiRetrievalError, fetch_shared_conversation  # noqa: E402
from src.google_auth import GoogleAuthManager  # noqa: E402
from src.matching import confirm_token, match_videos  # noqa: E402
from src.mock_data import SAMPLE_DRIVE_URL, SAMPLE_GEMINI_TEXT  # noqa: E402
from src.redaction import redact  # noqa: E402
from src.results import build_result, drive_preview_link, summary_row  # noqa: E402
from src.ui import components as ui  # noqa: E402
from src.ui import drive_panel  # noqa: E402
from src.ui.mapping_panel import render_mapping_panel  # noqa: E402
from src.validation import validate_sources  # noqa: E402
from src.video_source import GoogleDriveSource, MockDriveSource, sync_records  # noqa: E402

st.set_page_config(page_title=settings.app_name, page_icon=settings.app_icon, layout="wide")
ss = st.session_state

ss.setdefault("flash", None)
ss.setdefault("content", {})
ss.setdefault("parse_result", None)
ss.setdefault("content_source", "")
ss.setdefault("drive_listing", None)
ss.setdefault("drive_error", None)
ss.setdefault("records", [])
ss.setdefault("match_overrides", {})
ss.setdefault("match_confirmed", set())
ss.setdefault("match_skipped", set())
ss.setdefault("originals", {})        # video_id -> datetime override (session only)
ss.setdefault("user_confirmed", {})   # (video_id, platform) -> True when the user says so
ss.setdefault("use_mock_drive", not settings.google_client_secrets_file.is_file())
if "auth" not in ss:
    # Token lives only in this session's memory – never written to disk or a database.
    ss.auth = GoogleAuthManager(settings.google_client_secrets_file,
                                store=oauth_state.SessionTokenStore(ss))
    cleanup_work_dir(settings.work_dir, 1)


def _state_key() -> bytes:
    return oauth_state.signing_key(settings.google_client_secrets_file)


# ---------- Google OAuth web callback ----------
params = st.query_params
if "code" in params or "error" in params:
    if "error" in params:
        ss.flash = ("warning", "Google access was not granted. Connect again and allow "
                               "read-only Drive access to use private folders.")
    else:
        try:
            if not oauth_state.verify_state(_state_key(), params.get("state", "")):
                raise AuthError("The Google sign-in link expired or is invalid. Try again.")
            ss.auth.complete_web_flow(settings.google_redirect_uri, params.get("state"),
                                      params.get("code"))
            ss.flash = ("success", "Google Drive connected (read-only). Paste your folder URL.")
        except (DriveError, ValueError) as exc:
            ss.flash = ("error", str(exc))
    st.query_params.clear()

# ---------- sidebar ----------
with st.sidebar:
    st.header("⚙️ Options")
    if st.button("Load sample data", use_container_width=True):
        ss.drive_url, ss.gemini_text, ss.use_mock_drive = SAMPLE_DRIVE_URL, SAMPLE_GEMINI_TEXT, True
        st.toast("Sample inputs filled. Click 'Get videos & captions'.")
    if st.button("Clear results", use_container_width=True):
        for k in ("records", "drive_listing", "drive_error", "content", "parse_result",
                  "match_overrides", "match_confirmed", "match_skipped", "originals",
                  "user_confirmed"):
            ss.pop(k, None)
        st.rerun()

auth_url = None
if settings.google_redirect_uri and not ss.use_mock_drive and ss.auth.client_configured() \
        and not ss.auth.is_connected():
    try:
        auth_url = ss.auth.authorization_url(settings.google_redirect_uri,
                                             oauth_state.make_state(_state_key()))
    except (DriveError, ValueError) as exc:
        st.sidebar.error(f"Google sign-in is misconfigured: {redact(exc)}")

g = drive_panel.render_google_account(ss.auth, auth_url)
if g["connect"]:
    with st.spinner("Waiting for Google sign-in in your browser…"):
        try:
            ss.auth.connect()
            ss.flash = ("success", "Google account connected (read-only).")
        except DriveError as exc:
            ss.flash = ("error", str(exc))
    st.rerun()
if g["disconnect"]:
    ss.auth.disconnect()
    for k in ("google_email", "drive_listing", "records"):
        ss.pop(k, None)
    ss.flash = ("info", "Google account disconnected and access revoked.")
    st.rerun()

source = MockDriveSource() if g["mock"] else GoogleDriveSource(ss.auth, settings.work_dir)

with st.sidebar:
    st.subheader("Original date/time")
    tz_name = settings.default_timezone
    default_date = st.date_input("Default original date", value=None,
                                 help="Used for every video without its own date. Leave empty "
                                      "to use each Drive file's modified time.")
    default_time = st.time_input("Default original time", value=time(18, 30), step=60)


def original_for(rec) -> datetime | None:
    if rec.video_id in ss.originals:
        return ss.originals[rec.video_id]
    if default_date:
        return datetime.combine(default_date, default_time)
    video = next((v for v in (ss.drive_listing.videos if ss.drive_listing else [])
                  if v.id == rec.video_id), None)
    raw = getattr(video, "modified_time", "") if video else ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
        return scheduling.to_local(dt, tz_name).replace(tzinfo=None) if dt else None
    except (ValueError, scheduling.ScheduleParseError):
        return None


def resync() -> None:
    videos = ss.drive_listing.videos if ss.drive_listing else []
    ss.records = sync_records(ss.records, videos, ss.content, ss.match_overrides,
                              ss.match_confirmed, ss.match_skipped)


# ---------- header ----------
st.title(f"{settings.app_icon} {settings.app_name}")
st.caption("Get Instagram & Facebook text and a proposed schedule date for each Drive video. "
           "This app never publishes or schedules posts – you do that in the native apps.")
if ss.flash:
    getattr(st, ss.flash[0])(ss.flash[1])
    ss.flash = None
if isinstance(ss.drive_error, (AuthExpiredError, NotConnectedError)) and not g["mock"]:
    st.error(f"🔑 {ss.drive_error} Use **Connect Google account** in the sidebar.")

# ---------- inputs ----------
with st.form("source_form"):
    c1, c2 = st.columns(2)
    drive_url = c1.text_input("Google Drive folder URL *", key="drive_url",
                              placeholder="https://drive.google.com/drive/folders/...")
    gemini_url = c2.text_input("Gemini shared conversation URL", key="gemini_url",
                               placeholder="https://share.gemini.google/… or "
                                           "https://gemini.google.com/share/…")
    gemini_text = st.text_area("Or paste the Gemini response (used instead of the URL)",
                               key="gemini_text", height=160)
    upload = st.file_uploader("Or upload the Gemini response as a text file",
                              type=["txt", "md", "json"])
    submitted = st.form_submit_button("Get videos & captions", type="primary")

if submitted:
    errors = validate_sources(drive_url, gemini_url, bool(gemini_text.strip()), upload is not None)
    for e in errors:
        st.error(e)
    if not errors:
        result = label = None
        try:
            if upload is not None:
                result, label = parse_uploaded_result(upload.name, upload.getvalue()), upload.name
            elif gemini_text.strip():
                result, label = parse_gemini_response(gemini_text), "pasted text"
            else:
                with st.spinner("Reading the Gemini conversation…"):
                    result = parse_gemini_response(fetch_shared_conversation(gemini_url))
                label = "Gemini shared URL"
        except GeminiRetrievalError as exc:
            st.warning(f"**Could not retrieve the Gemini conversation automatically.** {exc} "
                       "Paste the response text into the box above instead.")
        except Exception as exc:  # malformed file etc.
            st.error(f"Could not read the Gemini content: {redact(exc)}")
        if result is not None and not result.has_content:
            st.error("No numbered responses with Instagram/Facebook sections were found.")
        elif result is not None:
            ss.content, ss.parse_result, ss.content_source = result.content, result, label
            ss.match_overrides, ss.match_confirmed, ss.match_skipped = {}, set(), set()
            ss.records = []
            try:
                with st.spinner("Reading the Google Drive folder…"):
                    ss.drive_listing = source.list_videos(drive_url)
                ss.drive_error = None
                resync()
                if not ss.drive_listing.videos:
                    st.warning("The folder has no supported video files (MP4/MOV).")
            except DriveError as exc:
                ss.drive_error, ss.drive_listing = exc, None
                st.error(f"Google Drive: {exc}")

if ss.parse_result:
    ui.render_parse_issues(ss.parse_result.issues, ss.content_source)
if ss.drive_listing and ss.drive_listing.skipped:
    with st.expander(f"{len(ss.drive_listing.skipped)} unsupported file(s) ignored"):
        for s in ss.drive_listing.skipped:
            st.caption(f"{s.name} – {s.reason}")

# ---------- matching ----------
if ss.content and ss.drive_listing and ss.drive_listing.videos:
    report = match_videos([v.to_dict() for v in ss.drive_listing.videos], list(ss.content),
                          ss.match_overrides, ss.match_confirmed, ss.match_skipped)
    actions = render_mapping_panel(report, sorted(k for k in ss.content if k != "(unassigned)"))
    for act in actions:
        kind, vid = act[0], act[1]
        if kind == "assign":
            ss.match_overrides[vid] = act[2]
        elif kind == "confirm":
            ss.match_confirmed.add(confirm_token(vid, act[2]))
        elif kind == "skip":
            ss.match_skipped.add(vid)
        elif kind == "unskip":
            ss.match_skipped.discard(vid)
        elif kind == "auto":
            ss.match_overrides.pop(vid, None)
            ss.match_confirmed = {t for t in ss.match_confirmed if not t.startswith(f"{vid}::")}
    if actions:
        resync()
        st.rerun()

# ---------- results ----------
records = [r for r in ss.records if not r.skipped]
if not records:
    st.info("Paste a Drive folder URL and the Gemini response, then click "
            "**Get videos & captions**. Use **Load sample data** to try it.")
    st.stop()

results = [build_result(r, original_for(r), sample=g["mock"]) for r in records]
st.subheader("Results")
st.caption(f"Times are wall-clock times ({tz_name}). The proposed date is a suggestion only – "
           "nothing has been scheduled.")
st.dataframe([summary_row(r) for r in results], hide_index=True, use_container_width=True)

for r in results:
    with st.expander(f"{r.filename}  ·  File #{r.file_number if r.file_number is not None else '—'}"
                     f" → Gemini #{r.response_number if r.response_number is not None else '—'}"
                     + ("  ⚠️" if r.warnings else ""), expanded=bool(r.warnings)):
        for w in r.warnings:
            st.warning(w)
        if r.link:
            l1, l2 = st.columns([1, 3])
            l1.link_button("▶️ Open video in Drive", r.link)
            if l2.toggle("Show preview", key=f"pv_{r.video_id}"):
                components.iframe(drive_preview_link(r.video_id), height=360)
        else:
            st.caption("Sample video – no Drive file.")

        d1, d2, d3 = st.columns([1, 1, 2])
        o = r.original or datetime.combine(date.today(), default_time)
        new_d = d1.date_input("Original date", o.date(), key=f"od_{r.video_id}")
        new_t = d2.time_input("Original time", o.time(), step=60, key=f"ot_{r.video_id}")
        new_orig = datetime.combine(new_d, new_t)
        if r.original is None or new_orig != r.original:
            if d1.button("Apply", key=f"oa_{r.video_id}"):
                ss.originals[r.video_id] = new_orig
                st.rerun()
        d3.markdown(f"**Proposed Schedule Date/Time:** {scheduling.fmt(r.proposed)}  \n"
                    "_Not scheduled – use this when scheduling manually._")

        ci, cf = st.columns(2)
        with ci:
            st.markdown("**Instagram Reels – caption & hashtags**")
            if r.instagram_text:
                st.code(r.instagram_text, language=None, wrap_lines=True)  # copy button
            else:
                st.warning("Instagram caption not found.")
        with cf:
            st.markdown("**Facebook Reels – description & hashtags**")
            if r.facebook_text:
                st.code(r.facebook_text, language=None, wrap_lines=True)  # copy button
            else:
                st.warning("Facebook description not found.")

        m1, m2 = st.columns(2)
        for col, platform in ((m1, "Instagram"), (m2, "Facebook")):
            key = (r.video_id, platform)
            ss.user_confirmed[key] = col.checkbox(
                f"I scheduled this on {platform} myself", value=ss.user_confirmed.get(key, False),
                key=f"uc_{r.video_id}_{platform}",
                help="Your own note for this session only – the app does not check or post.")

