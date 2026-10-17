"""Allow python -m fuzzy_surgical."""

from __future__ import annotations

import sys

from fuzzy_surgical.cli import main


if __name__ == "__main__":
    sys.exit(main())
