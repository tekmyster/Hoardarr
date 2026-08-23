# Hoardarr — Telemetry, Hardware Lifecycle Tracking, hoardarr.com Ingestion, and Offline Queueing

Continue from the exact current Hoardarr repository and release-candidate state.

Do not restart or undo validated storage, telemetry, controller-redundancy, installer, appliance, KPI, graphing, Guided-mode, or Advanced-mode work.

This is an additive product feature.

The goal is to create a privacy-conscious but useful **Hoardarr fleet telemetry and hardware lifecycle system** backed by:

`hoardarr.com`

The system should help understand:

* number of active Hoardarr installations
* Hoardarr version adoption
* system hardware
* storage hardware
* drive models and capacities
* drive age and health
* drive intake
* drive replacement
* drive decommission
* the same physical drive moving between Hoardarr installations
* controller/enclosure usage
* storage-layout popularity
* capacity/free-space trends
* ARR application adoption
* feature usage
* country/timezone distribution
* compatibility trends

The central system must continue functioning when installations temporarily have no internet access.

# 1. Telemetry levels

Implement explicit telemetry levels.

## Level 0 — Required anonymous installation heartbeat

This minimal heartbeat remains enabled.

Send only what is needed to measure Hoardarr installations and software adoption:

* persistent random Hoardarr installation ID
* Hoardarr version
* build/commit identifier where available
* telemetry schema version
* operating platform family
* heartbeat timestamp

Do not include hardware inventory, application inventory, drive identity, paths, filenames, or detailed configuration.

The UI must describe this accurately.

Do not show a master control claiming all communication is disabled if this heartbeat continues.

## Level 1 — Hardware and product telemetry

Enabled by default with user opt-out.

Collect non-content system information.

### System

* CPU vendor/model
* CPU architecture
* core/thread count
* installed memory
* OS
* kernel
* hardware platform vendor/model where exposed
* virtualization status where determinable

### Storage hardware

* drive vendor
* drive model
* capacity
* HDD/SSD/NVMe
* interface/protocol
* logical sector size
* physical sector size
* controller family/model
* enclosure family/model
* firmware where appropriate
* SMART/NVMe overall health
* temperature summary
* power-on hours where exposed
* SSD/NVMe percentage used
* lifetime host writes where exposed
* normalized error/health summary

### Drive lifecycle

* first seen
* last seen
* added to storage
* storage role
* health changes
* warnings
* removal
* replacement
* decommission
* failure reason where actually known

### Storage configuration

* individual disk
* mergerFS
* SnapRAID
* ZFS
* Linux MD
* filesystem type
* redundancy level
* controller redundancy
* path count
* SSD/download tier
* number of pools
* total/raw/usable capacity
* free-space percentage

### Applications detected

Product identity only by default:

* Plex
* Jellyfin
* Emby
* Sonarr
* Radarr
* Lidarr
* Readarr
* Whisparr
* Prowlarr
* qBittorrent
* SABnzbd
* NZBGet
* Transmission
* Deluge
* supported additional integrations

Do not send:

* API keys
* application passwords
* application URLs
* usernames
* FQDNs
* filesystem paths

at Level 1.

### Feature usage

Examples:

* Guided mode used
* Advanced mode used
* mergerFS created
* SnapRAID configured
* ZFS configured
* MD configured
* ARR path automation used
* drive imported
* drive replaced
* controller redundancy enabled
* multipath enabled
* cache/download tier enabled
* secure wipe used

Track product events, not user content.

# 2. Enhanced diagnostics — explicit opt-in

Create a separate opt-in level.

Potential fields:

* share names
* mount names
* folder categories
* file-size distributions
* file counts
* more detailed topology
* selected detailed provider diagnostics

Keep this off by default.

# 3. Content diagnostics — separate explicit opt-in

Only this level may transmit actual:

* file names
* folder names

Never transmit file contents.

Do not combine this consent with normal hardware diagnostics.

# 4. Never transmitted

Never transmit:

* passwords
* API keys
* session tokens
* CHAP secrets
* SNMP communities
* private signing keys
* encryption keys
* file contents

Add tests specifically preventing these values from entering telemetry payloads.

# 5. Country/timezone detection during installation

During first setup detect:

### Timezone

Prefer the host OS timezone.

### Country/region

Infer a suggested country from available sources such as:

* timezone
* OS locale
* optional coarse network lookup when internet access exists

Do not silently treat inferred country as guaranteed correct.

Present both to the user for confirmation:

> Country / Region
> Timezone

Allow editing.

Store:

* confirmed country
* confirmed timezone
* detection method

Possible detection methods:

* `os_timezone`
* `locale`
* `network`
* `manual`

The confirmed setting becomes canonical.

Do not retain source IP solely for geographic analytics.

# 6. Installation identity

Generate a persistent random Hoardarr installation ID.

Requirements:

* high-entropy UUID or equivalent
* not derived from hostname
* not derived from MAC address
* not derived from disk serial
* survives restart
* survives upgrades
* survives reinstall when configuration is retained
* explicit reset mechanism

This represents the Hoardarr installation, not a person.

# 7. Cross-system pseudonymous drive identity

Hoardarr should recognize the same physical drive if it later appears in another Hoardarr installation.

Build canonical identity using the strongest available hardware identifier.

Suggested priority:

1. WWN/NAA
2. NVMe NGUID
3. NVMe EUI-64
4. normalized serial + vendor/model fallback

Do not transmit full raw serial numbers by default.

Generate a deterministic **pseudonymous drive ID** that resolves to the same value across Hoardarr installations.

Version the algorithm:

`drive_identity_version`

Do not describe this as cryptographically anonymous.

It is a persistent pseudonymous hardware identifier.

# 8. Partial serials

Where useful, include only a short serial fragment.

Example:

`…A93F`

Use this only for human troubleshooting/display.

The pseudonymous hardware ID is the central lifecycle identity.

Do not allow logs to expand this into the full serial.

# 9. Cross-system lifecycle

The central system should support:

```text
Drive X
  first observed in Installation A
  → used
  → ages
  → health changes
  → removed
  → appears later in Installation B
  → lifecycle continues
```

Track:

* first Hoardarr sighting
* latest sighting
* number of installations observed
* power-on hours at each observation
* SMART/NVMe health progression
* warnings
* temperature summaries
* host writes/endurance where available
* storage roles
* replacement/decommission events

Do not interpret movement between installations as proof of ownership transfer.

# 10. Heartbeats, snapshots, and events

Do not transmit complete snapshots continuously.

Use distinct message types.

### Heartbeat

Small indication that an installation remains active.

### Inventory snapshot

Occasional current hardware/configuration state.

### Lifecycle event

Examples:

* `drive_first_seen`
* `drive_assigned`
* `drive_health_changed`
* `drive_warning`
* `drive_removed`
* `drive_replaced`
* `drive_decommissioned`
* `pool_created`
* `pool_expanded`
* `storage_layout_changed`
* `controller_added`
* `controller_redundancy_enabled`
* `application_detected`
* `application_removed`
* `hoardarr_updated`

### Periodic aggregate observation

Examples:

* capacity
* free-space percentage
* health summary

Do not upload high-frequency local KPI history by default.

# 11. hoardarr.com ingestion API

Build the central ingestion service for:

`hoardarr.com`

Use versioned endpoints.

Conceptual examples:

* `POST /api/telemetry/v1/register`
* `POST /api/telemetry/v1/heartbeat`
* `POST /api/telemetry/v1/inventory`
* `POST /api/telemetry/v1/events`
* `POST /api/telemetry/v1/batch`

Exact endpoint structure may differ if a cleaner architecture is appropriate.

# 12. HTTPS only

All communications must use HTTPS.

Normal TLS certificate validation is required.

Do not add a production option to disable TLS verification.

If TLS fails:

* preserve data locally
* retry later

# 13. Installation registration credential

On registration, hoardarr.com issues a unique installation credential.

Do not embed one universal telemetry secret into every Hoardarr build.

The credential should:

* identify/authenticate one installation
* be stored locally with appropriate permissions
* support rotation
* support re-registration

# 14. Authenticated telemetry envelopes

Each batch should contain concepts such as:

* installation ID
* schema version
* sequence number
* creation timestamp
* unique batch ID
* payload digest

Authenticate the message using the installation credential.

An HMAC-based design is acceptable if correctly implemented.

Server should reject:

* invalid authentication
* malformed signature/MAC
* replayed batch
* invalid schema
* oversized body
* unsupported schema/version

# 15. Client-controlled limitation

Do not claim telemetry is impossible to manipulate.

A user with root/control of their Hoardarr host can fabricate local data.

The server can provide:

* authentication
* tamper detection in transit
* replay prevention
* plausibility checking
* duplicate detection

but not trusted hardware attestation.

Document this honestly.

# 16. Durable offline queue

Implement a persistent local outbound telemetry queue.

This is mandatory.

If internet access or hoardarr.com is unavailable:

* continue collecting eligible events
* persist them locally
* do not require browser presence
* do not keep the backlog only in RAM

The queue must survive:

* API restart
* worker restart
* host restart
* extended internet outage

# 17. Store-and-forward behavior

Implement:

```text
online
  → submit batch
  → server acknowledges
  → remove acknowledged records

offline
  → persist records
  → exponential backoff + jitter

online again
  → batch backlog
  → upload
  → deduplicate server-side
  → acknowledge
  → safely remove local copy
```

# 18. Queue bounding

Prevent uncontrolled local disk growth.

Configure bounds for:

* maximum queued records
* maximum queue bytes
* maximum event age

When pruning is necessary:

Prefer retaining:

* drive lifecycle
* health warnings
* decommission/replacement
* significant configuration changes

over:

* repetitive old heartbeats

Allow repetitive heartbeats to collapse/coalesce where appropriate.

# 19. Retry behavior

Handle:

* no DNS
* timeout
* connection refusal
* TLS failure
* server 500
* server 429
* expired/invalid credential
* schema rejection
* temporary database outage

Use bounded exponential backoff with jitter.

Do not hammer hoardarr.com.

# 20. Dead-letter behavior

Permanently invalid records should not retry forever.

Move them to a bounded diagnostic/dead-letter state with:

* rejection reason
* schema
* timestamp

Allow inspection from Advanced diagnostics.

# 21. Settings → Telemetry & Privacy

Build a real settings page.

Show:

### Anonymous installation heartbeat

Required.

### Hardware & product telemetry

On by default, user can disable.

### Enhanced diagnostics

Off by default.

### Content diagnostics

Off by default.

Also show:

* installation ID
* telemetry endpoint
* connection status
* last successful upload
* last attempted upload
* queued records
* queued bytes
* telemetry schema version
* country
* timezone

Buttons:

* View exactly what is sent
* Send now
* Export pending payload
* Clear unsent optional telemetry
* Reset telemetry identity

# 22. View exactly what is sent

This is mandatory.

Show the exact pending/request payload in a readable JSON/data viewer.

Indicate which telemetry level caused each group of fields to be included.

Do not hide transmitted fields from the user.

# 23. Central data model

Build normalized central concepts for:

* Installation
* InstallationHeartbeat
* Version
* HardwareSnapshot
* Drive
* DriveObservation
* DriveLifecycleEvent
* ControllerObservation
* StorageLayoutObservation
* ApplicationObservation
* CapacityObservation
* FeatureUsageObservation
* GeographicSetting

Use an appropriate server database.

Do not assume appliance SQLite is the correct database for hoardarr.com.

# 24. Aggregate analytics

Implement internal analytics for:

### Fleet

* active installations
* installations by version
* installations by country
* installations by timezone
* upgrade adoption

### Hardware

* CPU models/vendors
* RAM distributions
* platform vendors/models
* controllers
* enclosures

### Drives

* drive vendor/model popularity
* capacity distribution
* HDD vs SSD/NVMe
* power-on hours at first sighting
* health-warning prevalence
* decommission observations
* drives observed in multiple Hoardarr installations

### Storage configuration

* mergerFS usage
* SnapRAID usage
* ZFS usage
* MD usage
* filesystem usage
* controller redundancy adoption
* average pool capacity
* free-space distributions

### ARR ecosystem

* Sonarr adoption
* Radarr adoption
* Lidarr adoption
* Prowlarr adoption
* Plex/Jellyfin/Emby
* common application combinations

# 25. Drive lifecycle analytics

Create useful longitudinal analytics.

Examples:

* age at first Hoardarr sighting
* power-on hours at intake
* time between first sighting and first warning
* power-on hours at decommission
* observed service duration
* model cohorts
* drives seen in multiple installations
* storage roles over life

Avoid publishing formal manufacturer “failure rates” unless methodology genuinely supports them.

Use language such as:

> Observed within Hoardarr installations.

# 26. Purchase metadata — optional

Allow users to optionally store locally and optionally contribute:

* new/used
* purchase source
* purchase date
* purchase price
* currency

Possible source options:

* eBay
* Micro Center
* Amazon
* manufacturer
* recycler
* local marketplace
* other

Never infer purchase source.

Keep this optional.

# 27. Affiliate analytics boundary

Aggregate telemetry may later inform:

* hardware recommendations
* compatibility guidance
* new/used purchase guidance
* affiliate links

Do not send individual telemetry records to retailers or affiliates.

Do not expose installation identity or pseudonymous drive identity to affiliate partners.

Affiliate decisions should be based on aggregate statistics.

# 28. Internal hoardarr.com admin dashboard

Build an authenticated maintainer dashboard.

Sections:

### Fleet

* active installations
* versions
* country/timezone distribution

### Hardware

* CPUs
* RAM
* controllers
* enclosures
* drives

### Drive lifecycle

* first-seen
* power-on age
* warnings
* decommissions
* cross-system sightings

### Storage

* layouts
* capacities
* free space
* redundancy

### Applications

* ARR/media applications

### Ingestion

* request volume
* rejected records
* authentication failures
* duplicate batches
* queue/processing latency
* schema versions

# 29. Telemetry schema evolution

Older Hoardarr versions will remain deployed.

Support:

* telemetry schema version
* client application version
* backwards-compatible server parsing for supported versions
* normalization/migration
* deprecation policy

Do not require every Hoardarr instance to run the newest version to submit telemetry.

# 30. Central retention

Define retention by data type.

Examples:

* raw request/access logs: shorter retention
* heartbeats: moderate retention
* hardware snapshots: appropriate historical retention
* drive lifecycle: long-term
* sensitive opt-in diagnostics: shortest appropriate retention

Drive lifecycle history is intentionally valuable long-term.

# 31. Source IP

The HTTP server inherently sees source IP.

Do not include source IP explicitly in Hoardarr telemetry payloads for geographic analytics.

Prefer confirmed:

* country
* timezone

If server logs contain source IPs, configure an explicit retention policy.

# 32. End-to-end cross-system drive test

Create two disposable Hoardarr installations:

`Installation A`

and:

`Installation B`

Present the same simulated physical drive identity to A.

Upload observations.

Then later present the same drive identity to B.

Verify:

* two distinct installation IDs
* same pseudonymous drive ID
* one central drive lifecycle
* both installation sightings retained
* no raw serial necessary

Then present another drive with:

* same model
* same capacity
* different stable hardware ID

Verify it remains a separate drive.

# 33. Offline queue end-to-end test

Execute:

```text
install online
  → successful telemetry

hoardarr.com unavailable
  → generate events
  → queue persists

restart Hoardarr
  → queue remains

restore hoardarr.com
  → backlog uploads
  → duplicates rejected/deduplicated
  → acknowledged records removed
```

# 34. Secret filtering tests

Explicitly attempt to inject into telemetry structures:

* ARR API key
* Hoardarr API key
* password
* session token
* CHAP secret
* SNMP community

Assert these cannot be serialized into outbound telemetry.

# 35. Completion requirements

Do not stop after implementing an HTTP endpoint.

Completion requires:

* installation identity
* telemetry levels
* country/timezone confirmation
* cross-system pseudonymous drive identity
* lifecycle events
* durable local offline queue
* retries/backoff
* authenticated TLS transport
* replay/duplicate protection
* central persistence
* aggregate analytics
* internal dashboard
* Settings UI
* View Payload UI
* cross-system drive tests
* outage/recovery tests

# Final report

Report:

1. Telemetry levels.
2. Required heartbeat fields.
3. Default hardware/product fields.
4. Enhanced opt-in fields.
5. Content opt-in fields.
6. Installation ID implementation.
7. Country/timezone setup behavior.
8. Drive identity algorithm/version.
9. Cross-system drive tracking result.
10. Offline queue implementation.
11. Queue limits.
12. Retry/backoff behavior.
13. Authentication mechanism.
14. Replay/duplicate handling.
15. hoardarr.com API.
16. Central database architecture.
17. Fleet analytics.
18. Hardware analytics.
19. Drive lifecycle analytics.
20. ARR/application analytics.
21. Optional purchase metadata.
22. Affiliate analytics boundary.
23. Admin dashboard.
24. End-to-end outage/recovery result.
25. Known limitations.

The intended result is:

**Hoardarr installations contribute useful hardware and lifecycle statistics to hoardarr.com while giving users clear visibility into what is transmitted and preserving telemetry through internet outages.**


