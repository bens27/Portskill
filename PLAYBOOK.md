# Friend share — Portskill

One short path for a trusted person. Ben does not need to be on the call. Remotes stay **HOLD**. This is **not** an App Store or notarized build.

## Pick a path

### A. AirDrop or zip (right-click Open)

1. Receive a **trusted** copy (AirDrop, zip from Ben). Unzip if needed.
2. If `dist/Portskill.app` is already in the folder: **right-click Portskill → Open** (then Open again in the Gatekeeper sheet). That is the unsigned zip fallback.
3. If there is no `.app` yet, use path B.

### B. Clone + `./scripts/install-mac.sh` (quarantine strip)

```bash
git clone https://github.com/bens27/Portskill.git
cd Portskill
./scripts/install-mac.sh          # → /Applications  (or --user for ~/Applications)
open /Applications/Portskill.app
```

The installer builds or reuses `dist/Portskill.app`, copies it to Applications, and runs `xattr -dr com.apple.quarantine`. Optional: `./scripts/install-mac.sh --keepalive`.

## After it opens

The listen port is sticky — do **not** assume `:8765`. Read:

```bash
python3 -c "import json,pathlib; print(json.load(open(pathlib.Path.home()/'.config/port-registry/listen.json'))['ui_url'])"
```

Loopback only. Prefer stdio MCP for agents. Remotes are HOLD (not implemented).

## Honesty (do not skip)

See **[SECURITY.md](SECURITY.md)** and the Gatekeeper section in **[README.md](README.md)**.

- Friend builds are **not** notarized unless someone actually ran `./scripts/notarize-mac.sh` with a Developer ID and Apple credentials.
- Ad-hoc codesign (`codesign --sign -`) ≠ notarized. `install-mac.sh` does **not** notarize.
- Prefer the installer after a **trusted local build** — it strips `com.apple.quarantine`. Right-click → Open remains the zip fallback.
- Do not Funnel the Portskill listen/UI port.

This playbook does **not** claim App Store Connect, notarization, or a public friend build.
