# Hoardarr — hoardarr.com Edge Security and Optional Advanced Appliance Security Integrations

Continue from the existing Hoardarr and hoardarr.com implementation state.

This is a separate infrastructure/security integration task.

Do not redesign the Hoardarr storage product.

Do not make heavy security packages mandatory on normal home installations.

The goals are:

1. Protect the publicly reachable `hoardarr.com` website and telemetry ingestion API appropriately.
2. Make optional security tooling available to advanced Hoardarr users who want it.
3. Expose useful detection/status information inside Hoardarr Advanced settings.
4. Avoid burdening normal Plex/ARR users with unnecessary IDS/WAF/antivirus complexity.

# 1. Product principle

Hoardarr's primary deployment is a home/homelab storage server.

Normal installations should remain lightweight.

Do not automatically install all of:

* CrowdSec
* Suricata
* ClamAV
* WAF tooling

on every installation.

Use these where they make sense.

# 2. hoardarr.com reverse proxy

Deploy a mature reverse proxy for the public hoardarr.com service.

NGINX is the preferred default unless existing hosting infrastructure already provides equivalent functionality.

Configure:

* HTTPS/TLS
* HTTP → HTTPS redirect
* reverse proxying
* request body size limits
* sane request timeouts
* connection limits
* per-route rate limits
* upstream health behavior
* access/error logging
* modern TLS configuration
* compression where appropriate
* appropriate security headers

Do not expose the application server directly when the deployment design expects NGINX.

# 3. Telemetry endpoint limits

The telemetry ingestion API is public-facing.

Apply specific controls for:

* registration
* heartbeat
* event submission
* inventory submission
* batch submission

Set:

* maximum request size
* request rate
* concurrent connection limits
* timeout limits

Legitimate Hoardarr installations already have offline retry queues, so temporary rate limiting must not lose telemetry.

# 4. CrowdSec on hoardarr.com

Deploy CrowdSec where appropriate for the public service.

Integrate it with NGINX or the selected reverse proxy.

Protect against obvious:

* scanners
* credential attacks
* abusive request sources
* repeated invalid API activity

Do not treat CrowdSec as a replacement for application authentication or schema validation.

# 5. WAF/AppSec

Evaluate CrowdSec AppSec/WAF or another appropriate WAF layer for hoardarr.com.

Protect:

* telemetry ingestion
* account authentication if later implemented
* admin dashboard
* public website/API

Initially validate rules for false positives.

If necessary:

1. deploy detection/monitoring mode
2. observe
3. tune
4. enable blocking

Do not allow WAF rules to silently block legitimate fleet telemetry indefinitely.

Queued clients should retry.

# 6. Server-side telemetry abuse detection

In addition to edge controls, application logic should detect:

* repeated invalid installation credentials
* replay attempts
* invalid signatures
* impossible schema versions
* oversized telemetry
* excessive registration attempts
* impossible sequence behavior
* repeated malformed hardware identifiers

Expose aggregate abuse statistics to the internal admin dashboard.

# 7. NGINX/CrowdSec observability

Monitor:

* requests/sec
* 2xx
* 4xx
* 5xx
* rate-limited requests
* WAF detections
* CrowdSec decisions
* upstream errors
* request latency

These belong to hoardarr.com operational monitoring.

# 8. Hoardarr appliance security menu

Add:

`Settings → Advanced → Security`

This page should remain hidden from normal/simple users unless Advanced mode is enabled.

Show optional integrations.

# 9. Security Overview

Display:

### Exposure

* LAN-only state where determinable
* reverse proxy detected where determinable
* externally reachable state only when it can be reasonably verified

Do not claim internet exposure/non-exposure without evidence.

### CrowdSec

* installed/not installed
* enabled
* running
* recent detections
* recent decisions/blocks

### WAF

* configured
* enabled
* recent detections/blocks

### Suricata

* installed
* IDS status
* IPS status
* monitored interfaces
* recent important alerts

### ClamAV

* installed
* service status
* signature database version
* last update
* last scan
* detections

# 10. CrowdSec on local Hoardarr systems

CrowdSec should be optional.

Do not install by default unless later product data justifies changing that decision.

Advanced users can:

* install
* enable
* disable
* update
* inspect detections
* inspect decisions

Explain its purpose in plain language:

> Optional monitoring and blocking of suspicious access attempts.

# 11. Reverse proxy integration

If an advanced user uses NGINX locally, allow Hoardarr to integrate with it where practical.

Do not force local NGINX installation solely because hoardarr.com uses NGINX centrally.

If Hoardarr itself already serves the UI appropriately on LAN, preserve the simple installation.

# 12. WAF on local Hoardarr

Offer WAF capability only for advanced users who deliberately expose services through a reverse proxy.

Do not require a WAF for ordinary trusted-LAN deployments.

Display:

* mode
* detection count
* blocking count
* rule status

# 13. Suricata

Offer Suricata as an optional Advanced integration.

Default recommendation:

`IDS mode`

not inline IPS.

Expose controls for:

* install
* enable IDS
* monitored interface selection
* rule update
* alert summary

If the user explicitly chooses IPS mode:

* explain that inline IPS changes network traffic handling
* require appropriate networking compatibility
* test configuration before activation
* offer rollback

Do not silently insert Suricata into the packet path.

# 14. Suricata integration data

Normalize high-value Suricata state into Hoardarr.

Display:

* service status
* rules version
* monitored interfaces
* packets processed
* dropped packets where appropriate
* high/critical alerts
* recent events

Do not flood normal Hoardarr Activity with every Suricata rule match.

# 15. ClamAV

Offer ClamAV as optional malware scanning.

Useful targets include:

* download directories
* import staging
* selected shares
* user-selected directories

Do not automatically scan every multi-terabyte media pool continuously.

# 16. ClamAV controls

Expose:

* install
* enable daemon
* update definitions
* manual scan
* scheduled scan
* selected paths
* exclusions
* optional on-access scanning

On-access scanning must be explicit.

Do not enable it automatically.

# 17. Download-folder integration

Because Hoardarr manages ARR/download workflows, offer a useful optional mode:

> Scan completed downloads before import

where technically feasible.

Possible workflow:

```text
download completes
  → optional malware scan
  → clean
      → continue import

  → detection
      → quarantine/needs attention
```

Do not silently delete infected files.

# 18. Malware detection handling

When a detection occurs:

* record event
* identify source path safely
* prevent automatic import if configured
* offer quarantine where supported
* show clear user action

Never expose file contents in telemetry.

# 19. Security events

Normalize important events into Hoardarr Health/Activity.

Examples:

* CrowdSec decision
* WAF block
* Suricata high-severity detection
* ClamAV malware detection

Use:

* summary
* severity
* timestamp
* source component

Keep detailed provider logs under Advanced.

# 20. Resource usage

These tools can consume significant resources.

Measure their actual resource usage after installation.

Expose where available:

* CPU
* memory
* disk usage
* log growth

Do not provide fabricated resource estimates.

# 21. Installation model

Security integrations should use Hoardarr's existing add-on/provider architecture where appropriate.

Each optional component should declare:

* required packages
* services
* privileges
* supported OS
* configuration files
* health checks
* update behavior
* uninstall behavior

# 22. Fail safely

A broken optional security package must not break core storage.

Examples:

* CrowdSec unavailable → Hoardarr still manages storage
* ClamAV unavailable → normal storage remains online
* Suricata disabled → storage/network management remains functional

If a user explicitly configured a workflow requiring a scan before import, fail that workflow clearly rather than silently bypassing it.

# 23. Updates

Support lifecycle management:

* install
* update
* enable
* disable
* remove

Do not leave abandoned configs/services after removal.

# 24. Testing — hoardarr.com

Test:

* NGINX → telemetry API proxy
* TLS
* request limits
* oversized body
* rate limit
* backend unavailable
* legitimate client retry
* CrowdSec integration
* WAF detection
* WAF blocking after tuning
* admin dashboard protection

# 25. Testing — local CrowdSec

Test in disposable Linux environment:

* install
* enable
* detection
* block
* disable
* uninstall
* Hoardarr status integration

# 26. Testing — Suricata

Test:

* installation
* IDS start
* monitored interface
* rule loading
* generated test alert
* Hoardarr event normalization
* disable/uninstall

IPS must be tested separately if implemented.

# 27. Testing — ClamAV

Use a standard harmless antivirus test signature/file where appropriate.

Verify:

* scanner detects test signature
* Hoardarr records detection
* configured import action occurs
* no real malware is needed

Test:

* manual scan
* scheduled scan
* download/import scan
* exclusion
* definitions update

# 28. UI behavior

Normal Hoardarr users should not see a giant security dashboard by default.

Advanced users should be able to open:

`Settings → Advanced → Security`

and get a clear control center.

Use simple first-level labels:

* Threat blocking
* Web protection
* Network detection
* Malware scanning

Then show exact product names and low-level configuration under details.

# 29. Do not over-secure the appliance

Avoid turning the home media server into a corporate SOC appliance.

Do not make:

* SIEM
* complex PKI
* enterprise identity management
* huge rule-management systems

release requirements for Hoardarr.

The goal is useful optional controls.

# 30. Completion criteria

For hoardarr.com:

* TLS reverse proxy
* sane request limits
* public API protection
* abuse controls
* operational metrics
* CrowdSec/WAF integration where selected

For Hoardarr Advanced:

* optional CrowdSec
* optional WAF/reverse proxy integration
* optional Suricata
* optional ClamAV
* status UI
* useful events
* installation/removal lifecycle
* resource monitoring
* tests

# Final report

Report:

1. hoardarr.com reverse proxy.
2. TLS configuration.
3. Request/rate limits.
4. CrowdSec deployment.
5. WAF deployment.
6. False-positive testing.
7. Telemetry retry behavior through rate limits/outages.
8. Local Advanced Security page.
9. Local CrowdSec support.
10. Local WAF/reverse-proxy support.
11. Suricata IDS support.
12. Suricata IPS status if implemented.
13. ClamAV support.
14. Download/import malware scanning.
15. Resource monitoring.
16. Normalized security events.
17. Installation/update/removal behavior.
18. Linux integration tests.
19. Remaining limitations.

The intended result is:

**Protect the public hoardarr.com service appropriately while keeping heavy security tooling optional and out of the way of ordinary Hoardarr home users.**


