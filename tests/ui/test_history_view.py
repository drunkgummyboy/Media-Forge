import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import pytest_asyncio

from mediaforge.app import Services
from mediaforge.core.planner import build_plan
from mediaforge.models.history import OpKind, RunSource
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia
from mediaforge.models.plan import PlanInput
from mediaforge.models.preset import PresetOptions
from mediaforge.ui.dnd_root import DnDCTk
from mediaforge.ui.history_view import HistoryView, when_label
from mediaforge.ui.theme import DARK

from helpers.tk import pump


def film(title: str) -> ResolvedMedia:
    parsed = ParsedMedia(source_name="m.mkv", kind=MediaKind.MOVIE, title=title)
    return ResolvedMedia(
        parsed=parsed, title=title, year=2024, episode_title=None, match_state=MatchState.CONFIDENT
    )


async def forge(services: Services, root: Path, names: dict[str, str], preset: str) -> int:
    inputs: list[PlanInput] = []
    for i, (filename, title) in enumerate(names.items(), start=1):
        (root / filename).write_bytes(b"video")
        inputs.append(
            PlanInput(
                item_id=i, source=root / filename, root=root, dropped_folder=root, media=film(title)
            )
        )
    plan = build_plan(inputs, "{ny}", PresetOptions())
    result = await services.executor.run(
        plan,
        source=RunSource.APP,
        preset_name=preset,
        on_progress=lambda e: None,
        cancel=asyncio.Event(),
    )
    assert result.run_id is not None
    return result.run_id


@pytest_asyncio.fixture
async def view(tk_root: DnDCTk, services: Services) -> AsyncIterator[HistoryView]:
    v = HistoryView(tk_root, services, DARK)
    v.pack(fill="both", expand=True)
    asked: list[str] = []
    told: list[str] = []

    async def yes(title: str, message: str) -> bool:
        asked.append(message)
        return True

    async def note(title: str, message: str) -> None:
        told.append(message)

    v.confirm = yes
    v.inform = note
    v.asked, v.told = asked, told  # type: ignore[attr-defined]
    await pump(tk_root, 0.05)
    try:
        yield v
    finally:
        v.destroy()


async def test_lists_runs_newest_first(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    first = await forge(services, tmp_path, {"a.mkv": "Alien"}, "Movie (Flat)")
    second = await forge(services, tmp_path, {"b.mkv": "Heat", "c.mkv": "Ran"}, "Auto")
    await view.reload()
    await pump(tk_root, 0.05)
    assert [card.run.id for card in view.cards] == [second, first]
    assert "2 files" in view.cards[0].summary_text()
    assert "Auto" in view.cards[0].summary_text()


async def test_empty_history_message(tk_root: DnDCTk, view: HistoryView) -> None:
    await view.reload()
    assert view.cards == []
    assert view.empty_label.winfo_manager() != ""


async def test_undo_run_button_reverts_files(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    run_id = await forge(services, tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"}, "Auto")
    await view.reload()
    await view.undo_run(run_id)
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".mkv") == ["a.mkv", "b.mkv"]
    assert view.asked and "2" in view.asked[0]  # type: ignore[attr-defined]
    assert view.cards[0].run.status == "undone"


async def test_undo_selected_only(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    run_id = await forge(services, tmp_path, {"a.mkv": "Alien", "b.mkv": "Heat"}, "Auto")
    await view.reload()
    card = view.cards[0]
    await card.expand()
    renames = [o for o in card.operations if o.kind is OpKind.RENAME]
    first = next(o for o in renames if o.src == str(tmp_path / "a.mkv"))
    for op_id in list(card.checks):
        card.tick(op_id, op_id == first.id)
    await view.undo_selected(run_id)
    assert (tmp_path / "a.mkv").exists()
    assert (tmp_path / "Heat (2024).mkv").exists()
    assert view.cards[0].run.status == "partly-undone"


async def test_undone_run_dimmed_with_tag(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    run_id = await forge(services, tmp_path, {"a.mkv": "Alien"}, "Auto")
    await view.reload()
    await view.undo_run(run_id)
    card = view.cards[0]
    assert card.tag_text().startswith("Undone")
    assert card.undo_button.cget("state") == "disabled"


async def test_skipped_items_are_reported(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    run_id = await forge(services, tmp_path, {"a.mkv": "Alien"}, "Auto")
    (tmp_path / "Alien (2024).mkv").rename(tmp_path / "moved.mkv")
    await view.reload()
    await view.undo_run(run_id)
    assert view.told and "moved or renamed again" in view.told[-1]  # type: ignore[attr-defined]


def test_when_labels() -> None:
    now = datetime(2026, 10, 26, 15, 0)
    assert when_label(datetime(2026, 10, 26, 14, 2), now) == "Today 14:02"
    assert when_label(datetime(2026, 10, 25, 21, 40), now) == "Yesterday 21:40"
    assert when_label(datetime(2026, 10, 19, 22, 23), now) == "19 Oct 22:23"
    assert when_label(datetime(2025, 3, 4, 8, 5), now) == "4 Mar 2025 08:05"


async def test_undo_twice_at_once_asks_once(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    import asyncio

    run_id = await forge(services, tmp_path, {"a.mkv": "Alien"}, "Auto")
    await view.reload()
    await asyncio.gather(view.undo_run(run_id), view.undo_run(run_id))
    assert len(view.asked) == 1  # type: ignore[attr-defined]


async def test_show_files_for_a_big_run_opens_quickly(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    # Review C1: one tick-box widget per file froze the window on big runs.
    import time

    names = {f"m{n}.mkv": f"Film {n}" for n in range(300)}
    await forge(services, tmp_path, names, "Auto")
    await view.reload()
    card = view.cards[0]
    start = time.perf_counter()
    await card.expand()
    tk_root.update()
    assert time.perf_counter() - start < 0.3
    assert len(card.selected_ids()) == 300


async def test_reload_gives_the_window_turns_while_building_cards(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path, monkeypatch
) -> None:
    import time

    await forge(services, tmp_path, {"a.mkv": "Alien"}, "Auto")
    [run] = await services.history.list_runs()
    runs = [run.model_copy(update={"id": run.id}) for _ in range(50)]

    async def fifty_runs() -> list:
        return runs

    monkeypatch.setattr(services.history, "list_runs", fifty_runs)
    gaps: list[float] = []
    done = False

    async def ticker() -> None:
        while not done:
            start = time.perf_counter()
            await asyncio.sleep(0)
            gaps.append(time.perf_counter() - start)

    tick = asyncio.ensure_future(ticker())
    await asyncio.sleep(0)
    await view.reload()
    done = True
    await tick
    assert len(view.cards) == 50
    assert max(gaps) < 0.2


def test_day_label_texts() -> None:
    from mediaforge.ui.history_view import day_label

    today = datetime(2026, 10, 31, 15, 0)
    assert day_label(datetime(2026, 10, 31, 9, 5), today) == "Today"
    assert day_label(datetime(2026, 10, 30, 23, 59), today) == "Yesterday"
    assert day_label(datetime(2026, 10, 29, 8, 0), today) == "Thu 29 Oct"
    assert day_label(datetime(2025, 3, 4, 8, 5), today) == "Tue 4 Mar 2025"


async def test_runs_are_grouped_under_day_headings(
    tk_root: DnDCTk, view: HistoryView, services: Services, tmp_path: Path
) -> None:
    from datetime import timedelta

    older = await forge(services, tmp_path, {"a.mkv": "Alien"}, "Auto")
    await forge(services, tmp_path, {"b.mkv": "Heat"}, "Auto")
    await forge(services, tmp_path, {"c.mkv": "Ran"}, "Auto")
    yesterday = (datetime.now() - timedelta(days=1)).timestamp()
    await services.db.conn.execute(
        "UPDATE runs SET started_at = ? WHERE id = ?", (yesterday, older)
    )
    await services.db.conn.commit()
    await view.reload()
    await pump(tk_root, 0.05)
    assert view.day_labels() == ["Today", "Yesterday"]
    assert [len(cards) for cards in view.cards_by_day().values()] == [2, 1]


async def test_automatic_runs_are_labelled(
    tk_root: DnDCTk, view: HistoryView, services: Services
) -> None:
    history = services.history
    run_id = await history.start_run(RunSource.AUTOMATIC, "Series (Folder)")
    op = await history.record_pending(
        run_id, OpKind.RENAME, Path(r"C:\d\a.mkv"), Path(r"C:\s\b.mkv")
    )
    await history.mark_done(op)
    assert await history.finish_run(run_id)
    await view.reload()
    await pump(tk_root, 0.05)
    assert view.cards[0].tag_text() == "automatic"
