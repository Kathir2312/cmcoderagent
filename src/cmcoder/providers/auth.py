"""Authentication providers.

Every request gets its credentials through `AuthProvider`, so SSO (e.g.
Okta/OIDC) can be added later as another provider without touching the rest
of the engine.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Protocol

KEYRING_SERVICE = "cmcoder"


class AuthError(Exception):
    pass


class AuthProvider(Protocol):
    async def get_headers(self) -> dict[str, str]: ...

    async def on_unauthorized(self) -> bool:
        """Called after a 401. Return True if credentials were refreshed and
        the request should be retried once."""
        ...


class NoAuth:
    async def get_headers(self) -> dict[str, str]:
        return {}

    async def on_unauthorized(self) -> bool:
        return False


def credentials_file() -> Path:
    return Path(os.environ.get("CMCODER_CONFIG_DIR", Path.home() / ".cmcoder")) / "credentials.json"


def store_api_key(provider: str, key: str) -> str:
    """Save a key in the OS keychain, falling back to a 0600 file. Returns where it went."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, provider, key)
        # Confirm the backend actually persists (the "fail" backend raises, the
        # "null" backend silently drops).
        if keyring.get_password(KEYRING_SERVICE, provider) == key:
            return "OS keychain"
    except Exception:
        pass
    data = _read_credentials_file()
    data[provider] = key
    _write_credentials_file(data)
    return f"{credentials_file()} (no OS keychain available)"


def delete_api_key(provider: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, provider)
    except Exception:
        pass
    data = _read_credentials_file()
    if data.pop(provider, None) is not None:
        _write_credentials_file(data)


def load_stored_api_key(provider: str) -> str | None:
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, provider)
        if key:
            return key
    except Exception:
        pass
    return _read_credentials_file().get(provider)


def _write_credentials_file(data: dict[str, str]) -> None:
    path = credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with owner-only permissions from the start.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _read_credentials_file() -> dict[str, str]:
    path = credentials_file()
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}


class ApiKeyAuth:
    """Static API key (e.g. a LiteLLM virtual key) sent as a Bearer token.

    Lookup order: explicit key (env var) > OS keychain / credentials file.
    """

    def __init__(self, provider: str, explicit_key: str | None = None) -> None:
        self.provider = provider
        self._explicit = explicit_key

    def resolve_key(self) -> str | None:
        return self._explicit or load_stored_api_key(self.provider)

    async def get_headers(self) -> dict[str, str]:
        key = self.resolve_key()
        if not key:
            raise AuthError(
                f"No API key for provider '{self.provider}'. "
                f"Run `cmcoder login` or set CMCODER_API_KEY."
            )
        return {"Authorization": f"Bearer {key}"}

    async def on_unauthorized(self) -> bool:
        return False
