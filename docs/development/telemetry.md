# Telemetry and storage analytics

Hoardarr stores normalized, timestamped readings from local operating-system counters and supported hardware providers. The canonical catalog is `hoardarr.telemetry.catalog`; the generated JSON copy is [metric-catalog.json](../telemetry/metric-catalog.json), and authenticated installations expose the same metadata at `GET /api/v1/telemetry/catalog`.

## Value and quality semantics

Every reading identifies an entity by Hoardarr's stable entity identity, not a kernel-assigned device name. A reading carries its timestamp, unit, provider, intended collection interval, raw/derived classification, and quality:

| Quality | Meaning |
|---|---|
| `available` | The source reported the current value. |
| `not_reported` | The entity/source exists but did not report this value. |
| `unsupported` | The detected source cannot provide this metric. |
| `temporarily_unavailable` | Collection failed or timed out and may recover. |
| `stale` | The last real value is older than three collection intervals (at least 30 seconds). |
| `estimated` | The value is an explicitly labeled estimate. |
| `derived` | The value was calculated from stored or current readings. |

Missing values remain null. Hoardarr does not replace them with zero. A stale value may retain its last reported value and timestamp so operators can see both the value and its age.

## Collection architecture

Fast collectors read inexpensive OS counters, including `psutil`, Linux block statistics, interface counters, and sysfs queue state. Device health uses the existing bounded SMART/NVMe cache. Slow inventory, controller, and enclosure discovery uses longer intervals. Each provider has an independent timeout and records provider health. A failed provider cannot stop the durable storage worker.

The durable worker owns the collector lifetime. FastAPI does not start or stop
collection, and polling/SSE/WebSocket consumers do not own it. Every browser may
disconnect while the worker continues to persist samples, build rollups, apply
retention, and evaluate alerts. Reconnected clients reconstruct the interval
through bounded history queries. The existing worker already provides this
separate process lifetime, so this release does not add a duplicate telemetry
daemon. An idempotent shutdown contract cancels queued provider work and stops
new collection when the worker exits.

Read-only Linux platform collection currently includes:

- `/proc/diskstats`, `/sys/class/block`, SMART and NVMe data through the storage sampler.
- `zpool list` and `/proc/spl/kstat/zfs/arcstats` when ZFS is installed.
- ZFS pool READ/WRITE/CKSUM counters from `zpool status -p`.
- `/proc/mdstat` for Linux MD state, membership, and rebuild/check progress.
- `multipathd show maps json`, including durable active path-group transition counts,
  when the installed multipath implementation provides group identities.
- `/sys/class/sas_phy` for reported SAS PHY counters and link-rate metadata.
- bounded `sg_ses --json` for reported enclosure temperature, cooling, PSU,
  voltage, LED, expander, slot, and redundant-path facts.
- `/sys/class/fc_host` for FC port state, identity, speed, traffic counters, and link errors.
- Existing hardware provider/controller/enclosure documents for values actually exposed by vendor or SES providers.

Provider output is size-bounded, parsed as untrusted input, and invoked with structured process arguments, `shell=False`, and timeouts. Hardware models, labels, and entity names are never turned into command arguments by the telemetry pipeline.

## Rates and time

Timestamps are stored as UTC-aware values and presented in the browser's local timezone. Counter derivatives use elapsed time and stable identity. A counter reset, duplicate timestamp, backward clock movement, excessive sample gap, or identity change yields `not_reported` for that interval rather than an artificial spike.

`Writes today` and `Reads today` are OS-level bytes transferred since the local persisted UTC-day baseline. They are not NAND writes. SMART/NVMe lifetime host writes are separate metrics; exact NAND writes are never claimed unless a future provider explicitly exposes that value.

## History and retention

SQLite uses one normalized sample table and one rollup table rather than a table per metric. Entity/metric/time indexes support bounded queries. Default retention is controlled by settings:

- recent raw observations: `telemetry_recent_retention_hours`
- hourly rollups: `telemetry_hourly_retention_days`
- daily rollups: `telemetry_daily_retention_days`

Collection, persistence, and rendering use separate cadences. Fast operating-system counters are normally collected every five seconds; device and hardware providers run less often. The UI redraws only while visible and never persists at animation-frame rate.

Rollups are created before raw deletion. Hourly and daily buckets preserve first, last, minimum, maximum, mean, count, and bounded percentiles. Text health-state buckets preserve the ordered states and transition count and are never averaged. Numeric envelopes retain peaks such as latency or temperature excursions. Counter rates are calculated before aggregation, so counter resets cannot become rollup spikes.

Cleanup is hourly and deletes in bounded batches. Its progress is restart-safe because an already-complete rollup is not replaced by the bounded raw remainder from a previous cleanup. Default history is 48 hours of raw detail, 90 days hourly, and 730 days daily. A license loss does not shorten the previously established retention policy or delete stored telemetry.

Historical requests default to `resolution=auto`. Hoardarr selects raw, hourly, or daily storage according to the requested range, catalog cadence, retained resolution, and graph point budget. Explicit oversize requests fail with `point_budget_exceeded`; they are not silently truncated. Aggregated responses identify the source resolution, interval, aggregation method, sample count, envelope, and state transitions. Narrower queries can retrieve finer retained data.

The default backend graph budget is 1,200 points per series; the current browser chart requests at most 800. API ranges, points, series, and total-observation settings are hard bounded. `GET /api/v1/telemetry/settings` reports the active intervals, retention, graph limits, database size, oldest data, cleanup schedule, extended-history state, and a labeled storage-growth estimate.

Live Overview and Storage graphs use fixed-size 60-sample windows. Analytics replaces, rather than appends to, each bounded historical response. Timers stop while the page is hidden, history requests use `AbortController`, and timers and requests are released on unmount. Hoardarr does not currently expose SSE or WebSocket telemetry queues, so slow-browser subscriber queues do not exist in this release.

## Analytics methodology

- Latency P50/P95/P99 uses nearest-rank percentiles and requires at least 20 actual observations. Percentiles are never reconstructed from an average.
- Capacity and endurance trends use a Theil-Sen median slope, at least seven readings, and at least seven days of history. Forecast dates are rounded to whole days and hidden for stable/declining or insufficient data.
- Endurance forecasting uses only device-reported percentage used. It does not infer TBW, remaining bytes, or NAND writes.
- Baselines use the median and median absolute deviation. Anomalies show observed value, expected range, sample count, methodology, and duration. They describe deviation, not hardware failure.
- Top-N excludes missing, unavailable, and stale values instead of ranking them as zero.
- Correlation groups simultaneous deviations by reported controller, port, expander, enclosure, path, or pool. It explicitly does not claim causation.
- Workload read ratio is byte based: `read bytes/s / (read bytes/s + write
  bytes/s)`. It is `not_reported` during zero-I/O or incomplete intervals and is
  never interchanged with an operation-count ratio.
- mergerFS imbalance is the percentage-point spread between the highest and
  lowest configured member utilization. It describes placement, not health.
- Tier occupancy uses `statvfs` for source paths bound to durable Hoardarr
  transfer identities. SSD/HDD type or advertised speed never establishes tier
  membership.

## Alerts

Basic alerts remain active without a license: critical device health, NVMe critical warnings, degraded/faulted health, capacity thresholds, temperature thresholds, and Hoardarr telemetry-provider failures. Built-in thresholds use clear values to prevent flapping.

The `metrics.alerting.advanced` capability enables user-defined numeric threshold rules. Rules support warning/critical values, sustained-condition windows, entity scope, enable/disable, and a separate clear value for hysteresis. Alert events are durable, acknowledgeable, and preserve related topology.

## Entitlements

Basic current health, capacity, throughput, IOPS, response time, utilization, drive health, temperatures, pool health, network metrics, recent alerts, and short history do not require a license.

Advanced endpoints check generic capability flags in the API. The browser is not the security boundary. License envelopes use Ed25519 signatures and an installation-bound identifier. The trust root is installed separately from the license. Invalid, corrupt, expired, mismatched, or not-yet-valid licenses fail closed to Basic. A temporarily unreadable license can use a still-valid cached decision until its signed expiration. Backward clock movement disables advanced capabilities. Existing storage operations and Basic collection do not consult the advanced license state.

## API and transport

The versioned `/api/v1/telemetry` group provides catalog, entitlements, provider health, history settings, entities, current readings, bounded history, forecasts, latency distributions, anomalies, topology correlations, Top-N, alerts, custom alert rules, reports, and Prometheus export.

The Web UI reuses Hoardarr's existing polling architecture. Analytics makes one coalesced current-data request every five seconds while visible, cancels replaced history requests, stops its timer on unmount, and performs advanced queries only when selection or entitlement capabilities change. No fabricated history or timer-driven progress is used. Graph details show requested range, retained resolution, raw/aggregate status, returned/displayed points, and aggregation method.

## Measured scale and memory behavior

The repository provides `scripts/benchmark-telemetry.py` for 1, 24, 60, 120, and 240 simulated drives and `scripts/soak-telemetry.py` for accelerated ingestion/retention runs. These are development-host measurements, not universal capacity guarantees. The validation report records exact results and environment. The ingestion path batches identity, duplicate, and insert work in groups of 500, keeping working memory bounded by the batch rather than the total sample count.

Prometheus export requires authentication and `metrics.export`. It exposes only bounded catalog names and the low-cardinality `entity_id` and `entity_type` labels. User-supplied labels, secrets, client identifiers, credentials, and hardware-provided strings are excluded.

## Known limits

Physical-controller, SES, SAS PHY, multipath, FC, and FCoE values depend on the local kernel and vendor tools. The catalog describes possible normalized values; the API reports only collected values. Fixtures validate parsers but are not hardware certification. SQLite remains the supported single-host store; performance validation is recorded for the tested simulated inventories, not advertised as a universal maximum.
