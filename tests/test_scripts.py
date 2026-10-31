"""install.ps1 and uninstall.ps1: Windows PowerShell 5.1 rules, and (opt-in) a real round trip."""

import io
import os
import re
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.ps1"
UNINSTALL = ROOT / "src" / "mediaforge" / "uninstall.ps1"
SCRIPTS = [INSTALL, UNINSTALL]
POWERSHELL = "powershell.exe"  # Windows PowerShell 5.1, what a new PC has


def run_ps(command: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_in_windows_powershell_51(script: Path) -> None:
    check = (
        "$errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$errors); "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }; "
        "if ($PSVersionTable.PSVersion.Major -ne 5) { 'not 5.1'; exit 2 }"
    )
    result = run_ps(check)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_are_ascii_and_avoid_51_traps(script: Path) -> None:
    data = script.read_bytes()
    assert all(b < 128 for b in data), (
        "ASCII only: a copy run with -File (no BOM) must read the same"
    )
    text = re.sub(r"<#.*?#>", "", data.decode("ascii"), flags=re.DOTALL)  # block comments
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "&&" not in code and "||" not in code
    assert "??" not in code
    assert not re.search(r"\)\s+\?\s+\S", code), "no ternary operator in 5.1"
    assert not re.search(r"^\s*exit\b", code, re.MULTILINE), "exit would close the user's window"


def app_archive(top: str = "Media-Forge-main") -> bytes:
    """The working tree's app entries, shaped like GitHub's branch download."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in ("main.py", "pyproject.toml"):
            z.write(ROOT / entry, f"{top}/{entry}")
        for path in (ROOT / "src").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, f"{top}/{path.relative_to(ROOT).as_posix()}")
        z.writestr(f"{top}/tests/test_x.py", "# never copied\n")
    return buffer.getvalue()


@pytest.mark.skipif(
    os.environ.get("MEDIAFORGE_INSTALLER_TESTS") != "1",
    reason="opt-in (MEDIAFORGE_INSTALLER_TESTS=1): needs the internet for pip, takes minutes",
)
def test_install_from_local_archive_then_quiet_uninstall(tmp_path: Path) -> None:
    from mediaforge import __version__

    zip_path = tmp_path / "Media-Forge-main.zip"
    zip_path.write_bytes(app_archive())
    install_dir = tmp_path / "Programs dir with spaces" / "MediaForge"
    key = rf"HKCU:\Software\MediaForge-test\Uninstall-{uuid.uuid4().hex}"
    try:
        result = run_ps(
            f"& '{INSTALL}' -Archive '{zip_path}' -InstallDir '{install_dir}' "
            f"-NoShortcuts -NoLaunch -NoMenu -UninstallKey '{key}'",
            timeout=900,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "MediaForge is ready" in result.stdout, result.stdout + result.stderr
        for entry in ("main.py", "pyproject.toml", "src", ".venv"):
            assert (install_dir / entry).exists(), entry
        assert not (install_dir / "tests").exists()
        shown = run_ps(f"(Get-ItemProperty -LiteralPath '{key}').DisplayVersion")
        assert shown.stdout.strip() == __version__

        uninstaller = install_dir / "src" / "mediaforge" / "uninstall.ps1"
        result = run_ps(f"& '{uninstaller}' -Quiet -UninstallKey '{key}'")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not install_dir.exists()
        assert run_ps(f"Test-Path -LiteralPath '{key}'").stdout.strip() == "False"
    finally:
        run_ps(
            r"Remove-Item -LiteralPath 'HKCU:\Software\MediaForge-test' -Recurse -Force "
            "-ErrorAction SilentlyContinue"
        )


def test_find_python_keeps_non_ascii_paths_intact(tmp_path: Path) -> None:
    # Review C1: Python printed its path in one code page and PowerShell read it in another,
    # so a user folder like "Jürgen Smit" came back garbled and the install stopped.
    import sys

    venv = tmp_path / "Jürgen Smit" / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    python = venv / "Scripts" / "python.exe"
    check = (
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{INSTALL}', [ref]$null, [ref]$null); "
        "$fn = $ast.FindAll({ param($n) $n -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$n.Name -eq 'Test-Python' }, $true) | Select-Object -First 1; "
        "Invoke-Expression $fn.Extent.Text; "
        f"$found = Test-Python '{python}'; "
        f"if ($found -eq '{python}') {{ 'SAME' }} else {{ 'DIFF' }}"
    )
    result = run_ps(check)
    assert result.stdout.strip().splitlines()[-1:] == ["SAME"], result.stdout + result.stderr
