"""Secure local credential storage backed by the operating system's credential vault.
    sys.exit(_cli(sys.argv[1:]))
if __name__ == "__main__":  # pragma: no cover


    return 2
    print(__doc__)
        return 0
            print(f"Deleted '{argv[1]}'.")
            store.delete()
        else:
            print(f"Stored '{argv[1]}' in the OS credential store.")
            store.set(value)
                return 1
                print("Nothing stored.")
            if not value:
            value = getpass.getpass(f"Value for {argv[1]} (input hidden): ")
        if argv[0] == "set":
        store = SecureStore(argv[1])
    if len(argv) == 2 and argv[0] in ("set", "delete"):
            return 1
            print(exc)
        except CredentialStoreError as exc:
            return 0
            print(f"Secure credential store OK: {backend_name(b)}")
            ensure_secure(b)
            b = _default_backend()
        try:
    if len(argv) >= 1 and argv[0] == "status":

    import getpass
def _cli(argv: list[str]) -> int:


        return ""
    except CredentialStoreError:
        return SecureStore(key).get() or ""
    try:
    """Best-effort read of an optional secret (e.g. the Meta app secret)."""
def get_secret(key: str) -> str:


        self.set(json.dumps(data))
    def save(self, data: dict) -> None:  # type: ignore[override]

            return {}
            self.delete()  # unreadable/corrupt entry -> force re-authorization
        except ValueError:
            return json.loads(raw)
        try:
            return {}
        if not raw:
        raw = self.get()
    def load(self) -> dict:  # type: ignore[override]

    """Stores a JSON object (e.g. several tokens) as one secret."""
class JsonSecureStore(SecureStore):


    load, save = get, set
    # TokenStore-compatible aliases (string payload)

            pass  # already absent
        except Exception:
            self.backend.delete_password(self.service, self.key)
        try:
    def delete(self) -> None:

                f"({type(exc).__name__}).") from None
                f"Could not save '{self.key}' to the OS credential store "
            raise CredentialStoreError(
        except Exception as exc:
            self.backend.set_password(self.service, self.key, value)
        try:
        ensure_secure(self.backend)
    def set(self, value: str) -> None:

                f"({type(exc).__name__}).") from None
                f"Could not read '{self.key}' from the OS credential store "
            raise CredentialStoreError(
        except Exception as exc:
            return self.backend.get_password(self.service, self.key)
        try:
    def get(self) -> str | None:

        return self._backend
            self._backend = _default_backend()
        if self._backend is None:
    def backend(self):
    @property

        self.key, self.service, self._backend = key, service, backend
    def __init__(self, key: str, service: str = SERVICE, backend=None):

    """One secret string under ``(SERVICE, key)`` in the OS credential vault."""
class SecureStore:


            "Secret Service keyring; credentials are not saved to plain files.")
            f"{backend_name(backend)}). Use Windows Credential Manager, macOS Keychain or a "
            "No secure OS credential store is available (backend: "
        raise CredentialStoreError(
    if any(h in name for h in _INSECURE_BACKEND_HINTS):
    name = backend_name(backend).lower()
def ensure_secure(backend) -> None:


    return f"{type(backend).__module__}.{type(backend).__name__}"
def backend_name(backend) -> str:


    return keyring.get_keyring()
        raise CredentialStoreError("The 'keyring' package is not installed.") from exc
    except ImportError as exc:  # pragma: no cover
        import keyring
    try:
def _default_backend():


    """Raised when the secure store is unavailable. Messages never contain secrets."""
class CredentialStoreError(Exception):


_INSECURE_BACKEND_HINTS = ("fail", "plaintext", "null", "unencrypted")
SERVICE = "stylepick_auto_publisher"

import sys
import json

from __future__ import annotations

"""
    python -m src.secure_store status
    python -m src.secure_store delete meta_app_secret
    python -m src.secure_store set meta_app_secret
CLI (stores the Meta app secret in the OS vault instead of .env):

to plaintext) and the user gets a clear message.
backend is available the store refuses to save (instead of silently falling back
Tokens are never written to plain files, environment files or logs. If no secure

- Windows: Credential Manager · macOS: Keychain · Linux: Secret Service (GNOME Keyring/KWallet)


