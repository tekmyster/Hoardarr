# Automation and signed webhooks

Hoardarr exposes read-only home-automation state at
`GET /api/v1/integrations/home-assistant/summary`. A Monitor only API key can
read this bounded, schema-versioned document. Home Assistant remains a
consumer; it cannot become Hoardarr's storage control plane through this
endpoint.

## Webhook delivery

Administrators configure webhook destinations in Settings. Hoardarr resolves
and records the approved destination IPs when the endpoint is created, rejects
credentials/query strings in URLs, and re-resolves before every delivery. The
network connection uses the approved literal IP with the original Host/SNI,
preventing DNS rebinding between validation and connection establishment.
Loopback delivery requires an explicit setting. Redirects are never followed.

The signing secret must contain 32–512 characters. It is encrypted with the
installation secret store, returned only as a fingerprint, and can never be
read through the API. Replace a compromised secret rather than attempting to
retrieve it.

Alert events are stored as durable deliveries before network IO. Event IDs are
unique per destination, so retries and repeated evaluation do not create a
second delivery. The background worker delivers them even when no browser or
API client is connected. HTTP 408/425/429, 5xx responses, and transport outages
retry the same row with bounded 30-second, 2-minute, 10-minute, and 1-hour
backoff. Delivery stops after five attempts. Permanent 4xx responses fail
without retry. External failures do not block storage operations.

Payloads use a fixed catalog of event types, an eight-level nesting limit, 100
items per collection, 1,024 characters per string, and a 32 KiB encoded limit.
Keys containing secret, password, credential, API key, or token are redacted
before persistence. Hoardarr does not accept user-defined event names.

Each request contains:

- `X-Hoardarr-Delivery`: durable delivery UUID
- `X-Hoardarr-Event`: catalog event type
- `X-Hoardarr-Timestamp`: Unix timestamp used in the signature
- `X-Hoardarr-Signature`: `v1=` followed by the HMAC-SHA256 digest

Receivers verify HMAC-SHA256 over:

```text
<X-Hoardarr-Timestamp>.<exact request body bytes>
```

Use a constant-time comparison, reject timestamps outside the receiver's replay
window, and deduplicate on `X-Hoardarr-Delivery`. The JSON body contains
`schema_version`, `delivery_id`, `event_id`, `event_type`, `occurred_at`, and a
bounded `payload` object.

Supported routed events in this release are alert opened, acknowledged,
suppressed, unsuppressed, and cleared, plus explicit test delivery. Settings
shows the actual latest durable status and attempt count; it does not display a
synthetic success.
