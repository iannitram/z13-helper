import re
from typing import Sequence

def missing_args(cmdline: str, wanted: Sequence[str]) -> list:
    """Find missing arguments from wanted list that are not present in cmdline.

    Returns the entries of wanted, in wanted order, that are not an exact
    whitespace-separated token of cmdline.
    """
    if not cmdline:
        return list(wanted)

    # Split cmdline into tokens
    tokens = cmdline.split()

    # Create a set of existing tokens for fast lookup
    existing_tokens = set(tokens)

    # Find missing args in wanted order
    missing = []
    for arg in wanted:
        if arg not in existing_tokens:
            missing.append(arg)

    return missing


def add_grub_args(line: str, args: Sequence[str]) -> str:
    """Add arguments to a GRUB_CMDLINE_LINUX_DEFAULT line.

    Args:
        line: A GRUB_CMDLINE_LINUX_DEFAULT line (e.g. 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"')
        args: Sequence of arguments to add (e.g. ('gpu_recovery=1', 'abmlevel=0'))

    Returns:
        The line with arguments added, modified, or appended as appropriate.

    Raises:
        ValueError: If line is not a GRUB_CMDLINE_LINUX_DEFAULT assignment.
    """
    # Leading whitespace, the quoted value and any trailing text are kept apart;
    # only the quoted value is edited (an escaped quote stays inside the value). Anything else is not the assignment.
    match = re.match(r'(\s*)(GRUB_CMDLINE_LINUX_DEFAULT|GRUB_CMDLINE_LINUX)="((?:[^"\\]|\\.)*)"(.*)$',
                     line, re.DOTALL)
    if not match:
        raise ValueError("Not a GRUB_CMDLINE_LINUX_DEFAULT line")

    leading = match.group(1)
    name = match.group(2)
    quoted_part = match.group(3)
    trailing = match.group(4)

    # An arg with whitespace or a shell-special character would break the quoted value.
    for arg in args:
        if not arg or any(c.isspace() or c in '"\\$`' for c in arg):
            raise ValueError(f"Not a single kernel argument: {arg!r}")

    def key_of(token):
        return token.split("=", 1)[0]

    # Each arg in turn: skip an exact token, replace same-key tokens in place, else append.
    tokens = quoted_part.split()
    for arg in args:
        if arg in tokens:
            continue
        key = key_of(arg)
        if any(key_of(t) == key for t in tokens):
            tokens = [arg if key_of(t) == key else t for t in tokens]
        else:
            tokens.append(arg)

    new_quoted = " ".join(tokens)

    # Reconstruct line, keeping leading whitespace and text after the closing quote
    return f'{leading}{name}="{new_quoted}"{trailing}'


# An active assignment of either variable, at a line start. The quoted value may continue over
# lines with a trailing backslash (SteamOS 3.9.5 sets its parameters that way).
_ASSIGN = re.compile(
    r'^([ \t]*)(GRUB_CMDLINE_LINUX_DEFAULT|GRUB_CMDLINE_LINUX)="((?:[^"\\]|\\[\s\S])*)"', re.MULTILINE)


def _assigns(text: str) -> list:
    return list(_ASSIGN.finditer(text or ""))


def _tokens(value: str) -> list:
    return value.replace("\\\n", " ").split()


def grub_value(text: str):
    """Combined value of the kernel-argument assignments in a grub file (GRUB joins
    GRUB_CMDLINE_LINUX and GRUB_CMDLINE_LINUX_DEFAULT), None when there is none.

    Backslash-newline continuations read as whitespace."""
    found = _assigns(text)
    if not found:
        return None
    return " ".join(" ".join(_tokens(m.group(3))) for m in found)


def _key_of(token: str) -> str:
    return token.split("=", 1)[0]


def _edit_value(value: str, args: Sequence[str]) -> str:
    """value with args applied: same-key tokens replaced where they stand, the rest added
    (a continued value gets one new continuation line before the closing quote)."""
    if "\n" not in value:
        return " ".join(_edit_tokens(value.split(), args))
    new, added = value, []
    for arg in args:
        tokens = _tokens(new)
        if arg in tokens or arg in added:
            continue
        if any(_key_of(t) == _key_of(arg) for t in tokens):
            new = re.sub(r'(?<![^\s"])' + re.escape(_key_of(arg)) + r'=\S*', arg.replace("\\", r"\\"), new)
        else:
            added.append(arg)
    if added:
        if new.rstrip().endswith("\\"):
            new += " ".join(added) + " \\\n"        # one more continuation line, same style
        else:
            new = new.rstrip() + " " + " ".join(added)
    return new


def _edit_tokens(tokens: list, args: Sequence[str]) -> list:
    for arg in args:
        if arg in tokens:
            continue
        if any(_key_of(t) == _key_of(arg) for t in tokens):
            tokens = [arg if _key_of(t) == _key_of(arg) else t for t in tokens]
        else:
            tokens.append(arg)
    return tokens


def add_grub_args_text(text: str, args: Sequence[str]) -> str:
    """Make the kernel arguments of a whole grub file carry args, judged on the COMBINED
    GRUB_CMDLINE_LINUX + GRUB_CMDLINE_LINUX_DEFAULT values.

    An arg already present with its value is left alone. One with a different value is
    replaced in each variable that holds it. A missing one is added to the variable that
    already holds amdgpu.* args, else GRUB_CMDLINE_LINUX_DEFAULT, else GRUB_CMDLINE_LINUX,
    so no arg ends up in both. Returns text unchanged when nothing is missing.

    Raises:
        ValueError: no assignment of either variable, or a bad arg.
    """
    found = _assigns(text)
    if not found:
        raise ValueError("no GRUB_CMDLINE_LINUX_DEFAULT or GRUB_CMDLINE_LINUX line")
    for arg in args:
        if not arg or any(c.isspace() or c in '"\\$`' for c in arg):
            raise ValueError(f"Not a single kernel argument: {arg!r}")
    toks = [_tokens(m.group(3)) for m in found]
    home = next((i for i, t in enumerate(toks) if any(x.startswith("amdgpu.") for x in t)), None)
    if home is None:
        home = next((i for i, m in enumerate(found) if m.group(2) == "GRUB_CMDLINE_LINUX_DEFAULT"), 0)
    todo = [[] for _ in found]
    for arg in args:
        holders = [i for i, t in enumerate(toks) if any(_key_of(x) == _key_of(arg) for x in t)]
        if holders:
            for i in holders:
                if any(x != arg and _key_of(x) == _key_of(arg) for x in toks[i]):
                    todo[i].append(arg)      # changed value: replaced where it stands
        else:
            todo[home].append(arg)
    out = text
    for m, extra in sorted(zip(found, todo), key=lambda p: -p[0].start()):
        if not extra:
            continue
        old = m.group(3)
        new = _edit_value(old, extra)
        if new != old:
            out = out[:m.start(3)] + new + out[m.end(3):]
    return out
