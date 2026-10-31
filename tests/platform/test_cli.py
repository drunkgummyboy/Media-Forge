from pathlib import Path

from mediaforge.platform.cli import LaunchArgs, parse_args


def test_parse_paths_preset_confirm() -> None:
    args = parse_args([r"C:\a.mkv", r"\\nas\share\b", "--preset", "Series (Flat)", "--confirm"])
    assert args == LaunchArgs(
        paths=[Path(r"C:\a.mkv"), Path(r"\\nas\share\b")], preset="Series (Flat)", confirm=True
    )


def test_no_arguments() -> None:
    assert parse_args([]) == LaunchArgs()


def test_auto_run_means_confirm() -> None:
    assert parse_args([r"C:\a.mkv", "--preset", "Auto", "--auto-run"]).confirm is True


def test_self_test_flag() -> None:
    assert parse_args(["--self-test"]).self_test is True
