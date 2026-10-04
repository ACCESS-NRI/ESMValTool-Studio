"""A per-session access token for the loopback GUI API."""

from __future__ import annotations

import os
import secrets


ACCESS_TOKEN = os.environ.get("ESMVAL_GUI_ACCESS_TOKEN") or secrets.token_urlsafe(32)
if len(ACCESS_TOKEN) < 32:
    raise ValueError("ESMVAL_GUI_ACCESS_TOKEN must contain at least 32 characters")


def authorized(presented: str | None) -> bool:
    return bool(presented) and secrets.compare_digest(presented, ACCESS_TOKEN)
