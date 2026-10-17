#!/usr/bin/env python3
"""FuzzySurgical launcher kept for source checkouts and the install scripts."""

from __future__ import annotations

import sys

from fuzzy_surgical.cli import main


if __name__ == "__main__":
    sys.exit(main())
