"""Streamlit components for the bulk publishing workflow."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.models import PUBLISHABLE_PLATFORMS, Platform, VideoRecord
from src.publishing_coordinator import ACTIVE, BatchJob, BatchSummary

STATUS_LABELS = {
    "queued": "⏳ Pending", "deferred": "⏸️ Deferred", "uploading": "⬆️ Uploading",
    "processing": "⚙️ Processing", "published": "🎉 Published", "failed": "❌ Failed",
    "cancelled": "🚫 Cancelled",
}


def render_batch_setup(records: list[VideoRecord]) -> dict | None:
    """Platform + scope selection. Returns plan request when 'Prepare batch' is clicked."""
    c1, c2 = st.columns(2)
    plats = c1.multiselect("Platforms", [p.value for p in PUBLISHABLE_PLATFORMS],
                           default=[p.value for p in PUBLISHABLE_PLATFORMS], key="batch_platforms")
    scope = c2.radio("Videos", ["All valid videos", "Selected videos"], horizontal=True,
                     key="batch_scope")
    valid = [r for r in records if r.is_valid]
    video_ids = None
    if scope == "Selected videos":
        names = st.multiselect("Choose videos", [r.filename for r in valid], key="batch_videos")
        video_ids = [r.video_id for r in valid if r.filename in names]
    disabled = not plats or not valid or (video_ids is not None and not video_ids)
    if st.button("📋 Prepare batch", type="primary", disabled=disabled, use_container_width=True):
        return {"platforms": [Platform(p) for p in plats], "video_ids": video_ids}
    return None


def render_queue(jobs: list[BatchJob], editable: bool) -> set[str]:
    """Job table; when editable, queued jobs can be ticked for cancellation."""
    df = pd.DataFrame([{
        "Cancel": False, "Video": j.filename, "Platform": j.platform.value,
        "Status": STATUS_LABELS[j.status] + (" (retry)" if j.is_retry else ""),
        "Details": j.message, "id": j.id,
    } for j in jobs])
    if df.empty:
        st.info("No jobs.")
        return set()
    if not editable:
        st.dataframe(df.drop(columns=["Cancel", "id"]), hide_index=True, use_container_width=True)
        return set()
    edited = st.data_editor(df, hide_index=True, use_container_width=True, key="batch_queue",
                            disabled=["Video", "Platform", "Status", "Details", "id"],
                            column_config={"id": None, "Cancel": st.column_config.CheckboxColumn(
                                help="Cancel this job before it starts")})
    active = {j.id for j in jobs if j.status in ACTIVE}
    return {i for i, c in zip(edited["id"], edited["Cancel"]) if c and i in active}


def render_summary(s: BatchSummary) -> None:
    cols = st.columns(6)
    cols[0].metric("Pending", s.queued + s.deferred)
    cols[1].metric("Uploading", s.uploading)
    cols[2].metric("Processing", s.processing)
    cols[3].metric("Published", s.published)
    cols[4].metric("Failed", s.failed)
    cols[5].metric("Cancelled", s.cancelled)
    st.progress(s.done / s.total if s.total else 0.0,
                text=f"{s.done}/{s.total} jobs finished")
    if s.partial:
        st.warning(f"Partial success: {s.published} published, {s.failed} failed, "
                   f"{s.processing} awaiting confirmation, {s.deferred} deferred. "
                   "Successful posts are kept; use **Retry Failed** for retryable failures.")
    elif s.total and s.published == s.total:
        st.success(f"All {s.total} posts published and confirmed by the platforms.")

