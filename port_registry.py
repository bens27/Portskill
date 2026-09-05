#!/usr/bin/env python3
"""Thin wrapper — prefer: port-registry …  or  python3 -m port_registry_app.cli"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from port_registry_app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
