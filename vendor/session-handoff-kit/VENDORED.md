# Vendored Session Handoff Kit

Vendored from Ben's Session Handoff Kit at kit SHA `a57e23a` (0.11.0, 2026-09-27), upstream verbatim. The previous copy (0.10.1 @ `0a201cf`) is in Portskill's git history; the 0.7.0 copy is backed up at `~/Development/_backups/session-handoff-kit-0.7.0-20260927/`.
The kit includes hooks, a ledger, plugins, agent skills, and a Chrome extension.
Its README and SPEC describe kit behavior; `scripts/package.sh` builds distribution artifacts.

Layout since 0.11.0: `skills/session-handoff/` is the single source (SKILL.md, template, `hooks/`, and `install.py`, which registers the hooks for Claude Code and Codex). `plugins/session-handoff/` symlinks it; the old `codex/` mirror is gone. Portskill's Codex install action copies that folder into `$CODEX_HOME/skills/` and runs its `install.py codex`.

In Portskill this integration is **Experimental (Beta)** and disabled by default.
The earlier Portskill-only edits (condensed skill docs, removed internal
checkpoint) were not reapplied; they remain in the 0.7.0 backup and in
Portskill's git history.
