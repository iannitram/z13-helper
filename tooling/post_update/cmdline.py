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
    # Check if this is a valid GRUB_CMDLINE_LINUX_DEFAULT line
    if not line.startswith('GRUB_CMDLINE_LINUX_DEFAULT='):
        raise ValueError("Not a GRUB_CMDLINE_LINUX_DEFAULT line")

    # Extract the quoted part and any trailing text
    match = re.match(r'GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"(.*$)', line)
    if not match:
        raise ValueError("Malformed GRUB_CMDLINE_LINUX_DEFAULT line")

    quoted_part = match.group(1)
    trailing = match.group(2)

    # Parse existing tokens
    existing_tokens = quoted_part.split() if quoted_part else []

    # Build result preserving the order of existing tokens, replacing keys as needed
    result_tokens = []
    processed_keys = set()

    # Process existing tokens in order, replacing keys with new values when found
    for token in existing_tokens:
        if '=' in token:
            key, _ = token.split('=', 1)
            # Check if this key is being replaced by any new arg
            replacement_found = False
            for arg in args:
                if arg.startswith(key + '='):
                    result_tokens.append(arg)
                    replacement_found = True
                    processed_keys.add(key)
                    break
            if not replacement_found:
                result_tokens.append(token)
        else:
            # Simple token without equals sign
            result_tokens.append(token)

    # Add new args that don't already exist (preserve order)
    for arg in args:
        if '=' in arg:
            key, _ = arg.split('=', 1)
            # If we already have a token with this key, don't add it again (already handled above)
            if key not in [t.split('=', 1)[0] if '=' in t else t for t in result_tokens]:
                result_tokens.append(arg)
        else:
            # Simple token without equals sign
            if arg not in result_tokens:
                result_tokens.append(arg)

    # Rejoin tokens
    new_quoted = " ".join(result_tokens) if result_tokens else ""

    # Reconstruct line
    if trailing:
        return f'GRUB_CMDLINE_LINUX_DEFAULT="{new_quoted}"' + trailing
    else:
        return f'GRUB_CMDLINE_LINUX_DEFAULT="{new_quoted}"'
