"""Check an installed wheel outside the checkout using isolated configuration.

Run with the Python executable from a virtualenv containing the installed wheel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="portskill-installed-") as tmp:
        root = Path(tmp)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PORTSKILL_HANDOFF_KIT", None)
        env.update({
            "PORT_REGISTRY_TAILSCALE_BIN": str(root / "tailscale-not-installed"),
            "PORT_REGISTRY_PATH": str(root / "registry.json"),
            "PORTSKILL_LISTEN_PATH": str(root / "listen.json"),
            "PORTSKILL_HTTP_AUTH_PATH": str(root / "http_auth.json"),
            "PORTSKILL_HTTP_PASSKEY_PATH": str(root / "http_passkey.json"),
            "PORTSKILL_HTTP_SESSIONS_PATH": str(root / "http_sessions.json"),
        })
        def run(args: list[str], input: str | None = None) -> subprocess.CompletedProcess:
            result = subprocess.run([sys.executable, *args], cwd=root, env=env,
                                    input=input, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stderr or result.stdout)
            return result

        run(["-m", "port_registry_app", "--help"])
        doctor = json.loads(run(["-m", "port_registry_app.cli", "doctor"]).stdout)
        assert doctor["status"] == "ok", doctor
        assert not doctor["handoff_kit"]["enabled"], doctor
        listed = run(["-m", "port_registry_app", "--mcp-stdio"],
                     json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        tools = json.loads(listed.stdout)["result"]["tools"]
        assert any(t["name"] == "portskill" for t in tools)
        assert not any(t["name"].startswith("handoff_") for t in tools)
        run(["-c", "from pathlib import Path; import port_registry_app; "
                    "from port_registry_app.server import render_page, build_view; "
                    "assert (Path(port_registry_app.__file__).parent/'static'/'port-registry-preview.html').is_file(); "
                    "assert 'Experimental (Beta)' in render_page(build_view({}), tailscale={'chip': 'Not installed'})"])
        print("PASS: installed package, doctor, stdio MCP defaults, and dashboard assets")


if __name__ == "__main__":
    main()
