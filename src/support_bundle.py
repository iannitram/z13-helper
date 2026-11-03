"""Support bundle skeleton for post-update repair (#27)."""

EXCLUDED_CLASSES = (
    "profile hooks",
    "credential values",
    "serial numbers",
    "network identifiers",
    "unrelated journal lines",
)


def build_bundle(
    inventory, journal_lines, include_hooks=False, include_credentials=False
) -> dict:
    """Return the bundle skeleton with the default exclusions recorded.

    TODO(#27): redact profile hooks, credential values, serial numbers, network
    identifiers and unrelated journal lines before any of them reach the bundle.
    This skeleton copies no data and filters nothing.
    """
    return {
        "inventory": dict(inventory or {}),
        "journal_lines": list(journal_lines or []),
        "include_hooks": include_hooks,
        "include_credentials": include_credentials,
        "excluded": {name: True for name in EXCLUDED_CLASSES},
    }


def preview(bundle) -> str:
    """Return a short summary of the bundle without printing its contents."""
    inventory = bundle.get("inventory") or {}
    journal_lines = bundle.get("journal_lines") or []
    return (
        f"Support bundle: {len(inventory)} inventory fields, "
        f"{len(journal_lines)} journal lines"
    )
