import threading
import uuid
from pathlib import Path

from mediaforge.platform.single_instance import (
    ConfirmBatcher,
    InstanceMessage,
    pipe_address,
    send_to_primary,
    try_become_primary,
)


def test_mutex_second_is_secondary(tmp_path: Path) -> None:
    name = f"test-{uuid.uuid4().hex}"
    first = try_become_primary(tmp_path / "k", name=name)
    try:
        assert first is not None
        assert try_become_primary(tmp_path / "k", name=name) is None
    finally:
        assert first is not None
        first.close()
    again = try_become_primary(tmp_path / "k", name=name)
    assert again is not None
    again.close()


def test_send_and_receive_over_pipe(tmp_path: Path) -> None:
    name = f"test-{uuid.uuid4().hex}"
    primary = try_become_primary(tmp_path / "k", name=name)
    assert primary is not None
    got: list[InstanceMessage] = []
    arrived = threading.Event()

    def on_message(msg: InstanceMessage) -> None:
        got.append(msg)
        arrived.set()

    primary.start(on_message)
    try:
        msg = InstanceMessage(paths=[r"C:\a.mkv"], preset="Auto", confirm=True)
        assert send_to_primary(msg, tmp_path / "k", name=name)
        assert arrived.wait(5)
        assert got == [msg]
    finally:
        primary.close()


def test_send_without_primary_gives_up(tmp_path: Path) -> None:
    msg = InstanceMessage(paths=[], preset=None, confirm=False)
    assert (
        send_to_primary(msg, tmp_path / "k", timeout_s=0.3, name=f"none-{uuid.uuid4().hex}")
        is False
    )


def test_wrong_key_is_rejected(tmp_path: Path) -> None:
    name = f"test-{uuid.uuid4().hex}"
    primary = try_become_primary(tmp_path / "right", name=name)
    assert primary is not None
    got: list[InstanceMessage] = []
    primary.start(got.append)
    try:
        msg = InstanceMessage(paths=["x"], preset=None, confirm=True)
        assert send_to_primary(msg, tmp_path / "wrong", timeout_s=0.5, name=name) is False
        assert got == []
    finally:
        primary.close()


def test_close_never_hangs_when_listener_already_stopped(tmp_path: Path) -> None:
    # Regression: close() used a full Client() handshake to wake the listener, which waited
    # forever when the listener thread had already stopped.
    name = f"test-{uuid.uuid4().hex}"
    primary = try_become_primary(tmp_path / "k", name=name)
    assert primary is not None
    primary.start(lambda msg: None)
    primary._closing = True  # pyright: ignore[reportPrivateUsage]
    with open(pipe_address(name), "r+b", buffering=0):
        pass
    thread = primary._thread  # pyright: ignore[reportPrivateUsage]
    assert thread is not None
    thread.join(3)
    assert not thread.is_alive()
    closed = threading.Event()
    threading.Thread(target=lambda: (primary.close(), closed.set()), daemon=True).start()
    assert closed.wait(5)


def test_key_file_race_gives_everyone_the_same_key(tmp_path: Path) -> None:
    # Explorer starts one process per selected file at the same moment; they must all end up
    # with the same key, or the hand-over to the first window fails.
    from mediaforge.platform.single_instance import load_key

    key_file = tmp_path / "sub" / "instance.key"
    keys: list[bytes] = []
    start = threading.Barrier(8)

    def grab() -> None:
        start.wait()
        keys.append(load_key(key_file))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(keys) == 8 and len(set(keys)) == 1 and len(keys[0]) == 32


def test_inbox_holds_messages_until_a_handler_attaches() -> None:
    from mediaforge.platform.single_instance import Inbox

    inbox = Inbox()
    first = InstanceMessage(paths=["a"], preset=None, confirm=True)
    second = InstanceMessage(paths=["b"], preset=None, confirm=True)
    inbox.deliver(first)
    got: list[InstanceMessage] = []
    inbox.attach(got.append)
    inbox.deliver(second)
    assert got == [first, second]


def test_batcher_groups_by_preset_after_quiet_period() -> None:
    batches: list[tuple[str | None, list[Path]]] = []
    b = ConfirmBatcher(quiet_s=0.4, on_batch=lambda preset, paths: batches.append((preset, paths)))
    b.add(InstanceMessage(paths=["a.mkv"], preset="Auto", confirm=True), now=10.0)
    b.add(InstanceMessage(paths=["b.mkv"], preset="Auto", confirm=True), now=10.2)
    b.add(InstanceMessage(paths=["c.mkv"], preset="Series (Flat)", confirm=True), now=10.3)
    b.flush_due(now=10.5)
    assert batches == []
    b.flush_due(now=10.8)
    assert batches == [("Auto", [Path("a.mkv"), Path("b.mkv")]), ("Series (Flat)", [Path("c.mkv")])]
    b.flush_due(now=20.0)
    assert len(batches) == 2
