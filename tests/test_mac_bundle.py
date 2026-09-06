"""Mac bundle replace + Finder-junk strip (stdlib; no Darwin required)."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "mac-bundle.sh"


def _bash(
    script: str,
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        env=run_env,
    )


def _make_app(root: pathlib.Path, body: str = "bin\n") -> pathlib.Path:
    macos = root / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "Portskill").write_text(body, encoding="utf-8")
    return root


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
            if rel == "scripts/mac-bundle.sh":
                self.assertIn("flock", text)
                self.assertIn("with_install_lock", text)
                self.assertIn("purge_bundle_root_residue", text)
                self.assertIn("bundle_stage_path", text)
            if rel == "scripts/install-mac.sh":
                self.assertIn("with_install_lock", text)

    def test_stage_path_is_sibling_never_inside_dest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-stage-") as tmp:
            dest = pathlib.Path(tmp) / "Applications" / "Portskill.app"
            dest.mkdir(parents=True)
            slashed = str(dest) + "/"
            proc = _bash(
                f"""
set -euo pipefail
. "{HELPER}"
p="$(bundle_stage_path "{dest}")"
p2="$(bundle_stage_path "{slashed}")"
printf '%s\\n%s\\n' "$p" "$p2"
case "$p" in
  "{dest}"/*) echo "stage inside dest" >&2; exit 1 ;;
esac
test "$(dirname "$p")" = "{dest.parent}"
test "$(dirname "$p2")" = "{dest.parent}"
test "$(basename "$p")" = ".Portskill.app.new.$$"
"""
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            lines = [ln for ln in proc.stdout.splitlines() if ln]
            self.assertEqual(len(lines), 2)
            for stage in lines:
                stage_path = pathlib.Path(stage)
                self.assertEqual(stage_path.parent, dest.parent)
                self.assertTrue(stage_path.name.startswith(".Portskill.app.new."))
                self.assertFalse(str(stage_path).startswith(str(dest) + "/"))

    def test_stage_is_sibling_during_replace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-sib-") as tmp:
            tmp_path = pathlib.Path(tmp)
            src = _make_app(tmp_path / "src" / "Portskill.app", "new-bin\n")
            dest = _make_app(tmp_path / "dest" / "Portskill.app", "old-bin\n")
            lock = tmp_path / "install.lock"
            seen = tmp_path / "seen.txt"
            script = f"""
set -euo pipefail
export PORTSKILL_INSTALL_LOCK="{lock}"
export PORTSKILL_TEST_REPLACE_SLEEP=0.6
. "{HELPER}"
replace_app_bundle "{src}" "{dest}"
"""
            watcher = f"""
set -euo pipefail
for i in $(seq 1 40); do
  for p in "{dest.parent}"/.Portskill.app.new.*; do
    if [ -d "$p" ]; then
      echo "$p" > "{seen}"
      if [ -d "{dest}/$(basename "$p")" ]; then
        echo "nested" >> "{seen}"
        exit 1
      fi
      exit 0
    fi
  done
  sleep 0.05
done
echo "timeout" > "{seen}"
exit 1
"""
            watch = subprocess.Popen(["bash", "-c", watcher])
            time.sleep(0.05)
            proc = _bash(script)
            watch.wait(timeout=8)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertEqual(watch.returncode, 0, seen.read_text() if seen.exists() else "no seen")
            reported = seen.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(pathlib.Path(reported).parent, dest.parent)
            self.assertFalse((dest / pathlib.Path(reported).name).exists())
            self.assertTrue((dest / "Contents" / "MacOS" / "Portskill").is_file())

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
            lock = tmp_path / "install.lock"

            script = f"""
set -euo pipefail
export PORTSKILL_INSTALL_LOCK="{lock}"
. "{HELPER}"
replace_app_bundle "{src}" "{dest}"
replace_app_bundle "{src}" "{dest}"
test -d "{dest}"
test ! -e "{leftover}"
test "$(cat "{dest}/Contents/MacOS/Portskill")" = "new-bin"
test ! -d "{dest}/Portskill.app"
test ! -d "{dest}/.Portskill.app.new.99999"
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
            self.assertFalse(any(dest.glob(".*.new.*")))

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

    def test_nested_stage_residue_is_purged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-nested-") as tmp:
            app = pathlib.Path(tmp) / "Portskill.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "MacOS" / "Portskill").write_text("keep\n", encoding="utf-8")
            nested = app / ".Portskill.app.new.57474"
            (nested / "Contents").mkdir(parents=True)
            (nested / "stale").write_text("raced-mv\n", encoding="utf-8")
            junk = app / "extra-unsealed-dir"
            junk.mkdir()
            (app / "unsealed.txt").write_text("nope\n", encoding="utf-8")
            proc = _bash(
                f"""
set -euo pipefail
. "{HELPER}"
purge_bundle_root_residue "{app}"
test -d "{app}/Contents"
test ! -e "{nested}"
test ! -e "{junk}"
test ! -e "{app}/unsealed.txt"
test "$(cat "{app}/Contents/MacOS/Portskill")" = "keep"
strip_finder_junk "{app}"
"""
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertTrue((app / "Contents" / "MacOS" / "Portskill").is_file())
            self.assertFalse(nested.exists())
            self.assertFalse(junk.exists())
            names = {p.name for p in app.iterdir()}
            self.assertEqual(names, {"Contents"})

    def test_replace_strips_preexisting_nested_stage_before_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-pre-") as tmp:
            tmp_path = pathlib.Path(tmp)
            src = _make_app(tmp_path / "src" / "Portskill.app", "new-bin\n")
            dest = _make_app(tmp_path / "dest" / "Portskill.app", "old-bin\n")
            nested = dest / ".Portskill.app.new.57474"
            (nested / "Contents").mkdir(parents=True)
            (nested / "stale").write_text("raced\n", encoding="utf-8")
            lock = tmp_path / "install.lock"
            proc = _bash(
                f"""
set -euo pipefail
export PORTSKILL_INSTALL_LOCK="{lock}"
. "{HELPER}"
replace_app_bundle "{src}" "{dest}"
test ! -e "{nested}"
test "$(cat "{dest}/Contents/MacOS/Portskill")" = "new-bin"
"""
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertFalse(nested.exists())
            self.assertFalse(any(dest.glob(".*.new.*")))
            self.assertEqual(
                (dest / "Contents" / "MacOS" / "Portskill").read_text(encoding="utf-8"),
                "new-bin\n",
            )

    def test_flock_serializes_overlapping_replaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-flock-") as tmp:
            tmp_path = pathlib.Path(tmp)
            src_a = _make_app(tmp_path / "src-a" / "Portskill.app", "aaa\n")
            src_b = _make_app(tmp_path / "src-b" / "Portskill.app", "bbb\n")
            dest = _make_app(tmp_path / "dest" / "Portskill.app", "old\n")
            lock = tmp_path / "install.lock"
            order = tmp_path / "order.log"
            env = os.environ.copy()
            env["PORTSKILL_INSTALL_LOCK"] = str(lock)
            env["PORTSKILL_TEST_REPLACE_SLEEP"] = "0.4"
            env["PORTSKILL_TEST_LOCK_LOG"] = str(order)

            def _launch(src: pathlib.Path, tag: str) -> subprocess.Popen[str]:
                run_env = env.copy()
                run_env["PORTSKILL_TEST_LOCK_TAG"] = tag
                script = f"""
set -euo pipefail
. "{HELPER}"
replace_app_bundle "{src}" "{dest}"
"""
                return subprocess.Popen(
                    ["bash", "-c", script],
                    cwd=str(ROOT),
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            a = _launch(src_a, "A")
            time.sleep(0.1)
            b = _launch(src_b, "B")
            out_a = a.communicate(timeout=15)
            out_b = b.communicate(timeout=15)
            self.assertEqual(a.returncode, 0, out_a[1] or out_a[0])
            self.assertEqual(b.returncode, 0, out_b[1] or out_b[0])
            lines = order.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 4, lines)
            # Exclusive lock: in/out are written inside replace (already holding flock).
            first, second = lines[0].split("-", 1)[0], lines[2].split("-", 1)[0]
            self.assertEqual(
                lines,
                [f"{first}-in", f"{first}-out", f"{second}-in", f"{second}-out"],
            )
            self.assertFalse(any(dest.glob(".*.new.*")))
            self.assertFalse((dest / "Portskill.app").exists())
            self.assertIn(
                (dest / "Contents" / "MacOS" / "Portskill").read_text(encoding="utf-8"),
                ("aaa\n", "bbb\n"),
            )

    def test_with_install_lock_serializes_without_replace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portskill-lockonly-") as tmp:
            tmp_path = pathlib.Path(tmp)
            lock = tmp_path / "install.lock"
            order = tmp_path / "order.log"
            env = os.environ.copy()
            env["PORTSKILL_INSTALL_LOCK"] = str(lock)

            def _launch(tag: str, sleep_s: str) -> subprocess.Popen[str]:
                script = f"""
set -euo pipefail
. "{HELPER}"
with_install_lock bash -c 'echo {tag}-in >> "{order}"; sleep {sleep_s}; echo {tag}-out >> "{order}"'
"""
                return subprocess.Popen(
                    ["bash", "-c", script],
                    cwd=str(ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            a = _launch("A", "0.4")
            time.sleep(0.08)
            b = _launch("B", "0")
            out_a = a.communicate(timeout=10)
            out_b = b.communicate(timeout=10)
            self.assertEqual(a.returncode, 0, out_a[1] or out_a[0])
            self.assertEqual(b.returncode, 0, out_b[1] or out_b[0])
            lines = order.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, ["A-in", "A-out", "B-in", "B-out"])


if __name__ == "__main__":
    unittest.main()
