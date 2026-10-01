"""TLS against a server whose certificate comes from a private ("internal") CA."""

from __future__ import annotations

import shutil
import ssl
import subprocess
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from cmcoder.cli.doctor import Doctor
from cmcoder.config.settings import Settings
from cmcoder.providers.auth import ApiKeyAuth
from cmcoder.providers.messages import Message
from cmcoder.providers.openai_compat import OpenAICompatProvider, TLSFailed
from cmcoder.providers.profiles import resolve_profile
from cmcoder.providers.transport import TransportOptions, build_client
from cmcoder.testing.mock_server import MockServer, MockState

from .conftest import API_KEY

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI not available")


@pytest.fixture(scope="module")
def internal_ca(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("pki")

    def run(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=d, check=True, capture_output=True)

    run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "ca.key",
        "-out",
        "ca.pem",
        "-days",
        "2",
        "-subj",
        "/CN=Test Internal Root CA",
    )
    run(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        "server.key",
        "-out",
        "server.csr",
        "-subj",
        "/CN=localhost",
    )
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    run(
        "x509",
        "-req",
        "-in",
        "server.csr",
        "-CA",
        "ca.pem",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-out",
        "server.pem",
        "-days",
        "2",
        "-extfile",
        "ext.cnf",
    )
    return {"ca": d / "ca.pem", "cert": d / "server.pem", "key": d / "server.key"}


@pytest.fixture
def tls_server(internal_ca: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Any:
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(internal_ca["cert"], internal_ca["key"])
    server = MockServer(
        MockState([{"content": "secure hello"}], api_key=API_KEY), tls=ctx, host="localhost"
    )
    with server:
        yield server


def provider(server: MockServer, ca: Path | None) -> OpenAICompatProvider:
    opts = TransportOptions(ca_cert_path=str(ca) if ca else None, read_timeout=10)
    return OpenAICompatProvider(
        "corp", server.base_url, ApiKeyAuth("corp", API_KEY), build_client(opts), max_retries=0
    )


async def test_untrusted_internal_ca_gives_clear_error(tls_server: MockServer) -> None:
    p = provider(tls_server, None)
    with pytest.raises(TLSFailed) as e:
        [
            ev
            async for ev in p.stream_chat(
                "qwen3-27b", [Message.user("hi")], [], resolve_profile("qwen3-27b")
            )
        ]
    await p.aclose()
    assert "caCertPath" in (e.value.hint or "")


async def test_ca_cert_path_makes_it_work(
    tls_server: MockServer, internal_ca: dict[str, Path]
) -> None:
    p = provider(tls_server, internal_ca["ca"])
    events = [
        ev
        async for ev in p.stream_chat(
            "qwen3-27b", [Message.user("hi")], [], resolve_profile("qwen3-27b")
        )
    ]
    await p.aclose()
    assert events[-1].message.content == "secure hello"  # type: ignore[union-attr]


async def test_doctor_names_the_untrusted_issuer(
    tls_server: MockServer, internal_ca: dict[str, Path]
) -> None:
    def doctor(ca: Path | None) -> tuple[Doctor, str]:
        settings = Settings.model_validate(
            {
                "providers": {
                    "corp": {
                        "baseUrl": tls_server.base_url,
                        **({"caCertPath": str(ca)} if ca else {}),
                    }
                },
                "model": "corp:qwen3-27b",
            }
        )
        console = Console(record=True, width=200)
        return Doctor(settings, console), ""

    d, _ = doctor(None)
    assert await d.check_network("corp") is False
    out = d.console.export_text()
    assert "TLS: certificate not trusted" in out
    assert "Test Internal Root CA" in out

    d, _ = doctor(internal_ca["ca"])
    assert await d.check_network("corp") is True
    assert "TLS: certificate verified" in d.console.export_text()
