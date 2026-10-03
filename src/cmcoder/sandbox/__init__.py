"""The Bash sandbox: the agent's shell runs where it can only write to the
project (and a temp folder), can't read credential folders, and reaches the
network only through cmcoder's filtering proxy (sandbox/proxy.py).

- Linux and WSL2: bubblewrap (`bwrap`), with its own network namespace; a
  bridge inside (sandbox/bridge.py) connects to the proxy's Unix socket.
- macOS: `sandbox-exec` with a generated profile; the proxy on localhost.
- Native Windows: not available (permission prompts remain the protection).

Inside the project, `.git/hooks`, `.git/config`, `.cmcoder`, `.vscode` and
`.mcp.json` stay read-only: changing them could run code outside the sandbox
later (git hooks, git's fsmonitor, editor tasks) or change cmcoder's own
permissions. Package caches go to the temp folder, so nothing poisons the real
caches that run unsandboxed later.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..compat import IS_WINDOWS, find_program, find_shell
from ..config.settings import SandboxConfig, config_dir
from .proxy import FilteringProxy

BRIDGE_PORT = 3128  # inside the Linux sandbox's own network namespace
# Never readable from the sandbox (credentials), besides sandbox.denyReadPaths.
DEFAULT_DENY_READ = (
    "~/.ssh",
    "~/.aws",
    "~/.azure",
    "~/.config/gcloud",
    "~/.kube",
    "~/.docker/config.json",
    "~/.netrc",
    "~/.git-credentials",
    "~/.gnupg",
    "~/.pypirc",
)
# Inside the project: read-only even in the sandbox.
PROTECTED_IN_PROJECT = (".git/hooks", ".git/config", ".cmcoder", ".vscode", ".mcp.json")


@dataclass
class Availability:
    kind: str | None  # "bwrap", "seatbelt", or None
    reason: str = ""  # why not, with what to do

    @property
    def ok(self) -> bool:
        return self.kind is not None


_detected: Availability | None = None


def detect(refresh: bool = False) -> Availability:
    """Whether a sandbox can run here (checked once: it starts a test sandbox)."""
    global _detected
    if _detected is None or refresh:
        _detected = _detect()
    return _detected


def _detect() -> Availability:
    if IS_WINDOWS:
        return Availability(
            None,
            "Not available on native Windows: run cmcoder inside WSL2 to get the sandbox. "
            "Permission prompts remain the protection here.",
        )
    if sys.platform == "darwin":
        exe = "/usr/bin/sandbox-exec"
        if not Path(exe).exists():
            return Availability(None, "sandbox-exec is missing from this macOS.")
        ok, err = _try([exe, "-p", "(version 1)(allow default)", "/usr/bin/true"])
        return Availability("seatbelt") if ok else Availability(None, f"sandbox-exec failed: {err}")
    bwrap = find_program("bwrap")
    if bwrap is None:
        return Availability(
            None,
            "bubblewrap is not installed: `sudo apt install bubblewrap` (Debian/Ubuntu, WSL2) or "
            "`sudo dnf install bubblewrap` (Fedora).",
        )
    true = find_program("true") or "/bin/true"
    ok, err = _try(
        [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-net"]
        + ["--unshare-pid", "--die-with-parent", "--new-session", "--", true]
    )
    if ok:
        return Availability("bwrap")
    hint = ""
    if "ermission" in err or "not permitted" in err:
        hint = (
            " Unprivileged user namespaces seem blocked; on Ubuntu 24.04 an admin can allow "
            "them with `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0`."
        )
    return Availability(None, f"bubblewrap can't start a sandbox here: {err}.{hint}")


def _try(argv: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    lines = (r.stderr or r.stdout).strip().splitlines()
    return False, lines[-1] if lines else f"exit code {r.returncode}"


def wanted(cfg: SandboxConfig) -> bool:
    return cfg.enabled is True or cfg.enabled == "auto"


def _expand(p: str) -> Path:
    return Path(p).expanduser()


class Sandbox:
    """One per session (shared with subagents): the proxy and the temp folder."""

    def __init__(self, cfg: SandboxConfig, project_root: Path, kind: str) -> None:
        self.cfg = cfg
        self.root = project_root.resolve()
        self.kind = kind
        self.proxy = FilteringProxy(cfg.network.allowed_hosts)
        self.tmp: Path | None = None
        self._port: int | None = None
        self._lock = asyncio.Lock()

    @property
    def auto_allow(self) -> bool:
        return self.cfg.auto_allow

    @property
    def allow_unsandboxed(self) -> bool:
        return self.cfg.allow_unsandboxed_commands

    def prompt_note(self) -> str:
        hosts = ", ".join(self.cfg.network.allowed_hosts) or "none"
        return (
            "Sandbox: Bash commands run in a sandbox. They can write only inside the project "
            "and the temp folder ($TMPDIR), can't change .git/hooks, .git/config or .cmcoder, "
            f"and reach the network only through a proxy that allows these hosts: {hosts}. "
            + (
                "If a command fails because of this and is really needed, run it again with "
                "dangerously_disable_sandbox (the user is asked)."
                if self.allow_unsandboxed
                else "The sandbox can't be turned off: if something is blocked, tell the user."
            )
        )

    def summary(self) -> str:
        how = "bubblewrap" if self.kind == "bwrap" else "macOS sandbox-exec"
        hosts = ", ".join(self.cfg.network.allowed_hosts) or "none"
        return f"{how}; writes: project and temp folder; network: {hosts}"

    async def _start(self) -> None:
        async with self._lock:
            if self.tmp is not None:
                return
            self.tmp = self._make_folders()
            if self.kind == "bwrap":
                await self.proxy.start_unix(str(self.tmp / "proxy.sock"))
            else:
                self._port = await self.proxy.start_tcp()

    def _make_folders(self) -> Path:
        # A read-only .cmcoder needs to exist to be protected (bwrap binds over it).
        (self.root / ".cmcoder").mkdir(exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="cmcoder-sbx-")).resolve()

    async def close(self) -> None:
        await self.proxy.close()
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def _env(self, port: int) -> dict[str, str]:
        assert self.tmp is not None
        proxy = f"http://127.0.0.1:{port}"
        env = {
            "CMCODER_SANDBOX": "1",
            "TMPDIR": str(self.tmp),
            # Caches in the temp folder: the real ones are used unsandboxed later.
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "PIP_CACHE_DIR": str(self.tmp / "cache" / "pip"),
            "npm_config_cache": str(self.tmp / "cache" / "npm"),
            "YARN_CACHE_FOLDER": str(self.tmp / "cache" / "yarn"),
            "GOCACHE": str(self.tmp / "cache" / "go-build"),
            "NO_PROXY": "localhost,127.0.0.1,::1",
        }
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            env[name] = env[name.lower()] = proxy
        env["no_proxy"] = env["NO_PROXY"]
        return env

    def _paths(self) -> tuple[list[Path], list[Path], list[Path]]:
        """(writable, read-only inside them, hidden), existing paths only."""
        assert self.tmp is not None
        writable = [self.root, self.tmp] + [
            p.resolve() for p in map(_expand, self.cfg.writable_paths) if p.exists()
        ]
        protected = [self.root / p for p in PROTECTED_IN_PROJECT if (self.root / p).exists()]
        hidden_names = [*DEFAULT_DENY_READ, *self.cfg.deny_read_paths]
        hidden = [p for p in map(_expand, hidden_names) if p.exists()]
        creds = config_dir() / "credentials.json"
        if creds.exists():
            hidden.append(creds)
        return writable, protected, hidden

    async def shell_command(self, shell: str, cwd: Path) -> tuple[list[str], dict[str, str]]:
        """The command line that starts `shell` in the sandbox, and its environment."""
        await self._start()
        assert self.tmp is not None
        writable, protected, hidden = self._paths()
        if self.kind == "bwrap":
            return self._bwrap(shell, cwd, writable, protected, hidden), self._env(BRIDGE_PORT)
        assert self._port is not None
        return self._seatbelt(shell, writable, protected, hidden), self._env(self._port)

    def _bwrap(
        self, shell: str, cwd: Path, writable: list[Path], protected: list[Path], hidden: list[Path]
    ) -> list[str]:
        assert self.tmp is not None
        bwrap = find_program("bwrap") or "bwrap"
        argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-net"]
        # A new, private, empty /tmp inside the sandbox (not a temp file of ours).
        private_tmp = ["--tmpfs", "/tmp"]  # nosec B108
        argv += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", *private_tmp]
        for p in writable:
            argv += ["--bind", str(p), str(p)]
        for p in protected:
            argv += ["--ro-bind", str(p), str(p)]
        for p in hidden:
            argv += ["--tmpfs", str(p)] if p.is_dir() else ["--ro-bind", "/dev/null", str(p)]
        argv += ["--chdir", str(cwd)]
        sock = self.tmp / "proxy.sock"
        bridge = " ".join(f'"{a}"' for a in bridge_command(BRIDGE_PORT, sock))
        start = (
            f"{bridge} >/dev/null 2>&1 &\n"
            f"for _ in $(seq 100); do (: </dev/tcp/127.0.0.1/{BRIDGE_PORT}) 2>/dev/null "
            "&& break; sleep 0.02; done\n"
            f'exec "{shell}" --noprofile --norc\n'
        )
        return argv + ["--", shell, "--noprofile", "--norc", "-c", start]

    def _seatbelt(
        self, shell: str, writable: list[Path], protected: list[Path], hidden: list[Path]
    ) -> list[str]:
        root = self.root
        always_protected = [root / p for p in PROTECTED_IN_PROJECT]  # existing or not
        profile = seatbelt_profile(writable, [*protected, *always_protected], hidden)
        return ["/usr/bin/sandbox-exec", "-p", profile, shell, "--noprofile", "--norc"]


def bridge_command(port: int, sock: Path) -> list[str]:
    """How to start sandbox/bridge.py inside the sandbox: with this Python, or,
    in the standalone build (no separate Python), cmcoder's own executable."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--sandbox-bridge", str(port), str(sock)]
    return [sys.executable, "-I", str(Path(__file__).with_name("bridge.py")), str(port), str(sock)]


def _sb(path: Path) -> str:
    text = str(path.resolve() if path.exists() else path)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def seatbelt_profile(writable: list[Path], protected: list[Path], hidden: list[Path]) -> str:
    """macOS sandbox profile (SBPL; later rules win)."""
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        *[f"  (subpath {_sb(p)})" for p in writable],
        '  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty")',
        '  (literal "/dev/dtracehelper") (regex #"^/dev/ttys[0-9]+$") (regex #"^/dev/fd/"))',
    ]
    if protected:
        lines += ["(deny file-write*", *[f"  (subpath {_sb(p)})" for p in protected], ")"]
    if hidden:
        lines += ["(deny file-read* file-write*", *[f"  (subpath {_sb(p)})" for p in hidden], ")"]
    lines += [
        "(deny network*)",
        # Local servers (the proxy, a test server): bind and accept on localhost,
        # connect only to localhost. Nothing else leaves the machine.
        '(allow network-bind (local ip "localhost:*"))',
        '(allow network-inbound (local ip "localhost:*"))',
        '(allow network-outbound (remote ip "localhost:*"))',
    ]
    return "\n".join(lines) + "\n"


def make_sandbox(cfg: SandboxConfig, project_root: Path) -> tuple[Sandbox | None, str | None]:
    """The session's sandbox, or None with a warning when one was asked for
    explicitly (`enabled: true`) but can't run here."""
    if not wanted(cfg) or find_shell() is None:
        return None, None
    avail = detect()
    if not avail.ok:
        warning = f"The Bash sandbox is on in settings, but: {avail.reason}"
        return None, warning if cfg.enabled is True else None
    assert avail.kind is not None
    return Sandbox(cfg, project_root, avail.kind), None
