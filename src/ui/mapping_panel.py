"""Streamlit component for reviewing, confirming and correcting video ↔ content mappings."""

from __future__ import annotations

import streamlit as st

from src.matching import MatchReport
from src.models import MatchStatus
from src.ui.components import MATCH_ICONS

NONE_LABEL = "— no content —"


def render_mapping_panel(report: MatchReport, content_keys: list[str]) -> list[tuple]:
    """Returns user actions: ('assign', video_id, key|''), ('confirm', video_id, key),
    ('skip', video_id), ('unskip', video_id), ('auto', video_id)."""
    actions: list[tuple] = []
    attention = report.needs_attention
    title = "Video ↔ content mapping"
    title += f" – {len(attention)} need attention" if attention else " – all matched ✅"
    with st.expander(title, expanded=True):
        st.caption("Review every mapping before preparing posts. Videos are matched to the Gemini "
                   "response with the same trailing number (e.g. R-YT-10.mp4 → response 10); "
                   "Drive listing order is never used.")
        st.dataframe([{
            "Video": m.filename,
            "Video #": m.video_number if m.video_number is not None else "—",
            "Response #": m.response_number if m.response_number is not None else "—",
            "Content": m.content_key or "—",
            "Method": m.method or "—",
            "Status": f"{MATCH_ICONS[m.status]} {m.status.value}"
                      + (" · duplicate" if m.duplicate_with else ""),
            "Notes": " ".join(m.notes),
        } for m in sorted(report.matches,
                          key=lambda m: (m.video_number is None, m.video_number or 0,
                                         m.filename.lower()))],
            hide_index=True, use_container_width=True)

        for n, names in report.duplicate_numbers.items():
            st.warning(f"Number {n} is used by several videos: {', '.join(names)}. "
                       f"Confirm which file belongs to response {n}, or skip/remap the others.")
        for n, names in report.missing_responses.items():
            st.warning(f"No Gemini response {n} for: {', '.join(names)}.")
        if report.unmatched_responses:
            st.info("Gemini responses without a video: "
                    + ", ".join(str(n) for n in report.unmatched_responses))
        if report.unnumbered_videos:
            st.warning("Videos without a trailing number (map manually): "
                       + ", ".join(report.unnumbered_videos))
        if report.duplicate_filenames:
            st.warning("Duplicate filenames in Drive: " + ", ".join(report.duplicate_filenames))
        if report.unused_content:
            st.info("Gemini content not assigned to any video: " + ", ".join(report.unused_content))

        options = [NONE_LABEL] + content_keys
        for m in attention:
            if m.status == MatchStatus.SKIPPED:
                continue
            st.markdown(f"**{m.filename}** — {MATCH_ICONS[m.status]} {m.status.value}"
                        + ("  ·  duplicate" if m.duplicate_with else ""))
            for note in m.notes:
                st.caption(note)
            default = m.content_key or (m.candidates[0] if m.candidates else None)
            c1, c2, c3 = st.columns([3, 1, 1])
            choice = c1.selectbox("Content for this video", options,
                                  index=options.index(default) if default in options else 0,
                                  key=f"map_sel_{m.video_id}", label_visibility="collapsed")
            if c2.button("✅ Confirm", key=f"map_ok_{m.video_id}", use_container_width=True):
                key = "" if choice == NONE_LABEL else choice
                actions.append(("confirm", m.video_id, key) if key == m.content_key and key
                               else ("assign", m.video_id, key))
            if c3.button("⏭️ Skip", key=f"map_skip_{m.video_id}", use_container_width=True,
                         help="Exclude this video from the review queue"):
                actions.append(("skip", m.video_id))

        st.divider()
        st.markdown("**Correct any mapping**")
        names = [m.filename for m in report.matches]
        if names:
            c1, c2 = st.columns(2)
            vname = c1.selectbox("Video", names, key="map_any_video")
            m = report.matches[names.index(vname)]
            current = m.content_key if m.content_key in content_keys else NONE_LABEL
            key = c2.selectbox("Content", options, index=options.index(current),
                               key=f"map_any_content_{m.video_id}")
            b1, b2, b3 = st.columns(3)
            if b1.button("Apply mapping", use_container_width=True):
                actions.append(("assign", m.video_id, "" if key == NONE_LABEL else key))
            if b2.button("Reset to automatic", use_container_width=True):
                actions.append(("auto", m.video_id))
            if m.status == MatchStatus.SKIPPED:
                if b3.button("Include again (unskip)", use_container_width=True):
                    actions.append(("unskip", m.video_id))
            elif b3.button("Skip this video", use_container_width=True):
                actions.append(("skip", m.video_id))
    return actions

