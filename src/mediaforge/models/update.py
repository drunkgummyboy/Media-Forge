"""What an update check found."""

from __future__ import annotations

from pydantic import BaseModel


class UpdateInfo(BaseModel):
    current: str  # version running now
    latest: str  # version on GitHub
    available: bool  # latest is newer than current
    installable: bool  # False in a development copy, which is never overwritten
