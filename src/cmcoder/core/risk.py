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
