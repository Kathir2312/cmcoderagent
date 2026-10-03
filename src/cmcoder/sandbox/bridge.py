"""Runs inside the Linux sandbox, which has no network: listens on
127.0.0.1:PORT (where HTTP(S)_PROXY points) and forwards each connection to
cmcoder's filtering proxy through a Unix socket bound into the sandbox.

    python -m cmcoder.sandbox.bridge PORT SOCKET

Standard library only, so it starts fast with the interpreter's own files.
"""

from __future__ import annotations

import socket
import sys
import threading


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _serve(client: socket.socket, path: str) -> None:
    if sys.platform == "win32":
        raise SystemExit("the sandbox bridge runs on Linux only")
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(path)
    except OSError:
        client.close()
        upstream.close()
        return
    back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
    back.start()
    _pipe(client, upstream)
    back.join()
    client.close()
    upstream.close()


def main(argv: list[str]) -> None:
    port, path = int(argv[0]), argv[1]
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    while True:
        client, _ = server.accept()
        threading.Thread(target=_serve, args=(client, path), daemon=True).start()


if __name__ == "__main__":
    main(sys.argv[1:])
