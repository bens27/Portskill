---
name: portskill
description: Manage shared local development ports and project services with Portskill. Use when the user asks to register a port, reserve or claim a port, register, host, serve, run or expose a project on Portskill or over Tailscale, to prevent port collisions, start or stop services, inspect the registry, connect its UI or MCP, or configure Tailscale exposure.
triggers:
  - port
  - register port
  - reserve port
  - port collision
  - serve project
  - expose project
  - tailscale
  - local services
  - start service
  - stop service
---

# Portskill

Read [the canonical skill](skill/SKILL.md) before using Portskill. This entrypoint
keeps repository-root skill discovery compatible without duplicating instructions.
The app provides an HTML UI and HTTP MCP on the same local listener, plus stdio MCP.
