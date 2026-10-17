"""Secure local credential storage backed by the operating system's credential vault.

- Windows: Credential Manager · macOS: Keychain · Linux: Secret Service (GNOME Keyring/KWallet)

Tokens are never written to plain files, environment files or logs. If no secure
backend is available the store refuses to save (instead of silently falling back
to plaintext) and the user gets a clear message.

CLI (stores the Meta app secret in the OS vault instead of .env):
    python -m src.secure_store set meta_app_secret
    python -m src.secure_store delete meta_app_secret
    python -m src.secure_store status
"""

from __future__ import annotations

import json
import sys

SERVICE = "stylepick_auto_publisher"
_INSECURE_BACKEND_HINTS = ("fail", "plaintext", "null", "unencrypted")


class CredentialStoreError(Exception):
    """Raised when the secure store is unavailable. Messages never contain secrets."""


def _default_backend():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover
        raise CredentialStoreError("The 'keyring' package is not installed.") from exc
    return keyring.get_keyring()


def backend_name(backend) -> str:
    return f"{type(backend).__module__}.{type(backend).__name__}"


def ensure_secure(backend) -> None:
    name = backend_name(backend).lower()
    if any(h in name for h in _INSECURE_BACKEND_HINTS):
        raise CredentialStoreError(
            "No secure OS credential store is available (backend: "
            f"{backend_name(backend)}). Use Windows Credential Manager, macOS Keychain or a "
            "Secret Service keyring; credentials are not saved to plain files.")


class SecureStore:
    """One secret string under ``(SERVICE, key)`` in the OS credential vault."""

    def __init__(self, key: str, service: str = SERVICE, backend=None):
        self.key, self.service, self._backend = key, service, backend

    @property
    def backend(self):
        if self._backend is None:
            self._backend = _default_backend()
        return self._backend

    def get(self) -> str | None:
        try:
            return self.backend.get_password(self.service, self.key)
        except Exception as exc:
            raise CredentialStoreError(
                f"Could not read '{self.key}' from the OS credential store "
                f"({type(exc).__name__}).") from None

    def set(self, value: str) -> None:
        ensure_secure(self.backend)
        try:
            self.backend.set_password(self.service, self.key, value)
        except Exception as exc:
            raise CredentialStoreError(
                f"Could not save '{self.key}' to the OS credential store "
                f"({type(exc).__name__}).") from None

    def delete(self) -> None:
        try:
            self.backend.delete_password(self.service, self.key)
        except Exception:
            pass  # already absent

    # TokenStore-compatible aliases (string payload)
    load, save = get, set


class JsonSecureStore(SecureStore):
    """Stores a JSON object (e.g. several tokens) as one secret."""

    def load(self) -> dict:  # type: ignore[override]
        raw = self.get()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            self.delete()  # unreadable/corrupt entry -> force re-authorization
            return {}

    def save(self, data: dict) -> None:  # type: ignore[override]
        self.set(json.dumps(data))


def get_secret(key: str) -> str:
    """Best-effort read of an optional secret (e.g. the Meta app secret)."""
    try:
        return SecureStore(key).get() or ""
    except CredentialStoreError:
        return ""


def _cli(argv: list[str]) -> int:
    import getpass

    if len(argv) >= 1 and argv[0] == "status":
        try:
            b = _default_backend()
            ensure_secure(b)
            print(f"Secure credential store OK: {backend_name(b)}")
            return 0
        except CredentialStoreError as exc:
            print(exc)
            return 1
    if len(argv) == 2 and argv[0] in ("set", "delete"):
        store = SecureStore(argv[1])
        if argv[0] == "set":
            value = getpass.getpass(f"Value for {argv[1]} (input hidden): ")
            if not value:
                print("Nothing stored.")
                return 1
            store.set(value)
            print(f"Stored '{argv[1]}' in the OS credential store.")
        else:
            store.delete()
            print(f"Deleted '{argv[1]}'.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv[1:]))
