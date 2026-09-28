"""Portskill — local UI + MCP + CLI (stdlib-only runtime)."""

import os
import pathlib

__version__ = "0.1.1"

__all__ = ["__version__"]


def _migrate_legacy_names() -> None:
    """One-time carry-over from the pre-rename "port-registry" names.

    PORT_REGISTRY_* env vars still work (mapped to PORTSKILL_*), and
    ~/.config/port-registry moves to ~/.config/portskill with a symlink left
    behind so absolute paths stored elsewhere keep resolving.
    """
    for key, value in list(os.environ.items()):
        if key == "PORT_REGISTRY_PATH":
            os.environ.setdefault("PORTSKILL_REGISTRY_PATH", value)
        elif key == "PORT_REGISTRY_APP_PORT":
            os.environ.setdefault("PORTSKILL_PORT", value)
        elif key.startswith("PORT_REGISTRY_"):
            os.environ.setdefault("PORTSKILL_" + key[len("PORT_REGISTRY_"):], value)
    config = pathlib.Path(os.path.expanduser("~/.config"))
    old, new = config / "port-registry", config / "portskill"
    try:
        if old.is_dir() and not old.is_symlink() and not new.exists():
            old.rename(new)
            old.symlink_to(new)
    except OSError:
        pass  # leave data where it is; doctor reports a missing registry


_migrate_legacy_names()
