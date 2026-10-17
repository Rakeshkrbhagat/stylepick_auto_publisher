"""Sidebar component for Facebook connection, Page selection and live-publishing toggle."""

from __future__ import annotations

import datetime as dt

import streamlit as st

from src.meta_auth import MetaAuthManager
from src.meta_errors import MetaError


def reconnect_facebook(auth: MetaAuthManager) -> None:
    """Run the login flow again (re-authorization) and refresh cached account data."""
    ss = st.session_state
    with st.spinner("Waiting for Facebook login in your browser…"):
        try:
            auth.connect()
            for k in ("fb_pages", "fb_token_status", "ig_accounts", "ig_notes", "fb_live", "ig_live"):
                ss.pop(k, None)
            ss.flash = ("success", "Facebook reconnected. Re-select your Page / Instagram account "
                                   "if needed.")
        except MetaError as exc:
            ss.flash = ("error", str(exc))
    st.rerun()


def render_facebook_account(auth: MetaAuthManager) -> dict:
    """Returns {'live': bool, 'page': FacebookPage|None}. Handles connect/select/disconnect."""
    ss = st.session_state
    st.sidebar.subheader("Facebook Page")
    out = {"live": False, "page": None}
    if not auth.configured():
        st.sidebar.info("Facebook publishing not configured (META_APP_ID / META_APP_SECRET). "
                        "Facebook posts are simulated.")
        return out
    if not auth.is_connected():
        st.sidebar.error("Facebook not connected")
        if st.sidebar.button("Connect Facebook", type="primary", use_container_width=True):
            reconnect_facebook(auth)
        return out

    if "fb_token_status" not in ss or st.sidebar.button("Re-check token", use_container_width=True):
        try:
            ss.fb_token_status = auth.check_token()
        except MetaError as exc:
            ss.fb_token_status = None
            st.sidebar.error(str(exc))
    ts = ss.get("fb_token_status")
    if auth.needs_reauth() or (ts is not None and not ts.valid):
        st.sidebar.error("Facebook authorization expired or was revoked. Live publishing is "
                         "paused until you reconnect.")
        if st.sidebar.button("🔑 Reconnect Facebook", type="primary", use_container_width=True):
            reconnect_facebook(auth)
        if st.sidebar.button("Disconnect Facebook", use_container_width=True):
            _disconnect(auth)
        return out
    if ts is not None:
        if ts.missing:
            st.sidebar.warning("Missing permissions: " + ", ".join(ts.missing))
            if st.sidebar.button("Grant missing permissions", use_container_width=True):
                reconnect_facebook(auth)
        else:
            exp = ("never" if not ts.expires_at else
                   dt.datetime.fromtimestamp(ts.expires_at).strftime("%Y-%m-%d"))
            st.sidebar.success(f"Token valid · permissions OK · expires {exp}")

    if "fb_pages" not in ss:
        try:
            ss.fb_pages = auth.list_pages()
        except MetaError as exc:
            ss.fb_pages = []
            st.sidebar.error(str(exc))
    pages = ss.fb_pages
    current = auth.selected_page()
    if pages:
        labels = [f"{p.name}{'' if p.can_publish else ' (no publish role)'}" for p in pages]
        ids = [p.id for p in pages]
        idx = ids.index(current.id) if current and current.id in ids else 0
        choice = st.sidebar.selectbox("Page", labels, index=idx, key="fb_page_choice")
        page = pages[labels.index(choice)]
        if not current or current.id != page.id:
            if st.sidebar.button("Use this Page", use_container_width=True):
                try:
                    auth.select_page(page)
                    ss.flash = ("success", f"Facebook Page '{page.name}' selected.")
                except MetaError as exc:
                    ss.flash = ("error", str(exc))
                st.rerun()
    else:
        st.sidebar.warning("No Facebook Pages found for this account.")

    if current:
        st.sidebar.caption(f"Selected Page: **{current.name}**")
        out["page"] = current
        out["live"] = st.sidebar.toggle(
            "Live Facebook publishing", key="fb_live", value=False,
            help="When on, 'Publish to Facebook' really posts Reels to the selected Page.")
        if out["live"]:
            st.sidebar.warning("LIVE: Facebook posts will be public.")
    if st.sidebar.button("Disconnect Facebook", use_container_width=True):
        _disconnect(auth)
    return out


def _disconnect(auth: MetaAuthManager) -> None:
    ss = st.session_state
    auth.disconnect()
    for k in ("fb_pages", "fb_token_status", "fb_live", "ig_accounts", "ig_notes", "ig_live"):
        ss.pop(k, None)
    ss.flash = ("info", "Facebook disconnected: permissions revoked and tokens removed from this "
                        "computer.")
    st.rerun()

