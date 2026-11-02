from typing import Sequence

def parse_args(argv: Sequence[str]) -> dict:
    """
    Parse command line arguments for post-update functionality.

    Args:
        argv: Sequence of command line arguments

    Returns:
        Dictionary with 'post_update' (bool) and 'only' (list[str]) keys

    Raises:
        ValueError: If --only is used without --post-update, or if --only has no value
    """
    post_update = False
    only = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--post-update":
            post_update = True
        elif arg == "--only":
            if i + 1 >= len(argv):
                raise ValueError("--only needs a value")
            value = argv[i + 1]
            if value.startswith("--"):
                raise ValueError("--only needs a value")
            # Split, strip, filter empty, and deduplicate while preserving order
            items = [item.strip() for item in value.split(",") if item.strip()]
            seen = set()
            deduped = []
            for item in items:
                if item not in seen:
                    seen.add(item)
                    deduped.append(item)
            only = deduped
            i += 1  # Skip the next argument as it's the value for --only
        # Ignore any other arguments
        i += 1

    if only and not post_update:
        raise ValueError("--only needs --post-update")

    return {"post_update": post_update, "only": only}
