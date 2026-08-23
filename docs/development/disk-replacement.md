# Disk onboarding and replacement

Hoardarr treats planned retirement and failed-disk recovery as two different workflows. A healthy
disk is replaced through the Storage Group lifecycle: add and activate the new backend, make it the
preferred write target, drain the old backend, verify the copy, then retire or explicitly release the
old disk. The Storage Group namespace and ARR/media paths remain unchanged throughout that flow.

## Failed SnapRAID data disk

Advanced Storage exposes **Replace a SnapRAID data disk** only when Hoardarr can parse an actual
SnapRAID configuration. The user selects one named data member and one detected, selectable,
unassigned replacement drive. The immutable review binds:

- the exact SnapRAID configuration digest and named data entry;
- the replacement drive's stable identity, geometry, capacity, and hardware snapshot;
- the filesystem and deterministic managed mount path;
- the latest bounded partition/signature scan, including incomplete-scan state.

The replacement is destructive only to the reviewed replacement drive and requires exact
`I AGREE` approval. The executor resolves the live disk through `/dev/disk/by-id`, revalidates its
identity, active-use state, and destructive-review signature summary before mutation, then revalidates
identity and active use before every destructive preparation step. It creates a GPT partition and
filesystem, mounts it, and changes exactly one SnapRAID `data` entry. Persistent by-id partitions use
Linux's `-part1` suffix; a kernel device name is never persisted as storage identity.

Recovery follows SnapRAID's documented lost-data-disk sequence using structured argv:

1. `status` validates the changed configuration.
2. `-d <name> fix` reconstructs only the missing named data disk.
3. `-d <name> -a check` reads and verifies the reconstructed member before parity state changes.
4. `sync` records the recovered state and makes parity current.

The durable journal exposes mounting, validation, reconstruction, audit verification, and final
synchronization as real Activity phases. Before reconstruction starts, a failure restores the prior
configuration and removes only Hoardarr-created mount/fstab state. Once reconstruction may have
started, a failure is reported as **needs attention** rather than pretending the operation rolled
back. The replacement filesystem is not reported current until the audit check and sync succeed.

## Existing data and limitations

Detected partitions or signatures are shown prominently. An incomplete signature scan is treated as
unknown existing data, never as a blank drive. Hoardarr does not format the missing member's old path,
does not recreate the Storage Group, and does not claim to recover files that were never included in
the last successful SnapRAID sync.

Generic ZFS and Linux MD member replacement require provider-specific resilver/rebuild semantics and
remain separate roadmap work; Hoardarr does not expose a decorative generic replacement control for
them. Matching physical-media certification remains distinct from disposable Linux loop validation.
