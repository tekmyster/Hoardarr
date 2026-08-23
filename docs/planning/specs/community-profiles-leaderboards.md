# Hoardarr — Community Profiles, Leaderboards, Build Gamification, and Public Stats Addendum

Continue from the current Hoardarr and hoardarr.com implementation state.

Do not restart or redesign the telemetry architecture already being built.

This is an additive community feature that sits on top of existing telemetry and hardware lifecycle data.

The goal is to let users optionally create a Hoardarr community profile and participate in public leaderboards and build showcases.

Hoardarr itself must remain fully usable without an account.

# 1. Account model

Add optional hoardarr.com user accounts.

Registration should support a simple modern flow such as:

* email/password or passwordless email
* optional social login later
* username/display name
* public profile slug
* avatar optional
* country optional/public toggle
* profile privacy settings

Do not require a hoardarr.com account to:

* install Hoardarr
* configure storage
* use telemetry locally
* receive updates
* use normal ARR integrations

An account is required only for community/profile features such as:

* public leaderboard participation
* claiming an installation
* publishing a build
* badges
* comparisons
* public achievements

# 2. Installation claiming

Allow a user to link one or more Hoardarr installations to their hoardarr.com account.

The flow should be simple and secure.

Example:

1. User signs into hoardarr.com.
2. Hoardarr generates a short-lived claim token or QR/link.
3. User confirms ownership from the local Hoardarr UI.
4. hoardarr.com associates that installation ID with the account.

Do not expose permanent installation credentials in the browser.

Allow:

* one account → multiple Hoardarr systems
* rename systems
* unclaim system
* transfer/remove association

# 3. Leaderboard participation

Leaderboard participation should be opt-out or opt-in according to the product decision, but make the setting explicit and visible.

Preferred community UX:

> Participate in Hoardarr community leaderboards

On by default for users who create an account, with a clear opt-out.

If disabled:

* account can still exist
* system can still use telemetry
* build remains private
* rankings are not public

Do not force anonymous telemetry users into public leaderboards.

# 4. Public build profile

Allow each claimed installation to have an optional public build page.

Possible public fields:

* build name
* owner/display name
* country/region if user chooses
* Hoardarr version
* server platform
* CPU model
* RAM
* total raw storage
* usable storage
* used storage
* drive count
* drive models
* controller models
* enclosure models
* storage layouts
* mergerFS/ZFS/SnapRAID/MD
* controller redundancy
* SSD/NVMe cache
* ARR applications in use
* Plex/Jellyfin/Emby
* uptime
* age of build
* badges
* leaderboard positions

Every field should have privacy controls where appropriate.

Do not publish:

* full serial numbers
* local IPs
* FQDNs
* API URLs
* usernames
* internal paths
* file/folder names
* share names

unless the user explicitly opts into that specific field.

# 5. Build privacy levels

Support simple privacy states:

### Public

Build appears on leaderboards and profile pages.

### Leaderboard only

Build contributes ranking values but detailed profile remains limited/private.

### Private

No public ranking or build details.

### Anonymous leaderboard

Optional mode where the system ranks but appears under a pseudonymous build name rather than the user's account.

# 6. Leaderboard categories

Create multiple categories rather than one meaningless global score.

Examples:

### Most storage

* raw capacity
* usable capacity
* used data

### Biggest data hoard

Rank by actual used capacity, not raw installed capacity.

### Most drives

* total drive count

### Most diverse storage

* number of storage technologies/layouts

### Oldest drive fleet

* oldest active drive by power-on hours
* average fleet age

Use caution with this category so it does not encourage unsafe hardware use.

### Most battle-tested

Possible metric based on:

* drive replacements
* recoveries
* parity rebuilds
* controller failovers

Do not reward destructive failures themselves.

### Most redundant

Possible factors:

* parity/redundancy
* controller redundancy
* multiple paths
* verified backups if Hoardarr can actually know this

Do not claim redundancy based on guesses.

### Fastest storage

Only compare systems with valid measured benchmark/workload evidence.

Do not rank based on advertised device specifications.

### Highest IOPS

Use standardized Hoardarr benchmark methodology if implemented.

### Lowest latency

Same requirement: standardized benchmark.

### Most efficient

Possible ratio:

* usable capacity / raw capacity

But make clear that protection overhead is a design choice, not necessarily inefficiency.

### Most power-efficient

Only if actual power data is available.

Do not estimate wattage from model tables and present it as measured.

### Most complex build

Possible score based on:

* controllers
* enclosures
* paths
* pools
* technologies

This can be a fun community category.

### Sickest system

This should be a community/fun composite category rather than pretending to be objective engineering truth.

Potential inputs:

* total capacity
* hardware diversity
* redundancy
* drive count
* performance
* topology complexity
* community votes

Expose the scoring formula.

# 7. Community-voted categories

Add categories that are explicitly subjective.

Examples:

* Sickest system
* Cleanest build
* Most ridiculous homelab
* Best budget build
* Best recycled hardware build
* Best storage shelf
* Most creative setup

These should rely on user-submitted photos/build descriptions, not telemetry alone.

Voting requires a Hoardarr account.

Add anti-abuse protections.

# 8. Standardized benchmarks

If performance leaderboards are included, build a standardized benchmark mode.

Examples:

* sequential read
* sequential write
* random read
* random write
* mixed workload

Because SSD endurance matters:

* default to read-heavy tests
* limit write volume
* display planned write budget
* require explicit confirmation for write benchmarks
* record device type
* record tested storage topology
* record benchmark version

Leaderboards should compare only results produced by compatible benchmark versions.

Do not accept arbitrary user-entered performance numbers as verified results.

# 9. Verified versus self-reported stats

Mark statistics clearly.

### Verified by Hoardarr

Collected from the local installation through telemetry or benchmark.

### User-provided

Entered manually.

### Community voted

Based on votes.

Do not mix these without labels.

# 10. Badges and achievements

Add fun achievements such as:

* First TB
* 10 TB club
* 100 TB club
* 1 PB club
* First parity-protected pool
* First controller redundancy
* Dual-controller club
* 10-drive club
* 24-drive club
* 60-drive club
* SAS addict
* NVMe addict
* mergerFS user
* ZFS user
* SnapRAID user
* Drive survivor
* Secondhand hero
* Old iron
* Data hoarder
* ARR completionist

Avoid achievements that encourage:

* unsafe temperatures
* excessive SSD writes
* ignoring failing drives
* intentionally causing failures

# 11. Drive lifecycle badges

Because Hoardarr tracks drives across installations, add optional interesting lifecycle achievements such as:

* Drive reached 50,000 power-on hours
* Drive moved between 2 Hoardarr systems
* Drive retired cleanly
* Drive survived a rebuild
* Oldest active drive in the community

Only surface these when the underlying telemetry supports them.

# 12. Build score

If you add an overall build score, make it transparent.

Do not create an opaque proprietary score.

Break it into components such as:

* capacity
* redundancy
* performance
* complexity
* efficiency
* longevity
* community votes

Show the formula and weights.

Allow the user to see why their score changed.

# 13. Seasons

Consider optional leaderboard seasons.

Examples:

* Monthly
* Quarterly
* All-time

This prevents permanently unbeatable early systems.

Keep all-time records separately.

# 14. Build history

A claimed build should have a timeline.

Examples:

* first Hoardarr install
* added drive
* expanded pool
* added controller redundancy
* replaced drive
* crossed 100 TB
* upgraded cache tier
* controller failover
* major Hoardarr update

Let users choose which timeline events are public.

# 15. Multiple builds per user

Support users with multiple systems.

Example profile:

`Tek`

Builds:

* Main Hoard
* Backup Hoard
* Lab Shelf
* Tiny NVMe Monster

Each can have separate:

* privacy
* leaderboard participation
* badges
* public fields

# 16. Global leaderboard UI

Build hoardarr.com pages such as:

`/leaderboards`

Categories:

* Storage
* Drives
* Performance
* Redundancy
* Longevity
* Community
* Weird & Wonderful

Filters:

* global
* country
* storage technology
* drive count class
* capacity class
* home lab type

Avoid filters that expose individual location below the country/region level.

# 17. In-app leaderboard integration

Add a lightweight Hoardarr UI entry:

`Community`

Show:

* current rank
* badges
* build profile status
* latest achievements
* leaderboard participation toggle

Do not clutter the core Storage workflow.

# 18. Community build page

Public build pages should look good enough that users want to share them.

Show:

* build name
* owner
* badges
* total storage
* storage topology summary
* drive fleet
* controller/enclosure hardware
* storage software/layout
* apps
* selected performance metrics
* build timeline
* optional photos

Do not expose private telemetry.

# 19. Photos

Allow users to optionally upload build photos.

Examples:

* server rack
* storage shelves
* drive cages
* cable management

Apply reasonable:

* file size limits
* content-type validation
* image processing
* metadata stripping

Strip EXIF/GPS metadata by default.

# 20. Affiliate links

Public hardware/build pages can support affiliate links later.

Example:

Drive model:

`Seagate Exos X18 18 TB`

Possible actions:

* Find new
* Find used
* Compatible alternatives

Affiliate/recommendation generation must be based on aggregate hardware/model data, not exposing individual users to retailers.

Clearly disclose affiliate links.

# 21. Hardware popularity statistics

Create public aggregate pages such as:

* Most popular HDDs
* Most popular SSDs
* Most common HBAs
* Most common enclosures
* Most common drive capacities
* Most common ZFS layouts
* Most common mergerFS + SnapRAID layouts

Useful for affiliate content and purchasing guidance.

# 22. Reliability statistics

Build carefully worded aggregate drive statistics.

Examples:

* number of Hoardarr-observed drives
* median power-on hours
* health-warning prevalence
* observed retirement age
* percentage seen in multiple systems

Do not label these as manufacturer failure rates unless the dataset/methodology supports that conclusion.

Prefer:

> Observed in Hoardarr systems

instead of:

> Failure rate

# 23. Used hardware insights

This could become particularly valuable.

For models frequently appearing with substantial power-on hours, show aggregate statistics like:

* typical power-on hours at first Hoardarr sighting
* median purchase/intake age where inferable
* current health-warning prevalence
* median remaining observed service duration

Do not infer purchase source unless the user explicitly records it.

# 24. Optional purchase-source field

Allow users to optionally record where hardware came from:

* eBay
* Micro Center
* Amazon
* manufacturer
* recycler
* local marketplace
* other

This should be explicit user-entered metadata.

Do not infer a retailer.

Later this enables analytics such as:

> Drives acquired used from marketplace sources had a median first-seen power-on age of X.

Only publish aggregates with adequate sample size.

# 25. Purchase price

Optional user-entered field:

* purchase price
* currency
* purchased new/used
* purchase date

This can enable fun analytics:

* cost per TB
* best value builds
* storage cost over time

Keep private by default.

Allow users to opt into public display.

# 26. Value leaderboard

Possible fun categories:

* Lowest cost per TB
* Best used-hardware value
* Most storage under $1,000

Only use user-provided price data where present.

Do not fabricate market prices.

# 27. Leaderboard integrity

Because users control their own Hoardarr installation, telemetry cannot be treated as cheat-proof.

Implement reasonable integrity controls:

* server-authenticated installation
* signed/authenticated telemetry envelopes
* server-side sanity checks
* duplicate detection
* benchmark validation
* impossible-value rejection
* rate limits

For performance categories, mark:

`Verified benchmark`

only when produced by the standardized Hoardarr benchmark.

For other categories use:

`Reported by Hoardarr`

rather than implying independent certification.

# 28. Anti-cheat

Detect obvious manipulation such as:

* impossible drive capacities
* duplicate hardware repeated excessively
* impossible power-on-hour movement
* implausible benchmark values
* replayed submissions
* manually altered benchmark payloads where detectable

Allow suspicious results to be excluded from public rankings without deleting the user's local data.

# 29. Public ranking privacy

Never rank or display:

* IP address
* FQDN
* full serial
* installation token
* filesystem paths
* filenames
* share names

without a specific explicit public-field opt-in.

# 30. Profile/leaderboard consent

Add:

`Settings → Community`

Controls:

* Hoardarr.com account
* Claimed installation
* Participate in leaderboards
* Public build profile
* Anonymous leaderboard mode
* Show country
* Show hardware details
* Show drive models
* Show storage capacity
* Show applications
* Show performance
* Show build timeline

Provide:

**Preview public profile**

so the user can see exactly what others will see.

# 31. Account deletion and unclaim

Support:

* unclaim installation
* remove build from leaderboards
* delete community profile
* delete uploaded photos
* disconnect installation from account

Do not delete the user's local Hoardarr storage/configuration.

# 32. Public API

If useful later, expose a bounded public API for aggregate leaderboard/statistics data.

Do not expose raw telemetry records.

Potential public data:

* top builds
* hardware popularity
* aggregate drive lifecycle stats
* leaderboard categories

Rate-limit it.

# 33. Gamification must not encourage unsafe behavior

Do not reward:

* writing excessive data to SSDs
* ignoring SMART failures
* highest drive temperature
* most failed drives
* most controller failovers
* running storage at dangerously high utilization

Gamification should reward interesting builds, scale, reliability, creativity, longevity, and efficiency.

# 34. Implementation tests

Test:

* account registration
* installation claiming
* multiple installations per account
* leaderboard opt-out
* anonymous leaderboard
* profile privacy
* public-field filtering
* build score calculation
* badges
* drive lifecycle achievements
* cross-install drive identity
* standardized benchmark verification
* suspicious result rejection
* account unclaim
* account deletion
* public profile preview

# 35. End-to-end scenario

Execute:

```text id="71foju"
Install A
   ↓
anonymous heartbeat
   ↓
user creates hoardarr.com account
   ↓
claims Install A
   ↓
enables leaderboard
   ↓
public build appears
   ↓
badges calculated
   ↓
leaderboard rank calculated
   ↓
user disables leaderboard
   ↓
build disappears from public rankings
   ↓
local Hoardarr continues working normally
```

Also test a user with Install A + Install B.

# 36. Final product philosophy

Hoardarr community features should create:

> “Look at this ridiculous storage server I built.”

energy.

They should not create:

> “I have to give an account to use my own storage.”

energy.

The self-hosted storage product remains independent.

Community participation is an optional layer on top.

# Final report

Report:

1. Account system.
2. Installation claiming.
3. Profile privacy.
4. Leaderboard opt-out.
5. Leaderboard categories.
6. Build score.
7. Badges.
8. Standardized benchmarks.
9. Verified/self-reported distinction.
10. Public build pages.
11. Build timeline.
12. Multiple-build support.
13. Hardware popularity stats.
14. Drive lifecycle stats.
15. Purchase-source/value analytics.
16. Affiliate integration boundary.
17. Anti-cheat controls.
18. Community Settings UI.
19. Public-profile preview.
20. End-to-end tests.
21. Remaining limitations.

The intended outcome is:

**Make Hoardarr builds fun to compare and share without making community participation a prerequisite for using Hoardarr.**


