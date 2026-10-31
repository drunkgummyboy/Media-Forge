import io
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from mediaforge.platform import update_files
from mediaforge.platform.update_files import (
    STAGING_DIR,
    UpdateFileError,
    apply_staged,
    install_archive,
    is_development_copy,
    is_newer,
    stage_archive,
)

TOP = "Media-Forge-main"


def archive(
    version: str,
    *,
    entries: tuple[str, ...] = ("main.py", "pyproject.toml", "src"),
    tops: tuple[str, ...] = (TOP,),
) -> bytes:
    """A zip shaped like GitHub's branch download."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for top in tops:
            if "main.py" in entries:
                z.writestr(f"{top}/main.py", f"# launcher {version}\n")
            if "pyproject.toml" in entries:
                z.writestr(f"{top}/pyproject.toml", f'[project]\nversion = "{version}"\n')
            if "src" in entries:
                z.writestr(f"{top}/src/mediaforge/__init__.py", f'__version__ = "{version}"\n')
                z.writestr(f"{top}/src/mediaforge/app.py", f"# app {version}\n")
            z.writestr(f"{top}/tests/test_x.py", "# must not be copied\n")
            z.writestr(f"{top}/README.md", "# readme\n")
    return buffer.getvalue()


def installed(tmp_path: Path, version: str = "1.1.0") -> Path:
    root = tmp_path / "Programs" / "MediaForge"
    with zipfile.ZipFile(io.BytesIO(archive(version))) as z:
        for info in z.infolist():
            parts = Path(info.filename).parts[1:]
            if parts and parts[0] in ("main.py", "pyproject.toml", "src"):
                target = root.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("venv", encoding="utf-8")
    (root / "notes.txt").write_text("keep me", encoding="utf-8")
    return root


def version_on_disk(root: Path) -> str:
    text = (root / "src" / "mediaforge" / "__init__.py").read_text(encoding="utf-8")
    return text.split('"')[1]


def test_stage_then_apply_replaces_only_app_entries(tmp_path: Path) -> None:
    root = installed(tmp_path)
    assert stage_archive(archive("1.2.0"), root, newer_than="1.1.0") == "1.2.0"
    assert version_on_disk(root) == "1.1.0"  # staging changes nothing yet
    assert apply_staged(root) == "1.2.0"
    assert version_on_disk(root) == "1.2.0"
    assert (root / "main.py").read_text(encoding="utf-8") == "# launcher 1.2.0\n"
    assert (root / ".venv" / "marker").read_text(encoding="utf-8") == "venv"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert not (root / "tests").exists()
    assert not (root / "README.md").exists()
    assert not (root / STAGING_DIR).exists()


def test_install_archive_swaps_at_once(tmp_path: Path) -> None:
    root = installed(tmp_path)
    assert install_archive(archive("1.3.0"), root) == "1.3.0"
    assert version_on_disk(root) == "1.3.0"
    assert not (root / STAGING_DIR).exists()


def test_stage_refuses_not_newer(tmp_path: Path) -> None:
    root = installed(tmp_path)
    with pytest.raises(UpdateFileError):
        stage_archive(archive("1.2.0"), root, newer_than="1.2.0")
    assert not (root / STAGING_DIR).exists()
    assert version_on_disk(root) == "1.1.0"


def test_archive_missing_an_entry_is_refused(tmp_path: Path) -> None:
    root = installed(tmp_path)
    with pytest.raises(UpdateFileError):
        stage_archive(archive("1.2.0", entries=("main.py", "src")), root)
    assert not (root / STAGING_DIR).exists()


def test_archive_with_two_top_folders_is_refused(tmp_path: Path) -> None:
    root = installed(tmp_path)
    with pytest.raises(UpdateFileError):
        stage_archive(archive("1.2.0", tops=(TOP, "Other-main")), root)


def test_not_an_archive_is_refused(tmp_path: Path) -> None:
    root = installed(tmp_path)
    with pytest.raises(UpdateFileError):
        stage_archive(b"<html>rate limited</html>", root)


def test_development_copy_is_detected(tmp_path: Path) -> None:
    root = installed(tmp_path)
    assert not is_development_copy(root)
    (root / "tests").mkdir()
    assert is_development_copy(root)
    other = installed(tmp_path / "other")
    (other / ".git").mkdir()
    assert is_development_copy(other)


def test_development_copy_never_applies(tmp_path: Path) -> None:
    root = installed(tmp_path)
    stage_archive(archive("1.2.0"), root)
    (root / "tests").mkdir()
    assert apply_staged(root) is None
    assert version_on_disk(root) == "1.1.0"


FILE_STEPS = ("_rename", "_replace", "_backup")  # every disk step a swap takes


def fail_at(monkeypatch: pytest.MonkeyPatch, step: int, error: BaseException) -> list[int]:
    """Make the swap's `step`-th disk operation (1-based) raise `error`."""
    count = [0]
    for name in FILE_STEPS:
        real = getattr(update_files, name)

        def wrapped(source: Path, destination: Path, _real=real) -> None:  # noqa: ANN001
            count[0] += 1
            if count[0] == step:
                raise error
            _real(source, destination)

        monkeypatch.setattr(update_files, name, wrapped)
    return count


def one_whole_version(root: Path) -> str:
    """The version on disk, after checking all three app entries belong to it."""
    version = version_on_disk(root)
    assert (root / "main.py").read_text(encoding="utf-8") == f"# launcher {version}\n"
    assert f'version = "{version}"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert (root / "src" / "mediaforge" / "app.py").read_text(
        encoding="utf-8"
    ) == f"# app {version}\n"
    return version


def test_interrupted_swap_leaves_a_working_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review focus 2: antivirus locks a file half-way through the swap.
    root = installed(tmp_path)
    stage_archive(archive("1.2.0"), root)
    fail_at(monkeypatch, 3, PermissionError("locked"))
    assert apply_staged(root) is None
    assert one_whole_version(root) == "1.1.0"
    monkeypatch.undo()
    assert apply_staged(root) is None  # the failed update is never retried, and nothing crashes
    assert one_whole_version(root) == "1.1.0"


def recover_from_main_py() -> Callable[[Path], None]:
    """main.py's repair step, with a lock name of its own: a real MediaForge running on this
    PC holds the real lock, and the repair rightly waits while it does."""
    import runpy
    import uuid

    main = Path(__file__).resolve().parents[2] / "main.py"
    recover = runpy.run_path(str(main), run_name="not_main")["recover"]
    name = f"recover-test-{uuid.uuid4().hex}"
    return lambda root: recover(root, lock_name=name)


@pytest.mark.parametrize("step", range(1, 7))
def test_power_cut_at_any_step_never_breaks_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: int
) -> None:
    # Review I2: a kill mid-swap must never leave a copy that can't start.
    root = installed(tmp_path)
    stage_archive(archive("1.2.0"), root)
    count = fail_at(monkeypatch, step, KeyboardInterrupt())  # like a kill: no rollback runs
    with pytest.raises(KeyboardInterrupt):
        update_files._apply_staged(root)
    monkeypatch.undo()
    assert count[0] == step, "the swap has fewer disk steps than this test expects"
    assert (root / "main.py").is_file(), "main.py must never be missing: it does the recovery"
    recover = recover_from_main_py()
    recover(root)  # what the next start does before importing anything
    assert one_whole_version(root) in ("1.1.0", "1.2.0")
    assert (root / ".venv" / "marker").is_file()
    recover(root)  # a second start changes nothing
    assert one_whole_version(root) in ("1.1.0", "1.2.0")


def test_finished_swap_with_leftover_staging_keeps_the_new_version(tmp_path: Path) -> None:
    root = installed(tmp_path)
    stage_archive(archive("1.2.0"), root)
    assert apply_staged(root) == "1.2.0"
    leftover = root / STAGING_DIR / "old"  # clean-up was cut short after a complete swap
    leftover.mkdir(parents=True)
    (leftover / "main.py").write_text("# launcher 1.1.0\n", encoding="utf-8")
    recover_from_main_py()(root)
    assert one_whole_version(root) == "1.2.0"
    assert not (root / STAGING_DIR).exists()


def test_apply_without_ready_marker_discards_staging(tmp_path: Path) -> None:
    root = installed(tmp_path)
    stage_archive(archive("1.2.0"), root)
    (root / STAGING_DIR / "ready").unlink()
    assert apply_staged(root) is None
    assert not (root / STAGING_DIR).exists()
    assert version_on_disk(root) == "1.1.0"


def test_staged_update_older_than_disk_is_discarded(tmp_path: Path) -> None:
    root = installed(tmp_path, "1.3.0")  # the installer ran after the update was staged
    stage_archive(archive("1.2.0"), root)
    assert apply_staged(root) is None
    assert version_on_disk(root) == "1.3.0"
    assert not (root / STAGING_DIR).exists()


def test_version_compare() -> None:
    assert is_newer("1.10.0", "1.9.0")
    assert is_newer("1.1.1", "1.1")
    assert not is_newer("1.1.0", "1.1.0")
    assert not is_newer("1.1", "1.1.0")
    assert not is_newer("garbage", "1.0.0")
    assert update_files.parse_version('"""doc"""\n\n__version__ = "1.2.0"\n') == "1.2.0"
    assert update_files.parse_version("nothing here") is None


def test_recovery_reloads_the_restored_code(tmp_path: Path) -> None:
    # After putting the old src back, nothing from the half-installed new src may stay loaded:
    # the app would show the new version number while running the old code.
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]

    def code_tree(target: Path, version: str) -> None:
        (target / "mediaforge" / "platform").mkdir(parents=True)
        (target / "mediaforge" / "__init__.py").write_text(
            f'__version__ = "{version}"\n', encoding="utf-8"
        )
        (target / "mediaforge" / "platform" / "__init__.py").write_text("", encoding="utf-8")
        for module in ("update_files.py", "instance_name.py"):
            shutil.copy2(
                repo / "src" / "mediaforge" / "platform" / module,
                target / "mediaforge" / "platform" / module,
            )

    root = tmp_path / "MediaForge"
    root.mkdir()
    shutil.copy2(repo / "main.py", root / "main.py")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.0"\n', encoding="utf-8")
    code_tree(root / "src", "1.2.0")  # the new src made it in ...
    old = root / STAGING_DIR / "old"
    code_tree(old / "src", "1.1.0")  # ... the old one is still kept aside ...
    (old / "pyproject.toml").write_text('[project]\nversion = "1.1.0"\n', encoding="utf-8")
    (root / STAGING_DIR / "new").mkdir()
    (root / STAGING_DIR / "new" / "main.py").write_text(
        "# 1.2.0\n", encoding="utf-8"
    )  # ... cut off
    script = (
        "import runpy\n"
        f"ns = runpy.run_path({str(root / 'main.py')!r}, run_name='not_main')\n"
        f"ns['recover'](ns['HERE'], lock_name='recover-test-{uuid.uuid4().hex}')\n"
        "import mediaforge\n"
        "print(mediaforge.__version__)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "1.1.0"
