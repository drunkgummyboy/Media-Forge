"""Where MediaForge keeps its database, cache, logs and downloaded tools."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "MediaForge"


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    local_dir: Path

    @property
    def db_file(self) -> Path:
        return self.data_dir / "mediaforge.db"

    @property
    def logs_dir(self) -> Path:
        return self.local_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.local_dir / "cache"

    @property
    def images_dir(self) -> Path:
        return self.cache_dir / "images"

    @property
    def tools_dir(self) -> Path:
        return self.local_dir / "tools"

    @property
    def instance_key_file(self) -> Path:
        return self.local_dir / "instance.key"

    def ensure(self) -> None:
        for d in (self.data_dir, self.logs_dir, self.images_dir, self.tools_dir):
            d.mkdir(parents=True, exist_ok=True)


def app_paths(env: Mapping[str, str] = os.environ) -> AppPaths:
    """MEDIAFORGE_HOME (development, tests) overrides the per-user Windows folders."""
    home = env.get("MEDIAFORGE_HOME")
    if home:
        return AppPaths(Path(home), Path(home))
    roaming = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    local = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return AppPaths(roaming / APP_DIR_NAME, local / APP_DIR_NAME)
