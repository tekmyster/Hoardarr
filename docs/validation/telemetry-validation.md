# Enterprise telemetry validation

Validated on 2026-08-22 from the Windows 11 development host and clean Ubuntu
24.04 GitHub runners using Python 3.12, systemd, mergerFS, loop devices, QEMU,
SQLite and Playwright Chromium. Hoardarr remained 0.3.10 Beta 1.

## Implemented boundary

- The canonical catalog contains 95 normalized KPI definitions: 86 raw and 9
  derived, across 24 entity types. Every definition now has a production
  collector or derivation path. The generated catalog explicitly labels 36
  software-verified definitions and 59 values that are collected only when the
  named provider reports them; those values remain `Not reported` when absent.
  Sixty definitions require physical-provider validation, including the
  software-verified durable multipath failover transition counter.
- Missing, unsupported, stale, and temporarily unavailable values remain
  distinct and are never changed to zero.
- SQLite stores normalized samples plus hourly/daily rollups. Rollups retain
  first, last, minimum, maximum, mean, count, bounded percentiles, and state
  transitions. Health states are not averaged.
- Default retention is 48 hours raw, 90 days hourly, and 730 days daily.
  Cleanup uses 10,000-row batches. Raw-history requests default to automatic
  resolution and are bounded to 1,200 backend points; the browser requests at
  most 800 points.
- Live Overview and Storage history is fixed at 60 readings. Analytics replaces
  the current bounded result, aborts replaced requests, suspends polling while
  hidden, and removes timers/requests on unmount. Timed-out providers have one
  in-flight future per provider in a fixed-size executor.
- Basic health and recent telemetry do not require a license. Extended history,
  analytics, alert rules, reports, and export are enforced by signed,
  installation-bound backend entitlements. License loss does not delete data.

## Defects found and corrected

| Finding | Correction | Evidence |
|---|---|---|
| Rollup construction grouped all old samples in memory | Stream entity/metric/time order and retain one bucket, with bounded percentile input | rollup and soak tests |
| Raw history silently stopped at a limit | Count and reject oversize explicit queries; automatic resolution chooses retained detail | API point-budget tests |
| Batched cleanup could overwrite a complete rollup with the remaining raw subset | Preserve a rollup whose complete sample count exceeds the bounded remainder | repeated cleanup test |
| A rollup bucket overlapping the requested start could be omitted | Align aggregate query start to the retained bucket boundary | automatic-resolution API test |
| Ingestion performed an identity and duplicate query per observation | Batch identity lookup, duplicate lookup, and inserts in groups of 500 | before/after scale results |
| Timed-out collector work could accumulate threads | Fixed worker pool, one in-flight future per provider, capacity backpressure | slow-provider test |
| Worker shutdown did not explicitly release telemetry provider threads | Idempotent telemetry close contract and `finally` cleanup in the durable worker | telemetry shutdown and worker regression tests |
| Analytics history requests survived selection changes | AbortController cancellation and unmount cleanup | component lifecycle test |
| First Analytics E2E run allowed `/telemetry/settings` to hit an absent proxy | Complete the browser API fixture and rerun | 8-test Playwright run |
| Linux release CI referenced a nonexistent `locked-project.txt` | Install both generated, hashed `requirements/runtime.lock` and `requirements/hoardarr.lock` from the wheel directory | release asset contract test and YAML validation |
| systemd verification ran before its referenced Hoardarr executables existed | Install the wheel in an empty Python 3.12 environment, expose that disposable environment at the unit's production entry-point path, then run `systemd-analyze verify` | Ubuntu CI release-bundle and installed-appliance jobs passed |

## KPI catalog closure

These were the 13 definitions without a complete production path before this
closure pass.

| Metric | Previous gap | Production implementation | Final catalog status |
|---|---|---|---|
| `io.weighted_time` | Missing collector | Exact-device field 14 from `/proc/diskstats`; absent/malformed fields are `not_reported` | PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED |
| `sas.phy.invalid_dwords` | Provider parser missing | Linux SAS transport sysfs, stable SAS-address/PHY identity, bounded integer parser | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `sas.phy.disparity_errors` | Provider parser missing | Linux `running_disparity_error_count`; loss-of-sync, reset, and negotiated/capable rates remain reported metadata | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `enclosure.temperature` | Provider parser missing | Bounded `sg_ses --json` temperature-element normalization | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `enclosure.fan.speed` | Provider parser missing | SES cooling-element RPM normalization; unknown speed is not estimated | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `enclosure.path.redundancy` | Provider parser missing | Counts distinct current SES device paths for the same reported enclosure identity | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `pool.errors.read` | Provider parser missing | Authoritative ZFS pool-summary READ counter from `zpool status -p` | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `pool.errors.write` | Provider parser missing | Authoritative ZFS pool-summary WRITE counter from `zpool status -p` | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `pool.errors.checksum` | Provider parser missing | Authoritative ZFS pool-summary CKSUM counter from `zpool status -p` | SUPPORTED WHEN PROVIDER REPORTS VALUE; physical validation pending |
| `mergerfs.distribution.imbalance` | Derivation missing | `max(member utilization %) - min(member utilization %)` using accessible configured branch `statvfs` values; it is a distribution measure, not a failure assertion | PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED |
| `multipath.failovers` | Derivation/state missing | Durable active path-group state; increments once only when the reported active-group identity changes | PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED; physical validation pending |
| `tier.occupancy` | Missing implementation | `statvfs` utilization for source paths bound to durable Hoardarr transfer identities; never inferred from drive media type | PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED |
| `analytics.workload.read_ratio` | Derivation missing | `read bytes/s / (read bytes/s + write bytes/s)`; zero-I/O and missing-input intervals are `not_reported` | PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED |

No supported source was proven incapable of exposing one of these definitions.
Provider-dependent metrics are retained because supported Linux/vendor sources
can expose them, but Hoardarr does not synthesize absent values.

## Measured scale

`scripts/benchmark-telemetry.py` inserted 20 observations for four metrics per
simulated drive. Results are development-host measurements, not production
guarantees.

| Drives | Observations | Ingest wall | Current query | Peak Python memory | DB bytes |
|---:|---:|---:|---:|---:|---:|
| 1 | 80 | 117.69 ms | 13.91 ms | 1,506,865 | 397,312 |
| 24 | 1,920 | 964.11 ms | 19.29 ms | 3,011,457 | 1,191,936 |
| 60 | 4,800 | 2,028.76 ms | 37.03 ms | 3,054,386 | 2,469,888 |
| 120 | 9,600 | 4,075.76 ms | 63.08 ms | 3,020,476 | 4,546,560 |
| 240 | 19,200 | 8,724.96 ms | 124.06 ms | 4,892,381 | 8,769,536 |

The pre-correction 240-drive ingest took 49,023.94 ms. Batching reduced it to
8,724.96 ms in the latest pass while keeping the ingestion working set bounded by the 500-sample
batch.

The second strengthened 60-drive/500-cycle soak ingested 120,000 observations in
105.789 s. Peak traced Python memory was 3,224,396 bytes; the warm-half range
was 1,552,109-2,009,687 bytes and ended 121,034 bytes below its first warm-half
sample. Cleanup deleted all 120,000 expired raw rows. This demonstrates
stabilization for that exact accelerated load; it is not a universal no-leak
claim.

The strengthened Playwright lifecycle soak navigated Analytics to Overview 200
times and changed range 200 times across Live, 1 hour, 24 hours, 7 days and 30
days. Chromium reported 10,000,000 bytes before warm-up, after 50 cycles, and
after 200 cycles, with exactly 200 current-metric requests. After leaving
Analytics, no additional current-metric request occurred during the 5.25-second
observation. Chromium's exposed heap value was coarse, so AbortController,
unmount, fixed-buffer, request-budget and provider-backpressure tests remain the
stronger structural evidence.

## Four-Device mergerFS Persistent Telemetry Workload

**4-DEVICE MERGERFS SOFTWARE VALIDATION: VERIFIED IN ISOLATION.**
**4-PHYSICAL-SSD VALIDATION: PENDING.**

The Windows host reported four 240,057,409,536-byte `CISCO SSD-240G V01` USB
devices, serials `STP26501RJH`, `STP26500SG9`, `STP26510Q4N`, and
`STP26501RAW`. Windows reported them RAW, offline, healthy, non-boot and
non-system. This is not the required Linux ancestry/mount/SMART proof and is not
explicit owner confirmation that the devices are disposable, so no physical
device was changed.

`tests/integration/run-mergerfs-telemetry-workload.sh` implements the safe
substitute on an ephemeral Ubuntu 24.04 runner. It requires root, a disposable
runner marker and either GitHub Actions or an explicitly disposable VM. It
creates four purpose-owned 2 GiB sparse images, verifies each loop backing file,
rejects root/boot/EFI/swap/mounted targets, quick-formats ext4 with lazy
initialization and no format-time discard, mounts four members, and mounts
mergerFS with `category.create=mfs`.

The harness recorded UTC timestamps for idle, small-file, sequential-write,
concurrent-write, sequential-read, actual fio random-read, mixed-read/write,
return-to-idle, browser-disconnected, browser-reconnected and service-restart
phases. The headless collector persists CPU, per-member and mergerFS aggregate
KPIs with zero API clients. It verifies files and bytes on all four members,
writes-today increase, pool KPI presence, samples throughout the disconnected
interval, reconstruction through the authenticated `/api/v1/telemetry/history`
endpoint after reconnect, real rollup creation, and history survival across a
fresh collector process. Independent evidence includes before/after
`/proc/diskstats`, one-second `iostat` and `vmstat`, plus `df` and `du`. Evidence is written to
`dist/validation/mergerfs-persistent-telemetry.json`. GitHub Actions run
`32580421790` completed successfully on Ubuntu 24.04 and published the evidence
artifact.

mergerFS aggregate throughput and IOPS are the sum of the member block-device
counters mapped from configured branch mount paths. Member metrics show those
same physical operations individually; pool plus members must not be added
together. The imbalance metric is the utilization percentage-point spread and
does not assert that an intentionally uneven pool is unhealthy.

The production lifetime owner is `hoardarr-worker`, not FastAPI and not a
browser subscription. The worker constructs one `TelemetryService`, persists
samples/rollups/alerts in short SQLite WAL transactions, and continues while
the API is stopped. API and browser processes only read that persistence.
Worker shutdown now closes the bounded provider executor in a `finally` block;
an actual worker outage remains an honest history gap.

The run used four 2,040,373,248-byte loop images with identities
`TEST-MERGERFS-1` through `TEST-MERGERFS-4`. It persisted 8,323 normalized
samples from 15:01:37 through 15:02:19 UTC, including 3,451 samples while no
browser client was connected. The authenticated history API reconstructed 41
points after reconnect. A second collector process retained prior history and
resumed collection; 203 rollups were created and retained.

Actual placement was 300 files/1,228,800 bytes, 301 files/135,446,528 bytes,
304 files/135,446,528 bytes and 304 files/538,099,712 bytes across members one
through four. Independent Linux evidence recorded 810,221,568 bytes through
`du`, 11% mergerFS utilization through `df`, 282 `iostat` samples, 39 `vmstat`
samples and per-loop `/proc/diskstats` deltas. Every member's writes-today
counter increased from zero. Cleanup was performed by the enclosing
purpose-created loop harness.

The workload commands are fixed in the harness rather than left to CI
operator choice: four 2 GiB `truncate`/`losetup` images; `mkfs.ext4 -F -E
lazy_itable_init=1,lazy_journal_init=1,nodiscard`; individual `noatime` mounts;
`mergerfs -o category.create=mfs,moveonenospc=true,cache.files=partial,
dropcacheonclose=true`; 1,200 deterministic 4 KiB files; four 128 MiB sequential
writers; four 32 MiB mixed-size writers; a complete sequential read; verified
`fio` random-read and 60/40 mixed-read/write phases; and a return-to-idle period.
The harness preserves a safety reserve and removes only its uniquely named test
dataset before detaching its purpose-created loop devices.

## Validation pass 1

| Command | Exit | Result |
|---|---:|---|
| `uv sync --project backend --all-groups --locked` | 0 | 39 locked packages checked |
| `npm ci --prefix frontend --no-audit --no-fund` | 0 | 166 locked packages installed; esbuild install-script approval warning |
| `uv run --project backend --locked ruff check ...` | 0 | no findings |
| `uv run --project backend --locked pytest --cache-clear -q backend/tests` | 0 | 288 passed, 0 failed, 0 skipped; one Starlette/httpx deprecation warning |
| `npm test --prefix frontend -- --run` | 0 | 24 files, 99 passed |
| `npm run test:a11y --prefix frontend` | 0 | 3 passed |
| `npm run build --prefix frontend` | 0 | TypeScript and Vite production build passed; JS 441.19 kB, CSS 62.83 kB |
| `npm run test:e2e --prefix frontend` | 0 | 8 passed, including 200-cycle heap/lifecycle soak |
| bootstrap unittest discovery | 0 | 72 passed, 1 POSIX-only skip |
| release unittest discovery | 0 | 19 passed, 1 Linux-only skip |
| `uv build --project backend --wheel` | 0 | `hoardarr-0.3.10-py3-none-any.whl` |
| `npm audit --prefix frontend --omit=dev --audit-level=high` | 0 | 0 vulnerabilities |
| `uvx pip-audit backend` | 0 | no known vulnerabilities |

The initial combined browser pass passed but logged a rejected proxy request for
the newly added history-settings endpoint. That fixture defect was corrected and
the browser suite was rerun cleanly; it is not counted as a clean first result.
Recursive cache/output deletion was rejected by the execution policy even for
verified repository paths. Both passes therefore used locked reinstall,
`pytest --cache-clear`, fresh temporary databases, `npm ci`, and build tools that
replace their outputs; no bypass was attempted.

## Second adversarial validation pass

| Command | Exit | Result |
|---|---:|---|
| locked uv sync and fresh `npm ci` | 0 | dependency states recreated |
| Ruff with `--no-cache` | 0 | no findings |
| pytest with `--cache-clear` | 0 | 288 passed, 0 failed, 0 skipped; same one deprecation warning |
| focused telemetry/worker/release contract | 0 | 41 plus 34 passed in focused runs |
| frontend Vitest | 0 | 24 files, 99 passed |
| accessibility | 0 | 3 passed |
| production frontend build | 0 | passed with same artifact sizes |
| Playwright complete run after lifecycle soak addition | 0 | 8 passed |
| bootstrap/release unittests | 0 | 91 passed, 2 environment skips |
| backend wheel | 0 | built |
| telemetry soak | 0 | 120,000 inserted and 120,000 expired rows deleted; bounded memory above |
| npm audit / pip-audit | 0 / 0 | no reported vulnerabilities |
| dangerous-subprocess/secret checks | mixed | no `shell=True`; detect-secrets candidates manually resolved to identifiers, hashes, fields, or fixtures; Bandit: 37 low, 4 reviewed medium, 0 high |

## Environment-blocked validation

- **Managed Deep Security Scan — BLOCKED BY EXECUTION ENVIRONMENT.** Exact
  error: `Deep Scan cannot safely start a read-only worker: the parent must
  provide a managed filesystem permission profile.` The workflow prohibited a
  retry, no substitute worker was launched, and no result was inferred.
- Standard Codex Security scan `b2a348a2-ad01-428f-b51a-4568d42bbd13`
  completed against revision `99dcc6b1290aea549d9b815f514123c175e1f170`.
  Its eight actionable code findings were corrected and regression-tested. The
  low-severity root-process blast-radius finding remains an explicit appliance
  design decision.
- Differential scan `e09ff547-3021-49a9-ab4e-57447f62a593` found one
  destination-publication race. The final POSIX transfer publishes by atomic
  no-clobber link after inode validation, and the Linux regression test passes.
- Ubuntu 24.04 CI executed live `/proc` and `/sys` telemetry, the locked release
  bundle, installed systemd services, the four-loop workload and QEMU boot.
  These results are isolated Linux evidence, not physical-controller
  certification and not a substitute for the blocked managed Deep Scan.

## Hardware validation pending and limitations

No physical storage controller, HBA, enclosure, multipath, FC, FCoE, ZFS, MD,
SnapRAID, SMB, NFS, or iSCSI target was exercised in this KPI run. Provider
parsers and honest missing-value behavior are isolated software evidence only.
Vendor and kernel values not reported by the local provider remain `Not
reported`. The current host also did not execute the privileged Linux
loop-device profile. See `hardware-certification.md` for the product-wide table.

Current release classification is **SOFTWARE-READY; PHYSICAL HARDWARE
CERTIFICATION PENDING**. The managed Deep Scan remains a formal release-gate
limitation if project policy requires it, and production signing-key provisioning
and public release publication remain external owner actions.

## KPIUI-03/KPIUI-04 operational WebUI validation — 2026-08-25

Scope was limited to local appliance telemetry provenance, quality, operational
graphs, and the directly affected tests. No beta storage topology, website/fleet
ingestion, public analytics, hypervisor, network configuration, or protected disk
was changed.

### Contract evidence

- All seven qualities round-trip through the production normalized store and API.
  Adversarial labels cannot override value, source, quality, unit, timestamps, or
  classification. Raw provider error text is rejected; only a lower-case bounded
  error code is accepted.
- Overview network derivatives require a complete identical interface set,
  monotonic counters, and positive elapsed time. Counter reset, missing values,
  membership change, and duplicate time produce `null` gaps. Storage activity is
  `Idle` only when both reported read and write values are zero.
- Overview and Storage Performance live histories use nullable fixed-length
  buffers, abort refresh requests on replacement/unmount, and label themselves as
  bounded live-session history rather than durable history.
- Analytics numeric history reads the rollup `mean` explicitly and exposes
  first/last/minimum/maximum/count in a keyboard-accessible table. A rollup-only
  min/max envelope preserves peaks; raw series do not claim an envelope.
  Categorical state buckets render observed order and transition counts.
- Controller path charts remain separate per path. Logical totals are sourced only
  from logical-storage observations; no path values or latency values are summed.
- Every card and graph exposes provider/source, observed time or range, interval,
  unit, classification, quality, reason, raw/rollup representation, resolution,
  points returned/displayed/budget, aggregation method, and formula/methodology
  where applicable.

### Executed checks

| Check | Result |
|---|---|
| Focused backend telemetry | 42 passed; one existing deprecation warning |
| Complete backend unit suite | 648 passed, 13 Linux/POSIX-only skipped; one existing deprecation warning |
| Focused frontend KPI units/components | 5 files, 24 passed |
| Complete frontend unit suite | 41 files, 194 passed; jsdom canvas notice from axe only |
| Ruff, including deterministic preview launcher | clean |
| TypeScript application and Node configurations | clean |
| Production frontend build | passed; CSS 84.66 kB, JS 714.47 kB; existing chunk-size advisory only |
| Focused Chromium quality/envelope/timeline/keyboard test | 1 passed, retries disabled |
| Final complete Chromium pass 1 | 40 passed, retries disabled, 4 workers |
| Final complete Chromium pass 2 | 40 passed, retries disabled, 4 workers |

Before the final pair, complete-browser runs exposed stale E2E resource-shape and
strict-selector assumptions: the fixtures still returned the superseded host
resource document, and newly accessible duplicate text caused global locators to
choose hidden content. Fixtures were aligned to the production resource contract
and assertions were scoped to the exact visible component. The affected workflows
passed in isolation before the final two clean full passes. No retry was used to
hide either failure.

### Browser evidence

The in-app browser exercised the production frontend served by the real FastAPI
application over a temporary migrated SQLite database. Data entered only through
the production telemetry ingestion path and was visibly named
`KPI QUALITY TEST DATA — TEST SSD`. The preview showed loading and empty/partial
states, all seven qualities, a sanitized `provider_timeout` collector failure,
derived methodology, a raw series, an hourly numeric rollup with one unavailable
gap, and an ordered health transition timeline. Desktop and 320-CSS-pixel layouts
were inspected. The history disclosure received keyboard focus; the Chromium E2E
test additionally proves Enter activation opens its accessible table. Browser
console inspection returned zero warnings/errors. Server access readback showed
authenticated telemetry catalog/entity/current/history/settings/alert responses;
the preview was stopped after inspection and its temporary database was not
installed into beta.

Retained screenshots were generated by the final retry-disabled Chromium run from
the same clearly labeled deterministic API evidence:

| Artifact | Dimensions | SHA-256 |
|---|---:|---|
| `frontend/test-results/WO-KPIUI-002/desktop-light-quality-rollup.png` | 1440 x 2634 | `eb5597dd7021383ddb59cc1451a1baa9c38701a92c6ed316b882c0f76b4efd08` |
| `frontend/test-results/WO-KPIUI-002/desktop-dark-state-timeline.png` | 1440 x 2361 | `273f293ebacf441f629eb9ae567e0d44b49aa32bb423440ce753179fe6201d2b` |
| `frontend/test-results/WO-KPIUI-002/mobile-320-state-timeline.png` | 320 x 3954 | `563c54c567c319856f0344b109c8a0534cb61e0b8a69976acb3a770a8e1b6e94` |
| `frontend/test-results/WO-KPIUI-002/mobile-320-keyboard-focus.png` | 320 x 900 | `fb91a1df8e89f2f5ecfa152ae6339bac2dbaec8c71fcc5e6b0e9607719bb783f` |

These artifacts are deterministic validation evidence, not beta or physical
hardware telemetry. KPIUI-05 was not started.

## KPIUI-15 persistent contextual graph validation — 2026-08-25

Scope was limited to contextual local appliance performance history and its exact
frontend, tests, and validation evidence. The accepted normalized telemetry API and
SQLite persistence contract required no backend change. No beta topology, website,
fleet/public analytics, hypervisor, network, or protected disk was changed.

### Contract and request evidence

- Overview, Storage, and Health open persistent Analytics history with the exact
  observed entity type and stable ID. Controller Redundancy uses the same shared
  presentation helpers while retaining separate physical path series.
- The entity selector offers only catalog-defined, entitled definitions applicable
  to the chosen entity. Ambiguous display names do not override a stable identity.
- History is authoritative server history. Browser-session Overview and Storage
  charts remain separately labelled and bounded; they do not satisfy or seed the
  persistent graph.
- History replacement clears obsolete data, aborts the prior request, and rejects a
  late response using a monotonically increasing request sequence. The deterministic
  browser scenario changed entity and 24-hour/7-day/30-day ranges without an
  inapplicable request and kept each request at `limit=800`; ten or fewer history
  requests served the complete scenario.
- Raw numeric points render without an aggregate envelope. Hourly and daily rollups
  expose mean, minimum, maximum, first, last, sample count, and quality in an
  accessible values table. Nullable unavailable buckets remain visible gaps.
  Categorical path/health history remains an ordered state timeline and is not
  averaged.
- Controller/path history is capped at eight visible path series and 240 points per
  series. Per-path observations are never summed into logical throughput, IOPS, or
  latency. Authoritative logical-storage observations remain distinct.
- Guided content uses the catalog human name and honest quality/loading/empty/error
  copy. Advanced diagnostics expose entity name/type/stable ID, metric ID,
  provider/source, unit, raw/derived/estimated classification and formula,
  requested/selected resolution, bucket interval, raw/rollup source, returned and
  displayed points, maximum point budget, retention/entitlement boundary, and
  aggregation method.

### Executed checks

| Check | Result |
|---|---|
| Focused backend enterprise telemetry compatibility | 42 passed; one existing Starlette/httpx deprecation warning |
| Focused frontend contextual-history units/components | 6 files, 32 passed |
| Complete frontend unit suite | 41 files, 200 passed; existing axe/jsdom canvas notice only |
| TypeScript production build | passed; CSS 84.66 kB, JS 721.99 kB; existing chunk-size advisory only |
| Focused retry-disabled Chromium contextual graph scenario | 1 passed |
| Final complete retry-disabled Chromium pass 1 | 41 passed, 4 workers |
| Final complete retry-disabled Chromium pass 2 | 41 passed, 4 workers |
| Included Analytics memory soak in each full pass | 200 visits and 200 range changes; reported heap 10,000,000 bytes before/warm/final |
| Scoped `git diff --check` | clean; line-ending conversion notices only |

The two complete Chromium passes were executed after the final contextual-banner
fix, with retries disabled. The deterministic scenario used the production frontend
contract and clearly labelled `KPI TEST DATA` entities for host, drive, pool,
storage-path, and enclosure contexts. It exercised available, stale, temporarily
unavailable, and unsupported current states; raw, hourly, and daily history; a
numeric rollup envelope; a categorical state timeline; a nullable gap; 24-hour,
7-day, and 30-day ranges; an intentional bounded history 503; desktop/320-pixel
layouts; light/dark themes; keyboard activation; and horizontal-overflow checks.
Console assertions permitted only the scenario's deliberate latest-snapshot 404 and
history 503. No unexpected browser error or request fan-out was accepted.

### Retained browser evidence

These are deterministic test-data artifacts, not beta or physical-provider
telemetry:

| Artifact | Dimensions | SHA-256 |
|---|---:|---|
| `frontend/test-results/WO-KPIUI-003/desktop-light-system-context.png` | 1440 x 2364 | `fcdec524a477c48644c43be127366700c5881d18e64cf70bb95dd5bd1f8d8102` |
| `frontend/test-results/WO-KPIUI-003/desktop-light-drive-rollup.png` | 1440 x 2360 | `99a5812ddeb72d210a7a16ffb35343c1c55c69b6ee1136cf8ab0e1effd83e5d6` |
| `frontend/test-results/WO-KPIUI-003/desktop-dark-state-history.png` | 1440 x 2017 | `7349a34464c90b397888d0e292409556dbbc3d111edfa8d6291735a84b746ae1` |
| `frontend/test-results/WO-KPIUI-003/desktop-dark-path-context.png` | 1440 x 2178 | `de8288b015fd92bb5e58d7b4d228c18aa06cdeac23227128daedf79a318b7ca8` |
| `frontend/test-results/WO-KPIUI-003/mobile-320-enclosure-unavailable.png` | 320 x 2977 | `16445f89cd05898a9bbf72b745648c034913bb7fa0064d70bb8281a37c22d4f2` |
| `frontend/test-results/WO-KPIUI-003/mobile-320-history-error.png` | 320 x 2305 | `8fa48bcebb1072408cf29d72546a7f802420a6cae023dea82ac1cda4f8515912` |

No physical provider was exercised. Catalog entries remain graphable only when the
selected real entity/provider reports them; unavailable values remain honest. This
run makes no physical-hardware, beta-deployment, or provider-certification claim.
KPIUI-05, KPIUI-06, KPIUI-08, KPIUI-18, KPIUI-20, and dependent rows were not
started.

### KPIUI-15 fail-closed identity correction — 2026-08-25

Supervisor review identified that a supplied but missing stable ID could still fall
through to a matching display name or sole same-type entity. The resolver now treats
every non-empty stable ID as authoritative: exact type + stable ID or no match. An
unresolved contextual selection clears and suppresses current cards, persistent
history, and advanced analysis; invalidates/aborts prior history work; disables the
metric/range/graph controls; and issues no history or analytics request for a
different entity. The storage selector remains available so the user can explicitly
choose different reported storage. That recovery is labelled as an explicit choice,
not a substitute for the missing requested identity.

Focused resolver/component coverage proves both prohibited resolver fallbacks, zero
history/top/endurance/anomaly requests and no retained/default values while blocked,
then one valid history request after explicit recovery. Final results after the
correction were 21/21 focused frontend tests, 203/203 complete frontend tests, a
clean TypeScript production build, 1/1 focused Chromium, and two complete
retry-disabled Chromium passes at 41/41 each. The existing 200-visit/200-range-change
memory soak remained bounded in both complete passes.
