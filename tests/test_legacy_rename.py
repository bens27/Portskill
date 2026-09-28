"""Existing port-registry installs keep working after the Portskill rename."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class LegacyRenameTests(unittest.TestCase):
    def test_legacy_config_project_and_env_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-legacy-") as tmp:
            home = (pathlib.Path(tmp) / "home").resolve()
            old_cfg = home / ".config" / "port-registry"
            old_cfg.mkdir(parents=True)
            proj = (pathlib.Path(tmp) / "proj").resolve()
            (proj / ".port-registry").mkdir(parents=True)
            (proj / ".port-registry" / "start.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (proj / ".port-registry" / "stop.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (proj / ".port-registry.json").write_text("{}", encoding="utf-8")
            registry = {
                "pool": {"start": 41000, "end": 41999},
                "projects": {
                    str(proj): {
                        "ranges": [{
                            "id": "r1", "start": 41000, "end": 41000, "state": "reserved",
                            "lifecycle": {"start_script": ".port-registry/start.sh",
                                          "stop_script": ".port-registry/stop.sh",
                                          "start_log": ".port-registry/start.log",
                                          "stop_log": ".port-registry/stop.log"},
                        }]
                    }
                },
            }
            (old_cfg / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("PORTSKILL_", "PORT_REGISTRY_"))}
            env.update({
                "HOME": str(home),
                "PORT_REGISTRY_TAILSCALE_BIN": str(pathlib.Path(tmp) / "no-tailscale"),
            })
            code = (
                "import os, portskill.cli as c, json;"
                "print(json.dumps({'bin': os.environ.get('PORTSKILL_TAILSCALE_BIN'), 'reg': str(c.registry_path())}))"
            )
            out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=60, check=True)
            info = json.loads(out.stdout.strip().splitlines()[-1])
            self.assertTrue(info["bin"].endswith("no-tailscale"))
            self.assertTrue(info["reg"].endswith("/.config/portskill/registry.json"))
            new_cfg = home / ".config" / "portskill"
            self.assertTrue((new_cfg / "registry.json").is_file())
            self.assertTrue(old_cfg.is_symlink())

            env["PYTHONPATH"] = str(ROOT)
            status = subprocess.run([sys.executable, "-m", "portskill.cli", "status", "--project", str(proj)], cwd=tmp,
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(status.returncode, 0, status.stderr or status.stdout)
            rng = json.loads(status.stdout)["ranges"][0]
            self.assertEqual(rng["lifecycle"]["start_script"], ".portskill/start.sh")
            self.assertTrue((proj / ".portskill" / "start.sh").is_file())
            self.assertTrue((proj / ".port-registry").is_symlink())
            self.assertTrue((proj / ".portskill.json").is_file())
            # Old script paths still resolve through the symlink.
            self.assertTrue((proj / ".port-registry" / "start.sh").is_file())


if __name__ == "__main__":
    unittest.main()
