import gc
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from mediaforge.app import Services
from mediaforge.ui.detail_panel import DetailPanel
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.theme import STUDIO_DARK

from helpers.tk import pump


async def test_clearing_poster_never_leaves_a_dead_image(
    tk_root: DnDCTk, services: Services
) -> None:
    # CTkLabel ignores image=None, so the inner Tk label kept pointing at a poster that was
    # then freed; the next update raised 'image "pyimage…" doesn't exist'.
    panel = DetailPanel(tk_root, services, STUDIO_DARK, lambda item: None)
    panel.pack()
    image = ctk.CTkImage(Image.new("RGB", (20, 30), "red"), size=(20, 30))
    panel._set_poster(image)  # pyright: ignore[reportPrivateUsage]
    await pump(tk_root, 0.05)
    del image
    panel.show(None)
    gc.collect()
    panel.show(None)
    await pump(tk_root, 0.05)
    panel.destroy()


async def test_detail_panel_shows_names_and_episode(
    tk_root: DnDCTk, services: Services, tmp_path: Path
) -> None:
    from mediaforge.core.matcher import Resolution
    from mediaforge.core.parser import parse_filename
    from mediaforge.core.queue import QueueItem
    from mediaforge.models.media import MatchState, ResolvedMedia

    parsed = parse_filename("VisionQuest.S01E02.Theseus.1080p.mkv")
    item = QueueItem(1, tmp_path / "VisionQuest.S01E02.Theseus.1080p.mkv", None, parsed)
    item.resolution = Resolution(
        media=ResolvedMedia(
            parsed=parsed,
            title="VisionQuest",
            year=2026,
            episode_title="Theseus",
            match_state=MatchState.CONFIDENT,
        )
    )
    panel = DetailPanel(tk_root, services, STUDIO_DARK, lambda i: None)
    panel.pack()
    target = tmp_path / "VisionQuest (2026)" / "VisionQuest (2026) - S01E02 - Theseus.mkv"
    panel.show(item, target)
    await pump(tk_root, 0.05)
    assert panel.title.cget("text") == "VisionQuest (2026)"
    assert panel.episode.cget("text") == "S01E02 · Theseus"
    assert panel.match.cget("text") == "Matched on TMDB"
    assert panel.destination.cget("text") == str(target)
    assert "VisionQuest.S01E02.Theseus.1080p.mkv" in panel.original.cget("text")
    assert panel.trailer.winfo_manager() == "pack"
    panel.destroy()
