"""Local post-update recipes loader."""

import os
import re
import tomllib


def parse_local(text: str) -> dict[str, object]:
    """Parse a TOML recipe string.

    Args:
        text: TOML content as string

    Returns:
        Dictionary with recipe fields

    Raises:
        ValueError: If parsing fails
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"invalid TOML: {e}") from e

    # Check required fields
    required_fields = ["id", "title", "fixes", "check", "apply"]
    for field in required_fields:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing or empty field: {field}")

    # Validate id format
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", data["id"]):
        raise ValueError("bad id: use lowercase letters, digits and dashes")

    # Check boolean flags
    boolean_flags = ["sudo", "network", "reboot", "pacman", "default_on"]
    for flag in boolean_flags:
        if flag in data and not isinstance(data[flag], bool):
            raise ValueError(f"flag {flag} must be true or false")

    # Build result with all fields
    result = {
        "id": data["id"],
        "title": data["title"],
        "fixes": data["fixes"],
        "check": data["check"],
        "apply": data["apply"],
        "sudo": data.get("sudo", False),
        "network": data.get("network", False),
        "reboot": data.get("reboot", False),
        "pacman": data.get("pacman", False),
        "default_on": data.get("default_on", False),
    }

    return result


def load_dir(path: str) -> tuple[list[dict[str, object]], list[str]]:
    """Load all TOML files from a directory.

    Args:
        path: Directory path to load from

    Returns:
        Tuple of (recipes, errors) where recipes is a list of parsed dicts,
        and errors is a list of error messages for each failed file
    """
    recipes = []
    errors = []
    seen_ids = set()

    try:
        files = sorted(os.listdir(path))
    except OSError:
        return ([], [])

    for filename in files:
        if not filename.endswith(".toml"):
            continue

        file_path = os.path.join(path, filename)
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            errors.append(f"{filename}: unable to read file")
            continue

        try:
            recipe = parse_local(content)
        except ValueError as e:
            errors.append(f"{filename}: {e}")
            continue

        if recipe["id"] in seen_ids:
            errors.append(f"{filename}: duplicate id: {recipe['id']}")
            continue

        recipes.append(recipe)
        seen_ids.add(recipe["id"])

    return (recipes, errors)