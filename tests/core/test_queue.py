import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mediaforge.core import queue as queue_module
from mediaforge.core.matcher import Resolution
from mediaforge.core.queue import ItemStatus, Queue, QueueEvent, scan_paths
from mediaforge.models.media import MatchState, MediaKind, ParsedMedia, ResolvedMedia, TypeHint
from mediaforge.models.tmdb import TmdbResult


class FakeResolver:
    def __init__(self, state: Callable[[ParsedMedia], MatchState] | None = None) -> None:
        self.state = state or (lambda p: MatchState.CONFIDENT)
        self.in_flight = 0
        self.max_in_flight = 0
        self.resolved: list[ParsedMedia] = []

    async def resolve(self, parsed: ParsedMedia) -> Resolution:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        self.resolved.append(parsed)
        return Resolution(media=ResolvedMedia.offline(parsed, self.state(parsed)))

    async def apply_choice(
        self, parsed: ParsedMedia, chosen: TmdbResult | None, *, remember: bool = True
    ) -> ResolvedMedia:
        if chosen is None:
            return ResolvedMedia.offline(parsed, MatchState.SKIPPED)
        return ResolvedMedia(
            parsed=parsed,
            title=chosen.title,
            year=chosen.year,
            episode_title=None,
            match_state=MatchState.REMEMBERED,
            tmdb_id=chosen.id,
        )


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"v")
    return path


def make_queue(resolver: FakeResolver | None = None) -> tuple[Queue, list[QueueEvent]]:
    events: list[QueueEvent] = []
    return Queue(resolver or FakeResolver(), events.append), events


async def test_add_folder_recursive_and_flat(tmp_path: Path) -> None:
    touch(tmp_path / "a.mkv")
    touch(tmp_path / "sub" / "b.mp4")
    q, _ = make_queue()
    assert await q.add_paths([tmp_path], recursive=False) == 1
    q.clear()
    assert await q.add_paths([tmp_path], recursive=True) == 2
    assert {i.path.name for i in q.items} == {"a.mkv", "b.mp4"}
    assert all(i.dropped_folder == tmp_path and i.root == tmp_path for i in q.items)


async def test_single_file_root_is_its_folder(tmp_path: Path) -> None:
    f = touch(tmp_path / "dl" / "a.mkv")
    q, _ = make_queue()
    await q.add_paths([f], recursive=True)
    [item] = q.items
    assert item.dropped_folder is None and item.root == tmp_path / "dl"


async def test_non_videos_ignored(tmp_path: Path) -> None:
    touch(tmp_path / "a.srt")
    touch(tmp_path / "b.nfo")
    q, _ = make_queue()
    assert await q.add_paths([tmp_path, tmp_path / "a.srt"], recursive=True) == 0


async def test_same_file_added_twice_appears_once_and_folder_root_wins(tmp_path: Path) -> None:
    # Review focus 5
    f = touch(tmp_path / "show" / "a.mkv")
    q, events = make_queue()
    await q.add_paths([f], recursive=True)
    await q.add_paths([f], recursive=True)
    assert len(q.items) == 1
    assert await q.add_paths([tmp_path / "show"], recursive=True) == 0
    assert len(q.items) == 1
    assert q.items[0].dropped_folder == tmp_path / "show"
    assert any(e.kind == "updated" for e in events)


async def test_lookups_set_ready_or_needs_choice(tmp_path: Path) -> None:
    touch(tmp_path / "Dune.2021.mkv")
    touch(tmp_path / "The.Office.S02E01.mkv")
    resolver = FakeResolver(
        lambda p: MatchState.UNCERTAIN if p.kind is MediaKind.EPISODE else MatchState.CONFIDENT
    )
    q, _ = make_queue(resolver)
    await q.add_paths([tmp_path], recursive=True)
    assert all(i.status is ItemStatus.LOOKING_UP for i in q.items)
    await q.wait_idle()
    status = {i.path.name: i.status for i in q.items}
    assert status == {
        "Dune.2021.mkv": ItemStatus.READY,
        "The.Office.S02E01.mkv": ItemStatus.NEEDS_CHOICE,
    }
    assert [i.path.name for i in q.uncertain()] == ["The.Office.S02E01.mkv"]


async def test_media_before_lookup_is_offline_parse(tmp_path: Path) -> None:
    touch(tmp_path / "Dune.2021.mkv")
    q, _ = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    assert q.items[0].media.title == "Dune" and q.items[0].media.year == 2021


async def test_choose_match_applies_to_same_key(tmp_path: Path) -> None:
    touch(tmp_path / "The.Office.S02E01.mkv")
    touch(tmp_path / "The.Office.S02E02.mkv")
    q, _ = make_queue(FakeResolver(lambda p: MatchState.UNCERTAIN))
    await q.add_paths([tmp_path], recursive=True)
    await q.wait_idle()
    chosen = TmdbResult(id=2316, title="The Office", date="2005-03-24")
    await q.choose_match(q.items[0].id, chosen)
    assert all(i.status is ItemStatus.READY for i in q.items)
    assert all(i.media.tmdb_id == 2316 for i in q.items)


async def test_set_hint_reparses_all(tmp_path: Path) -> None:
    touch(tmp_path / "Show.S01E02.mkv")
    q, _ = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    await q.wait_idle()
    assert q.items[0].parsed.kind is MediaKind.EPISODE
    await q.set_hint(TypeHint.MOVIE)
    await q.wait_idle()
    assert q.hint is TypeHint.MOVIE
    assert q.items[0].parsed.kind is MediaKind.MOVIE
    assert q.items[0].status is ItemStatus.READY


async def test_events_emitted(tmp_path: Path) -> None:
    touch(tmp_path / "a.mkv")
    q, events = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    await q.wait_idle()
    assert [e.kind for e in events][:1] == ["added"]
    assert any(e.kind == "updated" and e.ids == (q.items[0].id,) for e in events)


async def test_remove_and_clear(tmp_path: Path) -> None:
    touch(tmp_path / "a.mkv")
    touch(tmp_path / "b.mkv")
    q, events = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    first = q.items[0].id
    q.remove(first)
    assert [i.id for i in q.items] != [first] and len(q.items) == 1
    assert events[-1] == QueueEvent(kind="removed", ids=(first,))
    q.clear()
    assert q.items == [] and events[-1].kind == "cleared"
    await q.wait_idle()  # cancelled lookups don't hang


async def test_rescan_after_toggle(tmp_path: Path) -> None:
    touch(tmp_path / "top.mkv")
    touch(tmp_path / "sub" / "deep.mkv")
    loose = touch(tmp_path.parent / f"{tmp_path.name}-loose" / "loose.mkv")
    q, _ = make_queue()
    await q.add_paths([tmp_path, loose], recursive=False)
    assert {i.path.name for i in q.items} == {"top.mkv", "loose.mkv"}
    await q.rescan(recursive=True)
    assert {i.path.name for i in q.items} == {"top.mkv", "deep.mkv", "loose.mkv"}


async def test_lookup_concurrency_limited(tmp_path: Path) -> None:
    for n in range(20):
        touch(tmp_path / f"Movie.{n}.2000.mkv")
    resolver = FakeResolver()
    q, _ = make_queue(resolver)
    await q.add_paths([tmp_path], recursive=True)
    await q.wait_idle()
    assert 1 < resolver.max_in_flight <= 6


async def test_scan_runs_off_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touch(tmp_path / "a.mkv")
    seen: list[Any] = []
    real = asyncio.to_thread

    async def spy(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        seen.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(queue_module.asyncio, "to_thread", spy)
    q, _ = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    assert scan_paths in seen


async def test_parsing_stays_on_the_loop_and_yields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CPU-heavy parsing in a worker thread starves the Tk thread of the GIL, so names are
    # parsed on the event loop in small chunks instead.
    for n in range(120):
        touch(tmp_path / f"Movie.{n}.2000.mkv")
    offloaded: list[Any] = []
    real = asyncio.to_thread

    async def spy(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        offloaded.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(queue_module.asyncio, "to_thread", spy)
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0)

    q, _ = make_queue()
    ticking = asyncio.create_task(ticker())
    await q.add_paths([tmp_path], recursive=True)
    before_parse = ticks
    stop.set()
    await ticking
    assert offloaded == [scan_paths]
    assert before_parse > 5


def test_scan_paths_sorted_and_tagged(tmp_path: Path) -> None:
    touch(tmp_path / "b.mkv")
    touch(tmp_path / "A.mkv")
    f = touch(tmp_path / "c.avi")
    found = scan_paths([tmp_path, f], recursive=True).files
    assert found[:2] == [(tmp_path / "A.mkv", tmp_path), (tmp_path / "b.mkv", tmp_path)]
    assert (f, None) in found


async def test_plan_inputs(tmp_path: Path) -> None:
    touch(tmp_path / "Dune.2021.mkv")
    q, _ = make_queue()
    await q.add_paths([tmp_path], recursive=True)
    await q.wait_idle()
    [pi] = q.plan_inputs()
    assert (pi.source, pi.root, pi.dropped_folder, pi.media.title) == (
        tmp_path / "Dune.2021.mkv",
        tmp_path,
        tmp_path,
        "Dune",
    )
    assert q.plan_inputs(ids=[]) == []


async def test_two_drops_at_once_add_each_file_once(tmp_path: Path) -> None:
    # Review I3: two overlapping add_paths calls (parsing takes a while) doubled the queue.
    for n in range(40):
        touch(tmp_path / f"Movie.{n}.2000.mkv")
    q, _ = make_queue()
    await asyncio.gather(q.add_paths([tmp_path], True), q.add_paths([tmp_path], True))
    assert len(q.items) == 40


async def test_clear_during_add_discards_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(5):
        touch(tmp_path / f"Movie.{n}.2000.mkv")
    gate = asyncio.Event()
    real_parse = queue_module._parse_all  # pyright: ignore[reportPrivateUsage]

    async def held_parse(names: Any, hint: Any) -> Any:
        await gate.wait()
        return await real_parse(names, hint)

    monkeypatch.setattr(queue_module, "_parse_all", held_parse)
    q, _ = make_queue()
    task = asyncio.create_task(q.add_paths([tmp_path], True))
    await asyncio.sleep(0.2)  # scanned, now parsing
    q.clear()
    gate.set()
    await task
    await q.wait_idle()
    assert q.items == []


async def test_finished_items_stay_finished(tmp_path: Path) -> None:
    # Review I2: changing preset type or picking a match must not turn renamed files
    # back into "to do", and their new path must be remembered.
    touch(tmp_path / "Show.S01E01.mkv")
    touch(tmp_path / "Show.S01E02.mkv")
    q, _ = make_queue()
    await q.add_paths([tmp_path], True)
    await q.wait_idle()
    first, second = q.items
    renamed = tmp_path / "Show - S01E01.mkv"
    q.mark_finished(first.id, renamed)
    await q.set_hint(TypeHint.MOVIE)
    await q.wait_idle()
    assert first.status is ItemStatus.DONE and first.path == renamed
    assert second.status is ItemStatus.READY and second.parsed.kind is MediaKind.MOVIE
    await q.choose_match(second.id, TmdbResult(id=1, title="Show"))
    assert first.status is ItemStatus.DONE
    assert [i.id for i in q.pending()] == [second.id]


# --- watched downloads (1.3) ------------------------------------------------------------------


async def test_watched_files_are_parsed_without_the_presets_hint(tmp_path: Path) -> None:
    q, _ = make_queue()
    await q.set_hint(TypeHint.TV)  # the window's preset is a Series one
    file = touch(tmp_path / "Dune.2021.mkv")
    [item_id] = await q.add_watched([(file, tmp_path)])
    await q.wait_idle()
    item = q.get(item_id)
    assert item is not None and item.watched == tmp_path
    assert item.parsed.kind is MediaKind.MOVIE
    assert item.root == tmp_path  # where a dropped file would be renamed; unused for watched ones


async def test_set_hint_leaves_watched_files_alone(tmp_path: Path) -> None:
    q, _ = make_queue()
    [watched] = await q.add_watched([(touch(tmp_path / "w" / "Dune.2021.mkv"), tmp_path / "w")])
    await q.add_paths([touch(tmp_path / "d" / "Heat.1995.mkv")], recursive=False)
    await q.wait_idle()
    await q.set_hint(TypeHint.TV)
    await q.wait_idle()
    kinds = {i.path.name: i.parsed.kind for i in q.items}
    assert kinds == {"Dune.2021.mkv": MediaKind.MOVIE, "Heat.1995.mkv": MediaKind.EPISODE}
    assert q.get(watched) is not None


async def test_a_watched_file_is_added_once(tmp_path: Path) -> None:
    q, _ = make_queue()
    file = touch(tmp_path / "Show.S01E01.mkv")
    assert len(await q.add_watched([(file, tmp_path)])) == 1
    assert await q.add_watched([(file, tmp_path)]) == []
    await q.add_paths([file], recursive=False)  # dropped on the window as well
    assert len(q.items) == 1


async def test_hold_keeps_a_reason(tmp_path: Path) -> None:
    q, events = make_queue()
    [item_id] = await q.add_watched([(touch(tmp_path / "Dune.mkv"), tmp_path)])
    await q.wait_idle()
    q.hold(item_id, "No year in the name: is it a movie?")
    item = q.get(item_id)
    assert item is not None and item.status is ItemStatus.HELD
    assert item.message == "No year in the name: is it a movie?"
    assert events[-1] == QueueEvent(kind="updated", ids=(item_id,))
    assert item in q.pending()  # not renamed yet


async def test_purge_finished_drops_yesterdays_done(tmp_path: Path) -> None:
    q, events = make_queue()
    ids = await q.add_watched([(touch(tmp_path / f"S01E0{n}.mkv"), tmp_path) for n in (1, 2, 3)])
    await q.wait_idle()
    q.mark_finished(ids[0], tmp_path / "x1.mkv")
    q.mark_finished(ids[1], tmp_path / "x2.mkv")
    old, new = (q.get(i) for i in ids[:2])
    assert old is not None and new is not None
    old.finished_at = 1000.0
    new.finished_at = 5000.0
    assert q.purge_finished(before=2000.0) == 1
    assert [i.id for i in q.items] == ids[1:]  # yesterday's done went; today's and pending stay
    assert events[-1] == QueueEvent(kind="removed", ids=(ids[0],))


async def test_forget_watched_drops_files_that_went_away(tmp_path: Path) -> None:
    q, _ = make_queue()
    a, b = touch(tmp_path / "a.mkv"), touch(tmp_path / "b.mkv")
    ids = await q.add_watched([(a, tmp_path), (b, tmp_path)])
    dropped = await q.add_paths([touch(tmp_path / "other" / "c.mkv")], recursive=False)
    await q.wait_idle()
    assert dropped == 1
    present = {queue_module._key(a)}  # pyright: ignore[reportPrivateUsage]
    assert q.forget_watched(present) == 1
    assert {i.path.name for i in q.items} == {"a.mkv", "c.mkv"}  # dropped files are not watched
    assert q.get(ids[0]) is not None


async def test_mark_finished_keeps_the_run(tmp_path: Path) -> None:
    q, _ = make_queue()
    [item_id] = await q.add_watched([(touch(tmp_path / "a.mkv"), tmp_path)])
    await q.wait_idle()
    q.mark_finished(item_id, tmp_path / "b.mkv", run_id=7)
    item = q.get(item_id)
    assert item is not None and item.run_id == 7 and item.status is ItemStatus.DONE


async def test_rescan_keeps_watched_files(tmp_path: Path) -> None:
    q, _ = make_queue()
    await q.add_watched([(touch(tmp_path / "w" / "a.mkv"), tmp_path / "w")])
    touch(tmp_path / "d" / "b.mkv")
    await q.add_paths([tmp_path / "d"], recursive=True)
    await q.wait_idle()
    await q.rescan(recursive=True)
    await q.wait_idle()
    assert sorted(i.path.name for i in q.items) == ["a.mkv", "b.mkv"]
    assert [i.watched for i in q.items if i.path.name == "a.mkv"] == [tmp_path / "w"]
