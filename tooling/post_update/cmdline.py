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


def _find_assign(text: str):
    """The assignment read and edited: GRUB_CMDLINE_LINUX_DEFAULT when present, else GRUB_CMDLINE_LINUX."""
    found = list(_ASSIGN.finditer(text or ""))
    for name in ("GRUB_CMDLINE_LINUX_DEFAULT", "GRUB_CMDLINE_LINUX"):
        for m in found:
            if m.group(2) == name:
                return m
    return None


def grub_value(text: str):
    """Value of the kernel-argument assignment in a grub file, None when there is none.

    Backslash-newline continuations read as whitespace."""
    m = _find_assign(text)
    return None if m is None else m.group(3).replace("\\\n", " ")


def add_grub_args_text(text: str, args: Sequence[str]) -> str:
    """Add args to the kernel-argument assignment of a whole grub file.

    A single-line value goes through add_grub_args. A value continued over lines is edited in
    place: a same-key token is replaced where it stands, missing args are added as one new
    continuation line before the closing quote. Returns text unchanged when nothing is missing.

    Raises:
        ValueError: no GRUB_CMDLINE_LINUX_DEFAULT or GRUB_CMDLINE_LINUX assignment, or a bad arg.
    """
    m = _find_assign(text)
    if m is None:
        raise ValueError("no GRUB_CMDLINE_LINUX_DEFAULT or GRUB_CMDLINE_LINUX line")
    for arg in args:
        if not arg or any(c.isspace() or c in '"\\$`' for c in arg):
            raise ValueError(f"Not a single kernel argument: {arg!r}")
    value = m.group(3)
    if "\n" not in value:
        return text[:m.start()] + add_grub_args(m.group(0), args) + text[m.end():]

    def key_of(token):
        return token.split("=", 1)[0]

    new, added = value, []
    for arg in args:
        tokens = new.replace("\\\n", " ").split()
        if arg in tokens or arg in added:
            continue
        if any(key_of(t) == key_of(arg) for t in tokens):
            new = re.sub(r'(?<![^\s"])' + re.escape(key_of(arg)) + r'=\S*', arg.replace("\\", r"\\"), new)
        else:
            added.append(arg)
    if added:
        if new.rstrip().endswith("\\"):
            new += " ".join(added) + " \\\n"        # one more continuation line, same style
        else:
            new = new.rstrip() + " " + " ".join(added)
    if new == value:
        return text
    start, end = m.span(3)
    return text[:start] + new + text[end:]
