"""Keep Python's cycle collector on the Tk thread.

If the collector runs in a worker thread (asyncio.to_thread), it can free Tk images,
fonts and variables there. Their cleanup then calls into Tk from the wrong thread, and each
call waits about a second for a Tk mainloop that never answers. So automatic collection is
switched off and the Tk thread collects on a timer instead: the young generation often
(cheap, like Python does by default), older generations rarely. Objects alive at startup
are frozen so full collections stay quick.
"""

from __future__ import annotations

import gc
from typing import Any

TICK_MS = 100
MIDDLE_EVERY = 20  # ticks: about every 2 s
FULL_EVERY = 300  # ticks: about every 30 s


def start(root: Any, tick_ms: int = TICK_MS) -> None:
    gc.collect()
    gc.freeze()
    gc.disable()
    ticks = 0

    def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks % FULL_EVERY == 0:
            gc.collect(2)
        elif ticks % MIDDLE_EVERY == 0:
            gc.collect(1)
        else:
            gc.collect(0)
        if root.winfo_exists():
            root.after(tick_ms, tick)

    root.after(tick_ms, tick)


def stop() -> None:
    gc.unfreeze()
    gc.collect()
    gc.enable()
