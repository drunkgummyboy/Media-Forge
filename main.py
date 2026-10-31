"""MediaForge's launcher: what the Start menu shortcut, the right-click menu and install.ps1 start
(`.venv\\Scripts\\pythonw.exe main.py`). Works from a checkout too.

Standard library only: see src/mediaforge/launch.py.
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))


def recover(here: Path, lock_name: str | None = None) -> None:
    """Repair an update that a power cut interrupted, before anything else is imported.

    Not while another MediaForge holds the lock: it may be swapping in an update right now.
    If the swap was cut off between moving the old src folder aside and putting the new one in,
    there is no src to import the repair code from: put the old one back first.
    """
    old = here / ".update" / "old"
    if not old.is_dir():
        return
    for _ in range(50):  # a swap that is running puts the new src in place within moments
        if (here / "src").exists():
            break
        time.sleep(0.1)
    try:
        if (here / "src").exists():
            from mediaforge.platform.instance_name import instance_running

            if instance_running(lock_name):
                return
        elif (old / "src").is_dir():
            os.rename(old / "src", here / "src")
        from mediaforge.platform.update_files import restore_interrupted

        if restore_interrupted(here):
            # The repair code came from the half-installed files: load them afresh.
            src = str(here / "src").casefold()
            for name, module in list(sys.modules.items()):
                if str(getattr(module, "__file__", "") or "").casefold().startswith(src):
                    del sys.modules[name]
    except Exception:
        pass  # the app still starts; the next update tries again


def run() -> int:
    recover(HERE)
    from mediaforge import launch

    launch.LAUNCHER = Path(__file__).resolve()
    return launch.main(sys.argv[1:], install_dir=HERE)


if __name__ == "__main__":
    sys.exit(run())
