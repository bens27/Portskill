"""Validation and loading for portable Portskill environment documents."""
from __future__ import annotations

import json
import pathlib

from .domain import DomainError


def load_environment_file(path):
    file_path = pathlib.Path(path).expanduser()
    if not file_path.exists():
        raise DomainError("environment_not_found", f"environment file not found: {file_path}")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DomainError(
            "invalid_environment", f"environment JSON is corrupt: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise DomainError("invalid_environment", "environment root must be an object")
    if data.get("kind") not in (None, "portskill-environment"):
        raise DomainError("invalid_environment", f"unexpected kind: {data.get('kind')}")
    services = data.get("services")
    if not isinstance(services, list):
        raise DomainError("invalid_environment", "services must be a list")
    return data, services
