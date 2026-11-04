from dataclasses import dataclass
from typing import Callable, Sequence

@dataclass(frozen=True)
class Recipe:
    id: str
    title: str
    fixes: str                      # one line
    check: Callable[[], str]        # read-only: "ok" | "needs" | "info" | "na"
    apply: Callable[[], None]       # idempotent
    sudo: bool = False
    network: bool = False
    reboot: bool = False
    pacman: bool = False
    default_on: bool = False
    replaces: tuple = ()            # files or folders apply() overwrites; the runner backs them up first
    creates: "Callable[[], Sequence[str]] | None" = None  # files apply() will add (asked before apply)
    undo: str = ""                  # one line for the rollback record: what undoes what files cannot

KEYRING_ID = "keyring"

def order_tasks(recipes: Sequence[Recipe]) -> list:
    """Order recipes with keyring first, then non-reboot, then reboot."""
    keyring_recipes = []
    non_reboot_recipes = []
    reboot_recipes = []

    for recipe in recipes:
        if recipe.id == KEYRING_ID:
            keyring_recipes.append(recipe)
        elif recipe.reboot:
            reboot_recipes.append(recipe)
        else:
            non_reboot_recipes.append(recipe)

    result = []
    result.extend(keyring_recipes)
    result.extend(non_reboot_recipes)
    result.extend(reboot_recipes)

    return result

def with_keyring(selected: Sequence[Recipe], available: Sequence[Recipe]) -> list:
    """Add keyring to selected recipes if needed."""
    # Check if any selected recipe has pacman flag and keyring is not already selected
    has_pacman = any(recipe.pacman for recipe in selected)
    has_keyring = any(recipe.id == KEYRING_ID for recipe in selected)

    if has_pacman and not has_keyring:
        # Find keyring in available recipes
        keyring_recipe = next((recipe for recipe in available if recipe.id == KEYRING_ID), None)
        if keyring_recipe:
            # Add keyring at the beginning
            return [keyring_recipe] + list(selected)

    # Return a copy of selected recipes
    return list(selected)

def plan_text(ordered: Sequence[Recipe]) -> str:
    """Generate plan text for ordered recipes."""
    if not ordered:
        return "Plan: nothing selected"

    count = len(ordered)
    task_text = "task" if count == 1 else "tasks"
    lines = [f"Plan: {count} {task_text}"]

    # Flag order for display
    flag_order = ["sudo", "network", "pacman", "reboot"]

    for i, recipe in enumerate(ordered, 1):
        lines.append(f"{i}. {recipe.id}: {recipe.title} - {recipe.fixes}")

        # Collect flags that are True
        flags = [flag for flag in flag_order if getattr(recipe, flag)]
        if flags:
            lines.append(f"   needs: {', '.join(flags)}")

    # Add reboot note if any recipe needs reboot
    if any(recipe.reboot for recipe in ordered):
        lines.append("A reboot is needed after this run. The app never reboots by itself.")

    return "\n".join(lines)
