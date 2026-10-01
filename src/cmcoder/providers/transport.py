"""HTTP transport: TLS trust, proxies and timeouts for remote model servers."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass
class TransportOptions:
    ca_cert_path: str | None = None
    connect_timeout: float = 15.0
    # Longest gap allowed between streamed chunks, including the wait for the
    # first token (a cold model on a busy server can take a while).
    read_timeout: float = 300.0
    use_system_trust: bool = True


def build_ssl_context(opts: TransportOptions) -> ssl.SSLContext:
    """TLS context that trusts the OS certificate store (company CAs), plus `ca_cert_path`.

    Verification is never disabled.
    """
    ca = opts.ca_cert_path or os.environ.get("CMCODER_CA_CERT")
    if ca:
        # Python's default context loads the OS/OpenSSL roots (on Windows: the
        # system ROOT and CA stores) and accepts an extra CA file everywhere,
        # which truststore does not on every platform.
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=os.path.expanduser(ca))
        return ctx
    if opts.use_system_trust:
        try:
            import truststore

            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # truststore unsupported on this platform/Python
            pass
    return ssl.create_default_context()


def build_client(
    opts: TransportOptions, headers: dict[str, str] | None = None
) -> httpx.AsyncClient:
    # httpx reads HTTPS_PROXY / NO_PROXY from the environment (trust_env=True).
    return httpx.AsyncClient(
        verify=build_ssl_context(opts),
        timeout=httpx.Timeout(
            connect=opts.connect_timeout,
            read=opts.read_timeout,
            write=60.0,
            pool=opts.connect_timeout,
        ),
        headers=headers or {},
        trust_env=True,
    )


def proxy_bypassed(url: str) -> bool | None:
    """Whether NO_PROXY covers `url`'s host. None when no HTTPS proxy is set."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        return None
    host = (urlparse(url).hostname or "").lower()
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    for entry in (e.strip().lower() for e in no_proxy.split(",")):
        if not entry:
            continue
        if entry == "*":
            return True
        entry = entry.lstrip("*")
        if host == entry.lstrip(".") or (entry.startswith(".") and host.endswith(entry)):
            return True
        if not entry.startswith(".") and host.endswith("." + entry):
            return True
    return False
