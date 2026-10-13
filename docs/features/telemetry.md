# Usage metrics (OpenTelemetry)

Off by default. An administrator (managed settings, for everyone) or you can
send usage metrics to an OpenTelemetry collector over OTLP/HTTP, for example
to see adoption, cost per team and tool errors.

```json
"telemetry": {
  "enabled": true,
  "endpoint": "https://otel.example.com:4318",
  "headers": { "Authorization": "Bearer ${OTEL_TOKEN}" },
  "resourceAttributes": { "team": "payments", "user.name": "${USERNAME}" }
}
```

| Setting | Meaning |
|---|---|
| `endpoint` | the collector's OTLP/HTTP address |
| `headers` | sent with each export; `${VAR}` is read from the environment |
| `resourceAttributes` | labels on every metric (team, department, ...) |
| `exportIntervalSeconds` | how often metrics are sent (60) |
| `caCertPath` | the collector's certificate authority (the OS store is trusted already) |

The standard `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_RESOURCE_ATTRIBUTES` and `CMCODER_TELEMETRY=1` work too.

## What's sent

Sessions (by front end), tokens and cost per model, tool calls by tool and
result (MCP tools by server), model errors, compactions, turns and their
duration.

**Only counts and timings:** never prompts, replies, code, file paths or
command text.

## For administrators

Set `telemetry` in the managed settings to turn it on for every PC
([../admin/README.md](../admin/README.md#5-managed-settings-rules-developers-cant-change)).
A repository's settings can't turn it on or point it elsewhere unless the
project is trusted. `cmcoder doctor` checks the collector.

Details: `src/cmcoder/telemetry.py`.
