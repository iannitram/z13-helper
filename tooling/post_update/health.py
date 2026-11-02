import re
from typing import Callable, Sequence

KEYRING_MIN = 50

def keyring_status(list_keys_output: str) -> str:
    r"""Count lines matching '^pub\s' in pacman-key --list-keys output.

    Return "ok" if count >= KEYRING_MIN, else "needs".
    Empty output or error message gives "needs".
    """
    if not list_keys_output:
        return "needs"

    # Check for error messages
    if "error:" in list_keys_output:
        return "needs"

    # Count lines starting with "pub" followed by whitespace
    count = 0
    for line in list_keys_output.splitlines():
        if re.match(r"pub\s", line):
            count += 1

    return "ok" if count >= KEYRING_MIN else "needs"


def carry_forward_missing(conf_texts: Sequence[str], exists: Callable[[str], bool]) -> list:
    """Return paths from conf_texts that don't exist, in first-seen order, deduplicated.

    Each line in conf_texts is stripped and treated as a path unless it's empty or starts with '#'.
    """
    missing = []
    seen = set()

    for text in conf_texts:
        for line in text.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Check if path is not already seen
            if line not in seen:
                if not exists(line):
                    missing.append(line)
                seen.add(line)

    return missing
