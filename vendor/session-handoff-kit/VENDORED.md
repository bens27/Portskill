# Vendored Session Handoff Kit

Vendored from Ben's Session Handoff Kit at kit SHA `7587834`.
The kit includes hooks, a ledger, plugins, agent skills, and a Chrome extension.
Its README and SPEC describe kit behavior; `scripts/package.sh` builds distribution artifacts.

In Portskill this integration is **Experimental (Beta)** and disabled by default.
Local modifications include Codex SessionStart JSON compatibility and removal of
an internal agent resume checkpoint from the distributed source tree.

Skill documentation is locally condensed: shorter discovery descriptions and
removed customization boilerplate; templates and hook/ledger protocols are retained.
