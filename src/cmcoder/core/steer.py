"""Steer the model from shell commands to the file tools.

Some models (Qwen3 among them) write files with `cat > f << EOF`, read them
with `cat`/`head`/`sed -n` or `python -c "open(...)"`, and search with
`grep -r`/`find`. That skips cmcoder's read-before-edit and staleness checks,
needs a permission prompt for every call, and floods the prompt with long
previews. When a Bash call is plainly one of these, cmcoder doesn't run it
and tells the model which tool to use instead.

Only simple, unambiguous commands are redirected: one command, no pipes or
chains (heredoc writes excepted). Anything else runs as usual. If the model
sends the same command again right after a redirect, it goes through, so a
genuine need for the shell is never blocked.
"""

from __future__ import annotations

import re
import shlex

_CHAIN = re.compile(r"[|;&`]|\$\(")
_DELIM = r"""<<-?\s*['"]?\w+['"]?"""
_TARGET = r"""(['"]?)([^\s'"<>|;&]+)\2"""
# (operator group, path group) for each way of writing a heredoc to a file
_HEREDOC_WRITES = [
    (re.compile(rf"^\s*cat\s+(>>?)\s*{_TARGET}\s*{_DELIM}\s*$"), 1, 3),  # cat > f << EOF
    (re.compile(rf"^\s*cat\s+{_DELIM}\s*(>>?)\s*{_TARGET}\s*$"), 1, 3),  # cat << EOF > f
    (re.compile(rf"^\s*tee\s+(-a\s+)?{_TARGET}\s*{_DELIM}\s*(?:>\s*/dev/null\s*)?$"), 1, 3),
]
# Options that take a value, which would be mistaken for the pattern or path.
_GREP_VALUE_OPTIONS = {"-A", "-B", "-C", "-e", "-f", "-m", "-t", "-g", "-T", "--type", "--glob"}
_ECHO_WRITE = re.compile(
    r"""^\s*(?:echo|printf)\b.*?(?<![0-9&])(>>?)\s*(['"]?)([^\s'"<>|;&]+)\2\s*$""", re.S
)
_SED_RANGE = re.compile(r"""^['"]?(\d+)(?:,(\d+))?p['"]?$""")


def _q(path: str) -> str:
    return '"' + path.replace('"', '\\"') + '"'


def _write_hint(op: str, path: str) -> str:
    if path in ("/dev/null", "/dev/stderr", "/dev/stdout"):
        return ""
    if op == ">>":
        return (
            f"Not run: to add to {path}, use the Edit tool (Read it first), or Write with the "
            "full new content."
        )
    return (
        f"Not run: to create or overwrite {path}, use the Write tool "
        f"(file_path={_q(path)}, content=the full text). Writing files through Bash skips "
        "cmcoder's checks and needs the user's approval every time; Write doesn't."
    )


def _read_hint(path: str, offset: int | None = None, limit: int | None = None) -> str:
    extra = ""
    if offset:
        extra += f", offset={offset}"
    if limit:
        extra += f", limit={limit}"
    return (
        f"Not run: to look at {path}, use the Read tool (file_path={_q(path)}{extra}). "
        "Reading files through Bash needs the user's approval every time; Read doesn't."
    )


def _int(s: str) -> int | None:
    return int(s) if s.isdigit() else None


def _head_tail(name: str, args: list[str]) -> str | None:
    n: int | None = 10
    files: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "--lines") and i + 1 < len(args):
            n = _int(args[i + 1].lstrip("+-"))
            i += 2
            continue
        if a.startswith("--lines="):
            n = _int(a.split("=", 1)[1])
        elif re.fullmatch(r"-n?\d+", a):
            n = _int(a.lstrip("-n"))
        elif a.startswith("-"):
            return None  # -f (follow), -c (bytes), ...: not a plain read
        else:
            files.append(a)
        i += 1
    if len(files) != 1 or n is None:
        return None
    if name == "head":
        return _read_hint(files[0], limit=n)
    return (
        _read_hint(files[0])
        + f" For the last {n} lines, check the line count Read reports and pass an offset."
    )


def _python_open(cmd: str) -> str | None:
    if not re.match(r"^\s*python[0-9.]*(?:\.exe)?\s+-c\s", cmd) or "open(" not in cmd:
        return None
    m = re.search(r"""open\(\s*(?:r|f)?(['"])([^'"]+)\1""", cmd)
    path = m.group(2) if m else "the file"
    writes = re.search(r"""['"](?:w|a|x|wb|ab)\+?['"]|\.write\(|write_text\(""", cmd)
    return _write_hint(">", path) if writes else _read_hint(path)


def file_work_redirect(command: str) -> str | None:
    """A message pointing to the right tool, or None to run the command."""
    cmd = command.strip()
    if not cmd:
        return None
    first = cmd.splitlines()[0]
    if "<<" in first:
        for pattern, op_group, path_group in _HEREDOC_WRITES:
            if m := pattern.match(first):
                op = m.group(op_group) or ">"
                op = ">>" if op.strip() in (">>", "-a") else ">"
                return _write_hint(op, m.group(path_group)) or None
        return None
    if py := _python_open(cmd):
        return py
    if "\n" in cmd or _CHAIN.search(cmd):
        return None
    if m := _ECHO_WRITE.match(cmd):
        return _write_hint(m.group(1), m.group(3)) or None
    if ">" in cmd or "<" in cmd:
        return None
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    name, args = words[0], words[1:]
    flags = [a for a in args if a.startswith("-")]
    paths = [a for a in args if not a.startswith("-")]

    if name == "cat" and len(paths) == 1 and set(flags) <= {"-n"}:
        return _read_hint(paths[0])
    if name in ("head", "tail"):
        return _head_tail(name, args)
    if name == "sed":
        if "-i" in args or any(a.startswith("-i") for a in flags):
            return (
                "Not run: to change a file, use the Edit tool (exact text replacement) after "
                "reading it with Read. `sed -i` skips cmcoder's checks and shows no diff."
            )
        if flags == ["-n"] and len(paths) == 2 and (r := _SED_RANGE.match(paths[0])):
            start = int(r.group(1))
            end = int(r.group(2) or start)
            return _read_hint(paths[1], offset=start, limit=max(1, end - start + 1))
        return None
    if name in ("grep", "egrep", "rg") and paths and not set(flags) & _GREP_VALUE_OPTIONS:
        recursive = (
            name == "rg"
            or any(f in flags for f in ("-r", "-R"))
            or any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", f) for f in flags)
        )
        if recursive or len(paths) >= 2:
            where = f", path={_q(paths[1])}" if len(paths) >= 2 else ""
            return (
                f"Not run: to search file contents, use the Grep tool "
                f"(pattern={_q(paths[0])}{where}). It respects .gitignore and needs no approval."
            )
        return None
    if name == "find" and paths:
        rest = args[1:] if args and not args[0].startswith("-") else args
        if len(rest) == 2 and rest[0] in ("-name", "-iname") and not args[0].startswith("-"):
            root = args[0]
            pattern = rest[1]
            glob = pattern if root in (".", "./") else f"{root.rstrip('/')}/**/{pattern}"
            if root in (".", "./"):
                glob = f"**/{pattern}"
            return (
                f"Not run: to find files by name, use the Glob tool (pattern={_q(glob)}). "
                "It respects .gitignore and needs no approval."
            )
        return None
    return None
