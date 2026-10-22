"""Neutral process, lifecycle-script, and Tailscale lifecycle primitives."""
from __future__ import annotations

import os
import pathlib
import shlex
import signal
import subprocess
import time

from .domain import DomainError, pid_alive as domain_pid_alive, utc_now
from .registry import normalize_settings, read_listen
from .registry_domain import (
    DEFAULT_LIFECYCLE_DIR,
    normalize_range_record,
)
from .service import script_is_placeholder
from .tailscale import probe_tailscale_status, tailscale_base_command

def needs_tailnet(project, count_or_range):
    if isinstance(count_or_range, int):
        subject = f"{count_or_range} port"
        if count_or_range != 1:
            subject += "s"
    else:
        subject = f"range {count_or_range}"
    return {
        "status": "needs_input",
        "input": "tailnet",
        "prompt": (
            f"Choose tailnet exposure for project {project} and {subject}: "
            "serve, funnel, or none."
        ),
        "options": ["serve", "funnel", "none"],
        "resume_hint": "--tailnet",
    }


def refuse_remote_process(registry, item, action="start"):
    mid = item.get("machine_id") or item.get("machine") or item.get("host_id") or "local"
    raise DomainError(
        "remote_machine",
        f"Cannot {action} processes for remote machine {mid} from this Mac. "
        "Remotes are registry entries + links only.",
        machine_id=mid,
        range_id=item.get("id"),
        hint="Use the remote host's own Portskill/keepalive, or open the port URL.",
    )


def should_use_sudo():
    return os.environ.get("PORTSKILL_TAILSCALE_SUDO", "1") != "0"


def tailscale_command(mode, port, off=False):
    command = tailscale_base_command()
    if mode == "funnel" and should_use_sudo():
        command = ["sudo"] + command
    command += [mode, "--bg", f"--https={port}", f"localhost:{port}"]
    if off:
        command.append("off")
    return command


def funnel_of_listen_port(port):
    """True when *port* is Portskill's own listen/UI/MCP port."""
    listen = read_listen_port_from_disk()
    try:
        return listen is not None and int(port) == int(listen)
    except (TypeError, ValueError):
        return False


def refuse_funnel_listen_message(port):
    return (
        f"Refusing Tailscale Funnel of Portskill listen/UI/MCP port {int(port)}. "
        "Funnel of the listen port is blocked in code. "
        "Serve of user claimed service ports stays a deliberate user action. "
        "Tailscale is not authentication."
    )


def run_tailnet(mode, port, off=False):
    if mode == "none":
        return
    if mode == "funnel" and not off and funnel_of_listen_port(port):
        raise DomainError(
            "funnel_listen_refused", refuse_funnel_listen_message(port)
        )
    command = tailscale_command(mode, port, off=off)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=20,
        )
    except FileNotFoundError:
        raise DomainError(
            "tailscale_missing",
            "Tailscale is not installed",
            hint="Install Tailscale or set PORTSKILL_TAILSCALE_BIN",
        )
    if result.returncode != 0:
        raise DomainError(
            "tailnet_command_failed",
            f"tailscale command exited with {result.returncode}",
            stdout=(result.stdout or "")[:500],
            stderr=(result.stderr or "")[:500],
        )


def ensure_tailscale_authenticated(mode):
    """Fail fast when Serve/Funnel needs auth. Never prompts for sudo password."""
    if mode not in ("serve", "funnel"):
        return probe_tailscale_status()
    status = probe_tailscale_status()
    if status.get("state") == "binary_missing":
        raise DomainError(
            "tailscale_bin_missing",
            status.get("message") or "Tailscale binary not found",
            tailscale=status,
            hint="Install Tailscale or set PORTSKILL_TAILSCALE_BIN",
        )
    if not status.get("logged_in"):
        raise DomainError(
            "tailscale_auth_required",
            "Tailscale Serve requires login. Use Browser Login in the UI, "
            "or run `portskill-cli tailscale login`.",
            tailscale=status,
            hint="tailscale login",
            AuthURL=status.get("AuthURL"),
        )
    return status


def lifecycle(item):
    return normalize_range_record(item)["lifecycle"]


def resolve_project_relative(project, value, field):
    if not isinstance(value, str) or not value:
        raise DomainError("invalid_lifecycle_path", f"{field} must be a project-relative path")
    project_dir = pathlib.Path(project).resolve()
    path = (project_dir / value).resolve()
    try:
        path.relative_to(project_dir)
    except ValueError:
        raise DomainError("invalid_lifecycle_path", f"{field} resolves outside project: {value}")
    return path


def placeholder_content(name):
    command = name[:-3] if name.endswith(".sh") else name
    return (
        "#!/bin/sh\n"
        f'echo "portskill: edit .portskill/{name} before using portskill '
        f'{command}" >&2\n'
        "exit 64\n"
    )


def scaffold_lifecycle_scripts(project):
    project_dir = pathlib.Path(project).resolve()
    scripts_dir = project_dir / DEFAULT_LIFECYCLE_DIR
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("start.sh", "stop.sh"):
        path = scripts_dir / name
        if not path.exists():
            path.write_text(placeholder_content(name), encoding="utf-8")
            path.chmod(0o755)


def validate_start_script(path):
    if not path.exists():
        raise DomainError("start_script_missing", f"start_script does not exist: {path}")
    if not path.is_file():
        raise DomainError("start_script_invalid", f"start_script is not a file: {path}")
    if not os.access(path, os.X_OK):
        raise DomainError("start_script_not_executable", f"start_script is not executable: {path}")
    if script_is_placeholder(path):
        raise DomainError("start_script_placeholder", f"start_script still contains the placeholder: {path}")


def pid_alive(pid):
    return domain_pid_alive(pid)


def process_group_pids(pgid):
    try:
        pgid = int(pgid)
    except (TypeError, ValueError):
        return []
    if pgid <= 0:
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    pids = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid_value = int(parts[0])
            pgid_value = int(parts[1])
        except ValueError:
            continue
        if pgid_value == pgid:
            pids.append(pid_value)
    return pids


def wait_for_processes_to_exit(pids, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in pids):
            return True
        time.sleep(0.1)
    return not any(pid_alive(pid) for pid in pids)


def run_stop_script_if_ready(item, project):
    life = lifecycle(item)
    stop_script = resolve_project_relative(project, life["stop_script"], "stop_script")
    if (
        stop_script.exists()
        and stop_script.is_file()
        and os.access(stop_script, os.X_OK)
        and not script_is_placeholder(stop_script)
    ):
        stop_log = resolve_project_relative(project, life["stop_log"], "stop_log")
        stop_log.parent.mkdir(parents=True, exist_ok=True)
        with stop_log.open("a", encoding="utf-8") as log:
            result = subprocess.run(
                [str(stop_script)],
                cwd=project,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        life["last_exit_code"] = result.returncode


def terminate_lifecycle_processes(item):
    life = lifecycle(item)
    pid = life.get("pid")
    pgid = life.get("pgid")
    live_pid = pid_alive(pid)
    live_group = False
    tracked_pids = []
    try:
        pgid_int = int(pgid)
    except (TypeError, ValueError):
        pgid_int = None
    if pgid_int is not None and pgid_int > 0:
        tracked_pids = process_group_pids(pgid_int)
        live_group = any(pid_alive(value) for value in tracked_pids)
    if live_pid:
        try:
            pid_int = int(pid)
            if pid_int not in tracked_pids:
                tracked_pids.append(pid_int)
        except (TypeError, ValueError):
            pass
    if pgid_int is not None and pgid_int > 0 and (live_pid or live_group):
        try:
            os.killpg(pgid_int, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if tracked_pids and not wait_for_processes_to_exit(tracked_pids, 5.0):
            try:
                os.killpg(pgid_int, signal.SIGKILL)
            except ProcessLookupError:
                pass
            wait_for_processes_to_exit(tracked_pids, 5.0)
    life["pid"] = None
    life["pgid"] = None
    life["stopped_at"] = utc_now()


def activate_item(item, project, range_id, tailnet_arg):
    recorded_mode = item.get("tailnet", {}).get("mode")
    mode = tailnet_arg if tailnet_arg is not None else recorded_mode
    if mode is None:
        raise DomainError(
            "needs_input", exit_code=3, payload=needs_tailnet(project, range_id)
        )
    item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    item["tailnet"]["mode"] = mode
    now = utc_now()
    if mode in ("serve", "funnel"):
        ensure_tailscale_authenticated(mode)
        port = item["start"]
        run_tailnet(mode, port, off=False)
        item["tailnet"]["port"] = port
        item["tailnet"]["configured_at"] = now
    else:
        item["tailnet"]["port"] = None
        item["tailnet"]["configured_at"] = None
    mark_item_active(item, now)


def mark_item_active(item, now=None):
    """Apply the shared reserved-to-active lifecycle transition."""
    now = now or utc_now()
    item["state"] = "active"
    item["activated_at"] = now


def release_item(item):
    tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    mode = tailnet.get("mode")
    # Only tear down Serve/Funnel that was actually configured (login may have skipped it).
    if item.get("state") == "active" and mode in ("serve", "funnel") and tailnet.get("configured_at"):
        port = tailnet.get("port") or item["start"]
        run_tailnet(mode, port, off=True)
    item["state"] = "released"
    item["released_at"] = utc_now()
    tailnet["configured_at"] = None


def range_has_live_process(item):
    life = lifecycle(item)
    if pid_alive(life.get("pid")):
        return True
    pgid = life.get("pgid")
    if pgid is None:
        return False
    return any(pid_alive(pid) for pid in process_group_pids(pgid))


def read_listen_port_from_disk():
    """Return Portskill listen port from sticky listen.json, or None."""
    data = read_listen()
    if data is None:
        return None
    port = data.get("port")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None
def resolve_stop_also_release(registry_or_settings=None, override=None) -> bool:
    """Whether stop should also release. override True/False wins; else the setting (default true)."""
    if override is not None:
        return bool(override)
    raw = {}
    if isinstance(registry_or_settings, dict):
        if "projects" in registry_or_settings or "pool" in registry_or_settings:
            inner = registry_or_settings.get("settings")
            raw = inner if isinstance(inner, dict) else {}
        else:
            raw = registry_or_settings
    return bool(normalize_settings(raw).get("stop_also_release", True))


def park_item_for_exit(item, project, also_release=False):
    """Stop.sh + tracked pgid terminate + tailnet off.

    When also_release is false, the range stays reserved (deactivate / Stop
    without Release). When true, fully release via release_item.
    """
    if item.get("state") == "released":
        return True, {"action": "already_released", "range": item}
    life = lifecycle(item)
    has_live = range_has_live_process(item)
    if has_live:
        run_stop_script_if_ready(item, project)
        terminate_lifecycle_processes(item)
    else:
        life["pid"] = None
        life["pgid"] = None
        if not life.get("stopped_at"):
            life["stopped_at"] = utc_now()
    if also_release:
        try:
            release_item(item)
        except DomainError:
            return False, {"reason": "release_failed", "range_id": item.get("id")}
        return True, {"action": "stopped_released", "range": item}
    # Tear down tailnet but keep allocation reserved
    tailnet = item.setdefault("tailnet", {"mode": None, "port": None, "configured_at": None})
    mode = tailnet.get("mode")
    if item.get("state") == "active" and mode in ("serve", "funnel"):
        port = tailnet.get("port") or item["start"]
        try:
            run_tailnet(mode, port, off=True)
        except DomainError:
            return False, {"reason": "tailnet_teardown_failed", "range_id": item.get("id")}
        tailnet["configured_at"] = None
    item["state"] = "reserved"
    return True, {"action": "stopped_reserved", "range": item}
