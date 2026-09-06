"""Session Handoff product surface — vendored kit + ledger wrappers.

The kit lives at vendor/session-handoff-kit/ (tracked source). Optional override:
settings.handoff_kit or env PORTSKILL_HANDOFF_KIT. Ledger writes go only through
the vendored handoff_ledger.py CLI. Stdlib only. Loopback / stdio — no Funnel.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR_KIT = PACKAGE_ROOT / "vendor" / "session-handoff-kit"

HANDOFF_TOOL_NAMES = (
    "handoff_status",
    "handoff_skill",
    "handoff_template",
    "handoff_list",
    "handoff_resolve",
    "handoff_new_path",
    "handoff_resume",
    "handoff_supersede",
    "handoff_install_help",
)

LEDGER_TOOL_NAMES = frozenset(
    {
        "handoff_list",
        "handoff_resolve",
        "handoff_new_path",
        "handoff_resume",
        "handoff_supersede",
    }
)

SKILL_REL = pathlib.Path("codex") / "skills" / "session-handoff" / "SKILL.md"
SKILL_REL_PLUGIN = (
    pathlib.Path("plugins") / "session-handoff" / "skills" / "session-handoff" / "SKILL.md"
)
TEMPLATE_REL = pathlib.Path("codex") / "skills" / "session-handoff" / "handoff-template.md"
LEDGER_REL = pathlib.Path("codex") / "hooks" / "handoff_ledger.py"
PACKAGE_SH_REL = pathlib.Path("scripts") / "package.sh"
HANDOFF_SKILL_FILENAME = "handoff-skill.md"
HANDOFF_SKILL_MAX_BYTES = 256 * 1024


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _looks_like_kit(root: pathlib.Path) -> bool:
    if not root.is_dir():
        return False
    readme = root / "README.md"
    ledger = root / LEDGER_REL
    ledger_plugin = root / "plugins" / "session-handoff" / "hooks" / "handoff_ledger.py"
    return readme.is_file() and (ledger.is_file() or ledger_plugin.is_file())


def configured_kit_override(settings: dict | None = None) -> str:
    env = (os.environ.get("PORTSKILL_HANDOFF_KIT") or "").strip()
    if env:
        return env
    if isinstance(settings, dict):
        raw = settings.get("handoff_kit")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def kit_root(settings: dict | None = None) -> pathlib.Path:
    override = configured_kit_override(settings)
    if override:
        return pathlib.Path(override).expanduser()
    return VENDOR_KIT


def kit_present(settings: dict | None = None) -> bool:
    return _looks_like_kit(kit_root(settings))


def kit_error(settings: dict | None = None) -> str:
    override = configured_kit_override(settings)
    if override:
        path = pathlib.Path(override).expanduser()
        if not _looks_like_kit(path):
            return f"handoff kit path is not a Session Handoff checkout: {path}"
        return ""
    if _looks_like_kit(VENDOR_KIT):
        return ""
    return "vendored Session Handoff kit missing (vendor/session-handoff-kit)"


def _registry_parent() -> pathlib.Path:
    """Directory of registry.json (same convention as listen.json / http_auth.json)."""
    configured = os.environ.get("PORT_REGISTRY_PATH", "~/.config/port-registry/registry.json")
    return pathlib.Path(configured).expanduser().parent


def default_custom_skill_path() -> pathlib.Path:
    return _registry_parent() / HANDOFF_SKILL_FILENAME


def configured_skill_override(settings: dict | None = None) -> str:
    if isinstance(settings, dict):
        raw = settings.get("handoff_skill")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def kit_skill_path(settings: dict | None = None) -> pathlib.Path | None:
    """Bundled Write-a-Handoff SKILL.md from the vendored kit (or kit override)."""
    root = kit_root(settings)
    for rel in (SKILL_REL, SKILL_REL_PLUGIN):
        path = root / rel
        if path.is_file():
            return path
    return None


def skill_path(settings: dict | None = None) -> pathlib.Path | None:
    override = configured_skill_override(settings)
    if override:
        path = pathlib.Path(override).expanduser()
        return path if path.is_file() else None
    return kit_skill_path(settings)


def skill_source(settings: dict | None = None) -> str:
    override = configured_skill_override(settings)
    if override:
        return "custom"
    return "vendored"


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def persist_custom_skill(text: str) -> dict:
    """Write a replacement Write-a-Handoff skill beside the registry."""
    if not isinstance(text, str):
        return {"ok": False, "error": "invalid_skill", "message": "expected skill text"}
    payload = text.replace("\r\n", "\n")
    if not payload.strip():
        return {"ok": False, "error": "invalid_skill", "message": "skill file is empty"}
    encoded = payload.encode("utf-8")
    if len(encoded) > HANDOFF_SKILL_MAX_BYTES:
        return {
            "ok": False,
            "error": "invalid_skill",
            "message": f"skill file exceeds {HANDOFF_SKILL_MAX_BYTES} bytes",
        }
    path = default_custom_skill_path()
    try:
        _atomic_write_text(path, payload if payload.endswith("\n") else payload + "\n")
    except OSError as exc:
        return {"ok": False, "error": "skill_write_failed", "message": str(exc)}
    return {"ok": True, "path": str(path), "source": "custom"}


def bundled_skill_text(settings: dict | None = None) -> dict:
    path = kit_skill_path(settings)
    if path is None:
        return {"ok": False, "error": "skill_missing", "message": "bundled SKILL.md not found in kit"}
    return {
        "ok": True,
        "path": str(path),
        "text": _read_text(path),
        "filename": "SKILL.md",
        "source": "vendored",
    }


def template_path(settings: dict | None = None) -> pathlib.Path | None:
    root = kit_root(settings)
    for rel in (
        TEMPLATE_REL,
        pathlib.Path("plugins") / "session-handoff" / "skills" / "session-handoff" / "handoff-template.md",
    ):
        path = root / rel
        if path.is_file():
            return path
    return None


def ledger_script(settings: dict | None = None) -> pathlib.Path | None:
    root = kit_root(settings)
    for rel in (
        LEDGER_REL,
        pathlib.Path("plugins") / "session-handoff" / "hooks" / "handoff_ledger.py",
    ):
        path = root / rel
        if path.is_file():
            return path
    return None


def package_script(settings: dict | None = None) -> pathlib.Path | None:
    path = kit_root(settings) / PACKAGE_SH_REL
    return path if path.is_file() else None


def codex_install_script(settings: dict | None = None) -> pathlib.Path | None:
    path = kit_root(settings) / "codex" / "install.sh"
    return path if path.is_file() else None


def chrome_extension_dir(settings: dict | None = None) -> pathlib.Path | None:
    path = kit_root(settings) / "chrome-extension"
    return path if (path / "manifest.json").is_file() else None


def doctor_handoff(settings: dict | None = None) -> tuple[dict, dict]:
    """Doctor check + top-level payload. Hard-fail when kit missing or override invalid."""
    override = configured_kit_override(settings)
    present = kit_present(settings)
    err = kit_error(settings)
    enabled = _as_bool((settings or {}).get("handoff_enabled"), False)
    configured = bool(override) or enabled
    info = {
        "present": present,
        "configured": configured,
        "enabled": enabled,
        "path": str(kit_root(settings)),
        "source": "override" if override else "vendored",
        "override": override or None,
        "error": err or None,
    }
    if not present:
        detail = err or "Session Handoff kit missing or override is not a kit"
        check = {"name": "handoff_kit", "ok": False, "detail": detail}
        return check, info
    bits = [
        f"present={info['path']}",
        f"source={info['source']}",
        f"configured={'yes' if configured else 'no'}",
        f"enabled={'on' if enabled else 'off'}",
    ]
    check = {"name": "handoff_kit", "ok": True, "detail": "; ".join(bits)}
    return check, info


def _read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _home() -> pathlib.Path:
    return pathlib.Path.home()


def _readable_file(path: pathlib.Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def _claude_code_probe() -> dict:
    home = _home()
    hits: list[str] = []
    candidates = [
        home / ".claude" / "skills" / "session-handoff" / "SKILL.md",
        home / ".claude" / "plugins" / "session-handoff" / ".claude-plugin" / "plugin.json",
        home
        / ".claude"
        / "plugins"
        / "session-handoff"
        / "skills"
        / "session-handoff"
        / "SKILL.md",
    ]
    plugins = home / ".claude" / "plugins"
    if plugins.is_dir():
        try:
            for child in plugins.iterdir():
                if not child.is_dir():
                    continue
                for extra in (
                    child / "session-handoff" / ".claude-plugin" / "plugin.json",
                    child / ".claude-plugin" / "plugin.json",
                ):
                    if extra.name == "plugin.json" and extra.parent.parent.name in (
                        "session-handoff",
                        child.name,
                    ):
                        candidates.append(extra)
        except OSError:
            pass
    for path in candidates:
        if _readable_file(path):
            hits.append(str(path))
    installed = bool(hits)
    return {
        "id": "claude_code",
        "label": "Claude Code",
        "installed": installed,
        "detectable": True,
        "paths": hits,
        "how": (
            "/plugin marketplace add <kit> then "
            "/plugin install session-handoff@session-handoff-kit"
        ),
    }


def _codex_probe() -> dict:
    home = _home()
    skill = home / ".codex" / "skills" / "session-handoff" / "SKILL.md"
    hooks = home / ".codex" / "hooks.json"
    hits: list[str] = []
    hooks_ok = False
    if _readable_file(skill):
        hits.append(str(skill))
    if _readable_file(hooks):
        try:
            text = hooks.read_text(encoding="utf-8")
        except OSError:
            text = ""
        if "context_watch" in text or "handoff_ledger" in text or "session-handoff" in text:
            hooks_ok = True
            hits.append(str(hooks))
    return {
        "id": "codex",
        "label": "Codex CLI",
        "installed": bool(hits),
        "detectable": True,
        "hooks_merged": hooks_ok,
        "paths": hits,
        "how": "bash <kit>/codex/install.sh  (then enable hooks in ~/.codex/config.toml)",
    }


def _artifact_probe(settings: dict | None, filename: str, surface_id: str, label: str, how: str) -> dict:
    root = kit_root(settings)
    artifact = root / "dist" / filename
    packaged = _readable_file(artifact)
    return {
        "id": surface_id,
        "label": label,
        "installed": False,
        "detectable": False,
        "packaged": packaged,
        "paths": [str(artifact)] if packaged else [],
        "how": how,
        "note": (
            "Account install cannot be detected from this machine. "
            "Run package.sh, then open the artifact."
            if not packaged
            else "Artifact ready — open it on that surface to install."
        ),
    }


def _chrome_probe(settings: dict | None) -> dict:
    ext = kit_root(settings) / "chrome-extension"
    present = (ext / "manifest.json").is_file()
    return {
        "id": "chrome",
        "label": "Chrome / Edge extension",
        "installed": False,
        "detectable": False,
        "packaged": present,
        "paths": [str(ext)] if present else [],
        "how": (
            "chrome://extensions → Developer mode → Load unpacked → "
            "select chrome-extension/"
        ),
        "note": "Browser extension install cannot be detected from Portskill.",
    }


def _surface_manage(settings: dict | None = None) -> dict[str, dict]:
    """Add/manage actions for the kit README install matrix (not Coming soon)."""
    root = kit_root(settings)
    claude_copy = (
        f"/plugin marketplace add {root}\n"
        "/plugin install session-handoff@session-handoff-kit"
    )
    chrome = chrome_extension_dir(settings)
    chrome_path = str(chrome) if chrome else str(root / "chrome-extension")
    return {
        "claude_code": {
            "action": "handoff-copy",
            "button": "Copy add commands",
            "copy": claude_copy,
            "honesty": (
                "Portskill cannot run /plugin. Paste these in Claude Code "
                "(CLI / VS Code / JetBrains)."
            ),
        },
        "cowork": {
            "action": "handoff-package",
            "button": "Package plugin",
            "honesty": (
                "Runs scripts/package.sh, then open dist/session-handoff.plugin "
                "in a Cowork conversation and click install."
            ),
        },
        "codex": {
            "action": "handoff-codex-install",
            "button": "Run install.sh",
            "honesty": (
                "Writes $CODEX_HOME (default ~/.codex): hooks + skill + hooks.json merge. "
                "Still enable [features] hooks = true in config.toml and approve hook trust. "
                "Portskill does not edit config.toml."
            ),
        },
        "chat": {
            "action": "handoff-package",
            "button": "Package skill",
            "honesty": (
                "Runs scripts/package.sh, then Save skill on "
                "dist/session-handoff-chat.skill (chat / Desktop / mobile)."
            ),
        },
        "chrome": {
            "action": "handoff-copy",
            "button": "Copy extension path",
            "copy": chrome_path,
            "honesty": (
                "chrome://extensions → Developer mode → Load unpacked → this folder. "
                "Portskill cannot load the extension into Chrome."
            ),
        },
    }


def install_matrix(settings: dict | None = None) -> list[dict]:
    root = str(kit_root(settings))
    manages = _surface_manage(settings)
    rows = [
        _claude_code_probe(),
        _artifact_probe(
            settings,
            "session-handoff.plugin",
            "cowork",
            "Claude Cowork",
            f"bash {root}/scripts/package.sh  then open dist/session-handoff.plugin",
        ),
        _codex_probe(),
        _artifact_probe(
            settings,
            "session-handoff-chat.skill",
            "chat",
            "Claude chat / Desktop",
            f"bash {root}/scripts/package.sh  then Save skill on session-handoff-chat.skill",
        ),
        _chrome_probe(settings),
    ]
    for item in rows:
        manage = manages.get(str(item.get("id") or ""))
        if manage:
            item["manage"] = manage
    return rows


def run_ledger(argv: list[str], settings: dict | None = None) -> tuple[int, Any, str, str]:
    """Invoke vendored handoff_ledger.py. Returns (code, parsed_json_or_None, stdout, stderr)."""
    script = ledger_script(settings)
    if script is None:
        return (
            2,
            {
                "ok": False,
                "error": "kit_missing",
                "message": kit_error(settings) or "handoff ledger script not found",
            },
            "",
            "",
        )
    cmd = [sys.executable, str(script), *argv]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return 2, {"ok": False, "error": "ledger_exec_failed", "message": str(exc)}, "", str(exc)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload: Any = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
    return completed.returncode, payload, stdout, stderr


def open_handoff_count(project_dir: str | None = None, settings: dict | None = None) -> dict:
    root = project_dir or os.getcwd()
    code, payload, stdout, stderr = run_ledger(
        ["list", str(root), "--json"],
        settings=settings,
    )
    if code != 0:
        return {
            "ok": False,
            "count": None,
            "project": root,
            "error": (payload or {}).get("message") if isinstance(payload, dict) else (stderr or stdout or f"exit {code}"),
        }
    items = payload if isinstance(payload, list) else []
    return {"ok": True, "count": len(items), "project": root, "handoffs": items}


def install_help_text(settings: dict | None = None) -> str:
    root = kit_root(settings)
    return (
        "Session Handoff Kit — install help\n"
        f"Kit: {root}\n"
        "\n"
        "Claude Code (CLI / VS Code / JetBrains)\n"
        f"  /plugin marketplace add {root}\n"
        "  /plugin install session-handoff@session-handoff-kit\n"
        "\n"
        "Claude Cowork\n"
        f"  bash {root / PACKAGE_SH_REL}\n"
        "  Open dist/session-handoff.plugin in a Cowork conversation and click install.\n"
        "\n"
        "Codex CLI\n"
        f"  bash {root / 'codex' / 'install.sh'}\n"
        "  Enable hooks in ~/.codex/config.toml ([features] hooks = true).\n"
        "\n"
        "Claude chat / Claude Desktop\n"
        f"  bash {root / PACKAGE_SH_REL}\n"
        "  Save dist/session-handoff-chat.skill (Settings → Capabilities), or upload the .skill.\n"
        "\n"
        "Chrome / Edge extension\n"
        "  chrome://extensions → Developer mode → Load unpacked →\n"
        f"  {root / 'chrome-extension'}\n"
        "\n"
        "Do not invent agent skill folders. Copy SKILL.md only into the skills "
        "directory your agent already documents (Claude Code plugin install and "
        "codex/install.sh are the supported paths).\n"
    )


def status_payload(project_dir: str | None = None, settings: dict | None = None) -> dict:
    root = kit_root(settings)
    err = kit_error(settings)
    present = kit_present(settings)
    skill = skill_path(settings)
    kit_skill = kit_skill_path(settings)
    ledger = ledger_script(settings)
    pkg = package_script(settings)
    skill_override = configured_skill_override(settings)
    opened = open_handoff_count(project_dir, settings) if ledger is not None else {
        "ok": False,
        "count": None,
        "error": err or "ledger unavailable",
    }
    return {
        "ok": present,
        "kit_path": str(root),
        "kit_source": "override" if configured_kit_override(settings) else "vendored",
        "installed": bool(skill and skill.is_file()),
        "skill_path": str(skill) if skill else None,
        "skill_source": skill_source(settings),
        "skill_override": skill_override or None,
        "bundled_skill_path": str(kit_skill) if kit_skill else None,
        "ledger_path": str(ledger) if ledger else None,
        "package_sh": str(pkg) if pkg else None,
        "error": err or None,
        "open_count": opened.get("count"),
        "open_count_ok": bool(opened.get("ok")),
        "open_count_error": opened.get("error"),
        "project": opened.get("project"),
        "install_matrix": install_matrix(settings),
        "handoff_enabled": _as_bool((settings or {}).get("handoff_enabled"), False),
        "skill_version": "0.7.0",
    }


def run_package_sh(settings: dict | None = None) -> dict:
    script = package_script(settings)
    if script is None:
        return {
            "ok": False,
            "error": "package_sh_missing",
            "message": "scripts/package.sh not found in the Session Handoff kit",
        }
    try:
        completed = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(script.parent.parent),
        )
    except OSError as exc:
        return {"ok": False, "error": "package_exec_failed", "message": str(exc)}
    out = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "output": out,
        "artifacts": [line for line in (completed.stdout or "").splitlines() if line.strip()],
        "handoff_notice": out or ("package.sh finished" if completed.returncode == 0 else "package.sh failed"),
    }


def run_codex_install(settings: dict | None = None, codex_home: str | None = None) -> dict:
    """Run the vendored codex/install.sh. Honors CODEX_HOME; does not edit config.toml."""
    script = codex_install_script(settings)
    if script is None:
        return {
            "ok": False,
            "error": "codex_install_missing",
            "message": "codex/install.sh not found in the Session Handoff kit",
        }
    env = os.environ.copy()
    home = (codex_home or env.get("CODEX_HOME") or str(_home() / ".codex")).strip()
    env["CODEX_HOME"] = home
    try:
        completed = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(script.parent),
            env=env,
        )
    except OSError as exc:
        return {"ok": False, "error": "codex_install_failed", "message": str(exc)}
    out = ((completed.stdout or "") + (completed.stderr or "")).strip()
    honesty = (
        "install.sh writes hooks + skill and merges hooks.json. "
        "Enable [features] hooks = true in config.toml and approve hook trust. "
        "Portskill does not edit config.toml."
    )
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "output": out,
        "codex_home": home,
        "honesty": honesty,
        "handoff_notice": (out + "\n\n" + honesty).strip() if out else honesty,
    }


def _tool_error(message: str, **extra: Any) -> dict:
    body = {"ok": False, "message": message}
    body.update(extra)
    return {
        "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
        "structuredContent": body,
        "isError": True,
    }


def _tool_ok(body: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(body, sort_keys=True)}],
        "structuredContent": body,
        "isError": False,
    }


def _project_dir(args: dict) -> str:
    raw = args.get("project") or args.get("dir") or args.get("directory")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return os.getcwd()


def call_handoff_tool(name: str, arguments: dict | None, settings: dict | None = None) -> dict:
    args = arguments or {}
    if name in LEDGER_TOOL_NAMES and ledger_script(settings) is None:
        return _tool_error(
            kit_error(settings) or "Session Handoff kit path is unset or ledger is missing",
            error="kit_missing",
        )
    if name == "handoff_status":
        body = status_payload(_project_dir(args), settings)
        return _tool_ok(body)
    if name == "handoff_skill":
        path = skill_path(settings)
        if path is None:
            return _tool_error("SKILL.md not found in kit", error="skill_missing")
        text = _read_text(path)
        return _tool_ok({"ok": True, "path": str(path), "text": text})
    if name == "handoff_template":
        path = template_path(settings)
        if path is None:
            return _tool_error("handoff-template.md not found in kit", error="template_missing")
        return _tool_ok({"ok": True, "path": str(path), "text": _read_text(path)})
    if name == "handoff_install_help":
        return _tool_ok({"ok": True, "text": install_help_text(settings), "matrix": install_matrix(settings)})
    if name == "handoff_list":
        argv = ["list", _project_dir(args), "--json"]
        if args.get("max_age_days") is not None:
            argv += ["--max-age-days", str(int(args["max_age_days"]))]
        code, payload, stdout, stderr = run_ledger(argv, settings)
        if code != 0:
            return _tool_error(stderr or stdout or f"ledger list exit {code}", error="ledger_failed")
        items = payload if isinstance(payload, list) else []
        return _tool_ok({"ok": True, "handoffs": items, "count": len(items)})
    if name == "handoff_resolve":
        topic = args.get("topic") or args.get("path")
        if not isinstance(topic, str) or not topic.strip():
            return _tool_error("expected topic or path", error="invalid_args")
        argv = ["resolve", topic.strip(), _project_dir(args), "--json"]
        code, payload, stdout, stderr = run_ledger(argv, settings)
        if code != 0:
            return _tool_error(stderr or stdout or f"ledger resolve exit {code}", error="ledger_failed")
        body = payload if isinstance(payload, dict) else {"raw": stdout}
        body = dict(body)
        body["ok"] = True
        return _tool_ok(body)
    if name == "handoff_new_path":
        topic = args.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            return _tool_error("expected topic", error="invalid_args")
        argv = ["new-path", topic.strip(), _project_dir(args), "--json"]
        code, payload, stdout, stderr = run_ledger(argv, settings)
        if code != 0:
            return _tool_error(stderr or stdout or f"ledger new-path exit {code}", error="ledger_failed")
        body = payload if isinstance(payload, dict) else {"raw": stdout}
        body = dict(body)
        body["ok"] = True
        return _tool_ok(body)
    if name == "handoff_resume":
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return _tool_error("expected path", error="invalid_args")
        code, _payload, stdout, stderr = run_ledger(["resume", path.strip()], settings)
        if code != 0:
            return _tool_error(stderr or stdout or f"ledger resume exit {code}", error="ledger_failed")
        return _tool_ok({"ok": True, "message": stdout})
    if name == "handoff_supersede":
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return _tool_error("expected path", error="invalid_args")
        argv = ["supersede", path.strip()]
        by_path = args.get("by")
        if isinstance(by_path, str) and by_path.strip():
            argv += ["--by", by_path.strip()]
        code, _payload, stdout, stderr = run_ledger(argv, settings)
        if code != 0:
            return _tool_error(stderr or stdout or f"ledger supersede exit {code}", error="ledger_failed")
        return _tool_ok({"ok": True, "message": stdout})
    return _tool_error(f"unknown handoff tool: {name}", error="unknown_tool")


HANDOFF_TOOL_DEFS = [
    {
        "name": "handoff_status",
        "description": (
            "Session Handoff kit status: vendored/override path, skill readable, "
            "open-handoff count, install matrix (Claude Code / Cowork / Codex / Chat / Chrome)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project dir for ledger list (default cwd)"},
            },
        },
    },
    {
        "name": "handoff_skill",
        "description": (
            "Return Write-a-Handoff SKILL.md text from the custom file under "
            "~/.config/port-registry/ when set, else the vendored kit (or kit override)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "handoff_template",
        "description": "Return handoff-template.md from the vendored kit.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "handoff_list",
        "description": "Wrap vendored handoff_ledger.py list --json (open handoffs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project directory (default cwd)"},
                "max_age_days": {"type": "integer", "minimum": 0},
            },
        },
    },
    {
        "name": "handoff_resolve",
        "description": "Wrap vendored handoff_ledger.py resolve <topic-or-path> --json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic name or handoff file path"},
                "path": {"type": "string", "description": "Alias of topic when it is a path"},
                "project": {"type": "string"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "handoff_new_path",
        "description": "Wrap vendored handoff_ledger.py new-path (deterministic path + created timestamp). Does not write a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "handoff_resume",
        "description": "Wrap vendored handoff_ledger.py resume <path> (marks status resumed).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "handoff_supersede",
        "description": "Wrap vendored handoff_ledger.py supersede <path> [--by <new-path>].",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "by": {"type": "string", "description": "Optional newer handoff path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "handoff_install_help",
        "description": "Install matrix + commands for Claude Code, Cowork, Codex, chat skill, and Chrome extension.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
