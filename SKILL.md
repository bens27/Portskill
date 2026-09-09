---
name: port-registry
description: Manage shared local development ports and project services with Portskill. Use to prevent port collisions, start or stop services, inspect the registry, connect its UI or MCP, or configure Tailscale exposure.
---

# Portskill

Read [the canonical skill](skill/SKILL.md) before using Portskill. This entrypoint
keeps repository-root skill discovery compatible without duplicating instructions.
The app provides an HTML UI and HTTP MCP on the same local listener, plus stdio MCP.
