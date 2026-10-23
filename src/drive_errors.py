"""User-facing error types for the Google Drive integration."""
from __future__ import annotations



class DriveError(Exception):
    """Base error. ``retryable`` tells the UI whether to offer a retry."""

    default_message = "Google Drive error."
    retryable = False

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)


class NotConnectedError(DriveError):
    default_message = "Google account not connected. Click 'Connect Google account' in the sidebar."


class AuthError(DriveError):
    default_message = "Google authentication failed."


class AuthCancelledError(AuthError):
    default_message = "Google sign-in was cancelled. Click 'Connect Google account' to try again."


class AuthExpiredError(AuthError):
    default_message = ("Google authorization expired or was revoked. "
                       "Please reconnect your Google account.")


class InvalidFolderUrlError(DriveError):
    default_message = ("Invalid Google Drive folder URL. Paste a link like "
                       "https://drive.google.com/drive/folders/<folder-id>")


class FolderNotFoundError(DriveError):
    default_message = ("Folder not found. It may have been deleted, moved to trash, "
                       "or the connected account cannot see it.")


class AccessDeniedError(DriveError):
    default_message = ("Access denied. Ask the folder owner to share it with your connected "
                       "Google account (public sharing is not required).")


class QuotaError(DriveError):
    default_message = "Google Drive API rate limit/quota reached. Please wait a moment and retry."
    retryable = True


class NetworkError(DriveError):
    default_message = "Network error while contacting Google Drive. Check your connection and retry."
    retryable = True


class DownloadError(DriveError):
    default_message = "Video download failed."
    retryable = True

