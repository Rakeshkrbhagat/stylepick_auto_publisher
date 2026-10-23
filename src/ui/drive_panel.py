"""Streamlit components for Google account status and the Drive video list."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.drive_client import FolderListing, human_size
from src.drive_errors import DriveError
from src.google_auth import GoogleAuthManager


def render_google_account(auth: GoogleAuthManager, auth_url: str | None = None) -> dict:
    """Sidebar block for the signed-in user's own Google account.

    ``auth_url`` (hosted deployments) renders a sign-in link; otherwise a local browser flow
    button is shown. Returns {'connect': bool, 'disconnect': bool, 'mock': bool}."""
    st.sidebar.subheader("Your Google Drive")
    mock = st.sidebar.toggle("Use sample Drive data (offline)", key="use_mock_drive",
                             help="Use sample videos instead of your Google Drive.")
    actions = {"connect": False, "disconnect": False, "mock": mock}
    if mock:
        st.sidebar.info("Sample mode – Google Drive is not contacted.")
        return actions
    if not auth.client_configured():
        st.sidebar.warning("Google sign-in is not configured on this server. Ask the "
                           "administrator to set GOOGLE_CLIENT_SECRETS_JSON (or GOOGLE_CLIENT_SECRETS_FILE) and GOOGLE_OAUTH_REDIRECT_URI, or use sample data.")
        return actions
    if auth.is_connected():
        email = st.session_state.get("google_email") or auth.account_email()
        st.session_state["google_email"] = email
        st.sidebar.success(f"Connected: {email or 'Google account'} (read-only)")
        actions["disconnect"] = st.sidebar.button("Disconnect Google account",
                                                  use_container_width=True)
    else:
        st.sidebar.error("Google account not connected")
        if auth_url:
            st.sidebar.link_button("Connect Google account (read-only)", auth_url,
                                   type="primary", use_container_width=True)
            st.sidebar.caption("You will return here after granting read-only access.")
        else:
            actions["connect"] = st.sidebar.button(
                "Connect Google account", type="primary", use_container_width=True,
                help="Opens your browser for Google sign-in (read-only Drive access).")
    return actions


def render_drive_error(err: DriveError | None, key: str) -> bool:
    """Show an error; returns True if the user clicked Retry (for retryable errors)."""
    if not err:
        return False
    st.error(str(err))
    if err.retryable:
        return st.button("🔁 Retry", key=key)
    return False


def render_video_selection(listing: FolderListing, selected: dict[str, bool],
                           download_state: dict[str, tuple[str, str]]) -> dict[str, bool]:
    """Editable table of Drive videos. Returns updated {file_id: include}."""
    st.markdown(f"**Folder:** {listing.folder_name or listing.folder_id} · "
                f"**{len(listing.videos)}** supported video(s) found")
    if not listing.videos:
        st.info("No supported videos (MP4/MOV) found in this folder. Add videos to the folder "
                "and click **Refresh folder**.")
    else:
        df = pd.DataFrame([{
            "Include": selected.get(v.id, True),
            "Filename": v.name,
            "Size": human_size(v.size),
            "Type": v.mime_type,
            "Modified": v.modified_time.replace("T", " ")[:16],
            "Status": " ".join(download_state.get(v.id, ("🟢 Listed", ""))).strip(),
            "file_id": v.id,
        } for v in listing.videos])
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True, key="drive_editor",
            disabled=["Filename", "Size", "Type", "Modified", "Status", "file_id"],
            column_config={"file_id": None,
                           "Include": st.column_config.CheckboxColumn(width="small")},
        )
        selected = dict(zip(edited["file_id"], edited["Include"]))
    if listing.skipped:
        with st.expander(f"Skipped {len(listing.skipped)} non-video/unsupported file(s)"):
            st.dataframe(pd.DataFrame([{"File": s.name, "Type": s.mime_type, "Reason": s.reason}
                                       for s in listing.skipped]),
                         hide_index=True, use_container_width=True)
    return selected

