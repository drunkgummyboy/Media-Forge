import time
from collections.abc import AsyncIterator

import customtkinter as ctk
import pytest_asyncio

from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.theme import DARK
from mediaforge.ui.widgets.virtual_list import Row, VirtualList

from helpers.colours import contrast
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
    v = VirtualList(holder, DARK)
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


def band_rows(n_bands: int, per_band: int = 2) -> list[Row]:
    rows: list[Row] = [Row(key=-1, kind="section", title="Needs you", subtitle=str(n_bands))]
    key = 0
    for b in range(n_bands):
        rows.append(
            Row(
                key=-(b + 10),
                kind="band",
                title=f"Show {b}",
                subtitle=f"Season 1 · {per_band} episodes → Series › Show {b} › Season 01",
                badge=f"Show {b}",
                art=f"/p{b}.jpg",
                backdrop=f"/b{b}.jpg",
                actions=(("choose", "Choose…"), ("rename", "Rename")),
            )
        )
        for e in range(per_band):
            rows.append(
                Row(
                    key=key,
                    kind="item",
                    title=f"Episode {e}",
                    code=f"S01E{e + 1:02d}",
                    faint=f"show.{b}.s01e{e + 1:02d}.1080p.web.mkv",
                    status="ready",
                )
            )
            key += 1
    return rows


async def test_band_actions_are_clickable(tk_root: DnDCTk, vlist: VirtualList) -> None:
    pressed: list[tuple[int, str]] = []
    vlist.on_action = lambda key, action: pressed.append((key, action))
    vlist.set_rows(band_rows(2))
    await pump(tk_root, 0.05)
    for action in ("rename", "choose"):
        x, y = vlist.action_point(-10, action)
        vlist.canvas.event_generate("<Button-1>", x=x, y=y)
    await pump(tk_root, 0.05)
    assert pressed == [(-10, "rename"), (-10, "choose")]


async def test_band_draws_without_images(tk_root: DnDCTk, vlist: VirtualList) -> None:
    # Review focus 2: no TMDB key, offline or no picture: a tinted band with initials.
    vlist.images = lambda row, width, height: (None, None)
    vlist.set_rows(band_rows(1))
    await pump(tk_root, 0.05)
    assert vlist.canvas.find_withtag("band-fill")
    initials = [
        vlist.canvas.itemcget(i, "text") for i in vlist.canvas.find_withtag("band-initials")
    ]
    assert initials == ["S0"]


async def test_band_uses_the_pictures_it_gets(tk_root: DnDCTk, vlist: VirtualList) -> None:
    from PIL import Image, ImageTk

    poster = ImageTk.PhotoImage(Image.new("RGB", (30, 45), "red"))
    backdrop = ImageTk.PhotoImage(Image.new("RGB", (600, 64), "blue"))
    vlist.images = lambda row, width, height: (poster, backdrop)
    vlist.set_rows(band_rows(1))
    await pump(tk_root, 0.05)
    shown = {vlist.canvas.itemcget(i, "image") for i in vlist.canvas.find_withtag("band-image")}
    assert shown == {str(poster), str(backdrop)}


async def test_only_visible_bands_ask_for_images(tk_root: DnDCTk, vlist: VirtualList) -> None:
    # Review focus 3: a thousand bands, pictures only for what is on screen.
    asked: list[str] = []

    def images(row: Row, width: int, height: int) -> tuple[None, None]:
        asked.append(row.title)
        return (None, None)

    vlist.images = images
    start = time.perf_counter()
    vlist.set_rows(band_rows(1000))
    assert time.perf_counter() - start < 0.3
    await pump(tk_root, 0.05)
    assert 0 < len(set(asked)) < 15


async def test_episode_lines_show_code_name_and_old_name(
    tk_root: DnDCTk, vlist: VirtualList
) -> None:
    vlist.set_rows(band_rows(1))
    await pump(tk_root, 0.05)
    texts = [
        vlist.canvas.itemcget(i, "text")
        for i in vlist.canvas.find_all()
        if vlist.canvas.type(i) == "text"
    ]
    assert "S01E01" in texts and "Episode 0" in texts
    assert any(t.startswith("show.0.s01e01") for t in texts)
    assert "NEEDS YOU   ·   1" in texts


async def test_coloured_codes_are_readable_in_dark(tk_root: DnDCTk, vlist: VirtualList) -> None:
    def fills() -> dict[str, str]:
        canvas = vlist.canvas
        return {
            canvas.itemcget(i, "text"): canvas.itemcget(i, "fill")
            for i in canvas.find_all()
            if canvas.type(i) == "text"
        }

    def readable(colour: str) -> bool:
        return min(contrast(colour, DARK.main), contrast(colour, DARK.surface)) >= 4

    vlist.set_rows(band_rows(1))  # an episode line's code
    await pump(tk_root, 0.05)
    assert readable(fills()["S01E01"])
    vlist.set_rows(many_rows(1))  # the code inside a new name (the confirm window)
    await pump(tk_root, 0.05)
    assert readable(fills()["S01E00"])
