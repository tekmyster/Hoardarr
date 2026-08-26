# WO-APP-006-C1-F3C result

## Result

- **Archive/repository verification: PASS.** The sole authorized request returned artifact `9601372214`; its byte length and SHA-256 exactly match the locked metadata. The extracted repository passed its signed/indexed repository verifier and both host and guest `SHA256SUMS` checks.
- **Retained-base simulation: FAIL.** The one authorized 109-root simulation executed exactly once and exited `100`. APT rejected the corrected repository's fresh `InRelease` with `NO_PUBKEY 1495B2CD95543BF5` because F3C replaced only `/opt/hoardarr/offline-repository`, as ordered, while the retained F2 guest's fixed `signed-by` path still contained the prior build's key. APT retained its stale package indexes; the simulation therefore reproduced the previous incomplete systemd-family conflict instead of evaluating the corrected index.
- **F3: FAIL / not yet proven against the retained base.** The repository artifact is internally coherent, but the retained-base transaction did not consume it.
- **C1: FAIL.** F3C neither authorized nor ran the ordinary two-pass gate.
- **OWNER-10: FAIL.** No LINSTOR, DRBD 9, DRBD Reactor, LINSTOR Gateway, kernel/Secure-Boot, or Proxmox-sidecar work was performed.

No source, test, package policy, manifest, workflow, production appliance, live service, credential, network, or protected media was changed. No workflow was dispatched and no package install was executed.

## Evidence

### Identity and single artifact request

- Work order SHA-256: `08fcdacafdcc5f17948a7a79164336a577ff14aeb6fa4e0274eebd3940385aa8`.
- Required and observed starting local/origin HEAD: `c1f822b3ed115bebfa7a4c9805c52d9f4d3fa88e`.
- F3B handoff SHA-256: `39ceafed3f828937b6cf76dc7203956db6487674dc32faca8827867bfbbef08e`.
- F3B transferred zero bytes: `.codex-temp\f3b-32953633660` was absent before F3C.
- Artifact: ID `9601372214`, name `hoardarr-offline-install-inputs`, run `32953633660`, build job `98130395836`, head `87043c98a35c231288ef40a99620bd80a067c751`, expiry `2026-08-29T09:41:39Z`.
- Locked/fresh metadata size: `1,054,964,607` bytes.
- Locked/fresh metadata digest: `sha256:87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`.

Before contacting GitHub, F3C created exactly
`C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f3c-32953633660`
with `[System.IO.Directory]::CreateDirectory`, proved its resolved path was under the repository `.codex-temp`, wrote/read/deleted one `f3c-sink-canary` with `[System.IO.File]`, and proved the final archive path absent. PowerShell was `7.6.5`.

Exactly one native request was issued:

```powershell
gh api repos/tekmyster/Hoardarr/actions/artifacts/9601372214/zip > C:\Users\dmessana\Documents\troubleshooting\Hoardarr\.codex-temp\f3c-32953633660\artifact-9601372214.zip
```

It exited `0` after approximately 124 seconds. The resulting archive is `1,054,964,607` bytes with SHA-256 `87c90870111cb81cb2aefc262874de188a50c662cbe4c0d03fe42780db42929a`. There was no retry or second network request.

### Extraction and repository

- ZIP entries: `1,067`; validated free of absolute paths, `..`, and duplicates before one extraction.
- Extracted files/bytes: `1,067` / `1,054,732,565`.
- Top level: exactly `offline-repository` and `hoardarr-release.tar.gz`.
- Release archive: `32,394,004` bytes; SHA-256 `209cc34c51f709db20a2d5110cf77b1f2819245ac17d60e471704862c0256c8d`.
- Repository files/bytes: `1,066` / `1,022,338,561`.
- Repository `SHA256SUMS` SHA-256: `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`.
- Package manifest: `526` identities; SHA-256 `6b5e4948d2a8f4286c99e9ea943c4f71a0715c9029aa597483d90071e4d3eac5`.
- Compatibility-family evidence: schema `1`, one family; SHA-256 `8e1b3792b9b848bebdefb9d18bd1d843b349c8133d6c072ee7f94b5633e08ce8`.
- Root evidence: `109` lines / `109` unique; SHA-256 `80798f4c96f2a14f817f13fa967ed376401f59b1f6da3f6e395adf3422832b1e`.

The exact family is coherent at `255.4-1ubuntu8.17`:

| Member | Manifest architecture |
|---|---|
| `systemd` | `amd64` |
| `systemd-sysv` | `amd64` |
| `systemd-timesyncd` | `amd64` |
| `systemd-resolved` | `amd64` |
| `udev` | `amd64` |
| `libudev1` | `amd64` |
| `libsystemd0` | `amd64` |
| `libsystemd-shared` | `amd64` |
| `libpam-systemd` | `amd64` |
| `libnss-systemd` | `amd64` |
| `systemd-dev` | `all` |

`scripts/build-offline-apt-repository.py verify` accepted the extracted repository. The guest mounted the transfer image read-only, and source and destination strict `SHA256SUMS` checks both completed. Their logs are each `51,966` bytes with SHA-256 `973ee1a0694c05596a701329790501599cc7678e5388143d63de2e8b4e135344`; both manifest hashes equal `ac133ac9e8401cf0b5c8d82333b3ce743541cc1700c1be9446e7d6f1b3e258e9`.

### Retained source and disposable topology

Retained F2 backing before and after:

- path: `.codex-temp\f2-32943411481\pass-2\offline-evidence\pass-2\os.qcow2`;
- physical size: `3,968,401,408` bytes;
- virtual size: `34,359,738,368` bytes;
- SHA-256: `2393e9b4c90aeaf56a580634b3538defd0c3af26fa30c3a3846654962e3ba60d` before and after;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0` before and after.

Transfer image:

- IMAPI ISO9660/Joliet image containing only the corrected repository;
- `1,023,664,128` bytes;
- SHA-256 `631c6257fc6332b2235ce917319a0731fdb65465b75163c9f26937538e56a59b`;
- 7-Zip test: PASS, `1,066` files / `8` folders, payload bytes `1,022,338,561`.

Fresh overlay after shutdown:

- direct backing file: the exact retained F2 QCOW2 above, not the D3 overlay;
- physical/virtual size: `1,046,085,632` / `34,359,738,368` bytes;
- SHA-256 `117472ddab62a260a6fca16cbeed4c72f0423293fbea0fe94672b5c7e52e7a8a`;
- dirty/corrupt: false/false;
- `qemu-img check`: exit `0`, `check-errors=0`.

QEMU was `11.1.0 (v11.1.0-12130-ge470268ff4)`, TCG, 4 GiB, two vCPUs. Its only drives were the writable overlay and the repository ISO with `media=cdrom,readonly=on`; it used the retained named kernel/initrd, `root=/dev/vda2 rw init=/bin/bash console=ttyS0`, loopback-only serial control, and explicit `-nic none`. `qemu-argv.json` SHA-256 is `11b77b2d683bdde999e4f60044235882b6cc67d561a0a64866c28371ca3030d6`; it names no protected media. Guest readback contained exactly interface `lo`, and `ip` reported it `state DOWN`. `/proc/net/route` was empty.

The files actually used were unchanged before/after:

- `vmlinuz-6.8.0-100-generic`: `15,030,664` bytes; SHA-256 `528d909745819a1464848a2c4d91c609db4f54b33ccae7069aad4178fb34606f`.
- `initrd.img-6.8.0-100-generic`: `74,664,884` bytes; SHA-256 `8e5094cfcc9cc0d6790a38efdc19521dca4a394a34ddbba48608c211e580e474`.

This explicit path/hash readback exposes that the F3B work order and D3 handoff invert these two hash labels. The files were selected by exact filename and booted successfully, but F3C should have treated the documented label mismatch as a pre-QEMU stop. This procedural discrepancy is reported rather than hidden; no retained file was modified.

### One exact APT transaction

The guest used the existing production source:

```text
deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main
```

Environment/options were the retained F2/D3 values: `DEBIAN_FRONTEND=noninteractive`, guarded `LVM_SYSTEM_DIR`, `NEEDRESTART_MODE=l`, `UCF_FORCE_CONFFOLD=1`, exact source list, no source parts, no languages, zero retries, and HTTP/HTTPS proxies disabled. The exact 109 `name=version` roots came from the corrected repository evidence. `exact-argv.txt` is `3,136` bytes with SHA-256 `ba646a52dd859ba32652316c9ba7946489a0ce96f0f428c31a96b27f1819fc29`.

`apt-get update` ran once before the simulation. Its `932`-byte log (SHA-256 `b12b6a3e2c7808a3a294298b68ae888fbe4784a633a32b5038462e75f330b624`) contains the decisive precondition failure:

```text
The following signatures couldn't be verified because the public key is not available: NO_PUBKEY 1495B2CD95543BF5
Some index files failed to download. They have been ignored, or old ones used instead.
```

Exactly one `apt-get --simulate --no-install-recommends install` transaction then ran. It exited `100`; its log is `3,745` bytes with SHA-256 `3a93e7bf1da1be73b68ee4fb5ec3659992e52bab4a62cc1703a303e3bb0c6079`. Counts were:

```text
Inst=0
Conf=0
Remv=0
Purg=0
removal phrases=0
downgrade phrases=0
unauthenticated phrases=0
```

Because the new signed index was not accepted, `apt-cache policy` still showed no corrected candidates for `systemd-sysv`, `systemd-resolved`, `libpam-systemd`, or `libnss-systemd`. The first retained-index dependency summary was:

```text
init : PreDepends: systemd-sysv but it is not going to be installed
```

The simulation never installed, removed, purged, or downgraded anything. The guest synchronized and powered off; serial SHA-256 is `d782e246f698729a0cf6536a203def64daa61d25dc80e4cb3636cb19fc1fec62`.

## Defects

1. F3C's repository-only transfer did not update the fixed `signed-by` keyring. Because the repository signing key is build-specific, the retained F2 guest could not authenticate the corrected artifact and reused stale indexes.
2. The corrected family therefore remains untested against the retained base despite passing repository-internal verification.
3. The F3B/D3 locked evidence transposes the kernel and initrd hash labels. F3C booted the correctly named files but crossed a documented fail-closed identity gate.
4. The host evidence controller received the guest's complete nonzero marker and then raised on the expected TCP reset during poweroff. Evidence was recovered read-only from the clean overlay; no simulation was rerun.
5. C1 and OWNER-10 remain failed.

## Blockers

- **F3:** blocked on one fresh retained-base preflight that makes the corrected repository's exact embedded archive key available at the source list's trusted keyring path before `apt-get update`, with an explicit hash/readback gate.
- **C1:** remains FAIL; no two-pass run is authorized by F3C.
- **OWNER-10:** remains FAIL independently.

## Next action

Authorize one replacement retained-base preflight on a new direct F2-backed overlay. Transfer both the already-verified corrected repository and its exact embedded `hoardarr-offline-archive-keyring.gpg`; before APT, verify that keyring against the artifact and place it at the existing source list's fixed trusted path inside only the disposable overlay. Then run one update and one unchanged 109-root simulation. Separately correct the transposed kernel/initrd labels in the successor's locked evidence before boot. Do not change product source, roots, or dispatch CI unless that preflight exits 0 with the coherent 11-member transition.
