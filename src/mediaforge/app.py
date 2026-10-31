"""Composition root: build every service, wire them to the window, run the loop."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import customtkinter as ctk  # pyright: ignore[reportMissingTypeStubs]
from async_tkinter_loop import main_loop  # pyright: ignore[reportMissingTypeStubs]

from mediaforge import launch
from mediaforge.api.tmdb import TmdbClient, TmdbError
from mediaforge.api.trailers import TrailerService
from mediaforge.api.updates import UpdateService
from mediaforge.core.auto import AutoRenamer
from mediaforge.core.executor import Executor
from mediaforge.core.formatter import render
from mediaforge.core.matcher import Resolver
from mediaforge.core.parser import parse_filename
from mediaforge.core.queue import Queue, QueueEvent
from mediaforge.core.recovery import recover
from mediaforge.core.undo import UndoService
from mediaforge.db.cache import CacheRepo
from mediaforge.db.database import Database
from mediaforge.db.history import HistoryRepo
from mediaforge.db.legacy_import import find_legacy, import_legacy
from mediaforge.db.matches import MatchRepo
from mediaforge.db.presets import PresetRepo
from mediaforge.db.settings import AppSettings, SettingsRepo
from mediaforge.logging_setup import UiLogBridge, configure_logging, set_secret, shutdown_logging
from mediaforge.models.media import ResolvedMedia
from mediaforge.platform import context_menu
from mediaforge.platform.cli import LaunchArgs
from mediaforge.platform.paths import AppPaths, app_paths
from mediaforge.platform.single_instance import Inbox, InstanceMessage, Primary

log = logging.getLogger("mediaforge.app")


class EventHub:
    """Fan queue events out to whichever views are listening."""

    def __init__(self) -> None:
        self.listeners: list[Callable[[QueueEvent], None]] = []

    def emit(self, event: QueueEvent) -> None:
        for listener in list(self.listeners):
            listener(event)

    def subscribe(self, listener: Callable[[QueueEvent], None]) -> Callable[[], None]:
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None


@dataclass
class TmdbRef:
    client: TmdbClient | None = None


class _LivePosters:
    """Poster source for the executor that always uses the current TMDB client."""

    def __init__(self, ref: TmdbRef) -> None:
        self._ref = ref

    async def image(self, path: str, size: str) -> bytes:
        if self._ref.client is None:
            raise TmdbError("No TMDB API key set")
        return await self._ref.client.image(path, size)


@dataclass
class Services:
    paths: AppPaths
    db: Database
    settings: SettingsRepo
    presets: PresetRepo
    matches: MatchRepo
    cache: CacheRepo
    history: HistoryRepo
    session: aiohttp.ClientSession
    resolver: Resolver
    queue: Queue
    executor: Executor
    undo: UndoService
    trailers: TrailerService
    log_bridge: UiLogBridge
    app_settings: AppSettings
    queue_events: EventHub
    updates: UpdateService
    tmdb_ref: TmdbRef = field(default_factory=TmdbRef)
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one rename run at a time
    auto: AutoRenamer = field(init=False)  # renaming on its own (started by `run`)

    @property
    def tmdb(self) -> TmdbClient | None:
        return self.tmdb_ref.client


def _make_tmdb(
    session: aiohttp.ClientSession, settings: AppSettings, cache: CacheRepo, paths: AppPaths
) -> TmdbClient | None:
    if not settings.tmdb_key.strip():
        return None
    return TmdbClient(
        session,
        settings.tmdb_key,
        language=settings.tmdb_language,
        cache=cache,
        images_dir=paths.images_dir,
    )


async def build_services(paths: AppPaths, legacy_dirs: Sequence[Path]) -> Services:
    bridge = configure_logging(paths.logs_dir)
    db = await Database.open(paths.db_file)
    settings, presets = SettingsRepo(db), PresetRepo(db)
    matches, cache, history = MatchRepo(db), CacheRepo(db), HistoryRepo(db)

    settled = await recover(history)
    if settled:
        log.warning("Settled %d change(s) left unfinished last time", settled)
    config, undo = find_legacy(legacy_dirs)
    await import_legacy(config, undo, settings, presets, history)
    await presets.seed_defaults_if_empty()

    app_settings = await settings.load()
    set_secret(app_settings.tmdb_key)
    session = aiohttp.ClientSession()
    ref = TmdbRef(_make_tmdb(session, app_settings, cache, paths))
    resolver = Resolver(ref.client, matches)
    hub = EventHub()
    trailers = TrailerService(paths.tools_dir, session, settings)
    services = Services(
        paths=paths,
        db=db,
        settings=settings,
        presets=presets,
        matches=matches,
        cache=cache,
        history=history,
        session=session,
        resolver=resolver,
        queue=Queue(resolver, hub.emit),
        executor=Executor(history, _LivePosters(ref), trailers),
        undo=UndoService(history),
        trailers=trailers,
        log_bridge=bridge,
        app_settings=app_settings,
        queue_events=hub,
        updates=UpdateService(session),
        tmdb_ref=ref,
    )
    services.auto = AutoRenamer(services)
    return services


async def rebuild_tmdb(services: Services) -> None:
    """Pick up a new API key or language."""
    services.app_settings = await services.settings.load()
    set_secret(services.app_settings.tmdb_key)
    client = _make_tmdb(services.session, services.app_settings, services.cache, services.paths)
    services.tmdb_ref.client = client
    services.resolver.set_client(client)


async def close_services(services: Services) -> None:
    await services.auto.stop()
    services.queue_events.listeners.clear()
    services.queue.clear()
    await services.session.close()
    await services.db.close()
    shutdown_logging()


def _legacy_dirs() -> list[Path]:
    return [Path.cwd(), Path(__file__).resolve().parents[2]]  # the install or project folder


@dataclass(frozen=True)
class RunOutcome:
    restart: bool = False  # the user chose "Restart to update" / "Update to …"


def _log_unhandled(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    exc = context.get("exception")
    log.error("Something went wrong: %s", exc or context.get("message"), exc_info=exc)


def start_palette(settings: AppSettings) -> Any:
    """The look this run uses: light, dark or Windows' choice, in the chosen accent colour."""
    from mediaforge.ui.theme import palette_for

    return palette_for(settings.theme, settings.accent)


async def run(args: LaunchArgs, primary: Primary) -> RunOutcome:
    from mediaforge.ui import gc_guard
    from mediaforge.ui.main_window import MainWindow
    from mediaforge.ui.router import WindowRouter

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_log_unhandled)
    # Listen first, so right-click launches of other files can hand over and exit while
    # this one is still loading; their files wait in the inbox.
    inbox = Inbox()

    def forward(msg: InstanceMessage) -> None:  # runs on the pipe thread
        loop.call_soon_threadsafe(inbox.deliver, msg)

    primary.start(forward)
    if args.confirm:
        inbox.deliver(
            InstanceMessage(paths=[str(p) for p in args.paths], preset=args.preset, confirm=True)
        )
    paths = app_paths()
    paths.ensure()
    services = await build_services(paths, _legacy_dirs())
    try:
        ctk.set_appearance_mode(services.app_settings.theme)
        palette = start_palette(services.app_settings)
        window = MainWindow(services, palette)
        gc_guard.start(window)
        shell = window.shell
        router = WindowRouter(window, shell, services, palette, confirm_only=args.confirm)
        router.start()
        if args.confirm:
            window.withdraw()  # only the confirm window shows; closing it ends the app
        else:
            if not services.app_settings.first_start_done:
                from mediaforge.ui.first_start import FirstStart

                shell.spawn(FirstStart.run(window, services, palette))
            if args.preset:
                shell.select_preset(args.preset)
            if args.paths:
                shell.spawn(shell.add_paths(args.paths))
        inbox.attach(router.handle)
        shell.spawn(shell.check_for_update())  # right-click-only launches update too
        shell.spawn(shell.refresh_menu())  # follow this copy's location and new presets
        # customtkinter keeps a new window hidden on Windows until its own update() or
        # mainloop() runs, and the async loop calls neither (a withdrawn confirm-only window
        # stays hidden).
        window.update()
        await main_loop(window)
        return RunOutcome(restart=shell.restart_requested)
    finally:
        # The lock itself is released by the launcher, after a downloaded update is swapped in.
        primary.stop_listening()
        await close_services(services)
        gc_guard.stop()


async def menu_action(*, install: bool) -> int:
    """`--install-menu` (install.ps1) / `--remove-menu` (uninstall.ps1): no window.

    Installing uses the presets picked in Settings, or every preset on a new install (the stored
    choice is then empty, meaning all), so presets imported on first start appear too once
    MediaForge refreshes the menu at start. Nothing in the settings is changed.
    """
    try:
        if not install:
            await asyncio.to_thread(context_menu.uninstall)
            return 0
        paths = app_paths()
        paths.ensure()
        db = await Database.open(paths.db_file)
        try:
            presets = PresetRepo(db)
            await presets.seed_defaults_if_empty()
            chosen = (await SettingsRepo(db).load()).context_menu_presets
            names = context_menu.menu_presets([p.name for p in await presets.list()], chosen)
            entries = context_menu.build_entries(
                names, context_menu.command_prefix(launch.LAUNCHER), context_menu.default_icon()
            )
            await asyncio.to_thread(context_menu.install, entries)
        finally:
            await db.close()
    except OSError as exc:
        log.error("Couldn't change the right-click menu: %s", exc)
        return 1
    return 0


async def self_test(root: Any = None) -> int:
    """Quick health check used by the build: parsing, database, formatting, drag and drop.

    Tests pass their shared root; the packaged app creates (and closes) its own.
    """
    try:
        parsed = parse_filename("VisionQuest.S01E02.Theseus.1080p.DSNP.WEB-DL.mkv")
        if (parsed.title, parsed.season, parsed.episode) != ("VisionQuest", 1, 2):
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            db = await Database.open(Path(tmp) / "self-test.db")
            try:
                presets = PresetRepo(db)
                await presets.seed_defaults_if_empty()
                series = (await presets.list())[3]
                rendered = render(series.format, ResolvedMedia.offline(parsed), ".mkv")
                if rendered.parts[-1] != "VisionQuest - S01E02 - Theseus.mkv":
                    return 1
            finally:
                await db.close()
        from mediaforge.ui.dnd_root import DnDCTk

        if root is not None:
            return 0 if getattr(root, "TkdndVersion", None) else 1
        own = DnDCTk()
        own.withdraw()
        ok = bool(getattr(own, "TkdndVersion", None))  # pyright: ignore[reportUnknownArgumentType]
        own.destroy()
        return 0 if ok else 1
    except Exception:
        log.exception("Self-test failed")
        return 1
