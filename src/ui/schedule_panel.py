"""Streamlit panel: review original vs proposed (+1 day) schedule, edit, confirm, submit."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from src.scheduling import (
    APPROVED, DRAFT_SAVED, FAILED, MODE_DRAFT, MODE_SCHEDULE, PROPOSED, SCHEDULED, SKIPPED,
    SUBMITTING, UNSUPPORTED, ScheduleProposal, can_retry, fmt,
)

STATUS_ICON = {PROPOSED: "📝 Proposed", APPROVED: "✅ Approved – not yet submitted",
               SKIPPED: "⏭️ Skipped – will not be submitted",
               SUBMITTING: "⏳ Submitting", SCHEDULED: "🗓️ Scheduled", DRAFT_SAVED: "💾 Draft saved",
               UNSUPPORTED: "⚠️ Not supported – schedule manually", FAILED: "❌ Failed"}
MODE_LABELS = {MODE_SCHEDULE: "Schedule for the proposed time",
               MODE_DRAFT: "Save as draft (stays unpublished)"}


def render_schedule_table(proposals: list[ScheduleProposal]) -> None:
    st.dataframe([{
        "Video": p.filename, "Platform": p.platform.value,
        "Original date": f"{p.original.day} {p.original:%B %Y}",
        "Original time": f"{p.original:%I:%M %p}".lstrip("0"),
        "Proposed date": f"{p.proposed.day} {p.proposed:%B %Y}",
        "Proposed time": f"{p.proposed:%I:%M %p}".lstrip("0") + (" ✏️" if p.edited else ""),
        "Action": MODE_LABELS[p.mode],
        "Status": STATUS_ICON.get(p.status, p.status),
        "Result": p.message,
    } for p in proposals], hide_index=True, use_container_width=True)


def render_proposal_editor(p: ScheduleProposal, platform_note: str = "") -> dict | None:
    """Edit one proposal. Returns {'proposed', 'mode', 'approve'} on submit, else None."""
    key = f"sch_{p.video_id}_{p.platform.value}"
    st.markdown(f"**{p.platform.value} – {p.filename}** · {STATUS_ICON.get(p.status, p.status)}")
    if platform_note:
        st.caption(platform_note)
    c1, c2 = st.columns(2)
    c1.markdown(f"Original: **{fmt(p.original)}**")
    c2.markdown(f"Proposed: **{fmt(p.proposed)}**" + (" (edited)" if p.edited else ""))
    st.text_area("Final caption + hashtags (edit in section 3)", p.caption, height=140,
                 disabled=True, key=f"{key}_cap")
    if p.message:
        (st.success if p.status in (SCHEDULED, DRAFT_SAVED) else st.warning)(p.message)
    if p.submitted:
        return None
    with st.form(f"{key}_form"):
        d1, d2, d3 = st.columns([1, 1, 2])
        new_date = d1.date_input("Proposed date", p.proposed.date(), key=f"{key}_d")
        new_time = d2.time_input("Proposed time", p.proposed.timetz().replace(tzinfo=None),
                                 step=60, key=f"{key}_t")
        mode = d3.radio("Action", [MODE_SCHEDULE, MODE_DRAFT], format_func=MODE_LABELS.get,
                        index=0 if p.mode == MODE_SCHEDULE else 1, key=f"{key}_m")
        ok = st.checkbox("I reviewed the video, caption, hashtags, date and time and approve "
                         "this schedule.", key=f"{key}_ok")
        s1, s2 = st.columns(2)
        save = s1.form_submit_button("Save changes", use_container_width=True)
        approve = s2.form_submit_button("Approve", type="primary", use_container_width=True)
    if not (save or approve):
        return None
    proposed = datetime.combine(new_date, new_time, tzinfo=p.proposed.tzinfo)
    if approve and not ok:
        st.error("Tick the confirmation box to approve.")
        approve = False
    return {"proposed": proposed, "mode": mode, "approve": approve}

