"""A stand-in for api.updates.UpdateService in UI tests."""

from __future__ import annotations

from pathlib import Path

from mediaforge.api.updates import UpdateError
from mediaforge.models.update import UpdateInfo


class FakeUpdates:
    def __init__(
        self,
        latest: str = "1.2.0",
        *,
        current: str = "1.1.0",
        installable: bool = True,
        fail: bool = False,
    ) -> None:
        self.latest = latest
        self.current = current
        self.installable = installable
        self.fail = fail
        self.install_dir = Path("C:/Fake/MediaForge")
        self.staged: list[str] = []
        self.installed: list[str] = []
        self.checks = 0

    async def check(self) -> UpdateInfo:
        self.checks += 1
        if self.fail:
            raise UpdateError("Could not reach GitHub.")
        return UpdateInfo(
            current=self.current,
            latest=self.latest,
            available=self.latest != self.current,
            installable=self.installable,
        )

    async def stage(self) -> str:
        self.staged.append(self.latest)
        return self.latest

    async def install(self) -> str:
        self.installed.append(self.latest)
        return self.latest
