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
`0007_telemetry_rollup_details`. The generated 95-row catalog is
`docs/telemetry/metric-catalog.json`. All 95 definitions have production
collector or derivation paths and explicit implementation/physical-validation
status. Collection, persistence, rendering, entitlements, forecasts, anomalies,
alerts, reporting/export, query budgeting, bounded live history, progressive
compression, retention, provider backpressure, and lifecycle cleanup are
exercised by backend, frontend, accessibility, Playwright, scale, and soak tests.
Exact outcomes and the remaining Linux and physical-hardware boundaries are recorded in
`docs/validation/telemetry-validation.md`.
