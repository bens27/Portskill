"""python -m portskill → launches app (UI + MCP HTTP by default)."""
from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
