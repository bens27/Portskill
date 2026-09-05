#!/usr/bin/env python3
"""Thin wrapper — UI helpers live in port_registry_app.server."""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from port_registry_app.server import (  # noqa: F401
    Handler,
    build_view,
    load_registry,
    main,
    registry_path,
    render_page,
    run_cli_action,
)

if __name__ == "__main__":
    # Legacy serve_ui was UI-only; default app also serves MCP — same port/schema.
    raise SystemExit(main())
