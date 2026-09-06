"""Mac bundle replace + Finder-junk strip (stdlib; no Darwin required)."""
from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "mac-bundle.sh"


def _bash(script: str, *, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
    )


class MacBundleHelperTests(unittest.TestCase):
    def test_scripts_source_helper_and_are_valid(self) -> None:
        self.assertTrue(HELPER.is_file())
        for rel in (
            "scripts/mac-bundle.sh",
            "scripts/install-mac.sh",
            "scripts/build-app.sh",
            "scripts/notarize-mac.sh",
        ):
            path = ROOT / rel
            text = path.read_text(encoding="utf-8")
            syn = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(syn.returncode, 0, f"{rel}: {syn.stderr or syn.stdout}")
            if rel != "scripts/mac-bundle.sh":
                self.assertIn("mac-bundle.sh", text)
            if rel in ("scripts/build-app.sh", "scripts/notarize-mac.sh"):
                self.assertIn("strip_finder_junk", text)
            if rel in ("scripts/install-mac.sh", "scripts/build-app.sh"):
                self.assertIn("replace_app_bundle", text)

    def test_replace_app_bundle_is_idempotent_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-bundle-") as tmp:
            tmp_path = pathlib.Path(tmp)
            src = tmp_path / "src" / "Portskill.app"
            dest = tmp_path / "dest" / "Portskill.app"
            (src / "Contents" / "MacOS").mkdir(parents=True)
            (src / "Contents" / "MacOS" / "Portskill").write_text("new-bin\n", encoding="utf-8")
            (dest / "Contents" / "MacOS").mkdir(parents=True)
            (dest / "Contents" / "MacOS" / "Portskill").write_text("old-bin\n", encoding="utf-8")
            leftover = dest.parent / ".Portskill.app.new.99999"
            leftover.mkdir()
            (leftover / "stale").write_text("stale\n", encoding="utf-8")

            script = f"""
set -euo pipefail
. "{HELPER}"
replace_app_bundle "{src}" "{dest}"
replace_app_bundle "{src}" "{dest}"
test -d "{dest}"
test ! -e "{leftover}"
test "$(cat "{dest}/Contents/MacOS/Portskill")" = "new-bin"
test ! -d "{dest}/Portskill.app"
"""
            proc = _bash(script)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertEqual(
                (dest / "Contents" / "MacOS" / "Portskill").read_text(encoding="utf-8"),
                "new-bin\n",
            )
            self.assertFalse(leftover.exists())
            nested = dest / "Portskill.app"
            self.assertFalse(nested.exists(), "cp -R into existing dest must not nest the bundle")

    def test_remove_tree_verified_clears_nonempty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-rm-") as tmp:
            target = pathlib.Path(tmp) / "Portskill.app"
            (target / "Contents").mkdir(parents=True)
            (target / "Contents" / "keep").write_text("x\n", encoding="utf-8")
            proc = _bash(
                f"""
set -euo pipefail
. "{HELPER}"
remove_tree_verified "{target}"
test ! -e "{target}"
remove_tree_verified "{target}"
"""
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertFalse(target.exists())

    def test_strip_finder_junk_removes_ds_store(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-ds-") as tmp:
            app = pathlib.Path(tmp) / "Portskill.app"
            (app / "Contents" / "Resources").mkdir(parents=True)
            (app / ".DS_Store").write_bytes(b"junk")
            (app / "Contents" / ".DS_Store").write_bytes(b"junk")
            (app / "Contents" / "._Icon").write_bytes(b"fork")
            (app / "Contents" / "Resources" / "keep.txt").write_text("ok\n", encoding="utf-8")
            proc = _bash(
                f"""
set -euo pipefail
. "{HELPER}"
strip_finder_junk "{app}"
test ! -e "{app}/.DS_Store"
test ! -e "{app}/Contents/.DS_Store"
test ! -e "{app}/Contents/._Icon"
test -f "{app}/Contents/Resources/keep.txt"
"""
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertFalse((app / ".DS_Store").exists())
            self.assertTrue((app / "Contents" / "Resources" / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
