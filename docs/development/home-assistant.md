# Home Assistant and read-only automation

Hoardarr exposes a versioned, bounded summary for home automation consumers:

```text
GET /api/v1/integrations/home-assistant/summary
Authorization: Bearer hak_...
```

Create a **Monitor only** API key in Settings and send it as a bearer token. The endpoint accepts read scope and cannot start storage, maintenance, backup, or update operations.

The schema reports application/database health, current persisted drive and Storage Group lifecycle state, a bounded recent-job window, current maintenance activity, evidence-backed metric/drive/operation warnings, and normalized logical-storage/controller/path topology. Lists are capped at 256 drives, 25 recent operations, 50 current summary alerts, 128 logical-storage objects, 128 controllers, and 512 paths. Topology contains stable identity, relationship, provider, state, and last-observation fields; it excludes raw provider/configuration dictionaries. The whole summary excludes integration API keys, backup/webhook credentials, operation requests, and hardware-provider raw output.

`schema_version` is currently `1`. Consumers should treat unknown fields as additive and use `captured_at` and `source` to distinguish this persisted control-plane view from live high-frequency telemetry. Home Assistant remains a read-only consumer; Hoardarr is the storage control plane.

Detailed telemetry remains available through the normalized telemetry API and authenticated Prometheus export. The summary deliberately avoids requiring a native Home Assistant plugin.
