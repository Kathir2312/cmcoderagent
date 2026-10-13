"""High-risk shell commands: ones that delete or overwrite data in bulk, rewrite
git history, escalate privileges or run code fetched from elsewhere.

They always need a person's approval: allow rules, acceptEdits and
bypassPermissions do not cover them, "always allow" is not offered, and
settings can turn them off entirely (`permissions.highRiskCommands: "deny"`).

Detection is deliberately broad: a false alarm costs one extra prompt, a miss
can cost a deleted folder. It is a safety net against a model's mistakes, not a
sandbox: a determined attacker can always hide a command from text matching.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable

# Commands that only wrap another command; the wrapped one is what runs.
_WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "nice", "env", "xargs", "timeout"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "powershell", "pwsh"}
_SEPARATORS = {";", "&", "&&", "|", "||", "(", ")", "|&", ";;"}
_SUBSHELL_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

# PowerShell cmdlets and aliases that delete or overwrite.
_PS_DELETE_RE = re.compile(
    r"\b(remove-item|clear-content|clear-recyclebin|format-volume|clear-disk|"
    r"remove-partition|rd|ri|del|erase|rmdir)\b[^;|]*(-recurse|-force|/s\b|\*)",
    re.I,
)


def _name(word: str) -> str:
    """`/usr/bin/rm`, `RM.EXE` -> `rm`."""
    base = re.split(r"[\\/]", word)[-1].lower()
    return base[:-4] if base.endswith((".exe", ".cmd", ".bat")) else base


def _flags(args: list[str]) -> set[str]:
    """Short flags split into letters (`-rf` -> r, f) plus long flags as given."""
    out: set[str] = set()
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            out.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            out.update(f"-{c}" for c in a[1:])
    return out


def _risky_path(arg: str) -> bool:
    """Globs, home, parent and absolute paths: deleting these reaches beyond a file."""
    return (
        "*" in arg
        or arg in (".", "./", "/", "~")
        or arg.startswith(("~", "/", "..", "$"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", arg))
    )


def _rm(args: list[str]) -> str | None:
    f = _flags(args)
    if f & {"-r", "-R", "--recursive"}:
        return "recursive delete"
    if f & {"-f", "--force"}:
        return "forced delete"
    if any(_risky_path(a) for a in args if not a.startswith("-")):
        return "deletes by wildcard or outside the current folder"
    return None


def _git(args: list[str]) -> str | None:
    # Skip global options such as `-C dir` or `-c k=v` to find the subcommand.
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c") else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]
    f = _flags(rest)
    if sub == "clean" and not f & {"-n", "--dry-run"}:
        return "git clean deletes untracked files"
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard discards uncommitted changes"
    if sub == "checkout" and (f & {"-f", "--force"} or "." in rest or "--" in rest):
        return "git checkout can discard uncommitted changes"
    if sub == "restore" and not (f & {"-S", "--staged"} and not f & {"-W", "--worktree"}):
        return "git restore discards uncommitted changes"
    if sub == "push" and (
        f & {"-f", "--force", "--force-with-lease", "--delete", "-d", "--mirror"}
        or any(a.startswith("+") or a.startswith(":") for a in rest)
    ):
        return "force push or remote branch deletion"
    if sub == "branch" and f & {"-D"}:
        return "git branch -D deletes an unmerged branch"
    if sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        return "git stash drop/clear deletes saved changes"
    if sub in ("filter-branch", "filter-repo"):
        return "rewrites git history"
    if sub == "update-ref" and f & {"-d"}:
        return "deletes a git ref"
    if sub == "reflog" and rest[:1] in (["expire"], ["delete"]):
        return "deletes git recovery history"
    if sub == "gc" and any(a.startswith("--prune") for a in rest):
        return "prunes unreachable git objects"
    return None


def _find(args: list[str]) -> str | None:
    if "-delete" in args:
        return "find -delete"
    for i, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args):
            inner = _segment_risk(args[i + 1 :])
            if inner:
                return f"find -exec: {inner}"
    return None


def _windows_del(args: list[str]) -> str | None:
    lowered = [a.lower() for a in args]
    if any(a in ("/s", "/q", "-recurse", "-force") for a in lowered):
        return "recursive or quiet delete"
    if any(_risky_path(a) for a in args if not a.startswith(("/", "-"))):
        return "deletes by wildcard or outside the current folder"
    return None


def _recursive(what: str) -> Callable[[list[str]], str | None]:
    def check(args: list[str]) -> str | None:
        return f"recursive {what}" if _flags(args) & {"-R", "--recursive"} else None

    return check


_ALWAYS: dict[str, str] = {
    "sudo": "runs as administrator",
    "su": "switches user",
    "doas": "runs as administrator",
    "runas": "runs as another user",
    "shred": "destroys file contents",
    "wipefs": "erases disk signatures",
    "dd": "writes raw data to files or disks",
    "fdisk": "changes disk partitions",
    "parted": "changes disk partitions",
    "diskpart": "changes disk partitions",
    "format": "formats a disk",
    "mkfs": "formats a disk",
    "truncate": "cuts files to a size",
    "srm": "destroys files",
    "shutdown": "shuts down the machine",
    "reboot": "restarts the machine",
    "icacls": "changes file permissions",
    "takeown": "takes ownership of files",
    "cipher": "can wipe free disk space",
}

_CHECKS: dict[str, Callable[[list[str]], str | None]] = {
    "rm": _rm,
    "unlink": lambda args: (
        "deletes a file outside the current folder" if any(_risky_path(a) for a in args) else None
    ),
    "rmdir": _windows_del,
    "rd": _windows_del,
    "del": _windows_del,
    "erase": _windows_del,
    "git": _git,
    "find": _find,
    "chmod": _recursive("permission change"),
    "chown": _recursive("ownership change"),
    "rsync": lambda args: (
        "rsync --delete removes files"
        if any(a.startswith("--delete") or a.startswith("--remove-source") for a in args)
        else None
    ),
    "crontab": lambda args: None if args[:1] == ["-l"] else "changes scheduled jobs",
    "schtasks": lambda args: (
        None if args[:1] and args[0].lower() == "/query" else "changes scheduled tasks"
    ),
    "reg": lambda args: (
        None if args[:1] and args[0].lower() == "query" else "changes the Windows registry"
    ),
    "mv": lambda args: (
        "moves files to /dev/null or outside the project"
        if any(a.startswith(("/dev/null", "~", "/", "..")) for a in args[-1:])
        else None
    ),
}


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drop `VAR=x`, `env -i`, `xargs -0`, `timeout 5` etc. in front of a command."""
    while words:
        if re.match(r"^[A-Za-z_]\w*=", words[0]):
            words = words[1:]
            continue
        name = _name(words[0])
        if name not in _WRAPPERS:
            break
        words = words[1:]
        # Their options and option values (`timeout 5`, `xargs -n 1`, `nice -n 10`).
        while words and (words[0].startswith("-") or words[0][:1].isdigit()):
            words = words[1:]
    return words


def _segment_risk(words: list[str]) -> str | None:
    words = _strip_wrappers(words)
    if not words:
        return None
    name, args = _name(words[0]), words[1:]
    if name.startswith("mkfs"):
        name = "mkfs"
    if name in _ALWAYS:
        return f"{name} {_ALWAYS[name]}"
    if name in _CHECKS:
        reason = _CHECKS[name](args)
        if reason:
            return reason
    if name in ("eval",) and args:
        return high_risk_reason(" ".join(args))
    if name in _SHELLS:
        # `bash -c "rm -rf x"`, `cmd /c del /s x`, `powershell -Command ...`
        for i, a in enumerate(args):
            if a.lower() in ("-c", "/c", "/k", "-command", "-encodedcommand", "-ec"):
                if a.lower() in ("-encodedcommand", "-ec"):
                    return "runs an encoded PowerShell command"
                inner = " ".join(args[i + 1 :])
                if name in ("powershell", "pwsh") and _PS_DELETE_RE.search(inner):
                    return "PowerShell recursive or forced delete"
                return high_risk_reason(inner)
    if name in ("powershell", "pwsh") and _PS_DELETE_RE.search(" ".join(args)):
        return "PowerShell recursive or forced delete"
    return None


def _split(line: str) -> list[tuple[str, list[str]]]:
    """Split one line into (operator before it, words) per simple command."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        tokens = line.split()
    out: list[tuple[str, list[str]]] = []
    op, cur = "", []
    skip_next = False
    for t in tokens:
        if skip_next:  # the target of a redirection is a file, not a command
            skip_next = False
            continue
        if t in _SEPARATORS:
            if cur:
                out.append((op, cur))
            op, cur = t, []
        elif set(t) <= set("<>&") and t:
            skip_next = True
        else:
            cur.append(t)
    if cur:
        out.append((op, cur))
    return out


def high_risk_reason(command: str) -> str | None:
    """Why a shell command is high-risk, or None."""
    for m in _SUBSHELL_RE.finditer(command):
        inner = m.group(1) or m.group(2) or ""
        reason = high_risk_reason(inner)
        if reason:
            return reason
    for line in re.split(r"[\r\n]+", command):
        for op, words in _split(line):
            reason = _segment_risk(words)
            if reason:
                return reason
            inner = _strip_wrappers(words)
            # `curl ... | sh`: a shell or interpreter reading its program from the pipe.
            if (
                op in ("|", "|&")
                and inner
                and _name(inner[0]) in _SHELLS | {"python", "python3", "node", "iex"}
                and all(a.startswith("-") for a in inner[1:])
            ):
                return f"pipes text into {_name(inner[0])}, which runs it as code"
    return None


# --- Outward actions: they leave this computer or change what's installed ----------------
#
# Not high-risk (a person can approve them for good, with "always allow"), but
# the `auto` permission mode still asks before them: pushing, publishing,
# deploying, sending data elsewhere, installing or removing packages, and
# changing machine-wide settings. Restoring a project's own dependencies
# (`npm ci`, `dotnet restore`, `pip install -r requirements.txt`) is not one.

_PACKAGES = "installs or removes packages"
_PUBLISH = "publishes a package"
_CLUSTER = "changes a cluster or cloud resources"
_REMOTE = "connects to another machine"
_SEND = "sends data to a server"
_SYSTEM = "changes machine-wide settings or software"

_READ_VERBS = {
    "list",
    "ls",
    "show",
    "get",
    "describe",
    "view",
    "status",
    "diff",
    "checks",
    "search",
    "version",
    "help",
    "logs",
    "top",
    "explain",
    "whoami",
    "api-resources",
    "cluster-info",
}


def _words_after_options(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _js_packages(args: list[str]) -> str | None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub in (
        "publish",
        "unpublish",
        "deprecate",
        "dist-tag",
        "owner",
        "access",
        "adduser",
        "login",
    ):
        return _PUBLISH if sub in ("publish", "unpublish", "deprecate") else _SEND
    if sub in ("install", "i", "add", "isntall", "update", "up", "upgrade"):
        if "-g" in rest or "--global" in rest or "global" in args[:1]:
            return _PACKAGES
        return _PACKAGES if _words_after_options(rest) else None  # bare install: the lock file
    if sub in ("remove", "rm", "uninstall", "un", "unlink", "r"):
        return _PACKAGES
    if sub == "dlx":  # pnpm dlx, yarn dlx
        return "may download and run a package"
    if sub == "global":
        return _PACKAGES
    return None


def _pip(args: list[str]) -> str | None:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub in ("uninstall", "download"):
        return _PACKAGES
    if sub == "install":
        operands, skip = [], False
        for a in rest:
            if skip:
                skip = False
                continue
            if a in ("-r", "--requirement", "-c", "--constraint"):
                skip = True
                continue
            if a in ("-e", "--editable"):
                continue
            if not a.startswith("-"):
                operands.append(a)
        # `pip install -r requirements.txt`, `pip install -e .`: the project's own
        return _PACKAGES if any(o not in (".", "./") for o in operands) else None
    return None


def _dotnet(args: list[str]) -> str | None:
    words = _words_after_options(args)
    if words[:1] in (["add"], ["remove"]) and "package" in words:
        return _PACKAGES
    if words[:1] == ["tool"] and words[1:2] in (["install"], ["uninstall"], ["update"]):
        return _PACKAGES
    if words[:1] == ["workload"] and words[1:2] in (["install"], ["uninstall"], ["update"]):
        return _PACKAGES
    if words[:1] == ["nuget"] and words[1:2] in (["push"], ["delete"]):
        return _PUBLISH
    return None


def _cli_with_read_verbs(what: str) -> Callable[[list[str]], str | None]:
    """aws, az, gcloud, gh, kubectl: anything but a read verb changes something."""

    def check(args: list[str]) -> str | None:
        words = _words_after_options(args)
        if not words:
            return None
        verbs = {w.lower() for w in words}
        if verbs & _READ_VERBS or any(
            w.startswith(("describe-", "list-", "get-", "head-")) for w in verbs
        ):
            return None
        if words[:2] == ["s3", "ls"] or words[:1] == ["config"] and "view" in verbs:
            return None
        return what

    return check


def _curl(args: list[str]) -> str | None:
    for i, a in enumerate(args):
        name = a.split("=", 1)[0]
        if name in (
            "-d",
            "--data",
            "--data-binary",
            "--data-raw",
            "--data-urlencode",
            "-F",
            "--form",
            "-T",
            "--upload-file",
            "--json",
        ) or (a.startswith(("-d", "-F", "-T")) and len(a) > 2 and not a.startswith("--")):
            return _SEND
        if name in ("-X", "--request"):
            method = a.split("=", 1)[1] if "=" in a else args[i + 1] if i + 1 < len(args) else ""
            if method.upper() not in ("GET", "HEAD", "OPTIONS"):
                return _SEND
    return None


def _git_outward(args: list[str]) -> str | None:
    words = _words_after_options(args)
    if words[:1] in (["push"], ["send-email"]):
        return "git push sends commits to the remote" if words[0] == "push" else _SEND
    if words[:1] == ["config"] and ("--global" in args or "--system" in args):
        reads = ("--get", "--get-all", "--get-regexp", "--list", "-l")
        return None if any(a in reads for a in args) else "changes your global git settings"
    return None


_OUTWARD: dict[str, Callable[[list[str]], str | None]] = {
    "git": _git_outward,
    "npm": _js_packages,
    "pnpm": _js_packages,
    "yarn": _js_packages,
    "bun": _js_packages,
    "npx": lambda args: "may download and run a package",
    "bunx": lambda args: "may download and run a package",
    "uvx": lambda args: "may download and run a package",
    "pip": _pip,
    "pip3": _pip,
    "uv": lambda args: (
        _PACKAGES
        if args[:1] in (["add"], ["remove"]) or args[:2] == ["tool", "install"]
        else _pip(args[1:])
        if args[:1] == ["pip"]
        else None
    ),
    "poetry": lambda args: _PACKAGES if args[:1] in (["add"], ["remove"]) else None,
    "pipx": lambda args: _PACKAGES if args[:1] in (["install"], ["uninstall"], ["run"]) else None,
    "conda": lambda args: _PACKAGES if args[:1] in (["install"], ["remove"], ["update"]) else None,
    "dotnet": _dotnet,
    "nuget": lambda args: (
        _PUBLISH if args[:1] in (["push"], ["delete"]) else _PACKAGES if args[:1] == ["install"] else None
    ),
    "cargo": lambda args: (
        _PUBLISH
        if args[:1] in (["publish"], ["yank"], ["owner"])
        else _PACKAGES
        if args[:1] in (["add"], ["remove"], ["install"], ["uninstall"])
        else None
    ),
    "go": lambda args: _PACKAGES if args[:1] in (["get"], ["install"]) else None,
    "gem": lambda args: (
        _PUBLISH if args[:1] in (["push"], ["yank"]) else _PACKAGES if args[:1] in (["install"], ["uninstall"]) else None
    ),
    "bundle": lambda args: _PACKAGES if args[:1] in (["add"], ["remove"]) else None,
    "composer": lambda args: _PACKAGES if args[:1] in (["require"], ["remove"]) else None,
    "twine": lambda args: _PUBLISH if args[:1] == ["upload"] else None,
    "mvn": lambda args: _PUBLISH if any(a in ("deploy", "release:perform") for a in args) else None,
    "mvnw": lambda args: _PUBLISH if any(a in ("deploy", "release:perform") for a in args) else None,
    "gradle": lambda args: _PUBLISH if any(a.startswith(("publish", "upload")) for a in args) else None,
    "gradlew": lambda args: _PUBLISH if any(a.startswith(("publish", "upload")) for a in args) else None,
    "docker": lambda args: (
        "pushes or removes container images, or changes containers"
        if _words_after_options(args)[:1]
        in (["push"], ["login"], ["rm"], ["rmi"], ["kill"], ["stop"], ["prune"])
        or "prune" in args
        else None
    ),
    "podman": lambda args: (
        "pushes or removes container images, or changes containers"
        if _words_after_options(args)[:1] in (["push"], ["login"], ["rm"], ["rmi"])
        else None
    ),
    "kubectl": _cli_with_read_verbs(_CLUSTER),
    "oc": _cli_with_read_verbs(_CLUSTER),
    "helm": _cli_with_read_verbs(_CLUSTER),
    "terraform": lambda args: (
        "changes infrastructure"
        if _words_after_options(args)[:1]
        in (["apply"], ["destroy"], ["import"], ["taint"], ["untaint"], ["force-unlock"])
        or _words_after_options(args)[:2] in (["state", "rm"], ["state", "mv"], ["state", "push"])
        else None
    ),
    "tofu": lambda args: (
        "changes infrastructure"
        if _words_after_options(args)[:1] in (["apply"], ["destroy"], ["import"])
        else None
    ),
    "pulumi": lambda args: (
        "changes infrastructure" if _words_after_options(args)[:1] in (["up"], ["destroy"], ["import"]) else None
    ),
    "aws": _cli_with_read_verbs(_CLUSTER),
    "az": _cli_with_read_verbs(_CLUSTER),
    "gcloud": _cli_with_read_verbs(_CLUSTER),
    "gsutil": lambda args: None if _words_after_options(args)[:1] in (["ls"], ["cat"], ["stat"]) else _SEND,
    "gh": _cli_with_read_verbs("changes things on GitHub"),
    "glab": _cli_with_read_verbs("changes things on GitLab"),
    "scp": lambda args: _REMOTE,
    "sftp": lambda args: _REMOTE,
    "ftp": lambda args: _REMOTE,
    "ssh": lambda args: _REMOTE,
    "rsync": lambda args: _REMOTE if any(":" in a and not a.startswith("-") for a in args) else None,
    "curl": _curl,
    "wget": lambda args: _SEND if any(a.split("=", 1)[0] in ("--post-data", "--post-file", "--method", "--body-data", "--body-file") for a in args) else None,
    "apt": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "remove", "purge", "upgrade", "full-upgrade", "autoremove") else None,
    "apt-get": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "remove", "purge", "upgrade", "dist-upgrade", "autoremove") else None,
    "yum": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "remove", "erase", "update", "upgrade") else None,
    "dnf": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "remove", "erase", "update", "upgrade") else None,
    "zypper": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "in", "remove", "rm", "update", "up") else None,
    "pacman": lambda args: _SYSTEM if any(a.startswith(("-S", "-R", "-U")) for a in args) else None,
    "apk": lambda args: _SYSTEM if args[:1] and args[0] in ("add", "del", "upgrade") else None,
    "brew": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "uninstall", "remove", "upgrade", "reinstall", "tap") else None,
    "choco": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "uninstall", "upgrade") else None,
    "winget": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "uninstall", "upgrade") else None,
    "scoop": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "uninstall", "update") else None,
    "snap": lambda args: _SYSTEM if args[:1] and args[0] in ("install", "remove", "refresh") else None,
    "systemctl": lambda args: None if _words_after_options(args)[:1] in (["status"], ["list-units"], ["show"], ["is-active"]) else _SYSTEM,
    "service": lambda args: None if "status" in args else _SYSTEM,
    "launchctl": lambda args: None if args[:1] == ["list"] else _SYSTEM,
    "sc": lambda args: None if args[:1] and args[0].lower() in ("query", "queryex", "qc") else _SYSTEM,
    "setx": lambda args: "changes environment variables for good",
}  # fmt: skip


def _segment_outward(words: list[str]) -> str | None:
    words = _strip_wrappers(words)
    if not words:
        return None
    name, args = _name(words[0]), words[1:]
    if name in ("python", "python3", "py") and args[:2] == ["-m", "pip"]:
        name, args = "pip", args[2:]
    check = _OUTWARD.get(name)
    return check(args) if check else None


def outward_reason(command: str) -> str | None:
    """Why a shell command reaches beyond this computer or project, or None."""
    for m in _SUBSHELL_RE.finditer(command):
        inner = m.group(1) or m.group(2) or ""
        if reason := outward_reason(inner):
            return reason
    for line in re.split(r"[\r\n]+", command):
        for _op, words in _split(line):
            if reason := _segment_outward(words):
                return reason
    return None
