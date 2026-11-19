"""ARE-only run access; identity never comes from a browser or SSH claim."""

from __future__ import annotations

import os
from pathlib import Path

from . import recipes, remote


def is_are() -> bool:
    # Set by the authenticated ARE launcher, separately from execution mode.
    # Direct PBS execution or an SSH connection alone is not an ARE deployment.
    return (os.name == "posix"
            and os.environ.get("ESMVAL_GUI_DEPLOYMENT") == "are"
            and os.environ.get("ESMVAL_GUI_REQUIRE_XSRF") == "1"
            and remote.execution_mode() == "local")


def identity(username: str = "") -> str:
    if not is_are():
        raise RuntimeError("Runs and Recipe monitor are available only in an ARE Studio session")
    import pwd
    # ARE launches one password-protected server under the session owner's UID.
    # Ignore the compatibility username field and environment identity strings.
    return pwd.getpwuid(os.geteuid()).pw_name


def allowed_users() -> set[str]:
    return {name.strip() for name in os.environ.get("ESMVAL_GUI_RECIPE_MONITOR_USERS", "rb5533").split(",")
            if name.strip()}


def overview(state_dir: Path, username: str) -> dict:
    root = recipes.available_recipe_root()
    latest = {}
    unlinked = 0
    mode = remote.execution_mode()
    hosts = {"gadi.nci.org.au", f"{username}@gadi.nci.org.au"}
    for path in state_dir.glob("*.json"):
        try:
            record = remote.read_job(state_dir, path.stem)
        except (ValueError, FileNotFoundError):
            continue
        if not isinstance(record, dict) or record.get("transport", "ssh") != mode:
            continue
        if mode == "ssh" and record.get("host") not in hosts:
            continue
        recipe_path = record.get("recipe_path")
        if not recipe_path:
            unlinked += 1
            continue
        previous = latest.get(recipe_path)
        if previous and previous.get("submitted_at", "") >= record.get("submitted_at", ""):
            continue
        latest[recipe_path] = {**record, **record.get("last_status", {})}
        if record.get("cancelled"):
            latest[recipe_path]["state"] = "cancelled"
    return {"user": username, "unlinked_runs": unlinked, "library_available": root is not None,
            "recipes": [{**recipe, "last_run": latest.get(recipe["path"])}
                        for recipe in recipes.list_recipes(root)] if root else []}
