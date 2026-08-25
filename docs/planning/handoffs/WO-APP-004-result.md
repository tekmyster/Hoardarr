# WO-APP-004 Result

## Result

- **Disposition:** IMPLEMENTED AND SOFTWARE-VERIFIED; supervisor acceptance and live deployment
  remain pending.
- **Dispatch baseline:** `rc/0.3.11-validation` at
  `6385549a85a32c002adc6bcb5406d4079c4d8667`.
- **Implementation commit:** `bf5ccca617bfdabd985842b582bb0df3c3787952`
  (`Make login resilient to SQLite writer contention`).
- The exact disposable failure is SQLite primary result `SQLITE_BUSY` (`5`) at
  `create_session(...).flush()`: a concurrent worker owns the WAL write lock while a valid login
  tries to insert its session. The old route has one connection-level five-second busy wait, no
  safe retry boundary, and no normalized `OperationalError` translation, so exhaustion becomes
  generic HTTP 500.
- The minimal correction separates password verification from mutation, reserves the SQLite
  writer with `BEGIN IMMEDIATE`, retries only a failed pre-write busy reservation, revalidates the
  verified password hash after reservation, and writes one session plus one success audit in one
  transaction. No flush or commit is ever replayed.
- Invalid credentials still return the same 401 document for an existing or absent account and
  create no session. Password rehashing remains supported but now occurs only inside the reserved
  write transaction.
- Database failures now return bounded Problem Details (`authentication_database_busy` or
  `authentication_database_unavailable`) and log only a normalized cause, fixed operation stage,
  request ID, and disposition. Raw SQL, parameters, usernames, paths, hashes, tokens, and exception
  text are never logged or returned.
- No credential was reset or printed. No live login was attempted. No session was revoked. No live
  database was written, checkpointed, or integrity-scanned. No service was restarted, no artifact
  was deployed, and no UI, website, virtualization, network, storage, or protected disk was changed.

### Login transaction diagnosis

Before this correction, a successful-password request performed:

1. Origin, rate-limit, and authentication-concurrency checks.
2. `SELECT users ...` followed by Argon2 verification.
3. Optional password-hash mutation in the same ORM session.
4. `AuthSession` add and `flush()`; this is the first required database write.
5. Success-audit add.
6. Commit.
7. Browser session and CSRF cookies only after commit.

The cookies were already correctly ordered after durability. The defect was step 4: an active WAL
writer can exhaust `PRAGMA busy_timeout=5000`, raising `OperationalError`; nothing in the route
translated or safely retried it, and the global middleware could report only its class.

An initial disposable hypothesis test also checked read-snapshot promotion after another writer
committed. Python 3.13 / SQLite 3.50 in the local test runtime did **not** reproduce
`SQLITE_BUSY_SNAPSHOT` because pysqlite's legacy transaction control does not begin the database
read transaction on that `SELECT`. That hypothesis was rejected. The accepted reproduction instead
holds a real concurrent `BEGIN IMMEDIATE` writer, matching the live WAL writer evidence, and obtains
`SQLITE_BUSY` from the old session-insert boundary.

After this correction, a valid login performs:

1. The same bounded password verification, without mutating the ORM row.
2. Copies only the durable user ID and observed password hash, then rolls back the read phase.
3. Reserves a writer with `BEGIN IMMEDIATE` on SQLite.
4. If and only if that reservation reports normalized `database_busy`, rolls back, waits 50 ms,
   and makes one final reservation attempt. The existing SQLite timeout applies independently to
   each attempt, so database waiting is bounded to approximately 10.05 seconds maximum.
5. Re-reads the user while holding the writer reservation. A constant-time comparison checks that
   the password hash is still the one that was verified. If it changed, the supplied password is
   reverified against the current row while the writer is reserved; failure produces the normal
   indistinguishable rejected-login path.
6. Applies a needed Argon2 rehash, inserts exactly one session, inserts exactly one success audit,
   flushes, and commits once.
7. Publishes cookies only after the successful commit, unchanged from the accepted behavior.

Once any auth row has been flushed, there is no retry. A flush or commit error rolls back and returns
the normalized 503, avoiding duplicate sessions or duplicate success audits under commit uncertainty.

### Normalized diagnostic contract

Internal classification is bounded to:

| SQLite/DBAPI condition | Internal safe cause |
|---|---|
| `BUSY`, `LOCKED`, or bounded equivalent text | `database_busy` |
| `READONLY` | `database_read_only` |
| `FULL` | `database_full` |
| `CORRUPT`, `NOTADB` | `database_integrity_error` |
| `CANTOPEN`, `IOERR`, `PERM` | `database_unavailable` |
| anything else | `database_operation_failed` |

Numeric SQLite primary codes are preferred. Raw DBAPI text is a classification fallback only and is
never emitted. Remote clients receive `authentication_database_busy` only for retryable contention;
all other database causes collapse to `authentication_database_unavailable`. Both are HTTP 503,
carry `Retry-After: 1`, and use the same non-secret detail.

Fixed log stages are:

```text
credential_read
writer_reservation
credential_revalidation_read
rejection_audit_write
rejection_commit
session_write
success_audit_write
success_commit
```

Example safe log shape:

```text
Authentication database failure request_id=<uuid> cause=database_busy stage=writer_reservation disposition=retrying
```

## Evidence

### Secret-safe pinned A/B readback

The observations below were made on 2026-08-25 through the accepted
`codex_ops01_ed25519` key (public fingerprint
`SHA256:iMYwsqS+lg3LvwSlwWPlU20qIDiO8sG+pX1nYuI5PFI`), strict host-key checking,
the accepted `hoardarr-a-lab` alias for A, and the pinned IP identity for B. Every command was
read-only. No password or vault material was used or emitted.

| Observation | Hoardarr-A | Hoardarr-B |
|---|---:|---:|
| host / address | `hoardarr-a` / `10.81.200.114` | `hoardarr-b` / `10.81.200.140` |
| app version | `0.3.11` | `0.3.11` |
| active relevant units | all five API/worker/status/account/storage units | same |
| API + worker database openers | 2 | 2 |
| DB / WAL / SHM bytes | 1,258,131,456 / 10,806,792 / 32,768 | 1,277,698,048 / 14,040,992 / 32,768 |
| filesystem free | 11,542,007,808 bytes | 10,812,657,664 bytes |
| SQLite mode/version | WAL, normal locking, 3.45.1 | same |
| users / sessions / API tokens | 1 / 2 / 0 (accepted inventory) | 2 / 3 / 0 (accepted inventory) |
| password hashes needing current rehash | 0 | 0 |
| recent API `OperationalError` login-class log entries | 2 | 1 |
| raw SQLite cause in live journal | unavailable; current log has class only | same |
| 10-second WAL writer-lock samples | 8 / 200; longest run 8 samples (about 0.4 s) | 56 / 200; longest run 56 samples (about 2.8 s) |

An instantaneous `/proc/locks` read also found the worker holding SQLite WAL shared-memory write
byte 120 on A, while API and worker held the expected read locks. Both nodes had current metric
samples and retention state. A's WAL modification time advanced during a separate bounded two-second
observation. These facts prove live concurrent writers and exclude bad credentials, obsolete hash
parameters, full filesystems, read-only mounts, and mismatched deployed route/engine code as the
supported diagnosis.

The deployed A/B login-route SHA-256 was
`9961b0769cdc9771bd37a15e5ea6d5b66c4a2afe91fd0a0e9cf2998f9324bf49`; deployed engine SHA-256 was
`8858ed159f93f5fac1f3e848d593f6742da26c0ec165d7ddcab03c700354669c`. Both matched the pre-fix local
files exactly. Because the accepted live log intentionally omitted `OperationalError.orig` and this
work order prohibited a new live login/write, the historic live extended SQLite code cannot be
retrospectively recovered. The exact reproducible cause is `SQLITE_BUSY`; the new normalized
diagnostic will make any future live cause and stage reviewable without exposing raw details.

### Exact disposable reproduction and correction tests

The deterministic reproduction creates a migrated disposable SQLite database using the production
engine pragmas, confirms WAL, seeds one user, one existing session and one audit, then:

1. opens a separate service/worker session;
2. reserves `BEGIN IMMEDIATE` and flushes an uncommitted audit row;
3. opens the login session, reads the user and verifies the valid password;
4. executes the former `create_session(...).flush()` path with a test-only 25 ms busy timeout;
5. receives DBAPI `sqlite_errorcode` primary `5` and name beginning `SQLITE_BUSY`;
6. rolls both transactions back and reads back exactly the original one session and one audit.

The corrected API tests then prove:

- first reservation busy, writer released at the retry boundary, HTTP 200, `/auth/me` 200, exactly
  one new session and one success audit;
- both reservations busy, bounded HTTP 503, no session/audit delta, no cookie, normalized retry and
  failure log stages;
- an unavailable-database exception containing a private path, SQL, token parameter and credential
  marker returns/logs none of them;
- a long-lived WAL reader remains valid while login writes and commits successfully;
- existing-account and absent-account invalid credentials produce identical 401 documents, no
  session, and one rejected audit each;
- an old Argon2 hash is rehashed under the reserved writer and the new hash verifies successfully.

### Executed validation

All test commands ran from `backend` against the committed source unless noted.

| Check | Result |
|---|---|
| seven exact WO-APP-004 API/reproduction cases | `7 passed`, 44 deselected, 1 existing dependency warning, 5.00 s |
| expanded API/auth/session/migration/worker/telemetry ownership suite | `125 passed`, 1 existing dependency warning, 40.46 s |
| complete backend suite, fresh pytest process | `691 passed, 13 skipped`, 1 existing dependency warning, 68.52 s |
| `python -m ruff check src tests` | passed |
| `python -m compileall -q src tests` | passed |
| mypy 1.17.1 on both changed production modules | passed, no issues |
| migration head/upgrade | included in focused and complete suites; passed |
| `uv build --wheel` | passed; `hoardarr-0.3.11-py3-none-any.whl`, 534,898 bytes |
| isolated installed-wheel API smoke | ready 200, setup claim 201, login 200, `/auth/me` 200 |

The 13 complete-suite skips are the existing Windows skips for Linux descriptor-relative mover,
mount/path, ownership, mode, and ACL behavior. None is part of the WO-APP-004 database/auth safety
case. The warning is the existing Starlette/httpx TestClient deprecation.

An exploratory mypy run that included the full pre-existing `tests/test_api.py` reported three
unrelated existing annotations at lines 3423, 3477 and 3888. The two changed production modules
passed the applicable scoped gate. Ruff, runtime coverage, and the complete suite cover the new test
cases.

The first installed-wheel smoke reached all four expected HTTP results but its harness then failed
to remove its temporary database because the seed engine had not been disposed. The harness was
corrected to dispose that engine and repeated against the same wheel; it passed cleanly. This was a
test-harness cleanup issue and caused no product-code change.

Built-wheel evidence (temporary validation artifact, not committed):

```text
path: C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-004-d193e60da3a34b91b7af6a4d8a87c2fe\hoardarr-0.3.11-py3-none-any.whl
sha256: e5f683ddaa7ae1f368e46de754485e4e543488eb7df1edc645df0a04f5580dd0
```

Committed scoped files and hashes:

```text
backend/src/hoardarr/api/routes/auth.py
  980bcb5b6367a7c6c8bcc8c46a06bf290ff3f834e83e997f75d7e95390ed9431
backend/src/hoardarr/auth/service.py
  32b7fb1f67083be60179cf8c0c1bc78d96433284a078f4cdc1372b35de4e2b30
backend/tests/test_api.py
  266da0460cf2a3c8cf6eb295b09495c5353e958dd92e0e2ccac239c9a189ce3e
```

## Defects

- No known WO-APP-004 software defect remains in the disposable reproduction.
- A/B still run the accepted pre-fix artifact. The work order explicitly prohibited deployment,
  restart, live login and live database mutation, so live valid-credential success is **not** claimed.
- The existing deployed logs cannot disclose the historic SQLite extended code. The corrected code
  adds the required safe cause/stage diagnostic for the next separately authorized deployment.
- The worker's telemetry/retention batch legitimately holds a SQLite writer transaction. This work
  order did not redesign worker persistence; it made authentication coexist safely through a bounded
  reservation/retry contract.
- The existing Starlette/httpx deprecation and 13 Windows platform skips remain unchanged.

## Blockers

- Supervisor acceptance is pending.
- Live A/B verification requires a separately authorized artifact deployment, service restart and
  valid login. None was performed here.
- This result does not authorize LAB-10, credential/session mutation, database maintenance,
  virtualization, networking, storage, UI, website, or infrastructure changes.

Final repository state at handoff writing:

- Branch: `rc/0.3.11-validation`.
- Scoped implementation commit: `bf5ccca617bfdabd985842b582bb0df3c3787952`.
- The three implementation/test paths are committed cleanly.
- All unrelated inherited dirty and untracked paths remain preserved and unstaged.

## Next action

Supervisor should independently review `bf5ccca617bfdabd985842b582bb0df3c3787952`, reproduce the
seven exact cases and focused/full checks, inspect the pre-write-only retry boundary and sanitized
diagnostic contract, then accept or reject WO-APP-004. Do not deploy or exercise a live login without
a separate authorized work order.
