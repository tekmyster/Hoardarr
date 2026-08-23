# Tiered storage and download workloads

Hoardarr's default tiering model is file-aware. It treats SSD storage as a
download workspace and HDD storage as the durable completed-data tier. This is
different from a transparent block cache and is easier to explain, monitor,
recover, and migrate.

The implemented transfer planner and durable executor distinguish torrent and
Usenet retention, validate source/destination identities and free space, reject
cross-filesystem hardlinks, use temporary destinations for restart recovery and
never delete the source before verified publication. Client-native move support
remains adapter-capability dependent; the local mover is the tested fallback.

## Recommended download flow

1. The download client writes incomplete data, unpacking work, and temporary
   files to an SSD-backed workspace.
2. The client verifies the completed payload while it is still on SSD.
3. The client moves the payload to its HDD-backed completed directory and
   updates its own job location.
4. A torrent is rechecked when the client requires it, then resumes seeding
   from HDD.
5. Only after the HDD transition succeeds is the completed job exposed to
   Sonarr, Radarr, or another importer.

Hoardarr should configure and observe a client's native incomplete/completed
directory support where possible. qBittorrent, Transmission, Deluge, SABnzbd,
and NZBGet need separate, versioned adapters because their state and move APIs
are not interchangeable. A Hoardarr mover is the fallback, not the first
choice. A fallback transition must pause the job, flush writes, copy to a
temporary destination, verify it, atomically publish the destination, update
the client path, recheck when supported, resume the job, and only then remove
the source.

The operation is a persistent job with restart recovery. A power failure or
full destination must leave either the verified SSD source or a recoverable
partial destination; it must never be represented as a successful move.

## Why torrents require special handling

Torrent clients continue to own completed files while checking and seeding.
Moving those files behind the client's back can produce missing-file errors,
full rechecks, duplicate downloads, or deletion of the wrong copy. Cross-
filesystem moves are copy-and-delete operations rather than atomic renames.

Hardlinks also cannot cross filesystems. To retain ARR hardlink behavior, the
HDD completed-download directory and media library must ultimately live on the
same underlying filesystem or on a mergerfs layout whose branch-selection
policy keeps both paths on the same filesystem. The SSD tier should normally
hold only incomplete work. Hoardarr must test this topology and warn before it
claims that hardlinks are available.

An optional advanced policy may keep completed torrents on an SSD hot-seeding
tier for a ratio or time target. The UI must explain that importing to an HDD
library during that interval requires a second copy because an SSD-to-HDD
hardlink is impossible.

## User-facing modes

- **Download workspace** is the recommended wizard choice. SSD contains
  incomplete and processing data; completed data and seeding move to HDD.
- **Hot-seeding workspace** is an advanced file-aware policy with explicit
  time, ratio, and capacity limits.
- **Read cache** is an advanced block-device feature using a supported cache
  implementation. Its failure and recovery behavior must be shown before
  creation.
- **Manual placement** exposes tier selection and lifecycle rules without
  automatic movement.

The Storage page now exposes the implemented file-aware mover as **Download &
landing tier**. It only becomes actionable when a real Storage Group contains
a `cache` or `landing` backend and a real `data` backend. The panel does not
infer tier membership from SSD/HDD media type. It asks for the completed file
and library destination, shows the backend's immutable transfer decision, and
then starts a durable Activity operation. Torrent imports retain their verified
source until seeding is reported complete or the operator explicitly cleans it
up. Usenet imports require download, repair, unpack, and verification stages
before the mover will accept them.

The review states whether the paths share a filesystem and whether the actual
method is a hardlink, copy, or move. A cross-filesystem transfer is never called
a hardlink. Progress shown by this surface is the durable operation state; it
does not synthesize a percentage when the worker has not reported one.

ZFS L2ARC is a read cache, a SLOG is a synchronous intent-log device, and a ZFS
special vdev stores selected allocation classes. None of them should be
presented as a general SSD download write cache. A special vdev is pool-critical
unless it is itself sufficiently redundant.

## Guardrails and telemetry

The tier manager records the download-client job ID, torrent/info hash when
applicable, source and destination storage IDs, byte counts, verification
evidence, and each state transition. It also enforces:

- configurable SSD high- and low-water marks;
- reserved free space and admission control for new jobs;
- destination-capacity and health checks before a move;
- no movement while unknown writers have a file open;
- bounded concurrent moves and configurable I/O priority;
- SSD wear, temperature, media-error, and TRIM monitoring;
- retryable failure states and an explicit operator recovery path;
- a dry-run explanation of paths, expected copies, and hardlink eligibility.

The simple wizard asks what should be accelerated, which SSD pool is the
workspace, which HDD pool receives completed data, and whether torrents must
continue seeding. Capacity thresholds and safe client-native movement are the
defaults. Block-cache layout, cache modes, and custom mover controls remain in
Advanced.

## Runtime tools

The `tiered-storage` bootstrap profile supplies observation and transfer tools
without configuring a cache or touching a disk. `inotify-tools` detects local
filesystem changes, `lsof` helps identify open files, and `rsync`/`rclone`
support restartable verified fallback transfers. `bcache-tools`, LVM/device-
mapper tooling, and thin-provisioning metadata tools support explicitly chosen
advanced block-cache designs. Hoardarr's backend, rather than a shell script,
owns the persistent state machine and download-client API adapters.
