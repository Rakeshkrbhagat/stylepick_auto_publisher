"""Sidebar component for selecting an Instagram Professional account and enabling live publishing."""

from __future__ import annotations

import streamlit as st

from src.meta_auth import INSTAGRAM_PERMISSIONS, MetaAuthManager
from src.meta_errors import MetaError


def render_instagram_account(auth: MetaAuthManager) -> dict:
    """Returns {'live': bool, 'account': InstagramAccount|None}."""
    ss = st.session_state
    out = {"live": False, "account": None}
    st.sidebar.subheader("Instagram")
    if not auth.configured() or not auth.is_connected():
        st.sidebar.info("Connect Facebook (above) to use an Instagram Professional account. "
                        "Instagram posts are simulated until then.")
        return out

    ts = ss.get("fb_token_status")
    if ts is not None and ts.valid:
        missing = ts.missing_for(INSTAGRAM_PERMISSIONS)
        if missing:
            st.sidebar.warning("Instagram permissions not granted: " + ", ".join(missing)
                               + ". Disconnect and reconnect Facebook, approving them.")

    if "ig_accounts" not in ss or st.sidebar.button("Refresh Instagram accounts",
                                                     use_container_width=True):
        try:
            ss.ig_accounts, ss.ig_notes = auth.list_instagram_accounts(ss.get("fb_pages"))
        except MetaError as exc:
            ss.ig_accounts, ss.ig_notes = [], [str(exc)]
    accounts, notes = ss.ig_accounts, ss.get("ig_notes", [])
    for n in notes:
        st.sidebar.caption(f"ℹ️ {n}")
    if not accounts:
        st.sidebar.error("No eligible Instagram Professional account found. Link a Business or "
                         "Creator account to one of your Facebook Pages.")
        return out

    current = auth.selected_instagram()
    labels = [a.label for a in accounts]
    ids = [a.id for a in accounts]
    idx = ids.index(current.id) if current and current.id in ids else 0
    choice = st.sidebar.selectbox("Instagram account", labels, index=idx, key="ig_choice")
    acc = accounts[labels.index(choice)]
    if not current or current.id != acc.id:
        if st.sidebar.button("Use this Instagram account", use_container_width=True):
            try:
                auth.select_instagram(acc)
                ss.flash = ("success", f"Instagram account {acc.label} selected.")
            except MetaError as exc:
                ss.flash = ("error", str(exc))
            st.rerun()
    if current:
        st.sidebar.caption(f"Selected: **{current.label}**")
        out["account"] = current
        out["live"] = st.sidebar.toggle(
            "Live Instagram publishing", key="ig_live", value=False,
            help="When on, 'Publish to Instagram' really posts Reels to the selected account.")
        if out["live"]:
            st.sidebar.warning("LIVE: Instagram Reels will be public.")
    return out

