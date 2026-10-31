import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mediaforge.logging_setup import (
    UiLogBridge,
    configure_logging,
    log_success,
    set_secret,
    shutdown_logging,
)


@pytest.fixture
def bridge(tmp_path: Path) -> Iterator[UiLogBridge]:
    b = configure_logging(tmp_path / "logs")
    try:
        yield b
    finally:
        shutdown_logging()


def log_text(tmp_path: Path) -> str:
    return (tmp_path / "logs" / "mediaforge.log").read_text(encoding="utf-8")


def test_records_reach_file_and_ui(tmp_path: Path, bridge: UiLogBridge) -> None:
    logging.getLogger("mediaforge.test").warning("hello %s", "world")
    [line] = bridge.drain()
    assert line.text == "hello world" and line.level == logging.WARNING and not line.success
    assert "hello world" in log_text(tmp_path)


def test_secret_redacted_in_file_and_ui(tmp_path: Path, bridge: UiLogBridge) -> None:
    set_secret("SECRET123")
    logging.getLogger("mediaforge.tmdb").error("GET https://x/?api_key=%s failed", "SECRET123")
    [line] = bridge.drain()
    assert "SECRET123" not in line.text and "***" in line.text
    assert "SECRET123" not in log_text(tmp_path)


def test_secret_redacted_in_tracebacks(tmp_path: Path, bridge: UiLogBridge) -> None:
    set_secret("TOPSECRET")
    try:
        raise RuntimeError("url had api_key=TOPSECRET")
    except RuntimeError:
        logging.getLogger("mediaforge.x").exception("boom")
    bridge.drain()
    text = log_text(tmp_path)
    assert "boom" in text and "RuntimeError" in text and "TOPSECRET" not in text


def test_success_flag(bridge: UiLogBridge) -> None:
    log_success(logging.getLogger("mediaforge.x"), "Renamed 3 files")
    [line] = bridge.drain()
    assert line.success and line.level == logging.INFO


def test_drain_from_other_thread(bridge: UiLogBridge) -> None:
    worker = threading.Thread(target=lambda: logging.getLogger("mediaforge.t").info("from thread"))
    worker.start()
    worker.join()
    assert [line.text for line in bridge.drain()] == ["from thread"]


def test_drain_limit(bridge: UiLogBridge) -> None:
    for n in range(5):
        logging.getLogger("mediaforge.t").info("line %d", n)
    assert len(bridge.drain(max_items=3)) == 3
    assert len(bridge.drain()) == 2


def test_configure_twice_does_not_duplicate(tmp_path: Path) -> None:
    configure_logging(tmp_path / "logs")
    second = configure_logging(tmp_path / "logs")
    try:
        logging.getLogger("mediaforge.t").info("once")
        assert [line.text for line in second.drain()] == ["once"]
    finally:
        shutdown_logging()
