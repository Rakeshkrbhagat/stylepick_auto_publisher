"""Streamlit component: filterable persistent publishing history."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.history_store import HistoryStore, HistoryStoreError

STATUSES = ["", "published", "processing", "failed", "uncertain", "started"]


def render_history_panel(store: HistoryStore | None) -> None:
    with st.expander("🗂️ Publishing history (saved)", expanded=False):
        if store is None:
            st.error("Publishing history database is unavailable.")
            return
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        platform = c1.selectbox("Platform", ["", "Facebook", "Instagram"], key="hist_platform",
                                format_func=lambda x: x or "All")
        status = c2.selectbox("Status", STATUSES, key="hist_status",
                              format_func=lambda x: x or "All")
        mode = c3.selectbox("Mode", ["", "live", "simulated"], key="hist_mode",
                            format_func=lambda x: x or "All")
        text = c4.text_input("Search (filename, video ID, caption, error)", key="hist_text")
        try:
            rows = store.attempts(platform or None, status or None, text.strip(), mode or None)
        except HistoryStoreError as exc:
            st.error(str(exc))
            return
        if not rows:
            st.caption("No matching publishing attempts.")
            return
        df = pd.DataFrame([{
            "#": a.id, "Started": a.started_at.replace("T", " ")[:19],
            "Finished": a.finished_at.replace("T", " ")[:19], "Video": a.filename,
            "Video ID": a.video_key, "Platform": a.platform, "Mode": a.mode,
            "Status": a.status, "Post ID": a.post_id, "URL": a.permalink,
            "Error": a.error, "Retryable": "yes" if a.retryable else "no", "Caption": a.caption,
        } for a in rows])
        st.dataframe(df, hide_index=True, use_container_width=True,
                     column_config={"URL": st.column_config.LinkColumn("URL")})
        st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name="publishing_history.csv", key="hist_csv")

