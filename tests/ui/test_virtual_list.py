import time
from collections.abc import AsyncIterator

import customtkinter as ctk
import pytest_asyncio

from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.theme import STUDIO_DARK
from mediaforge.ui.widgets.virtual_list import Row, VirtualList

from helpers.tk import pump


def many_rows(n: int) -> list[Row]:
    rows: list[Row] = []
    for i in range(n):
        if i % 50 == 0:
            rows.append(Row(key=-(i + 1), kind="group", title=f"Group {i}"))
        rows.append(
            Row(
                key=i,
                kind="item",
                title=f"Show - S01E{i:02d} - Name.mkv",
                subtitle=f"raw{i}.mkv",
                badge="Show",
                status="ready",
                highlight=(7, 13),
            )
        )
    return rows


@pytest_asyncio.fixture
async def vlist(tk_root: DnDCTk) -> AsyncIterator[VirtualList]:
    holder = ctk.CTkFrame(tk_root, width=700, height=500)
    holder.place(x=0, y=0)
    holder.pack_propagate(False)
    v = VirtualList(holder, STUDIO_DARK)
    v.pack(fill="both", expand=True)
    await pump(tk_root, 0.05)
    try:
        yield v
    finally:
        holder.destroy()


async def test_virtual_list_draws_only_visible(tk_root: DnDCTk, vlist: VirtualList) -> None:
    rows = many_rows(1000)
    start = time.perf_counter()
    vlist.set_rows(rows)
    assert time.perf_counter() - start < 0.2
    await pump(tk_root, 0.05)
    visible = vlist.visible_keys()
    assert 0 < len(visible) < 40
    assert visible[0] == rows[0].key


async def test_virtual_list_scroll_changes_visible(tk_root: DnDCTk, vlist: VirtualList) -> None:
    vlist.set_rows(many_rows(1000))
    await pump(tk_root, 0.05)
    before = vlist.visible_keys()
    vlist.scroll_to_fraction(0.5)
    await pump(tk_root, 0.05)
    after = vlist.visible_keys()
    assert after and after != before
    assert any(400 <= k <= 600 for k in after)


async def test_select_notifies_and_survives_new_rows(tk_root: DnDCTk, vlist: VirtualList) -> None:
    picked: list[int | None] = []
    vlist.on_select = picked.append
    vlist.set_rows(many_rows(10))
    vlist.select(3)
    assert picked == [3] and vlist.selected == 3
    vlist.set_rows(many_rows(10))
    assert vlist.selected == 3
    vlist.set_rows(many_rows(2))
    assert vlist.selected is None


async def test_item_keys_skip_groups(vlist: VirtualList) -> None:
    vlist.set_rows(many_rows(3))
    assert vlist.item_keys() == [0, 1, 2]


async def test_tick_boxes_toggle_on_click_and_respect_disabled(
    tk_root: DnDCTk, vlist: VirtualList
) -> None:
    toggled: list[tuple[int, bool]] = []
    vlist.on_toggle = lambda key, value: toggled.append((key, value))
    vlist.set_rows(
        [
            Row(key=1, kind="item", title="a", checked=True),
            Row(key=2, kind="item", title="b", checked=True, check_disabled=True),
        ]
    )
    await pump(tk_root, 0.05)
    x, y_first, y_second = vlist.check_point(1), vlist.row_middle(1), vlist.row_middle(2)
    vlist.canvas.event_generate("<Button-1>", x=x, y=y_first)
    vlist.canvas.event_generate("<Button-1>", x=x, y=y_second)
    await pump(tk_root, 0.05)
    assert toggled == [(1, False)]
    assert [r.checked for r in vlist.rows] == [False, True]
