# WO-APP-006-C1-F13 Result

## Result

PASS for the bounded observability objective; FAIL for the unchanged complete regression. Both independent Linux jobs produced valid, identical receipts that identify the exact phase-10 state transition. The private manager root was empty immediately before the unchanged condition command. After that command returned status 1, it contained exactly one entry: `systemd-units-load`, a regular file with mode `0444`, UID 0, and GID 0.

The unchanged final emptiness assertion then failed with original status 1. No entry was deleted, allowlisted, reinterpreted, or fixed. No retry, manual workflow, or artifact download occurred. F8, C1, and OWNER-10 remain FAIL.

## Exact commits and authorized diff

- Starting local/origin HEAD observed before editing: `dd943772e2d67c35328f44f737ed9306802f6c47`.
- Work-order identity: 6,634 bytes; SHA-256 `561800EAF028F10370C532E89CB586B86B3D82C3D44083CC0003E888D12FC46F`.
- F12 handoff identity: 6,071 bytes; SHA-256 `80715E95B1546752343374F1E7E659ED6F0E68C2AA428E1DC67B3A2119FD2B37`.
- Implementation commit: `3a5996a3e697addbd8a4d29fb9117287dc8009e8` (`test(appliance): observe private manager root`).
- Authorized implementation diff: only `tests/release/test_offline_appliance.py`, 344 insertions and 1 deletion.
- No production payload, F11 wrapper, F12 hard-link boundary, package policy, workflow, appliance input, or product code changed.

## Receipt contract

- Exact files: `$work/manager-root-before.tsv` and `$work/manager-root-after.tsv`, direct children of the private fixture root and outside `run-systemd` and every bind-mounted subtree.
- Format: deterministic UTF-8/LF; fixed `HMROOT|1|<stage>|status=<value>` header followed by sorted tab-separated entry rows.
- Entry fields are limited to relative path, lstat type, octal mode, numeric UID, and numeric GID.
- No contents, symlink targets, timestamps, inodes, absolute paths, stdout/stderr, environment values, or command output are retained.
- Enumeration is non-following, `-xdev`, depth at most 5, at most 128 entries, relative path at most 192 bytes, and receipt at most 32 KiB.
- Path components are limited to `[A-Za-z0-9_.@:+,-]+`; absolute, empty, dot, dot-dot, traversal, control, duplicate, and unsorted paths fail closed.
- Known type, mode `^[0-7]{3,4}$`, numeric ownership, exact stage/version/status, size, ordering, and full-file grammar are independently validated by Python before diagnostic rendering.
- Snapshot failures use bounded statuses 120–126 and never publish a partial receipt as valid.

## Local QA

- Ruff check — PASS.
- Ruff format/check — PASS; one authorized file formatted and then confirmed stable.
- Python compilation — PASS.
- Focused receipt/parser/PCP/nonactivation/trace/real-PCP selection — 5 run, 3 PASS, 2 expected Windows platform skips, 0 failures, 0.067 seconds.
- Complete `tests/release` discovery — 62 PASS, 5 expected platform skips, 0 failures, 62.365 seconds.
- Parser negatives passed for missing file/header, wrong version/stage/status, overlong/absolute/traversal/control/excess-depth paths, unknown type, invalid mode/UID/GID, duplicate/out-of-order paths, excess entries, oversized receipt, appended text, outside exact path, and a status-bearing before receipt.
- Full generated harness — 49,783 UTF-8 bytes; phases 05–15 retained; exact snapshot/condition ordering and receipt paths verified; Git Bash `bash -n` PASS.
- The unchanged manager-root emptiness assertion remains present exactly twice and generated line 1027 is still its closing instance.
- `git diff --check` — PASS.
- Staged-path gate — exactly `tests/release/test_offline_appliance.py`.

## Automatic CI evidence

- Run `33019337780`, push event, exact head `3a5996a3e697addbd8a4d29fb9117287dc8009e8` — terminal FAILURE as expected from the preserved assertion.
- `release-bundle-systemd` job `98345518301` — 62 tests, 1 failure, 61.773 seconds.
- `backend` job `98345518261` — 62 tests, 1 failure, 60.071 seconds.
- Both jobs have the same terminal trace:
  - phases 01–09 PASS;
  - `HPCP|1|BEGIN|10-host-manager-isolation|status=-|line=-|function=-|label=host-manager-isolation`;
  - `HPCP|1|EXIT|10-host-manager-isolation|status=1|line=1027|function=main|label=host-manager-isolation`.
- Generated line 1027 remains exactly:

  ```bash
  [[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
  ```

### Validated receipt — release-bundle-systemd

```text
HMROOT|1|before|status=-
```

```text
HMROOT|1|after|status=1
ENTRY	systemd-units-load	regular	444	0	0
```

### Validated receipt — backend

```text
HMROOT|1|before|status=-
```

```text
HMROOT|1|after|status=1
ENTRY	systemd-units-load	regular	444	0	0
```

- Comparison: byte-equivalent receipt content in both jobs. Before entry count 0; after entry count 1; condition status 1; identical relative path, type, mode, UID, and GID.
- The receipts show the state transition brackets the exact existing command at generated lines 1023–1024. They do not by themselves establish why the command creates the entry or whether that behavior is an acceptable replacement for the current oracle.
- Successful CI jobs: `central-fleet-postgres` `98345518258`, `frontend` `98345518296`, `installed-appliance-smoke` `98345518321`, `minio-control-plane-backup` `98345518335`, and `ubuntu-installer` `98345518397`.
- CI artifacts were recorded by metadata only and not downloaded:
  - `controller-redundancy-browser-evidence`, ID `9625893173`, 8,013,932 bytes, digest `sha256:865a3227d494e59604bccb990d6e3db504fecbaf5adae33dd7a3caeaf4a5dc46`.
  - `minio-control-plane-backup-evidence`, ID `9625894357`, 1,252 bytes, digest `sha256:9c3d7832f0898ec21e957897de74968e8a2c735845a13c5546a98d53380f69b4`.

## Automatic appliance evidence

- Run `33019337775`, push event, exact head `3a5996a3e697addbd8a4d29fb9117287dc8009e8` — terminal SUCCESS.
- `build` job `98345517870` — SUCCESS, 2026-08-26 22:20:44Z through 22:28:49Z.
- Manual-only `offline-install` job `98347197689` — SKIPPED with zero execution time.
- Artifacts were recorded by metadata only and not downloaded:
  - `hoardarr-appliance`, ID `9626042164`, 4,408,887,396 bytes, digest `sha256:f4b627b4e646fc9b11cb7b4e8884b266d09ff712bf898a412aec8f3a31d051b7`.
  - `hoardarr-offline-install-inputs`, ID `9626045343`, 1,072,360,532 bytes, digest `sha256:eed9a13dd25c4f6c4be2fdd8c38cc5448107a30cf027c6e597bfb1afc8284fa1`.

## Defects and blockers

- F13 closes the evidence gap: the sole new manager-root entry is identified consistently, but its causal implementation semantics are not yet independently established.
- The unchanged oracle still fails, so phases 11–15 are not reached and the complete F8 regression is not accepted.
- F8, C1, and OWNER-10 remain FAIL.

## Next action

Authorize one narrow causal-validation successor using authoritative systemd behavior/source plus an executable disposable private-root reproduction to determine whether `systemd-analyze condition` itself creates `systemd-units-load` without contacting a manager. Only after that proof should a separate order decide whether the final oracle must distinguish this exact local cache marker from manager contact. Do not allowlist or remove the entry based on F13 receipts alone.
