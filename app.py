"""StylePick Finds Auto Publisher – Streamlit dashboard.

Google Drive: real (OAuth) or sample mode. Gemini: shared URL with paste/file fallback.
Matching: deterministic filename/ID matching with manual confirmation.
Publishing: bulk coordinator → Facebook Page Reels / Instagram Reels (live, opt-in) or simulated.
History: every attempt persisted in SQLite; statuses survive restarts; duplicate protection.
Security: tokens only in the OS credential vault; logs/errors redacted; guided re-authorization.

Run: streamlit run app.py
"""

from __future__ import annotations

from src.redaction import install_log_redaction

install_log_redaction()  # before anything else logs

import streamlit as st  # noqa: E402

from src.config import settings  # noqa: E402
from src.content_parser import ParseResult, parse_gemini_response, parse_uploaded_result  # noqa: E402
from src.drive_client import cleanup_work_dir  # noqa: E402
from src.drive_errors import AuthExpiredError, DriveError, NotConnectedError  # noqa: E402
from src.facebook_publisher import FacebookReelsPublisher  # noqa: E402
from src.gemini_parser import GeminiRetrievalError, fetch_shared_conversation  # noqa: E402
from src.google_auth import GoogleAuthManager  # noqa: E402
from src.history_store import SIMULATED, HistoryStore, HistoryStoreError  # noqa: E402
from src.instagram_publisher import InstagramReelsPublisher  # noqa: E402
from src.job_store import PUBLISHED, REMOTE_PENDING, JobStore  # noqa: E402
from src.live_publisher import RoutingPublisher  # noqa: E402
from src.matching import confirm_token, match_videos, stem  # noqa: E402
from src.meta_auth import MetaAuthManager  # noqa: E402
from src.meta_errors import MetaError, MetaTokenExpiredError, MetaUnsupportedContentError  # noqa: E402
from src.meta_graph import GraphClient  # noqa: E402
from src.mock_data import SAMPLE_DRIVE_URL, SAMPLE_GEMINI_TEXT, SAMPLE_GEMINI_URL  # noqa: E402
from src.models import PUBLISHABLE_PLATFORMS, Platform, Status  # noqa: E402
from src.publishing_coordinator import ACTIVE, PublishingCoordinator, restore_status, summarize  # noqa: E402
from src.publishing_service import MockPublisher  # noqa: E402
from src.redaction import redact  # noqa: E402
from src.ui import batch_panel  # noqa: E402
from src.ui import components as ui  # noqa: E402
from src.ui import drive_panel  # noqa: E402
from src.ui.facebook_panel import reconnect_facebook, render_facebook_account  # noqa: E402
from src.ui.history_panel import render_history_panel  # noqa: E402
from src.ui.instagram_panel import render_instagram_account  # noqa: E402
from src.ui.mapping_panel import render_mapping_panel  # noqa: E402
from src.validation import revalidate, validate_sources  # noqa: E402
from src.video_source import GoogleDriveSource, MockDriveSource, sync_records  # noqa: E402

st.set_page_config(page_title=settings.app_name, page_icon="📹", layout="wide")

# ---------- session state ----------
ss = st.session_state
ss.setdefault("records", [])
ss.setdefault("history", [])
ss.setdefault("publisher", MockPublisher(delay=0.05))
ss.setdefault("flash", None)
ss.setdefault("content", {})
ss.setdefault("parse_result", None)
ss.setdefault("content_source", "")
ss.setdefault("drive_listing", None)
ss.setdefault("drive_url_loaded", "")
ss.setdefault("drive_selected", {})
ss.setdefault("download_state", {})
ss.setdefault("drive_error", None)
ss.setdefault("match_overrides", {})
ss.setdefault("match_confirmed", set())
ss.setdefault("match_skipped", set())
ss.setdefault("batch_jobs", [])
ss.setdefault("batch_started", False)
ss.setdefault("retryable", {})
ss.setdefault("use_mock_drive", not settings.google_client_secrets_file.is_file())
if "auth" not in ss:
    ss.auth = GoogleAuthManager(settings.google_client_secrets_file)
    cleanup_work_dir(settings.work_dir, settings.download_retention_hours)
if "meta_auth" not in ss:
    ss.graph = GraphClient(settings.meta_graph_version)
    ss.meta_auth = MetaAuthManager(settings.meta_app_id, settings.meta_app_secret, ss.graph,
                                   redirect_port=settings.meta_redirect_port,
                                   platforms=settings.meta_platforms)
    ss.job_store = JobStore(settings.job_db_path)
if "history_store" not in ss:
    try:
        ss.history_store = HistoryStore(settings.job_db_path)
        ss.history_error = None
        if ss.history_store.recovered:
            ss.flash = ("warning", f"{ss.history_store.recovered} publishing attempt(s) were "
                                   "interrupted last time. They are marked 'uncertain' and will be "
                                   "verified before any retry.")
    except HistoryStoreError as exc:
        ss.history_store, ss.history_error = None, str(exc)

# ---------- sidebar ----------
with st.sidebar:
    st.header("⚙️ Options")
    if st.button("Load sample data", use_container_width=True):
        ss.drive_url, ss.gemini_url, ss.gemini_text = SAMPLE_DRIVE_URL, SAMPLE_GEMINI_URL, SAMPLE_GEMINI_TEXT
        ss.use_mock_drive = True
        st.toast("Sample inputs filled. Click 'Check Videos & Captions'.")
    st.download_button("Download sample caption file", SAMPLE_GEMINI_TEXT.encode("utf-8"),
                       file_name="sample_gemini_captions.txt", use_container_width=True)
    if st.button("Reset session", use_container_width=True,
                 help="Clears the current session. Saved publishing history is kept."):
        for k in ("records", "history", "drive_listing", "drive_selected", "download_state",
                  "drive_error", "content", "parse_result", "match_overrides", "match_confirmed",
                  "match_skipped", "batch_jobs", "batch_started", "retryable"):
            ss.pop(k, None)
        ss.publisher = MockPublisher(delay=0.05)
        st.rerun()


def connect_google() -> None:
    with st.spinner("Waiting for Google sign-in in your browser…"):
        try:
            ss.auth.connect()
            ss.drive_error = None
            ss.flash = ("success", "Google account connected.")
        except DriveError as exc:
            ss.flash = ("error", str(exc))
    st.rerun()


g = drive_panel.render_google_account(ss.auth)
if g["connect"]:
    connect_google()
if g["disconnect"]:
    ss.auth.disconnect()
    ss.pop("google_email", None)
    ss.drive_listing, ss.records = None, []
    ss.flash = ("info", "Google account disconnected: access revoked and token removed from this "
                        "computer.")
    st.rerun()
fb = render_facebook_account(ss.meta_auth)
if not ss.meta_auth.is_connected():
    for k in ("ig_accounts", "ig_notes", "ig_live"):
        ss.pop(k, None)
meta_reauth = ss.meta_auth.is_connected() and ss.meta_auth.needs_reauth()
ig = render_instagram_account(ss.meta_auth) if not meta_reauth else {"live": False, "account": None}

source = MockDriveSource() if g["mock"] else GoogleDriveSource(ss.auth, settings.work_dir)


def selected_videos():
    lst = ss.drive_listing
    return [v for v in lst.videos if ss.drive_selected.get(v.id, True)] if lst else []


def account_for(platform: Platform) -> tuple[str, str]:
    if platform == Platform.FACEBOOK and fb["live"] and fb["page"]:
        return fb["page"].id, "live"
    if platform == Platform.INSTAGRAM and ig["live"] and ig["account"]:
        return ig["account"].id, "live"
    return SIMULATED, SIMULATED


def apply_stored_status() -> None:
    if ss.history_store is not None:
        try:
            ss.retryable.update(restore_status(ss.records, ss.history_store, account_for))
        except HistoryStoreError as exc:
            st.error(str(exc))
    live = {Platform.FACEBOOK: fb["page"].id if fb["page"] else None,
            Platform.INSTAGRAM: ig["account"].id if ig["account"] else None}
    for platform, account_id in live.items():
        if not account_id:
            continue
        for rec in ss.records:
            job = ss.job_store.get(rec.video_id, platform.value.lower(), account_id)
            if not job:
                continue
            if job.status == PUBLISHED:
                rec.platform_status[platform] = Status.PUBLISHED
            elif (job.status in REMOTE_PENDING
                  and rec.platform_status.get(platform) != Status.PUBLISHED):
                rec.platform_status[platform] = Status.PROCESSING


def resync() -> None:
    ss.records = sync_records(ss.records, selected_videos(), ss.content, ss.match_overrides,
                              ss.match_confirmed, ss.match_skipped)
    apply_stored_status()


def load_folder(url: str) -> bool:
    try:
        with st.spinner("Reading Google Drive folder…"):
            listing = source.list_videos(url)
    except DriveError as exc:
        ss.drive_error = exc
        return False
    ss.drive_error, ss.drive_listing, ss.drive_url_loaded = None, listing, url
    ss.drive_selected = {v.id: ss.drive_selected.get(v.id, True) for v in listing.videos}
    ss.download_state = {k: v for k, v in ss.download_state.items() if k in ss.drive_selected}
    resync()
    return True


def load_gemini(form: dict) -> tuple[ParseResult | None, str]:
    if form["upload"] is not None:
        return parse_uploaded_result(form["upload"].name, form["upload"].getvalue()), \
            f"file '{form['upload'].name}'"
    if form["text"].strip():
        return parse_gemini_response(form["text"]), "pasted text"
    with st.spinner("Retrieving Gemini shared conversation…"):
        text = fetch_shared_conversation(form["gemini_url"])
    return parse_gemini_response(text), "Gemini shared URL"


def resolve_video_path(rec):
    if g["mock"]:
        raise MetaUnsupportedContentError(
            "Live publishing needs real Google Drive videos – sample Drive mode is on.")
    video = next((v for v in (ss.drive_listing.videos if ss.drive_listing else [])
                  if v.id == rec.video_id), None)
    if video is None:
        raise MetaUnsupportedContentError(f"Drive file for '{rec.filename}' not found.")
    try:
        path = source.download(video)
    except (AuthExpiredError, NotConnectedError) as exc:
        ss.drive_error = exc
        raise MetaUnsupportedContentError(f"Google authorization needed: {exc}") from exc
    except DriveError as exc:
        raise MetaUnsupportedContentError(f"Could not download '{rec.filename}': {exc}") from exc
    ss.download_state[video.id] = ("✅ Downloaded & verified", str(path))
    return path, None


def current_publisher(platforms: set[Platform]):
    fb_pub = ig_pub = None
    if Platform.FACEBOOK in platforms and fb["live"] and fb["page"]:
        page = ss.meta_auth.page_for_publishing()
        fb_pub = FacebookReelsPublisher(ss.graph, page.id, page.access_token, ss.job_store,
                                        poll_timeout_s=settings.fb_poll_timeout_s)
    if Platform.INSTAGRAM in platforms and ig["live"] and ig["account"]:
        acc = ss.meta_auth.instagram_for_publishing()
        ig_pub = InstagramReelsPublisher(ss.graph, acc.id, acc.access_token, ss.job_store,
                                         poll_timeout_s=settings.fb_poll_timeout_s)
        ig_pub.check_quota()
    if not (fb_pub or ig_pub):
        return ss.publisher
    return RoutingPublisher(fb_pub, resolve_video_path, ss.publisher, instagram=ig_pub)


def mode_text() -> str:
    return " · ".join([
        f"Facebook {'LIVE → ' + fb['page'].name if fb['live'] else 'simulated'}",
        f"Instagram {'LIVE → ' + ig['account'].label if ig['live'] else 'simulated'}"])


def prepare(jobs) -> None:
    ss.batch_jobs, ss.batch_started, ss.confirm_batch = jobs, False, False
    if ss.history_store is not None and jobs:
        try:
            n = PublishingCoordinator.mark_duplicates(jobs, ss.history_store, account_for)
        except HistoryStoreError as exc:
            n = 0
            ss.flash = ("error", str(exc))
        if n:
            ss.flash = ("warning", f"⚠️ {n} job(s) were already published before and were held back "
                                   "as duplicates. Review them below.")


def run_jobs(jobs) -> None:
    active = [j for j in jobs if j.status in ACTIVE]
    if not active:
        ss.flash = ("warning", "Nothing to publish – no pending jobs.")
        return
    if ss.history_store is None:
        ss.flash = ("error", "Publishing is disabled because the history database is unavailable "
                             f"({ss.history_error}). Posts would not be tracked.")
        return
    try:
        publisher = current_publisher({j.platform for j in active})
    except MetaTokenExpiredError as exc:
        ss.meta_auth.mark_needs_reauth(str(exc))
        ss.flash = ("error", f"🔑 {redact(exc)} Click 'Reconnect Facebook' in the sidebar, then "
                             "start again. Nothing was published.")
        return
    except MetaError as exc:
        ss.flash = ("error", f"Publishing blocked before start: {redact(exc)}")
        return
    coord = PublishingCoordinator(publisher, ss.history, store=ss.history_store,
                                  account_for=account_for)
    holder = st.empty()

    def on_update(job, summary):
        with holder.container():
            st.caption(f"{job.filename} → {job.platform.value}: {job.status}")
            batch_panel.render_summary(summary)

    s = coord.run(jobs, ss.records, on_update)
    for j in jobs:
        if j.status in ("failed", "published", "processing"):
            ss.retryable[(j.video_id, j.platform.value)] = j.retryable
    msg = (f"Batch finished ({mode_text()}): {s.published} published, {s.processing} awaiting "
           f"confirmation, {s.failed} failed, {s.deferred} deferred, {s.cancelled} cancelled.")
    if coord.auth_blocked:
        ss.meta_auth.mark_needs_reauth("Token rejected during publishing.")
        msg += (" 🔑 Authorization expired for " + ", ".join(p.value for p in coord.auth_blocked)
                + " – reconnect Facebook, then use Retry Failed. Successful posts are kept.")
    ss.flash = ("success" if not (s.failed or s.processing or s.deferred) else "warning", msg)


ui.render_header(settings.app_name)
if ss.history_store is None:
    st.error(f"Publishing history unavailable – publishing is disabled. {ss.history_error}")

# ---------- re-authorization banners ----------
if meta_reauth:
    b1, b2 = st.columns([4, 1])
    b1.error("🔑 Your Facebook/Instagram authorization expired or was revoked. Live publishing is "
             "paused; nothing will be posted until you reconnect. Pending posts are kept and "
             "verified before any retry.")
    if b2.button("Reconnect Facebook", type="primary", key="banner_fb"):
        reconnect_facebook(ss.meta_auth)
if isinstance(ss.drive_error, (AuthExpiredError, NotConnectedError)) and not g["mock"]:
    b1, b2 = st.columns([4, 1])
    b1.error(f"🔑 {ss.drive_error}")
    if b2.button("Reconnect Google", type="primary", key="banner_google",
                 disabled=not ss.auth.client_configured()):
        connect_google()

if fb["live"] or ig["live"]:
    st.warning(f"🔴 Live publishing is ON – {mode_text()}")
if ss.flash:
    getattr(st, ss.flash[0])(ss.flash[1])
    ss.flash = None

# ---------- source input ----------
form = ui.render_source_inputs()
if form:
    errors = validate_sources(form["drive_url"], form["gemini_url"],
                              bool(form["text"].strip()), form["upload"] is not None)
    for e in errors:
        st.error(e)
    if not errors:
        result = None
        try:
            result, label = load_gemini(form)
        except GeminiRetrievalError as exc:
            st.warning(f"**Could not retrieve the Gemini conversation.** {exc}")
        except Exception as exc:  # malformed file etc.
            st.error(f"Could not read caption content: {redact(exc)}")
        if result is not None:
            if not result.has_content:
                st.error("No video sections found in the Gemini content. Expected platform sections "
                         "(YouTube Shorts / Instagram Reels / Facebook Reels) under headers like "
                         "`=== VIDEO: filename.mp4 ===`.")
            else:
                ss.content, ss.parse_result, ss.content_source = result.content, result, label
                ss.records, ss.batch_jobs, ss.batch_started = [], [], False
                ss.match_overrides, ss.match_confirmed, ss.match_skipped = {}, set(), set()
                if load_folder(form["drive_url"]):
                    records = ss.records
                    done = sum(1 for r in records for p in PUBLISHABLE_PLATFORMS
                               if r.platform_status.get(p) == Status.PUBLISHED)
                    st.success(f"Loaded {len(records)} videos; {sum(r.is_valid for r in records)} "
                               f"valid" + (f"; {done} platform post(s) already published earlier."
                                           if done else "."))

if ss.parse_result:
    ui.render_parse_issues(ss.parse_result.issues, ss.content_source)

# ---------- Google Drive videos ----------
if not isinstance(ss.drive_error, (AuthExpiredError, NotConnectedError)):
    if drive_panel.render_drive_error(ss.drive_error, key="retry_list"):
        load_folder(ss.get("drive_url", "") or ss.drive_url_loaded)
        st.rerun()

if ss.drive_listing:
    st.subheader("Google Drive videos")
    new_sel = drive_panel.render_video_selection(ss.drive_listing, ss.drive_selected,
                                                 ss.download_state)
    if new_sel != ss.drive_selected:
        ss.drive_selected = new_sel
        resync()
        st.rerun()
    d1, d2, d3 = st.columns(3)
    if d1.button("🔄 Refresh folder", use_container_width=True):
        load_folder(ss.drive_url_loaded)
        st.rerun()
    sel = selected_videos()
    failed_ids = {k for k, (s, _) in ss.download_state.items() if "Failed" in s}
    dl_targets = None
    if d2.button(f"⬇️ Download selected ({len(sel)})", disabled=not sel, use_container_width=True):
        dl_targets = sel
    if d3.button("🔁 Retry failed downloads", disabled=not failed_ids, use_container_width=True):
        dl_targets = [v for v in sel if v.id in failed_ids]
    if dl_targets:
        bar = st.progress(0.0)
        for i, v in enumerate(dl_targets):
            ss.download_state[v.id] = ("⬇️ Downloading", "")
            try:
                path = source.download(
                    v, lambda p, i=i, v=v: bar.progress(
                        min((i + p) / len(dl_targets), 1.0), text=f"Downloading {v.name} ({p:.0%})"))
                ss.download_state[v.id] = ("✅ Downloaded & verified", str(path))
            except (AuthExpiredError, NotConnectedError) as exc:
                ss.drive_error = exc
                ss.download_state[v.id] = ("❌ Failed", str(exc))
                break
            except DriveError as exc:
                ss.download_state[v.id] = ("❌ Failed", str(exc))
        ok = sum("verified" in ss.download_state[v.id][0] for v in dl_targets
                 if v.id in ss.download_state)
        ss.flash = ("success" if ok == len(dl_targets) else "warning",
                    f"Downloaded {ok}/{len(dl_targets)} video(s) to {settings.work_dir}.")
        st.rerun()
    for v in sel:
        state, msg = ss.download_state.get(v.id, ("", ""))
        if "Failed" in state:
            st.error(f"{v.name}: {msg}")

# ---------- matching ----------
if ss.content and selected_videos():
    report = match_videos([v.to_dict() for v in selected_videos()], list(ss.content),
                          ss.match_overrides, ss.match_confirmed, ss.match_skipped)
    actions = render_mapping_panel(report, sorted(k for k in ss.content if k != "(unassigned)")
                                   + (["(unassigned)"] if "(unassigned)" in ss.content else []))
    if actions:
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
        resync()
        st.rerun()

records = ss.records
if not ss.batch_started or not any(j.status in ACTIVE for j in ss.batch_jobs):
    apply_stored_status()
st.divider()
ui.render_summary_cards(records)

if not records:
    ui.render_empty_state()
    render_history_panel(ss.history_store)
    st.stop()

# ---------- table ----------
st.subheader("2. Videos & captions")
ui.render_video_table(records)

# ---------- content preview ----------
st.subheader("3. Content preview & editing")
names = [r.filename for r in records]
sel_name = st.selectbox("Select a video", names, key="selected_video")
rec = records[names.index(sel_name)]
st.markdown(f"**Status:** {ui.badge(rec.overall_status)} · **Mapping:** {ui.match_badge(rec)}"
            + (f" → `{rec.content_key}` ({rec.match_method})" if rec.content_key else ""))
for e in rec.errors:
    st.error(e)
rec_issues = [i for i in (ss.parse_result.issues if ss.parse_result else [])
              if rec.content_key and i.video and stem(i.video) == stem(rec.content_key)]
if ui.render_content_editor(rec, rec_issues, ss.content.get(rec.content_key) if rec.content_key else None):
    revalidate(rec)
    st.rerun()

c1, c2, c3 = st.columns(3)
single = None
if c1.button("Publish to Facebook", disabled=not rec.is_valid, use_container_width=True):
    single = [Platform.FACEBOOK]
if c2.button("Publish to Instagram", disabled=not rec.is_valid, use_container_width=True):
    single = [Platform.INSTAGRAM]
if c3.button("Publish to Both", disabled=not rec.is_valid, use_container_width=True):
    single = list(PUBLISHABLE_PLATFORMS)
if single:
    jobs = PublishingCoordinator.plan(records, single, [rec.video_id])
    if not jobs:
        ss.flash = ("info", "Already published or in progress on the chosen platform(s).")
    else:
        prepare(jobs)
        if not any(j.duplicate for j in jobs):
            ss.batch_started = True
            run_jobs(jobs)
    st.rerun()

# ---------- bulk publishing ----------
st.subheader("4. Bulk publishing")
st.caption(mode_text())
if not any(r.is_valid for r in records):
    st.error("Publishing is disabled: there are no valid records.")
else:
    excluded = [r.filename for r in records if not r.is_valid]
    if excluded:
        st.caption(f"Excluded until corrected or skipped: {len(excluded)} video(s).")
    req = batch_panel.render_batch_setup(records)
    if req:
        jobs = PublishingCoordinator.plan(records, req["platforms"], req["video_ids"])
        prepare(jobs)
        if not jobs:
            ss.flash = ("info", "Nothing to publish – everything selected is already published or "
                                "in progress.")
        st.rerun()

r1, r2 = st.columns(2)
retry_jobs = PublishingCoordinator.plan_retry(records, ss.retryable)
non_retryable = sum(1 for r in records for p in r.selected_platforms
                    if r.platform_status.get(p) == Status.FAILED
                    and not ss.retryable.get((r.video_id, p.value), True))
if r1.button(f"🔁 Retry Failed ({len(retry_jobs)})", disabled=not retry_jobs,
             use_container_width=True,
             help="Retries only failures verified as retryable and re-checks processing/uncertain "
                  "posts. Platforms that already succeeded are never re-posted."):
    prepare(retry_jobs)
    st.rerun()
if non_retryable:
    r2.caption(f"{non_retryable} failure(s) need fixing first (e.g. permissions or unsupported "
               "content) – they are not retried automatically.")

jobs = ss.batch_jobs
if jobs:
    pending = [j for j in jobs if j.status in ACTIVE]
    dups = [j for j in jobs if j.duplicate and j.status == "cancelled"]
    st.markdown(f"**Current batch – {len(jobs)} job(s)**")
    if dups and not ss.batch_started:
        st.warning(f"⚠️ {len(dups)} duplicate(s) held back: these video/platform combinations were "
                   "already published successfully. Publishing again creates a second post.")
        if st.button(f"Publish duplicates anyway ({len(dups)})"):
            PublishingCoordinator.allow_duplicates(jobs)
            st.rerun()
    if not ss.batch_started and pending:
        to_cancel = batch_panel.render_queue(jobs, editable=True)
        b1, b2, b3 = st.columns(3)
        if b1.button(f"🚫 Cancel ticked ({len(to_cancel)})", disabled=not to_cancel,
                     use_container_width=True):
            PublishingCoordinator.cancel(jobs, to_cancel)
            st.rerun()
        if b2.button("Discard batch", use_container_width=True):
            PublishingCoordinator.cancel(jobs, {j.id for j in pending})
            ss.batch_jobs = []
            st.rerun()
        if b3.button(f"✅ Start publishing ({len(pending)})", type="primary",
                     use_container_width=True, disabled=ss.history_store is None):
            ss.confirm_batch = True
        if ss.get("confirm_batch"):
            st.warning(f"⚠️ Publish **{len(pending)}** job(s)? ({mode_text()})")
            y, n = st.columns(2)
            if y.button("Confirm", type="primary", use_container_width=True):
                ss.confirm_batch, ss.batch_started = False, True
                run_jobs(jobs)
                st.rerun()
            if n.button("Back", use_container_width=True):
                ss.confirm_batch = False
                st.rerun()
    else:
        batch_panel.render_queue(jobs, editable=False)
    batch_panel.render_summary(summarize(jobs))

# ---------- progress & history ----------
st.subheader("5. Progress & history")
ui.render_progress(records)
ui.render_history(ss.history)
render_history_panel(ss.history_store)

