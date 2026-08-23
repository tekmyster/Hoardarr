# Hoardarr unified product and hoardarr.com work queue

Status: **ACTIVE — dependency-ordered implementation in progress**

This is the authoritative execution queue for the three supplied specifications and the additional hoardarr.com website direction. The source specifications are preserved verbatim; a task is not complete unless every bullet, constraint, test, and final-report requirement in its linked source section is satisfied.

## Priority rule

Hoardarr storage functionality and testing remain the highest priority. Website, telemetry, community, affiliate, and optional security work must not regress storage safety, Guided/Advanced workflows, controller redundancy, persistent telemetry, packaging, installer behavior, or release validation.

Priority meanings:

- **P0:** Preserve and reconfirm the working Hoardarr appliance.
- **P1:** Required foundations before hoardarr.com accepts production telemetry.
- **P2:** Public website, aggregate statistics, administration, and operational readiness.
- **P3:** Optional accounts, profiles, forums, community participation, and validation.
- **P4:** Gamification, affiliate features, and optional advanced appliance security.

## Execution gates

Every implementation batch must pass:

1. Focused unit/integration/API/UI tests.
2. Authentication, privacy, secret-filtering, input-bound, and abuse tests applicable to the batch.
3. Clean backend and frontend regressions.
4. Linux packaging/service checks when appliance code changes.
5. Staging validation for hoardarr.com changes.
6. Rollback evidence for website, database, and deployment changes.
7. No production deployment, destructive website replacement, public launch, or production-key publication until separately selected and approved.

## P0 — protect current Hoardarr functionality

| ID | Task | Acceptance | Status |
|---|---|---|---|
| RC-01 | Preserve the 0.3.11 Beta 1 release-candidate storage behavior | No regression to identity, immutable plans, system-disk exclusion, executor recovery, controller redundancy, mergerFS, telemetry persistence, Guided/Advanced flows, ARR paths, installer, update, or rollback behavior. | QUEUED |
| RC-02 | Maintain two-pass release validation | Backend, frontend, accessibility, browser, Linux storage, migration, package, appliance, telemetry, memory, and security-fallback checks agree from two clean states after material batches. | QUEUED |
| RC-03 | Keep physical certification separate | Virtual/fixture validation is never described as physical SSD, enclosure, controller, multipath-provider, or appliance-hardware certification. | QUEUED |
| RC-04 | Preserve bounded external blockers | Managed Deep Security Scan and unavailable physical hardware remain bounded gates; neither stops unrelated implementation. | QUEUED |
| RC-05 | Keep expensive release workflows deliberate and actionable | The two-node QEMU/storage stress workflow is manually dispatched for relevant release validation, does not run on every ordinary push, does not cancel a deliberately started evidence run, and its failure is diagnosed before retry. | SOFTWARE VERIFIED — actionlint; prior failure reconciled to passing successor |

## hoardarr.com website and forum tasks added from the current direction

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| WEB-01 | P1 | Inventory the current www server and site | Record hosting stack, DNS/TLS, services, source, database, assets, content, redirects, analytics, integrations, backups, permissions, secrets locations, and rollback constraints without changing production. | QUEUED |
| WEB-02 | P1 | Create a recoverable current-site archive | Produce timestamped source/content/database/configuration backups, checksums, restore instructions, and a tested staging restore before replacement. Do not call an untested archive recoverable. | QUEUED |
| WEB-03 | P2 | Establish the Hoardarr visual system | Inventory the approved new logos, colors, typography, iconography, light/dark behavior, responsive rules, and accessible contrast. Preserve recognizable Hoardarr/ARR character without copying another community's distinctive assets. | QUEUED |
| WEB-04 | P1 | Select and document the website architecture | Choose the simplest maintainable platform for public pages, ingestion/API, accounts, forum, uploads, moderation, search, analytics, and deployment; define service/data boundaries and direct-to-latest upgrade behavior. | QUEUED |
| WEB-05 | P2 | Build the modern public site | Implement responsive home, product, download/docs, hardware statistics, build showcase, community, privacy, security, and status experiences using real data only. | QUEUED |
| WEB-06 | P2 | Build public hardware/statistics pages | Publish privacy-safe aggregate hardware, storage-layout, lifecycle, and adoption statistics with methodology, sample size, freshness, missing-data semantics, and bounded APIs. | QUEUED |
| WEB-07 | P4 | Add disclosed affiliate hardware links | Support new/used/alternative hardware links, explicit affiliate disclosure, editorial separation, aggregate-only targeting, click attribution boundaries, and no retailer inference from private telemetry. | QUEUED |
| WEB-08 | P3 | Build the community forum/discussion system | Provide accounts, categories, threads, replies, quoting, reactions, search, subscriptions, reporting, moderation, spam controls, rate limits, accessibility, and mobile usability. | QUEUED |
| WEB-09 | P3 | Define the community taxonomy | Include hardware, data hoarding, homelab, Plex/Jellyfin/Emby, ARR applications, downloads, mergerFS, SnapRAID, ZFS, Linux MD, controllers/enclosures, build logs, troubleshooting, deals, and off-topic/community areas. | QUEUED |
| WEB-10 | P3 | Add forum governance and safety | Publish rules, moderator roles, report/appeal flow, anti-spam/anti-abuse controls, attachment policy, retention/deletion behavior, and privacy policy. | QUEUED |
| WEB-11 | P2 | Build staging, cutover, and rollback | Validate redirects, SEO metadata, sitemap, robots policy, TLS, caches, database migration, asset integrity, link checks, monitoring, and one-command/atomic rollback before production cutover. | QUEUED |
| WEB-12 | P2 | Validate quality and accessibility | Test responsive breakpoints, keyboard use, focus, semantic structure, contrast, screen-reader names, performance budgets, browser compatibility, broken links, and error/empty/loading states. | QUEUED |
| WEB-13 | P2 | Add website operations | Implement health checks, metrics, logs, alerting, database/storage backups, restore drills, uptime monitoring, capacity monitoring, and incident runbooks. | QUEUED |
| WEB-14 | P1 | Add end-to-end staging validation | Exercise anonymous visitor, telemetry client, outage/retry, account, forum, moderation, profile privacy, leaderboard opt-out, affiliate disclosure, admin, and rollback scenarios. | QUEUED |
| WEB-15 | P2 | Production launch gate | Archive and replace the current site only after an explicitly selected launch task, successful restore drill, staging acceptance, final content approval, and rollback readiness. | QUEUED |

## Source specifications

- [hoardarr.com edge security and optional appliance integrations](specs/hoardarr-com-edge-security.md)
- [fleet telemetry, hardware lifecycle, ingestion, and offline queueing](specs/fleet-telemetry-hardware-lifecycle.md)
- [community profiles, leaderboards, gamification, and public statistics](specs/community-profiles-leaderboards.md)


## EDGE — Edge security and optional appliance security

Every row includes the complete scope and acceptance requirements in its linked source section; the link is normative.

| ID | Priority | Source requirement | Scope/acceptance | Status |
|---|---|---|---|---|
| EDGE-01 | P1 | 1. Product principle | Implement and prove every requirement in [EDGE §1](specs/hoardarr-com-edge-security.md#1-productprinciple). | QUEUED |
| EDGE-02 | P1 | 2. hoardarr.com reverse proxy | Implement and prove every requirement in [EDGE §2](specs/hoardarr-com-edge-security.md#2-hoardarrcomreverseproxy). | QUEUED |
| EDGE-03 | P1 | 3. Telemetry endpoint limits | Implement and prove every requirement in [EDGE §3](specs/hoardarr-com-edge-security.md#3-telemetryendpointlimits). | QUEUED |
| EDGE-04 | P1 | 4. CrowdSec on hoardarr.com | Implement and prove every requirement in [EDGE §4](specs/hoardarr-com-edge-security.md#4-crowdseconhoardarrcom). | QUEUED |
| EDGE-05 | P1 | 5. WAF/AppSec | Implement and prove every requirement in [EDGE §5](specs/hoardarr-com-edge-security.md#5-wafappsec). | QUEUED |
| EDGE-06 | P1 | 6. Server-side telemetry abuse detection | Implement and prove every requirement in [EDGE §6](specs/hoardarr-com-edge-security.md#6-server-sidetelemetryabusedetection). | QUEUED |
| EDGE-07 | P1 | 7. NGINX/CrowdSec observability | Implement and prove every requirement in [EDGE §7](specs/hoardarr-com-edge-security.md#7-nginxcrowdsecobservability). | QUEUED |
| EDGE-08 | P4 | 8. Hoardarr appliance security menu | Implement and prove every requirement in [EDGE §8](specs/hoardarr-com-edge-security.md#8-hoardarrappliancesecuritymenu). | QUEUED |
| EDGE-09 | P4 | 9. Security Overview | Implement and prove every requirement in [EDGE §9](specs/hoardarr-com-edge-security.md#9-securityoverview). | QUEUED |
| EDGE-10 | P4 | 10. CrowdSec on local Hoardarr systems | Implement and prove every requirement in [EDGE §10](specs/hoardarr-com-edge-security.md#10-crowdseconlocalhoardarrsystems). | QUEUED |
| EDGE-11 | P4 | 11. Reverse proxy integration | Implement and prove every requirement in [EDGE §11](specs/hoardarr-com-edge-security.md#11-reverseproxyintegration). | QUEUED |
| EDGE-12 | P4 | 12. WAF on local Hoardarr | Implement and prove every requirement in [EDGE §12](specs/hoardarr-com-edge-security.md#12-wafonlocalhoardarr). | QUEUED |
| EDGE-13 | P4 | 13. Suricata | Implement and prove every requirement in [EDGE §13](specs/hoardarr-com-edge-security.md#13-suricata). | QUEUED |
| EDGE-14 | P4 | 14. Suricata integration data | Implement and prove every requirement in [EDGE §14](specs/hoardarr-com-edge-security.md#14-suricataintegrationdata). | QUEUED |
| EDGE-15 | P4 | 15. ClamAV | Implement and prove every requirement in [EDGE §15](specs/hoardarr-com-edge-security.md#15-clamav). | QUEUED |
| EDGE-16 | P4 | 16. ClamAV controls | Implement and prove every requirement in [EDGE §16](specs/hoardarr-com-edge-security.md#16-clamavcontrols). | QUEUED |
| EDGE-17 | P4 | 17. Download-folder integration | Implement and prove every requirement in [EDGE §17](specs/hoardarr-com-edge-security.md#17-download-folderintegration). | QUEUED |
| EDGE-18 | P4 | 18. Malware detection handling | Implement and prove every requirement in [EDGE §18](specs/hoardarr-com-edge-security.md#18-malwaredetectionhandling). | QUEUED |
| EDGE-19 | P4 | 19. Security events | Implement and prove every requirement in [EDGE §19](specs/hoardarr-com-edge-security.md#19-securityevents). | QUEUED |
| EDGE-20 | P4 | 20. Resource usage | Implement and prove every requirement in [EDGE §20](specs/hoardarr-com-edge-security.md#20-resourceusage). | QUEUED |
| EDGE-21 | P4 | 21. Installation model | Implement and prove every requirement in [EDGE §21](specs/hoardarr-com-edge-security.md#21-installationmodel). | QUEUED |
| EDGE-22 | P4 | 22. Fail safely | Implement and prove every requirement in [EDGE §22](specs/hoardarr-com-edge-security.md#22-failsafely). | QUEUED |
| EDGE-23 | P4 | 23. Updates | Implement and prove every requirement in [EDGE §23](specs/hoardarr-com-edge-security.md#23-updates). | QUEUED |
| EDGE-24 | P1 | 24. Testing — hoardarr.com | Implement and prove every requirement in [EDGE §24](specs/hoardarr-com-edge-security.md#24-testinghoardarrcom). | QUEUED |
| EDGE-25 | P4 | 25. Testing — local CrowdSec | Implement and prove every requirement in [EDGE §25](specs/hoardarr-com-edge-security.md#25-testinglocalcrowdsec). | QUEUED |
| EDGE-26 | P4 | 26. Testing — Suricata | Implement and prove every requirement in [EDGE §26](specs/hoardarr-com-edge-security.md#26-testingsuricata). | QUEUED |
| EDGE-27 | P4 | 27. Testing — ClamAV | Implement and prove every requirement in [EDGE §27](specs/hoardarr-com-edge-security.md#27-testingclamav). | QUEUED |
| EDGE-28 | P4 | 28. UI behavior | Implement and prove every requirement in [EDGE §28](specs/hoardarr-com-edge-security.md#28-uibehavior). | QUEUED |
| EDGE-29 | P4 | 29. Do not over-secure the appliance | Implement and prove every requirement in [EDGE §29](specs/hoardarr-com-edge-security.md#29-donotover-securetheappliance). | QUEUED |
| EDGE-30 | P4 | 30. Completion criteria | Implement and prove every requirement in [EDGE §30](specs/hoardarr-com-edge-security.md#30-completioncriteria). | QUEUED |

## FLEET — Fleet telemetry and hardware lifecycle

Every row includes the complete scope and acceptance requirements in its linked source section; the link is normative.

| ID | Priority | Source requirement | Scope/acceptance | Status |
|---|---|---|---|---|
| FLEET-01 | P1 | 1. Telemetry levels | Implement and prove every requirement in [FLEET §1](specs/fleet-telemetry-hardware-lifecycle.md#1-telemetrylevels). | QUEUED |
| FLEET-02 | P1 | 2. Enhanced diagnostics — explicit opt-in | Implement and prove every requirement in [FLEET §2](specs/fleet-telemetry-hardware-lifecycle.md#2-enhanceddiagnosticsexplicitopt-in). | QUEUED |
| FLEET-03 | P1 | 3. Content diagnostics — separate explicit opt-in | Implement and prove every requirement in [FLEET §3](specs/fleet-telemetry-hardware-lifecycle.md#3-contentdiagnosticsseparateexplicitopt-in). | QUEUED |
| FLEET-04 | P1 | 4. Never transmitted | Implement and prove every requirement in [FLEET §4](specs/fleet-telemetry-hardware-lifecycle.md#4-nevertransmitted). | QUEUED |
| FLEET-05 | P1 | 5. Country/timezone detection during installation | Implement and prove every requirement in [FLEET §5](specs/fleet-telemetry-hardware-lifecycle.md#5-countrytimezonedetectionduringinstallation). | QUEUED |
| FLEET-06 | P1 | 6. Installation identity | Implement and prove every requirement in [FLEET §6](specs/fleet-telemetry-hardware-lifecycle.md#6-installationidentity). | QUEUED |
| FLEET-07 | P1 | 7. Cross-system pseudonymous drive identity | Implement and prove every requirement in [FLEET §7](specs/fleet-telemetry-hardware-lifecycle.md#7-cross-systempseudonymousdriveidentity). | QUEUED |
| FLEET-08 | P1 | 8. Partial serials | Implement and prove every requirement in [FLEET §8](specs/fleet-telemetry-hardware-lifecycle.md#8-partialserials). | QUEUED |
| FLEET-09 | P1 | 9. Cross-system lifecycle | Implement and prove every requirement in [FLEET §9](specs/fleet-telemetry-hardware-lifecycle.md#9-cross-systemlifecycle). | QUEUED |
| FLEET-10 | P1 | 10. Heartbeats, snapshots, and events | Implement and prove every requirement in [FLEET §10](specs/fleet-telemetry-hardware-lifecycle.md#10-heartbeatssnapshotsandevents). | QUEUED |
| FLEET-11 | P1 | 11. hoardarr.com ingestion API | Implement and prove every requirement in [FLEET §11](specs/fleet-telemetry-hardware-lifecycle.md#11-hoardarrcomingestionapi). | QUEUED |
| FLEET-12 | P1 | 12. HTTPS only | Implement and prove every requirement in [FLEET §12](specs/fleet-telemetry-hardware-lifecycle.md#12-httpsonly). | QUEUED |
| FLEET-13 | P1 | 13. Installation registration credential | Implement and prove every requirement in [FLEET §13](specs/fleet-telemetry-hardware-lifecycle.md#13-installationregistrationcredential). | QUEUED |
| FLEET-14 | P1 | 14. Authenticated telemetry envelopes | Implement and prove every requirement in [FLEET §14](specs/fleet-telemetry-hardware-lifecycle.md#14-authenticatedtelemetryenvelopes). | QUEUED |
| FLEET-15 | P1 | 15. Client-controlled limitation | Implement and prove every requirement in [FLEET §15](specs/fleet-telemetry-hardware-lifecycle.md#15-client-controlledlimitation). | QUEUED |
| FLEET-16 | P1 | 16. Durable offline queue | Implement and prove every requirement in [FLEET §16](specs/fleet-telemetry-hardware-lifecycle.md#16-durableofflinequeue). | QUEUED |
| FLEET-17 | P1 | 17. Store-and-forward behavior | Implement and prove every requirement in [FLEET §17](specs/fleet-telemetry-hardware-lifecycle.md#17-store-and-forwardbehavior). | QUEUED |
| FLEET-18 | P1 | 18. Queue bounding | Implement and prove every requirement in [FLEET §18](specs/fleet-telemetry-hardware-lifecycle.md#18-queuebounding). | QUEUED |
| FLEET-19 | P1 | 19. Retry behavior | Implement and prove every requirement in [FLEET §19](specs/fleet-telemetry-hardware-lifecycle.md#19-retrybehavior). | QUEUED |
| FLEET-20 | P1 | 20. Dead-letter behavior | Implement and prove every requirement in [FLEET §20](specs/fleet-telemetry-hardware-lifecycle.md#20-dead-letterbehavior). | QUEUED |
| FLEET-21 | P1 | 21. Settings → Telemetry & Privacy | Implement and prove every requirement in [FLEET §21](specs/fleet-telemetry-hardware-lifecycle.md#21-settingstelemetryprivacy). | QUEUED |
| FLEET-22 | P1 | 22. View exactly what is sent | Implement and prove every requirement in [FLEET §22](specs/fleet-telemetry-hardware-lifecycle.md#22-viewexactlywhatissent). | QUEUED |
| FLEET-23 | P1 | 23. Central data model | Implement and prove every requirement in [FLEET §23](specs/fleet-telemetry-hardware-lifecycle.md#23-centraldatamodel). | QUEUED |
| FLEET-24 | P2 | 24. Aggregate analytics | Implement and prove every requirement in [FLEET §24](specs/fleet-telemetry-hardware-lifecycle.md#24-aggregateanalytics). | QUEUED |
| FLEET-25 | P2 | 25. Drive lifecycle analytics | Implement and prove every requirement in [FLEET §25](specs/fleet-telemetry-hardware-lifecycle.md#25-drivelifecycleanalytics). | QUEUED |
| FLEET-26 | P2 | 26. Purchase metadata — optional | Implement and prove every requirement in [FLEET §26](specs/fleet-telemetry-hardware-lifecycle.md#26-purchasemetadataoptional). | QUEUED |
| FLEET-27 | P2 | 27. Affiliate analytics boundary | Implement and prove every requirement in [FLEET §27](specs/fleet-telemetry-hardware-lifecycle.md#27-affiliateanalyticsboundary). | QUEUED |
| FLEET-28 | P2 | 28. Internal hoardarr.com admin dashboard | Implement and prove every requirement in [FLEET §28](specs/fleet-telemetry-hardware-lifecycle.md#28-internalhoardarrcomadmindashboard). | QUEUED |
| FLEET-29 | P1 | 29. Telemetry schema evolution | Implement and prove every requirement in [FLEET §29](specs/fleet-telemetry-hardware-lifecycle.md#29-telemetryschemaevolution). | QUEUED |
| FLEET-30 | P1 | 30. Central retention | Implement and prove every requirement in [FLEET §30](specs/fleet-telemetry-hardware-lifecycle.md#30-centralretention). | QUEUED |
| FLEET-31 | P1 | 31. Source IP | Implement and prove every requirement in [FLEET §31](specs/fleet-telemetry-hardware-lifecycle.md#31-sourceip). | QUEUED |
| FLEET-32 | P1 | 32. End-to-end cross-system drive test | Implement and prove every requirement in [FLEET §32](specs/fleet-telemetry-hardware-lifecycle.md#32-end-to-endcross-systemdrivetest). | QUEUED |
| FLEET-33 | P1 | 33. Offline queue end-to-end test | Implement and prove every requirement in [FLEET §33](specs/fleet-telemetry-hardware-lifecycle.md#33-offlinequeueend-to-endtest). | QUEUED |
| FLEET-34 | P1 | 34. Secret filtering tests | Implement and prove every requirement in [FLEET §34](specs/fleet-telemetry-hardware-lifecycle.md#34-secretfilteringtests). | QUEUED |
| FLEET-35 | P1 | 35. Completion requirements | Implement and prove every requirement in [FLEET §35](specs/fleet-telemetry-hardware-lifecycle.md#35-completionrequirements). | QUEUED |

## COMM — Community profiles and leaderboards

Every row includes the complete scope and acceptance requirements in its linked source section; the link is normative.

| ID | Priority | Source requirement | Scope/acceptance | Status |
|---|---|---|---|---|
| COMM-01 | P3 | 1. Account model | Implement and prove every requirement in [COMM §1](specs/community-profiles-leaderboards.md#1-accountmodel). | QUEUED |
| COMM-02 | P3 | 2. Installation claiming | Implement and prove every requirement in [COMM §2](specs/community-profiles-leaderboards.md#2-installationclaiming). | QUEUED |
| COMM-03 | P3 | 3. Leaderboard participation | Implement and prove every requirement in [COMM §3](specs/community-profiles-leaderboards.md#3-leaderboardparticipation). | QUEUED |
| COMM-04 | P3 | 4. Public build profile | Implement and prove every requirement in [COMM §4](specs/community-profiles-leaderboards.md#4-publicbuildprofile). | QUEUED |
| COMM-05 | P3 | 5. Build privacy levels | Implement and prove every requirement in [COMM §5](specs/community-profiles-leaderboards.md#5-buildprivacylevels). | QUEUED |
| COMM-06 | P4 | 6. Leaderboard categories | Implement and prove every requirement in [COMM §6](specs/community-profiles-leaderboards.md#6-leaderboardcategories). | QUEUED |
| COMM-07 | P4 | 7. Community-voted categories | Implement and prove every requirement in [COMM §7](specs/community-profiles-leaderboards.md#7-community-votedcategories). | QUEUED |
| COMM-08 | P4 | 8. Standardized benchmarks | Implement and prove every requirement in [COMM §8](specs/community-profiles-leaderboards.md#8-standardizedbenchmarks). | QUEUED |
| COMM-09 | P3 | 9. Verified versus self-reported stats | Implement and prove every requirement in [COMM §9](specs/community-profiles-leaderboards.md#9-verifiedversusself-reportedstats). | QUEUED |
| COMM-10 | P4 | 10. Badges and achievements | Implement and prove every requirement in [COMM §10](specs/community-profiles-leaderboards.md#10-badgesandachievements). | QUEUED |
| COMM-11 | P4 | 11. Drive lifecycle badges | Implement and prove every requirement in [COMM §11](specs/community-profiles-leaderboards.md#11-drivelifecyclebadges). | QUEUED |
| COMM-12 | P4 | 12. Build score | Implement and prove every requirement in [COMM §12](specs/community-profiles-leaderboards.md#12-buildscore). | QUEUED |
| COMM-13 | P4 | 13. Seasons | Implement and prove every requirement in [COMM §13](specs/community-profiles-leaderboards.md#13-seasons). | QUEUED |
| COMM-14 | P3 | 14. Build history | Implement and prove every requirement in [COMM §14](specs/community-profiles-leaderboards.md#14-buildhistory). | QUEUED |
| COMM-15 | P3 | 15. Multiple builds per user | Implement and prove every requirement in [COMM §15](specs/community-profiles-leaderboards.md#15-multiplebuildsperuser). | QUEUED |
| COMM-16 | P3 | 16. Global leaderboard UI | Implement and prove every requirement in [COMM §16](specs/community-profiles-leaderboards.md#16-globalleaderboardui). | QUEUED |
| COMM-17 | P3 | 17. In-app leaderboard integration | Implement and prove every requirement in [COMM §17](specs/community-profiles-leaderboards.md#17-in-appleaderboardintegration). | QUEUED |
| COMM-18 | P3 | 18. Community build page | Implement and prove every requirement in [COMM §18](specs/community-profiles-leaderboards.md#18-communitybuildpage). | QUEUED |
| COMM-19 | P3 | 19. Photos | Implement and prove every requirement in [COMM §19](specs/community-profiles-leaderboards.md#19-photos). | QUEUED |
| COMM-20 | P4 | 20. Affiliate links | Implement and prove every requirement in [COMM §20](specs/community-profiles-leaderboards.md#20-affiliatelinks). | QUEUED |
| COMM-21 | P4 | 21. Hardware popularity statistics | Implement and prove every requirement in [COMM §21](specs/community-profiles-leaderboards.md#21-hardwarepopularitystatistics). | QUEUED |
| COMM-22 | P4 | 22. Reliability statistics | Implement and prove every requirement in [COMM §22](specs/community-profiles-leaderboards.md#22-reliabilitystatistics). | QUEUED |
| COMM-23 | P4 | 23. Used hardware insights | Implement and prove every requirement in [COMM §23](specs/community-profiles-leaderboards.md#23-usedhardwareinsights). | QUEUED |
| COMM-24 | P4 | 24. Optional purchase-source field | Implement and prove every requirement in [COMM §24](specs/community-profiles-leaderboards.md#24-optionalpurchase-sourcefield). | QUEUED |
| COMM-25 | P4 | 25. Purchase price | Implement and prove every requirement in [COMM §25](specs/community-profiles-leaderboards.md#25-purchaseprice). | QUEUED |
| COMM-26 | P4 | 26. Value leaderboard | Implement and prove every requirement in [COMM §26](specs/community-profiles-leaderboards.md#26-valueleaderboard). | QUEUED |
| COMM-27 | P3 | 27. Leaderboard integrity | Implement and prove every requirement in [COMM §27](specs/community-profiles-leaderboards.md#27-leaderboardintegrity). | QUEUED |
| COMM-28 | P3 | 28. Anti-cheat | Implement and prove every requirement in [COMM §28](specs/community-profiles-leaderboards.md#28-anti-cheat). | QUEUED |
| COMM-29 | P3 | 29. Public ranking privacy | Implement and prove every requirement in [COMM §29](specs/community-profiles-leaderboards.md#29-publicrankingprivacy). | QUEUED |
| COMM-30 | P3 | 30. Profile/leaderboard consent | Implement and prove every requirement in [COMM §30](specs/community-profiles-leaderboards.md#30-profileleaderboardconsent). | QUEUED |
| COMM-31 | P3 | 31. Account deletion and unclaim | Implement and prove every requirement in [COMM §31](specs/community-profiles-leaderboards.md#31-accountdeletionandunclaim). | QUEUED |
| COMM-32 | P4 | 32. Public API | Implement and prove every requirement in [COMM §32](specs/community-profiles-leaderboards.md#32-publicapi). | QUEUED |
| COMM-33 | P4 | 33. Gamification must not encourage unsafe behavior | Implement and prove every requirement in [COMM §33](specs/community-profiles-leaderboards.md#33-gamificationmustnotencourageunsafebehavior). | QUEUED |
| COMM-34 | P3 | 34. Implementation tests | Implement and prove every requirement in [COMM §34](specs/community-profiles-leaderboards.md#34-implementationtests). | QUEUED |
| COMM-35 | P3 | 35. End-to-end scenario | Implement and prove every requirement in [COMM §35](specs/community-profiles-leaderboards.md#35-end-to-endscenario). | QUEUED |
| COMM-36 | P4 | 36. Final product philosophy | Implement and prove every requirement in [COMM §36](specs/community-profiles-leaderboards.md#36-finalproductphilosophy). | QUEUED |


## Reconciled product implementation backlog

These task families extend—rather than replace—the original 120 rows. Status is requirement-level: a row leaves `QUEUED` only after its complete backend, persistence, API, UI, test, and documentation acceptance boundary is met.

### LIFE — Core storage lifecycle

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| LIFE-01 | P1 | Storage Group persistence | Persist named Storage Groups with purpose, stable namespace, policy, lifecycle state, and timestamps. | SOFTWARE VERIFIED — migration/service/API/UI tests |
| LIFE-02 | P1 | Physical disk registry | Persist stable local disk identity separately from kernel path, location, and last observation. | SOFTWARE VERIFIED — path-renumber test |
| LIFE-03 | P1 | Backend identity and stable namespace | Associate logical/physical backends with one Storage Group without binding identity to /dev names. | SOFTWARE VERIFIED — derived identity and namespace tests |
| LIFE-04 | P1 | Assignment and activation | Implement safe ASSIGN → ACTIVE transitions with validation and durable history. | SOFTWARE VERIFIED — service and authenticated API tests |
| LIFE-05 | P1 | Preferred-write invariant | Allow exactly one preferred-write backend per group and atomically demote the prior preference. | SOFTWARE VERIFIED — two-backend invariant test |
| LIFE-06 | P1 | Drain, verify, read-only, and retire states | Model DRAIN → VERIFY → READ-ONLY → RETIRE without permitting unsafe direct skips. | VERIFIED IN ISOLATION — durable operation owns each guarded transition; two-loop ext4 CI completed |
| LIFE-07 | P1 | Optional reuse/wipe linkage | Link retired media to explicit reuse or capability-aware wipe workflows; never wipe as an implicit retirement step. | QUEUED |
| LIFE-08 | P1 | Storage lifecycle API | Expose bounded authenticated group, disk, backend, lifecycle, and event contracts with Problem Details errors. | SOFTWARE VERIFIED — drain start/progress/pause/resume and bounded immutable-plan contracts included |
| LIFE-09 | P1 | Storage Groups UI | Provide real loading, empty, error, group, backend, namespace, preferred-write, and lifecycle presentation. | SOFTWARE VERIFIED — create/assign/activate/prefer plus drain approval, progress, pause/resume, error and report states |
| LIFE-10 | P1 | Lifecycle events and audit | Persist ordered state-change evidence, actor, reason, operation, and sanitized details. | SOFTWARE VERIFIED — lifecycle and operation events preserve actor, plan ownership, phases, and sanitized report details |
| LIFE-11 | P1 | Discovery reconciliation | Reconcile current hardware snapshots with the registry while preserving identity across path/location changes. | SOFTWARE VERIFIED — worker scan hook, unstable-device skip, and renumbering tests |
| LIFE-12 | P1 | Lifecycle end-to-end validation | Prove add, assign, activate, prefer, drain, verify, read-only, retire, and optional reuse/wipe behavior. | IN PROGRESS — drain through retirement is browser- and Linux-verified in isolation; optional reuse/wipe remains LIFE-07 |

### DRAIN — Drain and evacuate engine

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| DRAIN-01 | P1 | Drain plan and preflight | Build immutable source/destination plans with identity, namespace, capacity, health, open-use, and ARR checks. | SOFTWARE VERIFIED — planner/API/UI, adversarial tests, and Linux CI pass |
| DRAIN-02 | P1 | Remove source from new-write placement | Change placement before copying so new files do not race evacuation. | SOFTWARE VERIFIED — atomic idempotent placement exclusion with operation/plan ownership |
| DRAIN-03 | P1 | Optional source read-only enforcement | Apply and verify read-only behavior only where the selected workflow supports it safely. | SOFTWARE VERIFIED — exact-mount capability gate, fail-closed remount/readback, and failure-safe restoration implemented; Linux execution pending this increment's isolated run |
| DRAIN-04 | P1 | Checkpointed mover | Copy through durable checkpoints and temporary destinations without deleting the verified source. | VERIFIED IN ISOLATION — descriptor-relative no-follow copy, atomic no-replace publish, durable per-file checkpoints on loop-backed ext4 |
| DRAIN-05 | P1 | Capacity and destination-health gates | Fail before and during movement when reserve, health, or destination identity becomes unsafe. | SOFTWARE VERIFIED — preflight and execution revalidate capacity, health, paths, stable identity, and filesystem identity |
| DRAIN-06 | P1 | ARR active-write awareness and windows | Coordinate imports/downloads/renames and configurable maintenance windows. | SOFTWARE VERIFIED — ARR write-sensitive preflight/runtime pauses plus fresh-per-resume maintenance windows implemented; broader provider activity coverage remains under MEDIA-04 |
| DRAIN-07 | P1 | Pause, resume, and restart recovery | Persist resumable state across cancellation boundaries, browser loss, worker restart, and service restart. | VERIFIED IN ISOLATION — UI/API pause/resume plus manifest-preserving stale-worker recovery executed on Linux |
| DRAIN-08 | P1 | Bandwidth and scheduling controls | Enforce bounded concurrency, bandwidth, IO priority, and operator scheduling. | IN PROGRESS — durable not-before scheduling, bounded single-mover concurrency, and byte-rate limiting implemented; explicit Linux IO-priority control pending |
| DRAIN-09 | P1 | Verification modes | Implement FAST, ACCURATE/BLAKE3 where available, and PARANOID full-read verification with explicit evidence. | IN PROGRESS — FAST, SHA-256 ACCURATE, and two-pass PARANOID implemented; ACCURATE executed on Linux; optional BLAKE3 optimization pending |
| DRAIN-10 | P1 | Finalize and namespace reconciliation | Remove/decommission only after verification and preserve stable ARR/share paths. | VERIFIED IN ISOLATION — verified-only deletion, read-only/retired transitions, unchanged namespace and hashes executed on Linux |
| DRAIN-11 | P1 | Drain reports and cost UX | Show files/bytes/errors, checkpoints, verification evidence, and measured/estimated CPU and IO cost. | IN PROGRESS — real phase/file/byte/rate/ETA progress and durable final report implemented; richer measured CPU/IO report pending |
| DRAIN-12 | P1 | Drain failure matrix | Test full destination, disappearing source/destination, ARR conflict, checksum failure, pause/resume, and worker crash. | IN PROGRESS — identity/capacity/collision/pause/restart paths covered; expanded Linux fault injection pending |

### EXPAND — Storage expansion planner

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| EXPAND-01 | P1 | Expansion discovery trigger | Recognize newly available eligible disks without treating them as assigned storage. | SOFTWARE VERIFIED — snapshot-bound read-only assessment excludes assigned/retired/stale/system disks and refreshes automatically after discovery |
| EXPAND-02 | P1 | Current-state analysis | Analyze groups, backends, health, capacity, mergerFS balance, parity, ZFS geometry, forecasts, and tiers. | IN PROGRESS — groups/backends/health, namespace capacity, distinct-member utilization spread, configured parity, and current pool capabilities implemented; forecast correlation pending |
| EXPAND-03 | P1 | mergerFS and SnapRAID candidates | Generate add-member, data-disk, and parity-expansion candidates with restrictions. | IN PROGRESS — mergerFS candidates now require one evidence-matched Storage Group instance and carry its exact target; SnapRAID resync/parity-size consequences implemented; explicit parity-role selection pending |
| EXPAND-04 | P1 | ZFS, new-pool, cache, and reserve candidates | Generate supported vdev/pool/tier/reserve choices without inventing executable capability. | IN PROGRESS — matched mirror/new pool and download-tier candidates implemented; authenticated/audited persistent reserve/release is software-verified through the real UI; wider vdev geometry pending |
| EXPAND-05 | P1 | Capacity and protection calculations | Show raw/usable change, protection impact, growth effect, and migration work with formulas. | SOFTWARE VERIFIED — candidate-specific raw/usable methodology, protection, restrictions, and migration work rendered in UI |
| EXPAND-06 | P1 | Guided and Advanced expansion wizard | Recommend in plain language while exposing exact geometry and policies on demand. | SOFTWARE VERIFIED — browser E2E proves a snapshot-bound recommendation opens the real wizard with the complete candidate disk set; remaining geometries track under EXPAND-04 |
| EXPAND-07 | P1 | Immutable expansion apply | Bind approval to hardware and plan, revalidate identity, and execute through durable operations. | SOFTWARE VERIFIED — candidate, disk set, exact mergerFS target, and assessment digest are carried into the canonical snapshot-bound plan; executor revalidates live membership and atomically replaces persistent mergerFS source rather than adding a duplicate mount |
| EXPAND-08 | P1 | Expansion tests | Cover mixed sizes, health blockers, parity needs, ZFS restrictions, stale discovery, and unsupported tools. | IN PROGRESS — existing-data, system-disk exclusion, matched-instance targeting, stale assessment rejection, mergerFS runtime/persistence, SnapRAID, download-tier, matched-mirror, reservation persistence/system protection, API, component, and real-browser tests added; disposable-Linux fault matrix pending |

### IMPORT — Foreign storage, Unraid, and archive intake

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| IMPORT-01 | P2 | Foreign signature detection | Detect ext4, XFS, Btrfs, NTFS, exFAT, MD, LVM, ZFS, and recognizable NAS layouts. | QUEUED |
| IMPORT-02 | P2 | Read-only foreign safety and confidence | Default foreign media to non-destructive read-only inspection with evidence-based type/origin confidence. | QUEUED |
| IMPORT-03 | P2 | MD/LVM/ZFS foreign assembly preview | Identify members and mountability without activating or importing automatically. | QUEUED |
| IMPORT-04 | P2 | Unraid disk and parity classification | Recognize independent data disks and distinguish identified, suspected, and unknown parity. | QUEUED |
| IMPORT-05 | P2 | Unraid read-only inventory | Preview filesystems, members, files, capacity, health, and warnings before selection. | QUEUED |
| IMPORT-06 | P2 | Unraid migration engine | Preserve relative paths, resolve collisions explicitly, verify copies, and never claim unproven parity reuse. | QUEUED |
| IMPORT-07 | P2 | Synology, QNAP, and generic NAS profiles | Add evidence-based import adapters without manufacturing origin from weak heuristics. | QUEUED |
| IMPORT-08 | P2 | Archive source discovery | Represent USB, flash, SD, enclosure, and legacy internal media as DISCOVERED EXTERNAL / READ ONLY. | QUEUED |
| IMPORT-09 | P2 | Archive preview analysis | Report files, bytes, extrema, timestamps, extensions, read errors, collisions, permissions, and required capacity. | QUEUED |
| IMPORT-10 | P2 | Archive selection and filters | Support full, selected-folder, include/exclude, and filtered intake plans. | QUEUED |
| IMPORT-11 | P2 | Archive durable intake and report | Copy, checkpoint, verify, resolve collisions, and produce a final manifest/report. | QUEUED |
| IMPORT-12 | P2 | Foreign/import integration tests | Exercise disposable filesystems, Unraid-like disks, malformed layouts, collisions, read errors, and restart recovery. | QUEUED |

### HW — Hardware health and topology

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| HW-01 | P1 | SMART short/long orchestration | Detect support, start tests, track ETA/state/result, and represent passthrough limitations honestly. | QUEUED |
| HW-02 | P1 | Capability-aware sanitization reports | Support metadata clear, overwrite, ATA/NVMe/SCSI sanitize, and legitimate crypto erase with immutable approval and reports. | QUEUED |
| HW-03 | P1 | Full physical topology model | Represent PCI BDF → HBA/controller → SAS host/PHY → expander → enclosure/bay → disk → path/logical storage/group. | QUEUED |
| HW-04 | P1 | Standards-first SCSI/SAS discovery | Integrate sysfs, SCSI VPD, SES/AES, SMP, HCTL, PCI, and stable hardware identity evidence. | QUEUED |
| HW-05 | P1 | SES enclosure provider | Normalize enclosure, slot, fan, PSU, sensor, LED, expander, and redundant-path state when reported. | QUEUED |
| HW-06 | P1 | Bay mapping evidence and confidence | Persist source, HIGH/MEDIUM/LOW/UNKNOWN confidence, and last confirmation; distinguish confirmed/inferred/not reported. | QUEUED |
| HW-07 | P1 | SAS PHY/link telemetry | Collect capable/negotiated/minimum rates and invalid-dword, disparity, sync-loss, and reset counters. | QUEUED |
| HW-08 | P1 | Slow-link interpretation | Compare capable and negotiated rates at device, expander, and HBA layers without labeling legitimate 3G devices failed. | QUEUED |
| HW-09 | P1 | Live topology UI | Productize real discovered chassis/controller/path/enclosure/bay/disk relationships and contextual actions. | QUEUED |
| HW-10 | P1 | Planning topology UI and templates | Model planned chassis, shelves, controllers, disks, expansion, and retirement using profiles or generic layouts. | QUEUED |
| HW-11 | P1 | Contextual hardware actions | Connect Details, SMART, Locate, Test, Drain, Replace, and Decommission only to implemented backend behavior. | QUEUED |
| HW-12 | P1 | Expected topology declarations | Persist operator-approved expected controllers, shelves, paths, bays, and rates separately from observations. | QUEUED |
| HW-13 | P1 | Topology drift events and annotations | Detect moved disks, missing shelves/controllers/paths, degraded link rates, and redundancy loss durably. | QUEUED |
| HW-14 | P1 | Sanitized real-hardware fixtures | Add SAS2308/SAS3008, DS424IOM6/DS224IOM6, expanders, SATA/SAS, 3G/6G/12G, mapped and unknown-bay fixtures. | QUEUED |
| HW-15 | P1 | Malformed-provider and regression tests | Test incomplete SES/SMP/VPD/sysfs/vendor output, conflicting mappings, counter reset, and untrusted metadata. | QUEUED |
| HW-16 | P1 | Physical provider certification profiles | Keep matching-controller/enclosure/firmware execution evidence separate from software fixture coverage. | QUEUED |

### BACKUP — Remote and control-plane backup

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| BACKUP-01 | P2 | Remote backup target model | Persist provider, endpoint, bucket/prefix, schedule, limits, status, and history without readable secrets. | QUEUED |
| BACKUP-02 | P2 | Backup credential handling | Encrypt credentials, redact APIs/logs, support rotation, and constrain endpoint/path inputs. | QUEUED |
| BACKUP-03 | P2 | S3-compatible providers | Support MinIO, AWS S3, R2, Wasabi, B2 S3, and generic compatible endpoints through one tested contract. | QUEUED |
| BACKUP-04 | P2 | Multipart/resumable upload and checksums | Resume safely and verify with provider-appropriate checksums rather than assuming multipart ETag equals MD5. | QUEUED |
| BACKUP-05 | P2 | Scheduling, retry, bandwidth, and history | Provide durable jobs, backoff, limits, status, cancellation boundaries, and reports. | QUEUED |
| BACKUP-06 | P2 | Remote restore validation | List, download, verify, and restore into disposable destinations before claiming recoverability. | QUEUED |
| BACKUP-07 | P2 | Control-plane export | Export database, configuration, preferences, topology declarations, integrations, and plugin configuration. | QUEUED |
| BACKUP-08 | P2 | Encrypted secrets and fresh-appliance restore | Exclude secrets by default; optionally encrypt; restore and reconcile disks by stable identity. | QUEUED |
| BACKUP-09 | P2 | Backup UI/API/E2E | Provide target/setup/history/restore surfaces and test outage, resume, corruption, insufficient space, and fresh restore. | QUEUED |

### AUTO — Automation, alerting, and runbooks

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| AUTO-01 | P2 | Stable machine API and read-only tokens | Expose bounded health, group, drive, job, capacity, topology, controller, path, alert, and maintenance state. | QUEUED |
| AUTO-02 | P2 | Home Assistant summary | Add a stable read-only summary endpoint consistent with the current API without making HA the control plane. | QUEUED |
| AUTO-03 | P2 | Webhooks | Deliver signed/bounded events with retry, deduplication, redaction, and test delivery. | QUEUED |
| AUTO-04 | P2 | Prometheus integration | Preserve stable metric names, bounded labels, authentication policy, and no secrets. | QUEUED |
| AUTO-05 | P2 | Alert lifecycle | Implement Critical/Warning/Info with active, acknowledged, suppressed, and cleared states. | QUEUED |
| AUTO-06 | P2 | Alert deduplication, routing, and suppression | Add windows, webhook/AppRise-compatible routing, runbook links, and flapping control. | QUEUED |
| AUTO-07 | P2 | Evidence-aware operational runbooks | Provide plain-language CRC, pending-sector, path, SnapRAID, ZFS, and related guidance without false certainty. | QUEUED |
| AUTO-08 | P2 | Automation integration tests | Test token scope, schema stability, webhook replay/outage, Prometheus cardinality, alert lifecycle, and redaction. | QUEUED |

### MEDIA — Graph, media, ARR, and download productization

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| MEDIA-01 | P1 | Graph UX coverage | Expose the existing persistent system/storage/disk/pool/controller/path/enclosure metrics with honest missing states. | QUEUED |
| MEDIA-02 | P1 | History and compression transparency | Show sampling, raw/hourly/daily retention, automatic resolution, point limits, DB use, and aggregate provenance. | QUEUED |
| MEDIA-03 | P1 | Plex/Jellyfin/Emby read-only collectors | Collect libraries, item counts, and capacity only where supported and without modifying media systems. | QUEUED |
| MEDIA-04 | P1 | Media-to-storage mapping confidence | Correlate libraries to Storage Groups with explicit confidence and Not reported behavior. | QUEUED |
| MEDIA-05 | P1 | Media growth history | Persist and graph evidence-backed library growth over time. | QUEUED |
| MEDIA-06 | P1 | ARR active-operation awareness | Detect downloads, imports, renames, moves, and hardlink-sensitive work through product-aware adapters. | QUEUED |
| MEDIA-07 | P1 | Filesystem-activity degraded fallback | Use local activity only when APIs are unavailable and label it as a degraded, non-equivalent signal. | QUEUED |
| MEDIA-08 | P1 | Maintenance and drain windows | Let users coordinate lifecycle jobs with media/download activity and safe schedules. | QUEUED |
| MEDIA-09 | P1 | Guided landing-tier setup | Offer the plain-language SSD/NVMe downloads and temporary-processing workflow. | QUEUED |
| MEDIA-10 | P1 | Torrent and Usenet state models | Separate incomplete/complete/seeding from download/repair/unpack/import/cleanup semantics. | QUEUED |
| MEDIA-11 | P1 | Tier occupancy and migration UX | Show real configured membership, capacity, queued moves, seeding retention, drain estimates, and failures. | QUEUED |

### ARCH — Architecture reconciliations

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| ARCH-01 | P1 | Canonical extension-model ADR | Document current signed host add-ons/providers and select one coherent extension architecture. | DOCUMENTED — implementation reconciliation reviewed against current code |
| ARCH-02 | P1 | In-process provider boundary | Define trusted, versioned hardware/storage/integration providers that belong in the Hoardarr process. | QUEUED |
| ARCH-03 | P2 | Isolated third-party extension boundary | Use systemd-isolated add-on processes for untrusted/privileged extensions instead of a competing container marketplace. | QUEUED |
| ARCH-04 | P1 | Systemd appliance deployment reconciliation | Make Ubuntu/systemd/wheel/release-bundle/QEMU authoritative and update obsolete Docker-only claims. | IN PROGRESS — decision recorded; stale-reference sweep pending |
| ARCH-05 | P2 | HA capability traceability | Separate multipath failover, controlled ownership handoff, automatic node failover, fencing/quorum, and state replication. | DOCUMENTED — five evidence classes explicit |
| ARCH-06 | P3 | Automatic HA/fencing/state replication | Implement only as a separately approved advanced roadmap capability; never infer it from controller failover tests. | QUEUED |

### VALID — Cross-cutting validation

| ID | Priority | Task | Acceptance | Status |
|---|---|---|---|---|
| VALID-01 | P1 | Lifecycle disposable-storage integration | Execute lifecycle and namespace tests on test-created Linux disks/filesystems. | QUEUED |
| VALID-02 | P1 | Drain interruption and fault injection | Execute restart, full destination, device loss, ARR conflict, verification failure, and resume scenarios. | QUEUED |
| VALID-03 | P2 | Foreign/archive integration | Execute read-only foreign and Unraid-like fixtures plus archive copy/verification reports. | QUEUED |
| VALID-04 | P2 | Backup and automation integration | Execute S3 outage/resume/checksum/restore and HA/webhook/metrics contract tests. | QUEUED |
| VALID-05 | P1 | Topology fixture regression | Parse sanitized real SCSI/SAS evidence and assert bay confidence, slow links, path loss, and drift. | QUEUED |
| VALID-06 | P1 | Two-pass release gates | Run focused and clean regression passes; update traceability without conflating fixture, VM, and physical evidence. | QUEUED |

## Dependency-ordered execution waves

### Wave 0 — release protection and architecture truth

RC-01 through RC-05, ARCH-01 through ARCH-05, and VALID-06 remain active throughout all later waves.

### Wave 1 — P1 storage lifecycle

LIFE-01 through LIFE-12 and DRAIN-01 through DRAIN-12. Establish durable Storage Groups, stable disk/backend identity, safe state transitions, and the restart-safe drain engine before layering convenience workflows on them.

### Wave 2 — P1 hardware, expansion, and media workflow

HW-01 through HW-15, EXPAND-01 through EXPAND-08, and MEDIA-01 through MEDIA-11. Productize disk health, topology evidence, bay confidence, Guided recommendations, ARR-aware scheduling, download tiers, and telemetry UX.

### Wave 3 — P2 foreign storage and archive intake

IMPORT-01 through IMPORT-12. Foreign media remains read-only until a reviewed, capacity-checked, verified migration operation is approved.

### Wave 4 — P2 backup and automation

BACKUP-01 through BACKUP-09 and AUTO-01 through AUTO-08. Complete recoverable control-plane/S3 backups, read-only integrations, alert lifecycle, and runbooks.

### Wave 5 — P2 fleet telemetry and public website

FLEET-01 through FLEET-35, WEB-01 through WEB-06, and WEB-11 through WEB-15. Production website work remains staging-first and must not displace storage release gates.

### Wave 6 — P3 community

WEB-08 through WEB-10 and COMM-01 through COMM-36. Accounts and publication remain optional; local Hoardarr remains fully usable without hoardarr.com.

### Wave 7 — P4 affiliate and optional appliance security

WEB-07 and EDGE-01 through EDGE-30. Edge protections required by a public ingestion surface are implemented with that surface; optional appliance CrowdSec, reverse proxy/WAF, Suricata, and ClamAV remain Advanced, resource-measured add-ons and never block core storage.

## Cross-cutting non-negotiables

- No file contents, secrets, raw API credentials, local IPs, FQDNs, full serials, or paths enter default telemetry or public profiles.
- Required anonymous heartbeat, default hardware telemetry, enhanced diagnostics, content diagnostics, community publication, and affiliate analytics remain distinct controls.
- Public statistics use minimum sample sizes and careful “observed in Hoardarr systems” language; they are not manufacturer failure-rate claims.
- Public profiles, rankings, forums, photos, benchmarks, and votes require bounded input, moderation, privacy, anti-abuse, deletion, and account-unclaim behavior.
- Benchmark and test workloads enforce conservative write budgets and do not reward harmful temperatures, excessive writes, ignored failures, or destructive events.
- Optional security failures never take storage offline. A deliberately configured scan-before-import policy fails visibly rather than silently bypassing scanning.
- hoardarr.com production changes require backups, staging, health checks, and a tested rollback.
- Completion is based on observable behavior and executed tests, not pages, schemas, workflows, mocks, or documentation alone.

## Backlog totals

- P0 release-protection tasks: 5
- Website/forum tasks added from current direction: 15
- EDGE source tasks: 30
- FLEET source tasks: 35
- COMM source tasks: 36
- Core lifecycle tasks: 12
- Drain/evacuate tasks: 12
- Expansion-planning tasks: 8
- Foreign-storage/archive tasks: 12
- Hardware/topology tasks: 16
- Backup tasks: 9
- Automation/alerting tasks: 8
- Media/ARR/graph tasks: 11
- Architecture-reconciliation tasks: 6
- Cross-cutting validation tasks: 6
- Total tracked tasks: 221

Implementation follows the dependency waves above. Production deployment, destructive physical-disk use, and public launch remain separately gated.
