"""Shell commands that only read: they run without asking, in every mode.

A command line is taken apart into its simple commands (`a | b && c; d`), each
with its redirections, by a parser that knows shell quoting: a `$(...)`,
backtick or `<(...)` inside a command makes that part dynamic (it runs code
the parts can't show), a `$VAR` makes it depend on the shell, and `> file`
is a file write. The command line is read-only when every part is a known
read-only program, used without its writing options (`find -delete`,
`sort -o`, `git diff --output`), on files inside the project that aren't
secrets, with output going nowhere but the screen or /dev/null.

Not a sandbox: this decides when asking would only be noise. Anything it
can't account for is not read-only, and goes through the usual rules.
"""

from __future__ import annotations

import glob
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..sensitive import is_secret


@dataclass
class Redirect:
    op: str  # ">", ">>", "<", "&>", ">&", "<<" ...
    target: str
    dynamic: bool = False  # the target uses $(...) or a variable


@dataclass
class Segment:
    """One simple command of a command line."""

    words: list[str] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    op: str = ""  # the operator before it: "", "|", "&&", "||", ";", "&", "("
    dynamic: bool = False  # runs code it doesn't show: $(...), `...`, <(...)
    expands: bool = False  # uses a variable ($HOME, ${X}): its text isn't what runs
    globs: set[int] = field(default_factory=set)  # indexes of words with unquoted * ? [
    inner: list[str] = field(default_factory=list)  # the commands inside $(...) and `...`


class _Unbalanced(Exception):
    pass


_SEPARATORS = ("&&", "||", "|&", ";;", "|", ";", "&")
NULL_DEVICES = {"/dev/null", "nul", "/dev/stdout", "/dev/stderr"}


def _closing(s: str, i: int, open_: str, close: str) -> int:
    """The index just past the `close` matching the `open_` at s[i-1] (quotes skipped)."""
    depth, n = 1, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unbalanced
            i = j + 1
            continue
        if c == '"':
            i = _closing_quote(s, i + 1)
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise _Unbalanced


def _closing_quote(s: str, i: int) -> int:
    """The index just past the `"` closing a double-quoted string that starts at s[i]."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        if s.startswith("$(", i):
            i = _closing(s, i + 2, "(", ")")
            continue
        if c == "`":
            j = s.find("`", i + 1)
            if j < 0:
                raise _Unbalanced
            i = j + 1
            continue
        i += 1
    raise _Unbalanced


def parse(command: str) -> list[Segment] | None:
    """The simple commands of a command line, or None if it doesn't parse
    (an unclosed quote or substitution, a redirection without a target)."""
    try:
        return _parse(command)
    except _Unbalanced:
        return None


def _parse(s: str) -> list[Segment]:
    segments: list[Segment] = []
    seg = Segment()
    word: list[str] = []
    in_word = False
    w_dynamic = w_expands = w_glob = w_quoted = False
    pending: str | None = None  # a redirection waiting for its target
    # Heredocs to read after the line: (delimiter, strip tabs, quoted, their command).
    heredocs: list[tuple[str, bool, bool, Segment]] = []
    i, n = 0, len(s)

    def end_word() -> None:
        nonlocal word, in_word, w_dynamic, w_expands, w_glob, w_quoted, pending
        if not in_word:
            return
        text = "".join(word)
        if pending is not None:
            if pending in ("<<", "<<-"):
                heredocs.append((text, pending == "<<-", w_quoted, seg))
            seg.redirects.append(Redirect(pending, text, w_dynamic or w_expands))
            seg.dynamic |= w_dynamic
            pending = None
        else:
            if w_glob:
                seg.globs.add(len(seg.words))
            seg.words.append(text)
            seg.dynamic |= w_dynamic
            seg.expands |= w_expands
        word, in_word, w_dynamic, w_expands, w_glob, w_quoted = (
            [],
            False,
            False,
            False,
            False,
            False,
        )

    def end_segment(op: str) -> None:
        nonlocal seg
        end_word()
        if pending is not None:
            raise _Unbalanced  # `ls >` with nothing after it
        if seg.words or seg.redirects:
            segments.append(seg)
        seg = Segment(op=op)

    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 < n and s[i + 1] == "\n":  # a line continuation
                i += 2
                continue
            word.append(s[i + 1] if i + 1 < n else "")
            in_word = w_quoted = True
            i += 2
            continue
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise _Unbalanced
            word.append(s[i + 1 : j])
            in_word = w_quoted = True
            i = j + 1
            continue
        if c == '"':
            j = _closing_quote(s, i + 1)
            inside = s[i + 1 : j - 1]
            for m in re.finditer(r"\$\(|`", inside):
                w_dynamic = True
                start = m.end()
                end = (
                    _closing(inside, start, "(", ")") - 1
                    if m.group() == "$("
                    else inside.index("`", start)
                )
                seg.inner.append(inside[start:end])
            if re.search(r"\$[\w{@*#?$!-]", inside):
                w_expands = True
            word.append(re.sub(r"\\([\\\"$`])", r"\1", inside))
            in_word = w_quoted = True
            i = j
            continue
        if s.startswith("$(", i):
            j = _closing(s, i + 2, "(", ")")
            seg.inner.append(s[i + 2 : j - 1])
            word.append(s[i:j])
            in_word = w_dynamic = True
            i = j
            continue
        if c == "`":
            j = s.find("`", i + 1)
            if j < 0:
                raise _Unbalanced
            seg.inner.append(s[i + 1 : j])
            word.append(s[i : j + 1])
            in_word = w_dynamic = True
            i = j + 1
            continue
        if c == "$" and i + 1 < n and (s[i + 1] in "{@*#?$!-_" or s[i + 1].isalnum()):
            if s[i + 1] == "{":
                j = _closing(s, i + 2, "{", "}")
                if re.search(r"\$\(|`", s[i:j]):  # ${x:-$(cmd)} runs cmd
                    seg.inner.append(s[i + 2 : j - 1])
                    w_dynamic = True
            else:
                m = re.match(r"\$(\w+|[@*#?$!-])", s[i:])
                j = i + (m.end() if m else 1)
            word.append(s[i:j])
            in_word = w_expands = True
            i = j
            continue
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "\n":
            end_segment(";")
            i += 1
            for delimiter, strip, quoted, owner in heredocs:
                # The heredoc's text: not commands, but an unquoted delimiter
                # (<<EOF, not <<'EOF') lets $(...) and `...` in it run.
                while i < n:
                    j = s.find("\n", i)
                    line = s[i:] if j < 0 else s[i:j]
                    i = n if j < 0 else j + 1
                    if (line.lstrip("\t") if strip else line) == delimiter:
                        break
                    if not quoted and re.search(r"\$\(|`|\$\{[^}]*(\$\(|`)", line):
                        owner.dynamic = True
                        owner.inner.append(line)
            heredocs.clear()
            continue
        if c == "#" and not in_word:
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "<>" and i + 1 < n and s[i + 1] == "(":  # <(...) >(...): runs a command
            j = _closing(s, i + 2, "(", ")")
            seg.inner.append(s[i + 2 : j - 1])
            word.append(s[i:j])
            in_word = w_dynamic = True
            i = j
            continue
        if c in "<>" or (c == "&" and s.startswith("&>", i)):
            fd = "".join(word) if in_word and word and "".join(word).isdigit() else None
            if fd is not None:  # `2>`: the digits were the descriptor, not a word
                word, in_word = [], False
            else:
                end_word()
            if pending is not None:
                raise _Unbalanced
            m = re.match(r"&>>|&>|<<<|<<-|<<|<>|<&|>>|>&|>\||<|>", s[i:])
            assert m
            pending = m.group()
            i += m.end()
            continue
        if c in "|&;":
            op = next(o for o in _SEPARATORS if s.startswith(o, i))
            end_segment(op)
            i += len(op)
            continue
        if c in "()":
            end_segment(c)
            i += 1
            continue
        if c in "*?[":
            w_glob = True
        if c == "{" and re.match(r"\{[^\s{}]*(,|\.\.)[^\s{}]*\}", s[i:]):
            w_expands = True  # {a,b} and {1..9}: one word becomes several
        word.append(c)
        in_word = True
        i += 1
    end_segment("")
    return segments


# --- what read-only means for each program -------------------------------------------


class _Paths:
    """Checks the files a command names: inside the project, not a secret."""

    def __init__(self, cwd: Path, root: Path) -> None:
        self.cwd = cwd
        self.root = root.resolve()

    def resolve(self, word: str) -> Path:
        word = os.path.expanduser(word) if word.startswith("~") else word
        m = re.match(r"^/([A-Za-z])(/.*)?$", word)
        if os.name == "nt" and m:  # Git Bash: /c/Users/x is C:/Users/x
            word = f"{m.group(1)}:{m.group(2) or '/'}"
        return (self.cwd / word).resolve()

    def ok(self, word: str, globbed: bool = False) -> bool:
        if word in ("", "-") or word.lower() in NULL_DEVICES:
            return True
        try:
            path = self.resolve(word)
        except (OSError, RuntimeError, ValueError):
            return False
        if not path.is_relative_to(self.root) or is_secret(path, self.root):
            return False
        if globbed:  # `cat *.env`: what the shell would pass
            pattern = str(self.cwd / word) if not os.path.isabs(word) else word
            for match in glob.glob(pattern, recursive=True):
                p = Path(match).resolve()
                if not p.is_relative_to(self.root) or is_secret(p, self.root):
                    return False
        return True


Check = Callable[[list[str], set[int], _Paths], bool]


def _files(
    values: frozenset[str] = frozenset(),
    deny: frozenset[str] = frozenset(),
    pattern_first: bool = False,
    pattern_options: frozenset[str] = frozenset({"-e", "--regexp"}),
    file_options: frozenset[str] = frozenset({"-f", "--file"}),
    max_files: int | None = None,
    check_paths: bool = True,
) -> Check:
    """A program whose operands are files (after a pattern, for grep-like ones).

    values: options that take the next word as their value (not a file);
    deny: options that write or run something; max_files: more operands
    than this means an output file (`uniq in out`)."""

    def check(args: list[str], globs: set[int], paths: _Paths) -> bool:
        pattern_given = not pattern_first
        operands = 0
        skip = False
        options_done = False
        for i, a in enumerate(args):
            if skip:
                skip = False
                continue
            if not options_done and a == "--":
                options_done = True
                continue
            if not options_done and a.startswith("-") and len(a) > 1:
                name = a.split("=", 1)[0]
                if name in deny or (not name.startswith("--") and _short_in(a, deny)):
                    return False
                if "=" not in a and name in pattern_options:
                    pattern_given, skip = True, True
                elif "=" not in a and name in file_options:
                    pattern_given = True
                    if i + 1 < len(args) and not paths.ok(args[i + 1], i + 1 in globs):
                        return False
                    skip = True
                elif name in pattern_options or name in file_options:
                    pattern_given = True
                elif "=" not in a and name in values:
                    skip = True
                continue
            if not pattern_given:
                pattern_given = True  # the first operand is the pattern
                continue
            operands += 1
            if max_files is not None and operands > max_files:
                return False
            if check_paths and not paths.ok(a, i in globs):
                return False
        return True

    return check


def _short_in(arg: str, deny: frozenset[str]) -> bool:
    """`-ri` holds `-i`: short options run together."""
    return any(f"-{c}" in deny for c in arg[1:]) if not arg.startswith("--") else False


def _no_args(args: list[str], globs: set[int], paths: _Paths) -> bool:
    return not args


def _any(args: list[str], globs: set[int], paths: _Paths) -> bool:
    return True


def _find(args: list[str], globs: set[int], paths: _Paths) -> bool:
    if any(
        a
        in (
            "-exec",
            "-execdir",
            "-ok",
            "-okdir",
            "-delete",
            "-fprint",
            "-fprint0",
            "-fprintf",
            "-fls",
        )
        for a in args
    ):
        return False
    for i, a in enumerate(args):  # the starting points, before the first test
        if a.startswith(("-", "(", "!")):
            break
        if not paths.ok(a, i in globs):
            return False
    return True


_SED_PRINT = re.compile(r"^(\d+|\$)(,(\d+|\$))?p$")


def _sed(args: list[str], globs: set[int], paths: _Paths) -> bool:
    """Only `sed -n 'N,Mp' file`: printing lines."""
    if "-n" not in args and "--quiet" not in args and "--silent" not in args:
        return False
    script_seen = False
    for i, a in enumerate(args):
        if a in ("-n", "--quiet", "--silent"):
            continue
        if a.startswith("-"):
            return False  # -i, -e with any script, -f, -s ...
        if not script_seen:
            if not _SED_PRINT.match(a):
                return False
            script_seen = True
        elif not paths.ok(a, i in globs):
            return False
    return script_seen


def _date(args: list[str], globs: set[int], paths: _Paths) -> bool:
    return all(a.startswith("+") or a in ("-u", "--utc", "-R", "-I") for a in args)


_VERSION_FLAGS = {"--version", "-v", "-V", "-version", "version", "--info"}


def _version(args: list[str], globs: set[int], paths: _Paths) -> bool:
    return len(args) == 1 and args[0] in _VERSION_FLAGS | {"--list-sdks", "--list-runtimes"}


def _subcommands(read: set[str], then: Check = _any) -> Check:
    """`npm ls`, `pip list`: read-only subcommands, or a version query."""

    def check(args: list[str], globs: set[int], paths: _Paths) -> bool:
        if _version(args, globs, paths):
            return True
        return bool(args) and args[0] in read and then(args[1:], set(), paths)

    return check


_GREP_VALUES = frozenset(
    {
        "-m",
        "--max-count",
        "-A",
        "-B",
        "-C",
        "--after-context",
        "--before-context",
        "--context",
        "--include",
        "--exclude",
        "--exclude-dir",
        "-d",
        "-D",
        "--color",
        "--colour",
        "--label",
        "--binary-files",
    }
)
_RG_VALUES = frozenset(
    {
        "-m",
        "--max-count",
        "-A",
        "-B",
        "-C",
        "-g",
        "--glob",
        "--iglob",
        "-t",
        "--type",
        "-T",
        "--type-not",
        "-j",
        "--threads",
        "-M",
        "--max-columns",
        "--max-depth",
        "-d",
        "--color",
        "--colors",
        "-E",
        "--encoding",
        "--sort",
        "--sortr",
        "--max-filesize",
        "-r",
        "--replace",
    }
)

_GIT_GLOBAL_OK = {"--no-pager", "--no-optional-locks", "-P", "--paginate"}
_GIT_NO_WRITE = frozenset({"--output", "--ext-diff", "-O", "--open-files-in-pager"})
_GIT_BRANCH_LIST = {
    "-a",
    "--all",
    "-r",
    "--remotes",
    "-v",
    "-vv",
    "--verbose",
    "-l",
    "--list",
    "--show-current",
    "--no-color",
    "--color",
    "--merged",
    "--no-merged",
    "--contains",
    "--no-contains",
    "--sort",
    "--format",
}
_GIT_READ = {
    "status": _files(deny=_GIT_NO_WRITE),
    "diff": _files(deny=_GIT_NO_WRITE),
    "log": _files(deny=_GIT_NO_WRITE),
    "show": _files(deny=_GIT_NO_WRITE),
    "shortlog": _files(deny=_GIT_NO_WRITE),
    "whatchanged": _files(deny=_GIT_NO_WRITE),
    "blame": _files(deny=_GIT_NO_WRITE, values=frozenset({"-L", "-S", "--contents"})),
    "annotate": _files(deny=_GIT_NO_WRITE),
    "grep": _files(
        deny=_GIT_NO_WRITE,
        pattern_first=True,
        values=frozenset({"-m", "-A", "-B", "-C", "--max-depth", "--threads"}),
    ),
    "ls-files": _files(),
    "ls-tree": _files(),
    "rev-parse": _files(),
    "rev-list": _files(),
    "describe": _files(),
    "name-rev": _files(),
    "merge-base": _files(),
    "cat-file": _files(),
    "show-ref": _files(),
    "for-each-ref": _files(),
    "count-objects": _files(),
    "check-ignore": _files(),
    "check-attr": _files(),
    "version": _any,
    "branch": lambda args, g, p: all(a in _GIT_BRANCH_LIST or a.startswith("--format=") for a in args),
    "tag": lambda args, g, p: (
        not args or args[0] in ("-l", "--list", "-n") or args[0].startswith("--sort")
    )
    and not any(a in ("-d", "--delete", "-a", "-s", "-f", "-m", "-F") for a in args),
    "remote": lambda args, g, p: (
        not args
        or args == ["-v"]
        or (args[0] in ("show", "get-url") and len(args) <= 3 and "-n" not in args[2:])
    ),
    "config": lambda args, g, p: any(
        a in ("--get", "--get-all", "--get-regexp", "--list", "-l") for a in args
    )
    and not any(
        a in ("--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e", "--rename-section",
              "--remove-section")
        for a in args
    ),
    "stash": lambda args, g, p: bool(args) and args[0] in ("list", "show"),
    "reflog": lambda args, g, p: not args or args[0] == "show" or args[0].startswith("-"),
    "worktree": lambda args, g, p: args[:1] == ["list"],
}  # fmt: skip


def _git(args: list[str], globs: set[int], paths: _Paths) -> bool:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            if not paths.ok(args[i + 1]):
                return False
            paths = _Paths(paths.resolve(args[i + 1]), paths.root)
            i += 2
            continue
        if args[i] not in _GIT_GLOBAL_OK:
            return False  # -c k=v, --git-dir, --exec-path: they change what runs
        i += 1
    if i >= len(args):
        return False
    check = _GIT_READ.get(args[i])
    shifted = {g - i - 1 for g in globs if g > i}
    return check is not None and check(args[i + 1 :], shifted, paths)


_XARGS_VALUES = {"-n", "-L", "-s", "-d", "-E", "-I", "-P", "--max-args", "--delimiter", "-a"}


def _xargs(args: list[str], globs: set[int], paths: _Paths) -> bool:
    """`xargs grep -l foo`: read-only when the command it runs is."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-a", "--arg-file") and i + 1 < len(args) and not paths.ok(args[i + 1]):
            return False
        i += 2 if args[i] in _XARGS_VALUES else 1
    if i >= len(args):
        return False  # plain `xargs` runs echo: harmless, but nothing to approve it for
    inner = args[i:]
    return program_read_only(inner, {g - i for g in globs if g >= i}, paths)


PROGRAMS: dict[str, Check] = {
    # Look at files and text
    "cat": _files(deny=frozenset()),
    "tac": _files(),
    "nl": _files(values=frozenset({"-s", "-w", "-b", "-v", "-i"})),
    "head": _files(values=frozenset({"-n", "-c", "--lines", "--bytes"})),
    "tail": _files(values=frozenset({"-n", "-c", "--lines", "--bytes", "-s", "--pid"})),
    "wc": _files(),
    "rev": _files(),
    "cut": _files(values=frozenset({"-d", "-f", "-c", "-b", "--delimiter", "--fields"})),
    "paste": _files(values=frozenset({"-d", "--delimiters"})),
    "fold": _files(values=frozenset({"-w", "--width"})),
    "column": _files(values=frozenset({"-s", "-t", "-c", "-o"})),
    "comm": _files(),
    "join": _files(values=frozenset({"-t", "-1", "-2", "-j", "-o", "-e"})),
    "cmp": _files(),
    "diff": _files(values=frozenset({"-U", "-C", "--label", "-x", "--exclude", "-I"})),
    "strings": _files(values=frozenset({"-n", "-t", "-e"})),
    "od": _files(values=frozenset({"-t", "-A", "-j", "-N", "-w"})),
    "hexdump": _files(values=frozenset({"-n", "-s", "-e"})),
    "xxd": _files(values=frozenset({"-l", "-s", "-c", "-g"}), deny=frozenset({"-r"}), max_files=1),
    "file": _files(deny=frozenset({"-C", "--compile"}), values=frozenset({"-m", "-F"})),
    "stat": _files(values=frozenset({"-c", "--format", "--printf", "-f"})),
    "md5sum": _files(),
    "sha1sum": _files(),
    "sha256sum": _files(),
    "sha512sum": _files(),
    "cksum": _files(),
    "sort": _files(
        values=frozenset({"-k", "-t", "-S", "--key", "--field-separator", "--buffer-size"}),
        deny=frozenset({"-o", "--output", "--compress-program", "-T", "--temporary-directory"}),
    ),
    "uniq": _files(values=frozenset({"-f", "-s", "-w"}), max_files=1),
    "tr": _any,  # reads stdin only; its operands are sets of characters
    "jq": _files(pattern_first=True, values=frozenset({"--indent", "--tab"})),
    "yq": _files(pattern_first=True, deny=frozenset({"-i", "--inplace"})),
    "sed": _sed,
    # Search
    "grep": _files(pattern_first=True, values=_GREP_VALUES),
    "egrep": _files(pattern_first=True, values=_GREP_VALUES),
    "fgrep": _files(pattern_first=True, values=_GREP_VALUES),
    "rg": _files(
        pattern_first=True,
        values=_RG_VALUES,
        deny=frozenset({"--pre", "--hostname-bin"}),
    ),
    "find": _find,
    "ls": _files(values=frozenset({"-I", "--ignore", "-w", "--width", "--sort", "--format"})),
    "tree": _files(values=frozenset({"-L", "-I", "-P", "--charset"}), deny=frozenset({"-o"})),
    "du": _files(values=frozenset({"-d", "--max-depth", "--exclude"})),
    "df": _files(),
    "realpath": _files(),
    "readlink": _files(),
    "basename": _files(check_paths=False),
    "dirname": _files(check_paths=False),
    # Print, about the system
    "echo": _any,
    "printf": _any,
    "pwd": _any,
    "true": _any,
    "false": _any,
    "test": _any,
    "[": _any,
    "seq": _any,
    "expr": _any,
    "sleep": _any,
    "which": _any,
    "type": _any,
    "whoami": _any,
    "id": _any,
    "uname": _any,
    "nproc": _any,
    "hostname": _no_args,
    "date": _date,
    # Version and list queries
    "git": _git,
    "xargs": _xargs,
    "dotnet": _version,
    "node": _version,
    "java": _version,
    "javac": _version,
    "go": lambda args, g, p: args in (["version"], ["env"]) or _version(args, g, p),
    "python": _version,
    "python3": _version,
    "py": _version,
    "tsc": _version,
    "deno": _version,
    "rustc": _version,
    "cargo": _version,
    "mvn": _version,
    "gradle": _version,
    "make": _version,
    "cmake": _version,
    "gcc": _version,
    "npm": _subcommands({"ls", "list", "outdated", "root", "prefix"}),
    "pnpm": _subcommands({"ls", "list", "outdated", "root"}),
    "yarn": _subcommands({"list", "info", "why"}),
    "pip": _subcommands({"list", "show", "freeze"}),
    "pip3": _subcommands({"list", "show", "freeze"}),
}


# Programs that exist to read files: one that isn't read-only here names a
# file outside the project or a secret, or uses a writing option.
READERS = frozenset(
    {
        "cat", "tac", "nl", "head", "tail", "wc", "rev", "cut", "paste", "fold", "column",
        "comm", "join", "cmp", "diff", "strings", "od", "hexdump", "xxd", "file", "stat",
        "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "sort", "uniq", "jq", "yq",
        "grep", "egrep", "fgrep", "rg", "find", "ls", "tree", "du", "realpath", "readlink",
    }
)  # fmt: skip


def program_read_only(words: list[str], globs: set[int], paths: _Paths) -> bool:
    """Whether one simple command (its words) only reads."""
    if not words:
        return True
    name = words[0]
    # A bare program name: `./grep` or `/tmp/cat` could be anything.
    check = PROGRAMS.get(name) if re.fullmatch(r"[\w.\[-]+", name) else None
    return check is not None and check(words[1:], {g - 1 for g in globs if g > 0}, paths)


@dataclass
class Verdict:
    read_only: bool  # the program only reads (and its files are fine)
    writes: list[Path]  # files it writes through redirections
    unknown_write: bool  # writes somewhere it can't tell (a variable, $(...))
    reads_ok: bool  # its `< file` inputs are inside the project, not secrets


def classify(segments: list[Segment], cwd: Path, root: Path) -> list[Verdict]:
    """A verdict per simple command. `cd dir` (inside the project) moves the
    folder the next commands' paths are checked from."""
    out: list[Verdict] = []
    paths = _Paths(cwd, root)
    for seg in segments:
        writes: list[Path] = []
        unknown = False
        reads_ok = True
        for r in seg.redirects:
            if r.op in ("<<", "<<-", "<<<"):
                continue  # text given on the command line
            if r.op in ("<&", ">&") and (r.target.isdigit() or r.target == "-"):
                continue  # 2>&1: between descriptors
            if r.dynamic:
                unknown = True
                continue
            if r.op == "<":
                reads_ok = reads_ok and paths.ok(r.target)
                continue
            if r.target.lower() in NULL_DEVICES:
                continue
            try:
                writes.append(paths.resolve(r.target))
            except (OSError, RuntimeError, ValueError):
                unknown = True
        words = seg.words
        assignment = bool(words) and re.match(r"^[A-Za-z_]\w*=", words[0]) is not None
        if words[:1] == ["cd"] and not seg.dynamic and not seg.expands:
            target = words[1] if len(words) == 2 else None
            ok = target is not None and paths.ok(target)
            if ok and target is not None:
                paths = _Paths(paths.resolve(target), paths.root)
            out.append(Verdict(ok, writes, unknown, reads_ok))
            continue
        read_only = (
            not seg.dynamic
            and not seg.expands
            and not assignment
            and program_read_only(words, seg.globs, paths)
        )
        out.append(Verdict(read_only, writes, unknown, reads_ok))
    return out


def is_read_only(command: str, cwd: Path, root: Path) -> bool:
    """Whether the whole command line only reads, inside the project."""
    segments = parse(command)
    if not segments:
        return False
    return all(
        v.read_only and v.reads_ok and not v.writes and not v.unknown_write
        for v in classify(segments, cwd, root)
    )
