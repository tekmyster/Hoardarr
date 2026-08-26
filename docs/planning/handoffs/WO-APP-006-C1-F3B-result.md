# WO-APP-006-C1-F3B result

## Result

- **Locked artifact metadata verification: PASS.** Fresh API readback exactly matched artifact ID, name, size, digest, expiry, run, job, and implementation head.
- **Artifact download and repository verification: FAIL — stopped before transfer.** The one authorized transfer command failed locally before a writable archive sink existed. It was not retried.
- **Retained-base simulation: NOT EXECUTED.** The work order requires an immediate stop on transfer failure.
- **F3: PENDING / FAIL at F3B artifact gate.** The compatibility-family implementation remains built and verified in CI, but its retained-base simulation remains unproven.
- **C1: FAIL.** No pass 1 or pass 2 was run.
- **OWNER-10: FAIL.** No cluster package or integration work was performed.

No artifact was downloaded, no workflow was dispatched, no repository was extracted, no overlay or transfer image was created, no QEMU command ran, no package simulation or install ran, and no retained/protected media was attached or changed.

## Evidence

### Identity

- Work order SHA-256: `fbb3b5a3281cced7b619ac1b0c09b990ef44ccade6eb2e3c53253a8ebb270e33`.
- Required and observed starting local/origin HEAD: `fa88d5df9d81258439d966a74e8a121c26e0df6f`.
- Automatic run: `32953633660`, event `push`, conclusion `success`.
- Build job: `98130395836`, conclusion `success`.
- Implementation head: `87043c98a35c231288ef40a99620bd80a067c751`.
- Offline-install job: `98132662989`, conclusion `skipped`, zero steps.

Fresh pre-transfer metadata for the only authorized artifact:

- ID: `9601372214`.
- Name: `hoardarr-offline-install-inputs`.
- Size: `1,054,964,607` bytes.
- Digest: `sha256:87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.
- Expired: `false`.
- Created/updated: `2026-08-26T09:41:44Z`.
- Expiry: `2026-08-29T09:41:39Z`.
- Artifact workflow run/head: `32953633660` / `87043c98a35c231288ef40a99620bd80a067c751`.

### Single failed transfer command

The prescribed destination did not exist before execution. The one command attempted to create it and then issue the artifact request:

```powershell
$root='C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f3b-32953633660'
if(Test-Path -LiteralPath $root){throw 'F3B target already exists'}
New-Item -ItemType Directory -LiteralPath $root | Out-Null
$zip=Join-Path $root 'artifact-9601372214.zip'
gh api repos/tekmyster/Hoardarr/actions/artifacts/9601372214/zip > $zip
```

PowerShell rejected the directory operation before creation:

```text
New-Item: A parameter cannot be found that matches parameter name 'LiteralPath'.
```

Because the directory did not exist, native-command redirection then failed to open the archive sink:

```text
Could not find a part of the path
'...\.codex-temp\f3b-32953633660\artifact-9601372214.zip'.
```

The wrapper classified the command as failed and stopped. Per the explicit no-retry rule, no corrected invocation was attempted.

Immediate readback after the failure:

```json
{
  "directory_exists": false,
  "archive_exists": false,
  "head": "fa88d5df9d81258439d966a74e8a121c26e0df6f",
  "origin": "fa88d5df9d81258439d966a74e8a121c26e0df6f"
}
```

No archive bytes were retained and no digest could be computed. The appliance artifact was not requested or downloaded. No earlier artifact was requested or downloaded.

### Gates not reached

Because F3B stops on any transfer failure, none of these were executed or inferred:

- extracted-tree and repository `SHA256SUMS` verification;
- repository verifier;
- package/file counts, package-manifest and family-evidence hashes;
- 109-root artifact readback;
- exact eleven-member version/architecture readback;
- retained source/kernel/initrd preflight;
- overlay or read-only transfer image creation;
- QEMU topology or no-NIC guest proof;
- APT update or the single exact 109-root simulation;
- solver action counts or output hashes;
- retained source before/after comparison.

The locked retained F2 source and reusable D3 kernel/initrd paths were never opened by F3B. No QEMU argv existed, so protected media could not have been attached.

## Defects

1. The transfer orchestration used `New-Item -LiteralPath`, which is not supported by the available PowerShell `New-Item` command. This caused a local pre-transfer failure.
2. F3's corrected retained-base transaction remains unverified because the no-retry safety rule correctly prevented a second download attempt.

## Blockers

- **F3:** blocked on a fresh, separately authorized single artifact-download attempt using a prevalidated destination-creation command.
- **C1:** remains FAIL; F3B authorizes no two-pass execution.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one replacement artifact-download attempt into the still-absent `.codex-temp\f3b-32953633660` directory, first proving the exact directory-creation primitive locally without contacting GitHub, then issuing one transfer for artifact `9601372214`. If and only if that succeeds and its archive digest matches, resume the unchanged F3B repository and retained-base preflight. Do not dispatch CI or alter product source.
