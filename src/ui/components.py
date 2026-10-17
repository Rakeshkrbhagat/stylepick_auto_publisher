"""Reusable Streamlit UI components. No business logic or API calls here."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.content_parser import ParseIssue
from src.models import (
    PUBLISHABLE_PLATFORMS, STATUS_ICONS, HistoryEntry, MatchStatus, Platform, PlatformContent,
    Status, VideoRecord,
)

PLATFORM_FIELDS = {
    Platform.INSTAGRAM: [("cover_title", "Reel cover title"), ("caption", "Caption"),
                         ("hashtags", "Hashtags"), ("on_screen_text", "On-screen text"),
                         ("transcript", "Optimized transcript")],
    Platform.FACEBOOK: [("seo_title", "SEO title"), ("description", "Description"),
                        ("caption", "Caption (if provided separately)"), ("hashtags", "Hashtags")],
    Platform.YOUTUBE: [("hook", "Hook"), ("title", "Title"), ("description", "Description"),
                       ("hashtags", "Hashtags"), ("youtube_tags", "YouTube tags"),
                       ("on_screen_text", "On-screen text"), ("transcript", "Transcript")],
}
_MULTILINE = {"caption", "description", "transcript", "on_screen_text"}
MATCH_ICONS = {
    MatchStatus.MATCHED: "🔗", MatchStatus.NEEDS_CONFIRMATION: "❓",
    MatchStatus.UNMATCHED: "⚠️", MatchStatus.SKIPPED: "⏭️",
}


def badge(status: Status) -> str:
    return f"{STATUS_ICONS.get(status, '')} {status.value}"


def match_badge(rec: VideoRecord) -> str:
    text = f"{MATCH_ICONS[rec.match_status]} {rec.match_status.value}"
    if rec.duplicate_with:
        text += " · duplicate"
    return text


def render_header(app_name: str) -> None:
    st.title(f"📹 {app_name}")
    st.caption("Google Drive: live (OAuth, read-only) or sample mode · Publishing: mock only – "
               "no data is sent to Meta.")


def render_summary_cards(records: list[VideoRecord]) -> None:
    total = len(records)
    ready = sum(1 for r in records if r.overall_status == Status.READY)
    published = sum(1 for r in records if r.overall_status == Status.PUBLISHED)
    failed = sum(1 for r in records if r.overall_status in (Status.FAILED, Status.RETRY_PENDING))
    invalid = sum(1 for r in records if r.overall_status == Status.INVALID)
    skipped = sum(1 for r in records if r.skipped)
    cols = st.columns(6)
    cols[0].metric("Total videos", total)
    cols[1].metric("Ready to publish", ready)
    cols[2].metric("Published", published)
    cols[3].metric("Failed", failed)
    cols[4].metric("Invalid", invalid)
    cols[5].metric("Skipped", skipped)


def render_account_status(accounts: dict[str, bool]) -> None:
    st.sidebar.subheader("Connected accounts")
    for name, connected in accounts.items():
        if connected:
            st.sidebar.success(f"{name}: Connected (mock)")
        else:
            st.sidebar.error(f"{name}: Not connected")


def render_source_inputs() -> dict:
    """Render the source form. Returns submitted values or {} when not submitted."""
    with st.form("source_form"):
        st.subheader("1. Sources")
        c1, c2 = st.columns(2)
        drive_url = c1.text_input("Google Drive folder URL *",
                                  placeholder="https://drive.google.com/drive/folders/...",
                                  key="drive_url")
        gemini_url = c2.text_input("Gemini shared conversation URL",
                                   placeholder="https://share.gemini.google/... or https://gemini.google.com/share/...",
                                   key="gemini_url",
                                   help="Use Gemini → Share → Create public link. Both "
                                        "share.gemini.google/<id> and gemini.google.com/share/<id> "
                                        "links are supported.")
        text = st.text_area(
            "Gemini response text (fallback – used instead of the URL when filled)", height=180,
            key="gemini_text",
            help="Paste the full Gemini response. Hindi, emojis, hashtags and line breaks are preserved.",
        )
        upload = st.file_uploader("Or import a Gemini response file", type=["txt", "md", "json"])
        submitted = st.form_submit_button("Check Videos & Captions", type="primary")
    if not submitted:
        return {}
    return {"drive_url": drive_url, "gemini_url": gemini_url, "text": text, "upload": upload}


def render_empty_state() -> None:
    st.info(
        "No videos loaded yet. Enter a Google Drive folder URL and Gemini content, "
        "then click **Check Videos & Captions**. Use **Load sample data** in the sidebar to try it out."
    )


def render_parse_issues(issues: list[ParseIssue], source_label: str) -> None:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    st.caption(f"Content source: {source_label}")
    if errors:
        with st.expander(f"⚠️ {len(errors)} parsing problem(s) need review", expanded=True):
            for i in errors:
                st.error(f"{i.video + ': ' if i.video else ''}{i.message}")
    if warnings:
        with st.expander(f"ℹ️ {len(warnings)} missing field(s) flagged"):
            st.dataframe(pd.DataFrame([{"Video": i.video, "Platform": i.platform,
                                        "Issue": i.message} for i in warnings]),
                         hide_index=True, use_container_width=True)


def _caption_status(rec: VideoRecord, p: Platform) -> str:
    if not rec.content:
        return "⚠️ Unmatched"
    return "✅ Found" if rec.has_caption(p) else "❌ Missing"


def render_video_table(records: list[VideoRecord]) -> None:
    rows = [{
        "Thumbnail": "🎬",
        "Video": r.filename,
        "Matched content": r.content_key or "—",
        "Match": match_badge(r),
        "Instagram caption": _caption_status(r, Platform.INSTAGRAM),
        "Facebook caption": _caption_status(r, Platform.FACEBOOK),
        "Platforms": ", ".join(p.value for p in r.selected_platforms) or "—",
        "Instagram status": badge(r.platform_status.get(Platform.INSTAGRAM, Status.PENDING)),
        "Facebook status": badge(r.platform_status.get(Platform.FACEBOOK, Status.PENDING)),
        "Validation": ("⏭️ Skipped" if r.skipped else
                       "✅ Valid" if r.is_valid else "⚠️ " + "; ".join(r.errors)),
    } for r in records]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _widget_key(rec: VideoRecord, platform: Platform, fld: str) -> str:
    return f"{rec.video_id}_{rec.content_key}_{platform.value}_{fld}"


def render_content_editor(rec: VideoRecord, issues: list[ParseIssue] | None = None,
                          original: dict[Platform, PlatformContent] | None = None) -> bool:
    """Edit platform content in tabs. Returns True if anything changed.

    Text is shown exactly as parsed; it only changes when the user edits it.
    Edited fields are marked and can be restored to the original Gemini text.
    """
    changed = False
    issues = issues or []
    original = original or {}
    if not rec.content:
        st.warning(
            f"No Gemini content matched **{rec.filename}**. Assign content in **Video ↔ content "
            "mapping** above, or make sure the response contains a "
            f"`=== VIDEO: {rec.filename} ===` section."
        )
    selected = st.multiselect(
        "Publish to", [p.value for p in PUBLISHABLE_PLATFORMS],
        default=[p.value for p in rec.selected_platforms], key=f"plat_{rec.video_id}",
    )
    new_sel = [Platform(v) for v in selected]
    if new_sel != rec.selected_platforms:
        rec.selected_platforms = new_sel
        changed = True

    tabs = st.tabs(["📸 Instagram Reels", "📘 Facebook Reels", "▶️ YouTube Shorts (reference)"])
    for tab, platform in zip(tabs, [Platform.INSTAGRAM, Platform.FACEBOOK, Platform.YOUTUBE]):
        with tab:
            content = rec.get_content(platform)
            orig = original.get(platform, PlatformContent())
            if platform == Platform.YOUTUBE:
                st.caption("YouTube publishing is not supported yet – shown for reference.")
            flagged = {i.field: i for i in issues if i.platform == platform.value and i.field}
            for i in issues:
                if i.platform == platform.value and not i.field:
                    st.warning(i.message)
            edited_fields = []
            for fld, label in PLATFORM_FIELDS[platform]:
                value = getattr(content, fld)
                issue = flagged.get(fld)
                if issue:
                    label = f"{label}  ⚠️"
                if original and value != getattr(orig, fld):
                    label = f"{label}  ✏️ edited"
                    edited_fields.append(fld)
                widget = st.text_area if fld in _MULTILINE else st.text_input
                kwargs = {"height": 140} if fld in _MULTILINE else {}
                new = widget(label, value=value, key=_widget_key(rec, platform, fld),
                             help=issue.message if issue else None, **kwargs)
                if new != value:
                    setattr(content, fld, new)
                    changed = True
            if edited_fields and st.button("↩️ Restore original Gemini text",
                                           key=f"restore_{rec.video_id}_{platform.value}"):
                for fld in edited_fields:
                    setattr(content, fld, getattr(orig, fld))
                    st.session_state.pop(_widget_key(rec, platform, fld), None)
                changed = True
            if platform != Platform.YOUTUBE:
                text = content.publish_text()
                st.caption(f"{len(text)} characters · {text.count('#')} hashtags")
                with st.expander("Preview final post text"):
                    st.text(text or "(empty)")
    return changed


def render_progress(records: list[VideoRecord]) -> None:
    jobs = [(r, p) for r in records if r.is_valid for p in r.selected_platforms]
    done = sum(1 for r, p in jobs if r.platform_status.get(p) == Status.PUBLISHED)
    failed = sum(1 for r, p in jobs if r.platform_status.get(p) in (Status.FAILED, Status.RETRY_PENDING))
    total = len(jobs)
    st.progress((done + failed) / total if total else 0.0,
                text=f"{done} published · {failed} failed · {total - done - failed} remaining of {total} posts")


def render_history(history: list[HistoryEntry]) -> None:
    with st.expander(f"📜 View History ({len(history)})"):
        if not history:
            st.caption("No publishing activity yet.")
            return
        df = pd.DataFrame([{
            "Time": h.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "Video": h.filename,
            "Platform": h.platform, "Status": h.status, "Details": h.message,
        } for h in reversed(history)])
        st.dataframe(df, hide_index=True, use_container_width=True)

