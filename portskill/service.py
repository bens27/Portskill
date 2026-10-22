"""Launch local services consistently for CLI, apply, and path workflows."""
from __future__ import annotations

import datetime
import os
import pathlib
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any


PLACEHOLDER_SENTINEL = "portskill: edit .portskill/"


@dataclass(frozen=True)
class ServiceLaunchOutcome:
    ok: bool
    reason: str | None = None
    message: str | None = None
    path: str | None = None
    source: str | None = None
    pid: int | None = None
    pgid: int | None = None
    started_at: str | None = None
    start_log: str | None = None

    def error_detail(self, range_id: object = None) -> dict[str, Any]:
        detail: dict[str, Any] = {"reason": self.reason or "start_failed"}
        if self.message:
            detail["message"] = self.message
        if self.path:
            detail["path"] = self.path
        if range_id is not None:
            detail["range_id"] = range_id
        return detail


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _project_relative(project: str, value: object, field: str) -> pathlib.Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a project-relative path")
    project_dir = pathlib.Path(project).resolve()
    path = (project_dir / value).resolve()
    try:
        path.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError(f"{field} resolves outside project: {value}") from exc
    return path


def _script_is_placeholder(path: pathlib.Path) -> bool:
    try:
        return PLACEHOLDER_SENTINEL in path.read_text(encoding="utf-8")
    except OSError:
        return False


def service_launch(item: dict[str, Any], command: str | None) -> tuple[list[str] | None, dict[str, str]]:
    """Build argv and environment, including PORT substitution for commands."""
    port = str(item["start"])
    env = {**os.environ, "PORT": port}
    if command:
        expanded = command.replace("${PORT}", port).replace("$PORT", port)
        return shlex.split(expanded), env
    return None, env


def launch_service(item: dict[str, Any], project: str) -> ServiceLaunchOutcome:
    """Launch one configured service, update lifecycle metadata, and never emit or exit."""
    existing_lifecycle = item.get("lifecycle")
    lifecycle = existing_lifecycle if isinstance(existing_lifecycle, dict) else {}
    command = _optional_string(item.get("command"))
    cwd = _optional_string(item.get("cwd")) or project
    source = "command" if command else "script"

    try:
        start_log = _project_relative(
            project,
            lifecycle.get("start_log") or ".portskill/start.log",
            "start_log",
        )
    except ValueError as exc:
        return ServiceLaunchOutcome(False, "invalid_lifecycle_path", str(exc), source=source)

    if command:
        try:
            argv, env = service_launch(item, command)
        except (KeyError, ValueError) as exc:
            return ServiceLaunchOutcome(False, "start_command_failed", str(exc), source=source)
        failure_reason = "start_command_failed"
    else:
        try:
            start_script = _project_relative(
                project,
                lifecycle.get("start_script") or ".portskill/start.sh",
                "start_script",
            )
        except ValueError as exc:
            return ServiceLaunchOutcome(False, "invalid_lifecycle_path", str(exc), source=source)
        if not start_script.exists():
            return ServiceLaunchOutcome(
                False,
                "start_script_missing",
                f"start_script does not exist: {start_script}",
                str(start_script),
                source,
            )
        if not start_script.is_file():
            return ServiceLaunchOutcome(
                False,
                "start_script_invalid",
                f"start_script is not a file: {start_script}",
                str(start_script),
                source,
            )
        if not os.access(start_script, os.X_OK):
            return ServiceLaunchOutcome(
                False,
                "start_script_not_executable",
                f"start_script is not executable: {start_script}",
                str(start_script),
                source,
            )
        if _script_is_placeholder(start_script):
            return ServiceLaunchOutcome(
                False,
                "start_script_placeholder",
                f"start_script still contains the placeholder: {start_script}",
                str(start_script),
                source,
            )
        try:
            _unused, env = service_launch(item, None)
        except KeyError as exc:
            return ServiceLaunchOutcome(False, "start_script_failed", str(exc), source=source)
        argv = [str(start_script)]
        failure_reason = "start_script_failed"

    try:
        start_log.parent.mkdir(parents=True, exist_ok=True)
        with start_log.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                argv,
                env=env,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        return ServiceLaunchOutcome(False, failure_reason, str(exc), source=source)

    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        pgid = process.pid
    started_at = _utc_now()
    lifecycle["pid"] = process.pid
    lifecycle["pgid"] = pgid
    lifecycle["started_at"] = started_at
    lifecycle["stopped_at"] = None
    lifecycle["start_log"] = str(lifecycle.get("start_log") or ".portskill/start.log")
    item["lifecycle"] = lifecycle
    return ServiceLaunchOutcome(
        True,
        source=source,
        pid=process.pid,
        pgid=pgid,
        started_at=started_at,
        start_log=lifecycle["start_log"],
    )
