import asyncio
from pathlib import Path

from mediaforge.app import Services
from mediaforge.core.parser import parse_filename
from mediaforge.core.queue import QueueItem
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.first_start import FirstStart
from mediaforge.ui.log_strip import LogStrip
from mediaforge.ui.match_picker import MatchPicker
from mediaforge.ui.theme import DARK
from mediaforge.ui.widgets.dialogs import ask_choice

from helpers.tk import pump


async def test_popups_can_close_before_they_are_shown(tk_root: DnDCTk, services: Services) -> None:
    # Regression: pop-ups marked `transient` with a size, destroyed before being shown,
    # left Tk in a state where building the next widget crashed the whole process.
    for _ in range(3):
        FirstStart(tk_root, services, DARK).destroy()
        item = QueueItem(1, Path("x.mkv"), None, parse_filename("Dune.2021.mkv"))
        MatchPicker(tk_root, item, services, DARK).destroy()
        question = asyncio.get_running_loop().create_task(
            ask_choice(tk_root, DARK, "Title", "Message", ["OK"])
        )
        await asyncio.sleep(0)
        for child in tk_root.winfo_children():
            if child.winfo_class() == "Toplevel":
                child.destroy()
        question.cancel()
        strip = LogStrip(tk_root, services.log_bridge, DARK)
        strip.pack()
        await pump(tk_root, 0.05)
        strip.destroy()
