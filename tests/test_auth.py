from __future__ import annotations

import stat
import sys

import pytest

from cmcoder.providers.auth import (
    ApiKeyAuth,
    AuthError,
    credentials_file,
    delete_api_key,
    load_stored_api_key,
    store_api_key,
)


def test_store_falls_back_to_private_file_without_keychain() -> None:
    where = store_api_key("corp", "sk-123")
    assert "no OS keychain" in where
    path = credentials_file()
    if sys.platform != "win32":  # Windows has no POSIX permission bits
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_stored_api_key("corp") == "sk-123"
    delete_api_key("corp")
    assert load_stored_api_key("corp") is None


async def test_api_key_auth_precedence() -> None:
    store_api_key("corp", "stored")
    assert await ApiKeyAuth("corp").get_headers() == {"Authorization": "Bearer stored"}
    assert await ApiKeyAuth("corp", explicit_key="env").get_headers() == {
        "Authorization": "Bearer env"
    }
    with pytest.raises(AuthError, match="cmcoder login"):
        await ApiKeyAuth("other").get_headers()


def test_masked_key_and_description() -> None:
    from cmcoder.providers.auth import mask_key

    assert mask_key("sk-1234567890abc6cd") == "sk-...6cd"
    assert mask_key("short") == "***"
    store_api_key("corp", "sk-stored-key-0001")
    assert ApiKeyAuth("corp").describe() == "stored key sk-...001"
    assert (
        ApiKeyAuth(
            "corp", explicit_key="sk-env-key-0002", explicit_source="CMCODER_API_KEY"
        ).describe()
        == "CMCODER_API_KEY sk-...002"
    )
