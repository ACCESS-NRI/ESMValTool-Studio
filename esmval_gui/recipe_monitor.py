"""Account-gated recipe overview; identity never comes from a browser claim."""

from __future__ import annotations

import os
from pathlib import Path

from . import recipes, remote


def identity(username: str = "") -> str:
    if remote.execution_mode() == "local":
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    # Always contact the real Gadi endpoint, never a browser-supplied host or
    # setup command that could print an allowlisted username on another host.
    target = remote.ssh_target("gadi.nci.org.au", username)
    return remote.execute(target, "/usr/bin/id -un", timeout=15, transport="ssh").strip()


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
