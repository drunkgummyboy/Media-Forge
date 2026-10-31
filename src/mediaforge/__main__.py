"""`python -m mediaforge` (development): the same start-up as main.py, see launch.py."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    from mediaforge import launch

    args = sys.argv[1:] if argv is None else argv
    return launch.main(args, install_dir=Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    sys.exit(main())
