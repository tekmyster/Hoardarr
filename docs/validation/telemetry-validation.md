# Enterprise telemetry validation

Validated on 2026-08-22 from Windows 11, Python 3.13 build host with the locked
Python 3.12-compatible project environment, Node 24.18.0, npm 11.16.0, uv
0.11.28, SQLite, and Playwright Chromium. Hoardarr remained 0.3.10 Beta 1.

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
| systemd verification ran before its referenced Hoardarr executables existed | Install the wheel in an empty Python 3.12 environment, expose that disposable environment at the unit's production entry-point path, then run `systemd-analyze verify` | CI workflow contract and YAML validation; remote execution pending |

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

**4-DEVICE MERGERFS SOFTWARE VALIDATION: CI IMPLEMENTED — EXECUTION RESULT
PENDING.** **4-PHYSICAL-SSD VALIDATION: PENDING.**

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

The harness records UTC timestamps for idle, small-file, sequential-write,
concurrent-write, sequential-read, actual fio random-read, mixed-read/write,
return-to-idle, browser-disconnected, browser-reconnected and service-restart
phases. The headless collector persists CPU, per-member and mergerFS aggregate
KPIs with zero API clients. It verifies files and bytes on all four members,
writes-today increase, pool KPI presence, samples throughout the disconnected
interval, reconstruction through the authenticated `/api/v1/telemetry/history`
endpoint after reconnect, real rollup creation, and history survival across a
fresh collector process. Independent evidence includes before/after
`/proc/diskstats`, one-second `iostat` and `vmstat`, plus `df` and `du`. Evidence is written to
`dist/validation/mergerfs-persistent-telemetry.json`; the artifact does not yet
exist because the workflow containing the uncommitted worktree could not be
executed remotely without committing and pushing.

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

The workload has not run, so phase timestamps, duration, generated byte count,
member placement, Linux comparison values, API-down continuity, graph shapes,
and cleanup outcome are all **execution pending**, not inferred. The CI artifact
is the required evidence boundary for those fields.

The pending workload commands are fixed in the harness rather than left to CI
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
- The allowed Standard Codex Security scan completed as scan
  `fd24caaa-8183-4e77-bcae-3a23af916b60` with zero reportable findings, but was
  focused/partial, ran against its starting snapshot while the working tree
  continued changing, and is not equivalent to the blocked Deep Scan. Manual
  review, adversarial tests, Ruff, npm audit, pip-audit, secret/output searches,
  structured subprocess review, query bounds, entitlement tests, and malformed
  provider fixtures supplied fallback assurance.
- `tests/integration/verify-linux-telemetry.py` exited 1 on Windows with
  `This read-only validation requires Linux.` The Ubuntu 24.04 CI job contains
  this command, but that remote job was not executed from this task.
- The Ubuntu release plan succeeded. Release bundle build exited 2 because this
  host is Windows/unknown OS/AMD64/Python 3.13 without pip rather than Ubuntu
  24.04/x86_64/Python 3.12. No success was inferred.
- GitHub authentication was available, but these workflow files exist only in
  the uncommitted worktree. Repository workflow listing therefore had no remote
  revision to execute. Committing/pushing was outside authorization, so the
  Linux, loop-device, QEMU, systemd, install, and rollback jobs remain **CI
  IMPLEMENTED — EXECUTION RESULT PENDING**.

## Hardware validation pending and limitations

No physical storage controller, HBA, enclosure, multipath, FC, FCoE, ZFS, MD,
SnapRAID, SMB, NFS, or iSCSI target was exercised in this KPI run. Provider
parsers and honest missing-value behavior are isolated software evidence only.
Vendor and kernel values not reported by the local provider remain `Not
reported`. The current host also did not execute the privileged Linux
loop-device profile. See `hardware-certification.md` for the product-wide table.

Current release classification remains **NOT RELEASE-READY** because Ubuntu
release/install/rollback, disposable Linux block-device execution, the managed
Deep Scan release gate, and physical hardware certification remain outstanding.
The bounded KPI software paths exercised in this report passed both local
validation passes.
