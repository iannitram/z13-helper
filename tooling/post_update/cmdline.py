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
    match = re.match(r'(\s*)GRUB_CMDLINE_LINUX_DEFAULT="((?:[^"\\]|\\.)*)"(.*)$', line, re.DOTALL)
    if not match:
        raise ValueError("Not a GRUB_CMDLINE_LINUX_DEFAULT line")

    leading = match.group(1)
    quoted_part = match.group(2)
    trailing = match.group(3)

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
    return f'{leading}GRUB_CMDLINE_LINUX_DEFAULT="{new_quoted}"{trailing}'
