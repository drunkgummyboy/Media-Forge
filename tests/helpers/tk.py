"""Drive Tk and asyncio together in tests, like the real main loop does."""

import asyncio
import time
from typing import Any


async def pump(widget: Any, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        widget.update()
        await asyncio.sleep(0.01)


async def pump_until(widget: Any, condition: Any, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not reached")
        widget.update()
        await asyncio.sleep(0.01)
