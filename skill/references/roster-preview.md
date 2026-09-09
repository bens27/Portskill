# Roster frontend preview

Use only for a requested Roster preview; a Roster checkout alone is not a trigger.


For a Roster checkout containing `server.ts` and `seed-data.ts`, use an existing explicit preview choice. If exposure or seeded-data intent is unspecified, offer:

```text
Launch the Roster frontend with its current seeded prototype data and expose it privately with Tailscale Serve?
```

Offer only `launch` and `skip`. Continue the user's original task after `skip`; do not keep prompting. On `launch`:

1. Run `status --project <roster-path>` and reuse a suitable unreleased range when possible; otherwise allocate one port with `--tailnet serve` and a note identifying it as the Roster seeded frontend preview.
2. Inspect `.port-registry/start.sh`. Preserve a customized hook. If it is still the scaffolded placeholder, replace it with a project-local command that sets `PORT_REGISTRY_SERVER_PORT` to the allocated range's first port and executes `node --import tsx server.ts` from the Roster root.
3. Set `ROSTER_EVENT_LOG_PATH` in the hook to `.port-registry/roster-preview-event-log.json`. Before starting a requested preview, remove only that preview-specific file so the server rebuilds it from the current `seed-data.ts`. Never delete the default `~/.config/roster-console/event-log.json` as part of this preview flow.
4. Run `start --range-id <id> --project <roster-path> --tailnet serve`. Use `serve`, never `funnel`, because this preview is private by default.
5. Verify the local HTTP endpoint responds, inspect Tailscale Serve status, and report both the local URL and the private Tailnet URL. If either check fails, report the failure and leave the other working endpoint accurately described.

Describe the preview as seeded prototype data: some Roster sections may already be event-backed while unfinished sections still render static mockup content. Do not imply that the whole console is production-backed.

