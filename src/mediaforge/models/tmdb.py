"""The parts of TMDB's responses MediaForge uses. Unknown fields are ignored."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class TmdbResult(BaseModel):
    """A movie or a TV show; TV's name/first_air_date map onto title/date."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str
    original_title: str | None = None
    date: str | None = None
    overview: str = ""
    poster_path: str | None = None
    popularity: float = 0

    @model_validator(mode="before")
    @classmethod
    def _map_tv_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d: dict[str, Any] = dict(data)  # pyright: ignore[reportUnknownArgumentType]
        d.setdefault("title", d.get("name"))
        d.setdefault("original_title", d.get("original_name"))
        d["date"] = d.get("release_date") or d.get("first_air_date") or d.get("date")
        if d.get("overview") is None:
            d["overview"] = ""
        return d

    @property
    def year(self) -> int | None:
        if self.date and len(self.date) >= 4 and self.date[:4].isdigit():
            return int(self.date[:4])
        return None


class TmdbEpisode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    episode_number: int
    name: str = ""


class TmdbSeason(BaseModel):
    model_config = ConfigDict(extra="ignore")

    season_number: int
    poster_path: str | None = None
    episodes: list[TmdbEpisode] = []
