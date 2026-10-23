"""Reusable Streamlit UI components. No business logic or API calls here."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.content_parser import ParseIssue
from src.models import STATUS_ICONS, MatchStatus, Status

MATCH_ICONS = {
    MatchStatus.MATCHED: "🔗", MatchStatus.NEEDS_CONFIRMATION: "❓",
    MatchStatus.UNMATCHED: "⚠️", MatchStatus.SKIPPED: "⏭️",
}

NO_PUBLISH_NOTICE = (
    "This app never publishes or schedules anything on Instagram, Facebook or any other "
    "platform. It prepares captions and proposed dates; you schedule each post yourself in the "
    "native app, then record it here as **Scheduled by User**.")


def badge(status: Status) -> str:
    return f"{STATUS_ICONS.get(status, '')} {status.value}"


def render_header(app_name: str, tagline: str, icon: str) -> None:
    st.title(f"{icon} {app_name}")
    st.caption(tagline)
    st.info(NO_PUBLISH_NOTICE, icon="🛡️")


def render_summary_cards(statuses: list[Status]) -> None:
    cols = st.columns(6)
    for col, s in zip(cols, (Status.READY, Status.NEEDS_REVIEW, Status.READY_TO_SCHEDULE,
                             Status.SCHEDULED_BY_USER, Status.NEEDS_ATTENTION, Status.SKIPPED)):
        col.metric(badge(s), sum(1 for x in statuses if x == s))


def render_source_inputs(drive_url: str, gemini_text: str) -> dict:
    """Source form. Returns submitted values or {} when not submitted."""
    with st.form("source_form"):
        st.subheader("1. Sources")
        c1, c2 = st.columns(2)
        drive_url = c1.text_input("Your Google Drive folder URL *", value=drive_url,
                                  placeholder="https://drive.google.com/drive/folders/...")
        gemini_url = c2.text_input(
            "Gemini shared conversation URL",
            placeholder="https://gemini.google.com/share/... or https://share.gemini.google/...",
            help="Gemini → Share → Create public link.")
        text = st.text_area(
            "Gemini response text (fallback – used instead of the URL when filled)", value=gemini_text,
            height=180, help="Hindi, emojis, hashtags and line breaks are preserved exactly.")
        upload = st.file_uploader("Or upload the Gemini response as a file",
                                  type=["txt", "md", "json"])
        submitted = st.form_submit_button("Load videos & captions", type="primary",
                                          help="Reads data only – nothing is posted anywhere.")
    if not submitted:
        return {}
    return {"drive_url": drive_url, "gemini_url": gemini_url, "text": text, "upload": upload}


def render_empty_state() -> None:
    st.info("Your review queue is empty. Enter your Google Drive folder and the Gemini content, "
            "then click **Load videos & captions**. Use **Load sample data** in the sidebar to "
            "try it out.")


def render_parse_issues(issues: list[ParseIssue], source_label: str) -> None:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    st.caption(f"Content source: {source_label}")
    if errors:
        with st.expander(f"⚠️ {len(errors)} extraction problem(s) need review", expanded=True):
            for i in errors:
                st.error(f"{i.video + ': ' if i.video else ''}{i.message}")
    relevant = [i for i in warnings if (i.platform or "") != "YouTube"]
    if relevant:
        with st.expander(f"ℹ️ {len(relevant)} missing field(s) flagged"):
            st.dataframe(pd.DataFrame([{"Video": i.video, "Platform": i.platform,
                                        "Issue": i.message} for i in relevant]),
                         hide_index=True, use_container_width=True)

