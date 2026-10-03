"""The sandbox's only way out: an HTTP proxy that allows listed hosts.

Sandboxed commands have no network of their own; `HTTP(S)_PROXY` points them
here (on Linux through a Unix socket and a small bridge inside the sandbox,
on macOS on a localhost port the sandbox may reach). Supports CONNECT (HTTPS,
git, pip, npm, curl) and plain HTTP requests. A host not in the list gets a
403 saying which setting allows it. Upstream connections use the user's own
proxy (HTTPS_PROXY / NO_PROXY) when set, as cmcoder does.
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import os
import sys
import urllib.parse
import urllib.request
from contextlib import suppress

MAX_HEAD = 64 * 1024
BLOCKED_HINT = "Add it to sandbox.network.allowedHosts in your cmcoder settings to allow it."


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.strip("[]").lower().rstrip(".")
    for pattern in allowed:
        p = pattern.lower().strip().rstrip(".")
        if not p:
            continue
        if p.startswith("*."):
            if host.endswith(p[1:]) and host != p[2:]:
                return True
        elif "*" in p:
            if fnmatch.fnmatchcase(host, p):
                return True
        elif host == p:
            return True
    return False


def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):  # [::1]:443
        host, _, rest = target[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    host, sep, port = target.rpartition(":")
    if not sep or not port.isdigit():
        return target, default_port
    return host, int(port)


def _upstream_for(scheme: str, host: str) -> tuple[str, int] | None:
    """The user's proxy for this scheme and host, if any (and NO_PROXY allows)."""
    proxies = urllib.request.getproxies_environment()
    url = proxies.get(scheme) or proxies.get("all")
    bypass = getattr(urllib.request, "proxy_bypass_environment")  # noqa: B009 (not in the stubs)
    if not url or bypass(host):
        return None
    parsed = urllib.parse.urlsplit(url if "://" in url else f"http://{url}")
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or 8080


class FilteringProxy:
    def __init__(self, allowed_hosts: list[str]) -> None:
        self.allowed = list(allowed_hosts)
        self.blocked: list[str] = []  # hosts refused, for messages and tests
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start_unix(self, path: str) -> None:
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets: Linux and macOS only")
        self._server = await asyncio.start_unix_server(self._handle, path=path)
        with suppress(OSError):
            os.chmod(path, 0o600)

    async def start_tcp(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with suppress(Exception):
                await self._server.wait_closed()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            if task is not None:
                self._tasks.discard(task)
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > MAX_HEAD:
            return
        request_line, _, rest = head.decode("latin-1").partition("\r\n")
        method, target, version = (request_line.split(" ") + ["", "", ""])[:3]
        if method.upper() == "CONNECT":
            host, port = _split_host_port(target, 443)
            if not await self._check(host, writer):
                return
            upstream = await self._open(host, port, "https", connect=True)
            if upstream is None:
                await self._reply(writer, 502, f"Could not connect to {host}:{port}.")
                return
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._pipe(reader, writer, *upstream)
            return
        url = urllib.parse.urlsplit(target)
        if url.scheme != "http" or not url.hostname:
            await self._reply(writer, 400, "Only http:// requests and CONNECT are supported.")
            return
        host, port = url.hostname, url.port or 80
        if not await self._check(host, writer):
            return
        via_proxy = _upstream_for("http", host)
        opened = await self._open(host, port, "http", connect=False)
        if opened is None:
            await self._reply(writer, 502, f"Could not connect to {host}:{port}.")
            return
        up_reader, up_writer = opened
        path = target if via_proxy else (url.path or "/") + (f"?{url.query}" if url.query else "")
        headers = [
            line
            for line in rest.split("\r\n")
            if line and not line.lower().startswith(("proxy-", "connection:"))
        ]
        up_writer.write(
            f"{method} {path} {version}\r\n".encode("latin-1")
            + "".join(f"{h}\r\n" for h in headers).encode("latin-1")
            + b"Connection: close\r\n\r\n"
        )
        await up_writer.drain()
        await self._pipe(reader, writer, up_reader, up_writer)

    async def _check(self, host: str, writer: asyncio.StreamWriter) -> bool:
        if host_allowed(host, self.allowed):
            return True
        self.blocked.append(host)
        await self._reply(
            writer, 403, f"cmcoder sandbox: network access to {host} is blocked. {BLOCKED_HINT}"
        )
        return False

    async def _open(
        self, host: str, port: int, scheme: str, *, connect: bool
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        upstream = _upstream_for(scheme, host)
        try:
            if upstream is None:
                return await asyncio.wait_for(asyncio.open_connection(host, port), 30)
            r, w = await asyncio.wait_for(asyncio.open_connection(*upstream), 30)
            if connect:
                target = f"[{host}]:{port}" if _is_ipv6(host) else f"{host}:{port}"
                w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
                await w.drain()
                reply = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 30)
                if b" 200" not in reply.split(b"\r\n", 1)[0]:
                    w.close()
                    return None
            return r, w
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return None

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, text: str) -> None:
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(status, "Error")
        body = (text + "\n").encode()
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        with suppress(Exception):
            await writer.drain()

    @staticmethod
    async def _pipe(
        r1: asyncio.StreamReader,
        w1: asyncio.StreamWriter,
        r2: asyncio.StreamReader,
        w2: asyncio.StreamWriter,
    ) -> None:
        async def copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                with suppress(Exception):
                    if dst.can_write_eof():
                        dst.write_eof()

        try:
            await asyncio.gather(copy(r1, w2), copy(r2, w1))
        finally:
            w2.close()
            with suppress(Exception):
                await w2.wait_closed()


def _is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False
