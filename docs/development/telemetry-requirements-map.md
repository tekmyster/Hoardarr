# Telemetry requirements-to-code map

This map records the implementation boundary found before the enterprise telemetry work began. A route or panel is not treated as proof of historical collection or analytics.

| Requirement area | Existing production source | Existing API/UI | Verified gap and implementation target |
|---|---|---|---|
| Host CPU and memory | `system/overview.py` reads `psutil` | `/system/resources`, Overview | Current values only; add normalized samples, load, swap, uptime and service health |
| Network interfaces | `system/overview.py` reads interface counters/state | `/system/overview`, Overview | Browser derives rates; move reset-safe rate calculation and history to backend |
| Block performance | `storage/telemetry.py` reads Linux block counters | `/storage/telemetry`, Overview, Storage | Current rates only; no normalized quality, queue depth, history, retention or entity metadata |
| Writes today | `StorageTelemetrySampler` persists counter baselines in a local JSON file | Storage/Overview | Real OS host-write counters, but not queryable history and UTC-day semantics are implicit |
| SMART/NVMe endurance | `smartctl -a -j`, bounded five-minute cache | Storage/Health | Lifetime writes and remaining percent only; normalize complete reported health without guesses |
| Capacity | hardware scan and live storage inventory | Overview/Storage | Raw inventory and selected pool capacity; no normalized host/pool/filesystem/tier history |
| Controllers/enclosures | hardware provider registry and fixture parsers | Storage topology/Health | Health normalization exists; no metric series or provider-health telemetry |
| ZFS/MD/SnapRAID/mergerFS | storage inventory providers | Storage/Health | State/status normalization exists; no historical KPI collector or analytics |
| Multipath/FC/FCoE | topology/capability providers | Storage topology/Storage Access | Capability/state only; metric model and honest unsupported/not-reported samples required |
| Connectivity services | connectivity runtime state | Storage Access | No SMB/NFS/iSCSI/FCoE KPI collection |
| Historical storage | none; browser keeps 60 resource points in memory | mini charts | Add bounded SQLite samples, hourly/daily rollups, cleanup and indexed queries |
| Derived analytics | simple UI thresholds only | Storage performance states | Add formulas, sufficient-history gates, forecasts, percentiles, baselines, anomalies and correlations |
| Alerts | operation failures and health summaries | Overview/Health | Add durable metric alerts, hysteresis, acknowledgement and topology context |
| Licensing | no metric entitlement boundary | none | Add signed installation-bound license validation and server-side capability enforcement; Basic is unconditional |
| Metric API | one-off resource/telemetry documents | polling | Add normalized catalog/current/history/entity/analytics/alert/report/export endpoints with bounded queries |
| Live transport | two coalesced polling documents | Overview timers | Reuse polling; provide a single coalesced current-metrics document and visibility/unmount cleanup |
| Dashboard customization | movable/removable panels stored in browser | Overview | Extend with honest metric-backed widgets and licensed-state presentation |
| Enterprise analytics UI | none | none | Add an Analytics page using stored backend metrics only |
| Export/reporting | process-only public Prometheus endpoint | `/metrics` | Add authenticated, entitled storage metric export and telemetry-backed reports |
| Performance/scale | no telemetry store scale suite | none | Add deterministic 1/24/60/120/240-entity ingestion/query tests and record measurements |

## Existing metric provenance verdict

The existing CPU, memory, network counters, block counters, capacity inventory, SMART/NVMe values and pool state are live backend readings. The small graphs on Overview contain only readings collected during the current browser session. No existing component fabricates historical samples, but no durable time-series subsystem existed at this baseline.

The implementation will preserve the existing live documents for compatibility while making the normalized catalog and time-series API the source for new analytics.

## Final implementation evidence

The production implementation now resides in `hoardarr.telemetry` with schema
revisions `0005_enterprise_telemetry`, `0006_metric_alert_rules`, and
`0007_telemetry_rollup_details`. The generated 101-row catalog is
`docs/telemetry/metric-catalog.json`. All 101 definitions have production
collector or derivation paths and explicit implementation/physical-validation
status. Collection, persistence, rendering, entitlements, forecasts, anomalies,
alerts, reporting/export, query budgeting, bounded live history, progressive
compression, retention, provider backpressure, and lifecycle cleanup are
exercised by backend, frontend, accessibility, Playwright, scale, and soak tests.
Exact outcomes and the remaining Linux and physical-hardware boundaries are recorded in
`docs/validation/telemetry-validation.md`.

## Persistent contextual performance graphs (KPIUI-15)

The normalized telemetry history API is now reachable from the operational context
that owns the entity. Overview opens the observed host, Storage opens an exact drive
or pool, Health opens an exact drive/controller/enclosure/pool, and Controller
Redundancy keeps each physical path separate. The contextual navigation contract is
`MetricHistoryContext`; it carries the stable entity ID, entity type, display name,
metric ID, and source surface. Resolution is fail closed: an exact type and stable ID
is preferred, a display-name match is accepted only when unique, and an ambiguous
name does not select a substitute entity.

The applicable catalog inventory is the checked-in 101-row catalog rather than a UI
list. KPIUI-15 currently exposes applicable definitions for these entity classes:

| Context | Catalog applicability | Persistent graph families |
|---|---:|---|
| Host/system | 25 | CPU, load, memory, swap, capacity, throughput, IOPS, latency, utilization, ARC, capacity growth |
| Drive | 37 | throughput, IOPS, latency, utilization, queue/busy/weighted time, reads/writes today, temperature, health, SMART/NVMe wear and endurance, latency analytics, workload ratio |
| Pool | 31 | capacity, throughput, IOPS, latency, utilization, queue, reads/writes today, health, fragmentation, scrub/rebuild, members/errors, ARC, latency/capacity/workload analytics |
| Logical storage | 17 | capacity, throughput, IOPS, latency, utilization, queue, reads/writes today, health, healthy/failed path count |
| Controller | 8 | queue, busy time, health, timeout, temperature, cache hit/state, battery state |
| Storage path | 16 | per-path throughput, IOPS, latency, utilization, queue, health/state, interface/SAS errors, latency analytics |
| Enclosure | 4 | health, temperature, fan speed, path redundancy |

Entitlement filtering is applied after entity applicability. A graph request is never
made for an inapplicable metric/entity pair. A missing provider sample retains its
reported quality (`not_reported`, `unsupported`, `temporarily_unavailable`, or
`stale`) and nullable value rather than becoming zero or idle.

`frontend/src/components/MetricHistoryPresentation.tsx` is the shared rendering
contract used by Analytics and controller/path history. It owns formatting, help,
sample provenance, numeric raw/rollup presentation, categorical state timelines,
accessible bucket values, and Advanced diagnostics. Numeric rollups display the
stored mean with minimum/maximum peak context and first/last/count; raw samples do
not claim an envelope. Categorical values retain observed order and are never
averaged. Logical-storage performance remains authoritative logical-storage data;
physical path series are not summed or converted into an inferred logical total.

Requests remain server-backed and bounded. The client requests at most the smaller
of the server-advertised graph budget and 800 points, supports 24-hour, 7-day, and
30-day ranges (plus longer entitled ranges), and discloses requested/selected
resolution, bucket interval, raw versus rollup, returned/displayed/budgeted point
counts, retention, entitlement, aggregation, source, unit, and methodology. Entity,
metric, and range replacement aborts the prior request and a sequence guard rejects
late responses. Controller/path history is additionally bounded to eight series and
240 displayed points per path. Overview and Storage session buffers remain explicitly
labelled live-session-only and are not used as persistent history.

## Operational WebUI provenance and quality contract (KPIUI-03/KPIUI-04)

The operational WebUI now uses one explicit contract from persisted observation
through rendering:

- `sample_document()` builds provider, observation/ingestion time, collection
  interval, unit, metric kind, and raw/derived/estimated classification only from
  protected sample columns and the checked-in catalog. Labels remain descriptive
  metadata and cannot upgrade a configured or user-entered value into an observed
  operational fact.
- `available`, `not_reported`, `unsupported`, `temporarily_unavailable`, `stale`,
  `estimated`, and `derived` survive ingestion/API/UI round-trip. Unavailable
  qualities carry `null`; they never render as zero or false idle. Error detail is
  a bounded sanitized provider code, not raw command output.
- Overview and Storage live histories are nullable, bounded session buffers.
  Failed/missing refreshes add gaps. Their labels say `Live session only`, target
  cadence, maximum sample count, source, and observed range.
- Browser-side network rates are calculated only when the same complete set of
  up interfaces reports monotonic counters across a positive elapsed interval.
  Missing counters, reset counters, duplicate timestamps, or membership changes
  produce a gap rather than an invented rate.
- Historical numeric rollups display the stored mean together with accessible
  first/last/minimum/maximum/count fields. The chart draws peak-preserving
  minimum/maximum boundaries only for aggregates. Raw samples have no envelope.
  Missing buckets remain gaps.
- Categorical history is an ordered state/transition timeline. It is never
  averaged or converted to a number.
- Per-path series remain per-path. The logical-storage summary uses only
  authoritative logical-storage observations; path throughput, IOPS, and latency
  are not summed, and unlike latency samples are never averaged into a claimed
  aggregate.
- Derived and estimated cards expose classification, source, time, interval,
  quality, unavailable reason, and formula/provider methodology.

Implementation is shared by `frontend/src/metricHistory.ts`, the Overview,
Storage Performance, Analytics, and Controller Redundancy components, and the
normalized telemetry documents in `backend/src/hoardarr/telemetry/store.py`.
The deterministic `frontend/e2e/kpiui_preview_server.py` launcher uses the real
FastAPI/SQLite/ingestion/frontend stack, labels its only entity as test evidence,
and never touches storage devices. Exact unit, browser, and artifact evidence is
recorded under the KPIUI-03/KPIUI-04 section of
`docs/validation/telemetry-validation.md`.
