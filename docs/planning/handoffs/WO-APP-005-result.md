# WO-APP-005 Result

## Result

- **Disposition:** IMPLEMENTED AND SOFTWARE-VERIFIED; Supervisor acceptance and any live reset
  remain pending.
- **Dispatch baseline:** `rc/0.3.11-validation` at
  `880f017dacea6cfd8390c11eaf9bd55c0730a2a6`.
- **Implementation commit:** `b42ee651d5ed2f8acbf318a1247222937abe7540`
  (`Add audited local administrator password reset`).
- Added the supported local operator surface:

  ```text
  hoardarr auth reset-password --username <exact-user> \
    --expected-active-sessions <count> --password-stdin --json
  ```

- The command rejects before reading standard input unless it is local root, the API unit is
  inactive, the configured database is an existing file-backed SQLite database, migrations are
  current, one exact normalized active administrator resolves, its verifier is supported, and its
  exact active-session count matches the operator precondition.
- The accepted password is read only from standard input and is never placed in process arguments,
  JSON, audit details, exceptions, logs, committed tests, or this handoff. The reset uses the same
  non-empty password policy as first-owner setup and rejects reuse of the current password.
- A read-only preflight captures the exact user ID, normalized username, role, enabled state,
  verifier identity/version and user-scoped active-session count. One later `BEGIN IMMEDIATE`
  transaction rechecks all of those facts before mutation.
- The protected transaction updates exactly one administrator row, deletes only that user's
  sessions whose expiry is later than the authentication boundary, preserves expired session
  history, writes one sanitized `auth.password.reset` audit event, verifies the new Argon2 verifier,
  verifies zero remaining active sessions for the target, and commits once.
- Count/identity drift, same-password reuse, hash generation/readback failure, exact-update/delete
  mismatch, audit failure and database failure roll password, session and audit changes back
  together. A commit exception produces a distinct `password_reset_commit_uncertain` response and
  is never retried automatically.
- Other users, their passwords and sessions, target and other-user API tokens, setup claims, roles,
  usernames, historical audits and non-authentication state remain unchanged. API tokens are not
  silently revoked.
- Added migration `0029_user_active_state`. Existing users migrate active. Disabled users now fail
  closed for password login, stored sessions and API-token authentication as well as reset
  preflight; this is an actual account state, not a reset-only sentinel.
- No command was run on Hoardarr-Build. No KeePass file was opened or changed. No live database,
  password, session or credential was read or mutated. No service was restarted, no package was
  deployed, and no UI, website, virtualization, networking, storage or protected disk was touched.

## Evidence

### Disposable transaction and safety cases

`backend/tests/test_password_reset_admin.py` uses only newly migrated disposable SQLite databases
and proves:

- one exact administrator with two active sessions resets successfully; both active sessions are
  revoked while its expired session remains;
- zero-active-session reset succeeds deterministically and remains audited;
- missing, ambiguous, disabled and non-administrator users fail closed;
- invalid usernames, invalid expected counts, unsupported verifiers, empty passwords, unchanged
  passwords and preflight count mismatch make no reset change and create no success audit;
- username/identity drift and an active session inserted after the first in-transaction count are
  detected and rolled back;
- injected password-hash generation, password readback, session-delete and audit failures restore
  the original verifier, active sessions and audit count;
- another administrator's verifier/session, the target API token, setup claim, prior audit and all
  expired session history survive a successful reset;
- root, API-service, supported-database, database-existence and migration gates reject before the
  password reader is called;
- target-state preflight rejections also occur before the password reader is called;
- success, every tested rejection and an injected exception containing secret-like/path/SQL text
  emit only the bounded JSON contract, never raw exception, SQL, database path, verifier, token or
  CSRF material;
- an injected commit exception calls the reset exactly once, rolls the open transaction back,
  emits the explicit uncertain result and does not retry;
- migration from revision 0028 preserves an existing user as active; disabled accounts cannot
  authenticate through password, session or API token.

### Executed validation

All commands ran against the scoped implementation tree/commit from `backend`.

| Check | Result |
|---|---|
| exact WO-APP-005 disposable cases | `25 passed`, 10.40 s |
| focused password/session/CLI/migration/API/backup suite | `113 passed`, 1 existing dependency warning, 41.26 s |
| complete backend suite | `716 passed, 13 skipped`, 1 existing dependency warning, 77.94 s |
| `python -m ruff check .` | passed |
| `python -m compileall -q src tests` | passed |
| mypy 1.17.1 on all three changed production modules | passed, no issues |
| migration 0028 to current head with existing user | passed; user read back active |
| `uv build --wheel` | passed |
| isolated installed-wheel CLI smoke | migration, initial disposable owner, zero-session reset, JSON readback and installed `--help` all passed |

The 13 complete-suite skips are the existing Windows skips for Linux descriptor-relative mover,
mount/path, ownership, mode and ACL behavior. None covers this SQLite/authentication command. The
warning is the existing Starlette/httpx TestClient deprecation.

Built-wheel evidence is temporary validation output, not deployed or committed:

```text
path: C:\Users\dmessana\AppData\Local\Temp\hoardarr-wo-app-005-57b0a3646aa54fbdb906ec9726451698\hoardarr-0.3.11-py3-none-any.whl
bytes: 537960
sha256: aff98f0a5151620daeab5db44a8beeeb20ea6e9e827c0b90d33cd4442dc9c585
```

Committed scoped file identities:

```text
backend/src/hoardarr/auth/service.py
  fc4ddb23c4b45050497c35248aa6d13ab1445f1dde1e9d3d05cbfa4dd3e9b4ae
backend/src/hoardarr/cli.py
  c2dea08fdcce7a358fe7851de5202e8837f15e5a51cc47dc110d534595d8497f
backend/src/hoardarr/db/models.py
  acb6a8442aa109ff62606260880c956ea869afdbb6bbbf4a675e18183c68d9e3
backend/src/hoardarr/db/migrations/versions/0029_user_active_state.py
  ee578292a84fbff0863e25540ca8606a2c5cc59f8ef24cd5e9bf2ffa1b7d3081
backend/tests/test_password_reset_admin.py
  aeda79c083a2aab203ebd819795c6a5ec2b3816b6062b65e6089fc6881e01538
```

## Defects

- No known WO-APP-005 software defect remains in disposable validation.
- This work order does not prove a live reset, live service coordination or a subsequent browser
  login. Those actions were explicitly prohibited and are not implied by the software result.
- The existing Starlette/httpx deprecation and 13 Windows platform skips remain unchanged.

## Blockers

- Supervisor acceptance is pending.
- Any Hoardarr-Build reset remains blocked on a separate owner-authorized execution order defining
  the exact accepted artifact, fresh active-session count, password generation/transfer, vault
  backup/write/readback, service outage, login verification and incident/rollback procedure.
- A reset cannot restore the old password after a committed transaction unless that credential is
  separately available. Commit uncertainty intentionally requires investigation rather than an
  automatic retry.
- Nothing in this result authorizes Build/KeePass mutation, deployment, service restart, live
  database/session/credential mutation, LAB-10, or infrastructure work.

Final repository state before handoff commit:

- Branch: `rc/0.3.11-validation`.
- Scoped implementation commit: `b42ee651d5ed2f8acbf318a1247222937abe7540`.
- All five implementation/test paths are committed cleanly.
- All inherited unrelated modified and untracked paths remain preserved and unstaged.

## Next action

Supervisor should review commit `b42ee651d5ed2f8acbf318a1247222937abe7540`, independently rerun
the exact disposable cases and complete backend gate, inspect the pre-input guards, exact-row/count
preconditions, rollback/readback behavior and non-retry commit-uncertainty contract, then accept or
reject WO-APP-005. Do not execute a live reset without a separate owner-authorized work order.
