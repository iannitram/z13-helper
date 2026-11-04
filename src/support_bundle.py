"""Conservative, local support bundle construction for post-update repair (#27)."""

import copy
import json
import re

EXCLUDED_CLASSES = (
    "profile hooks",
    "credential values",
    "serial numbers",
    "network identifiers",
    "unrelated journal lines",
    "unreviewed inventory fields",
    "unreviewed journal lines",
)

# Only these scalar version fields have a known, non-private representation.
# Units, permissions and kernel arguments can contain arbitrary private values.
_VERSION_FIELDS = {
    "steamos_build", "kernel", "bios_ec", "firmware", "mesa_vulkan",
    "backend_version",
}
_VERSION = re.compile(r"[0-9][0-9A-Za-z.+_/-]{0,127}\Z")
_SENSITIVE_KEYS = (
    ("profile hooks", re.compile(r"\b(hooks?|commands?|scripts?)\b", re.I)),
    ("credential values", re.compile(
        r"\b(credentials?|passwords?|passwd|secrets?|tokens?|api ?key|authorization|private ?key)\b", re.I
    )),
    ("serial numbers", re.compile(r"\b(serials?|uuid|machine ?id)\b", re.I)),
    ("network identifiers", re.compile(
        r"\b(network|hostname|host name|ssid|mac|ip ?address|ipv[46]|email)\b", re.I
    )),
)


def build_bundle(
    inventory, journal_lines, include_hooks=False, include_credentials=False
) -> dict:
    """Copy only reviewed inventory data; omit unscoped, free-form journal text.

    Unknown inventory fields fail closed, including nested data. Explicit hook
    and credential opt-ins apply only to fields identified as those classes.
    Serial/network fields are always excluded. Journal relevance cannot yet be
    established, so omissions are reported as unreviewed, not as unrelated.
    """
    excluded = dict.fromkeys(EXCLUDED_CLASSES, False)

    def sensitive_classes(value):
        found = set()
        if isinstance(value, dict):
            for key, child in value.items():
                found.update(name for name, pattern in _SENSITIVE_KEYS
                             if pattern.search(re.sub(r"[^a-z0-9]+", " ", str(key).lower())))
                found.update(sensitive_classes(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                found.update(sensitive_classes(child))
        return found

    cleaned = {}
    for key, value in (inventory or {}).items():
        classes = sensitive_classes({key: value})
        blocked = classes - ({"profile hooks"} if include_hooks else set())
        if include_credentials:
            blocked.discard("credential values")
        if blocked:
            for name in classes:
                excluded[name] = True
            continue
        if classes:
            cleaned[key] = copy.deepcopy(value)
        elif key in _VERSION_FIELDS and (
            value is None or (isinstance(value, str) and _VERSION.fullmatch(value))
        ):
            cleaned[key] = value
        else:
            excluded["unreviewed inventory fields"] = True

    # Raw lines can contain identifiers or secrets without recognizable labels.
    # Do not rely on a best-effort regex to make arbitrary journal text safe.
    for _ in journal_lines or ():
        excluded["unreviewed journal lines"] = True

    return {
        "inventory": cleaned,
        "journal_lines": [],
        "include_hooks": include_hooks,
        "include_credentials": include_credentials,
        "excluded": excluded,
    }


def preview(bundle) -> str:
    """Show the complete shareable payload, including its exclusion report."""
    return json.dumps(bundle, indent=2, ensure_ascii=True)
