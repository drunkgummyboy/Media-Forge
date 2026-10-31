import gc

import pytest

from mediaforge.ui import gc_guard
from mediaforge.ui.dnd_root import DnDCTk

from helpers.tk import pump


async def test_collector_runs_on_tk_timer_not_automatically(
    tk_root: DnDCTk, monkeypatch: pytest.MonkeyPatch
) -> None:
    generations: list[int] = []
    monkeypatch.setattr(gc_guard, "MIDDLE_EVERY", 3)
    gc_guard.start(tk_root, tick_ms=10)
    monkeypatch.setattr(gc_guard.gc, "collect", lambda gen=2: generations.append(gen) or 0)
    assert not gc.isenabled()
    await pump(tk_root, 0.2)
    assert generations.count(0) >= 3
    assert 1 in generations
