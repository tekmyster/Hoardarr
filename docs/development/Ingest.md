# Storage ingest

Ingest answers a focused question: **Can this storage media be trusted for its
intended role?**

The operator chooses how intensive the evaluation should be. Hoardarr explains
the time, device wear, data-loss risk, and confidence associated with each test
profile before it begins. A less intensive test may produce a useful result,
but it must never be presented with the same confidence as a complete media
test.

Ingest does not import existing files or adopt an existing storage system. See
[Import.md](Import.md) for that workflow. Ingest may feed a later import,
storage-creation, or replacement plan.

## Principles

- Never convert missing evidence into a pass.
- Bind all results to stable device identities rather than volatile `/dev`
  paths.
- Identify every test as read-only or destructive before it is queued.
- Explain the evidence behind every pass, warning, failure, or unknown result.
- Revalidate device identity immediately before every test stage.
- Journal progress so long-running tests can be observed and safely recovered.
- Keep raw evidence available alongside the normalized Hoardarr assessment.

## Test intensity

The estimated durations shown in the interface must be based on detected
capacity, connection speed, device type, and prior measured throughput. They
must not be fixed promises.

### Inventory only

Use when the operator wants to identify media without testing it.

- Stable identity, model, serial, WWN, capacity, firmware, transport, controller,
  enclosure, and bay discovery.
- Partition, filesystem, pool, array, and volume-manager signature discovery.
- Read-only status and current active-use checks.
- No surface or self-test work.

This profile normally ends with `unknown`, not `pass`, because media health was
not tested.

### Quick

Use for a fast initial screen.

- Everything in Inventory only.
- Available SMART or NVMe health and error-log evidence.
- Short device self-test when supported.
- Basic connection and identity stability checks.

Quick is useful for rejecting obviously unhealthy devices. It is not proof
that the complete media surface is readable.

### Standard (recommended)

Use as the normal acceptance test for media entering active service.

- Everything in Quick.
- Extended device self-test when supported.
- Read-only sampling across the addressable media.
- Temperature, error-counter, and transport-error observations before and after
  the test.
- Repeated identity and capacity validation.

Sampling locations must be deterministic from the operation and device
identities so the result can be reproduced without always reading the same
small region.

### Full read

Use when the operator wants every addressable block read without changing data.

- Everything in Standard.
- Sequential read of the complete accessible device.
- Read-error location and count reporting.
- Throughput and temperature observations over the full test.

Completion means the accessible surface was read during this test. It does not
guarantee future reliability.

### Destructive burn-in

Use for empty media when the operator explicitly accepts that all existing data
will be destroyed.

- Stable-identity and active-use gates immediately before the first write.
- One or more operator-selected write/read patterns.
- Full verification of each selected pattern.
- SMART/NVMe and temperature comparison before, during, and after testing.
- Final signature scan proving whether prior storage metadata remains.

Destructive burn-in requires an immutable plan and exact destructive approval
bound to the selected devices. It must not be available for mounted, held,
boot-chain, read-only, or ambiguously identified media.

## Results

Each individual test and the overall ingest operation use four normalized
states:

- `pass`: the selected test completed and met its defined acceptance rules.
- `warning`: the test completed but reported evidence that needs operator
  judgment.
- `fail`: the test completed and violated an acceptance rule.
- `unknown`: the test could not establish a result, was unsupported, or was not
  selected.

The overall result must include a confidence statement tied to the selected
profile. For example, a successful Quick profile can state that no immediate
failure was detected, but cannot claim that the complete surface passed.

## Evidence record

Every test result records:

- operation, plan, and hardware-snapshot identities;
- stable device identity and the current resolved device path;
- model, serial, WWN, firmware, capacity, transport, controller, enclosure, and
  bay when available;
- test type, intensity profile, destructive classification, and parameters;
- tool/provider name and version;
- start, heartbeat, completion, and elapsed times;
- raw status and bounded raw diagnostic evidence;
- normalized result and stable reason codes;
- threshold or rule responsible for the assessment;
- source, confidence, and provenance;
- readings before and after the test;
- what could not be tested and why; and
- recommended disposition: accept, monitor, reject, or investigate.

Secrets, unrelated host data, and unbounded command output must never enter the
evidence record.

## Explainable outcomes

The interface should lead with the conclusion and keep the supporting evidence
one level below it. Examples:

> **Fail — reject this disk.** The extended self-test failed at a reported
> address and the device reports pending sectors. View the raw device evidence.

> **Unknown — SMART unavailable.** The USB bridge did not provide the required
> pass-through command. No health conclusion was inferred from USB attachment
> time.

> **Pass with limited confidence.** The short self-test and current health
> counters passed. The full media surface was not read because Quick testing was
> selected.

## Workflow states

```text
draft -> reviewed -> approved-if-destructive -> queued -> running
      -> passed | warning | failed | unknown | needs-attention
```

Cancellation is allowed between safe test boundaries. If interruption occurs
during a destructive pattern, the device remains in `needs-attention` until
Hoardarr proves its current state and the operator chooses how to proceed.

## Follow-on actions

After ingest, Hoardarr may offer:

- accept the media into a new storage plan;
- inspect or import detected existing storage;
- rerun with a more intensive profile;
- quarantine the media for investigation;
- reject or decommission the media; or
- download the evidence report.

