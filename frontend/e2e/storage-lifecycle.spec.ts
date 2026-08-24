import { expect, test, type Page } from "@playwright/test";

async function storageLifecycleServer(page: Page) {
  const now = "2026-08-23T16:00:00Z";
  const groupId = "22222222-2222-4222-8222-222222222222";
  const sourceId = "33333333-3333-4333-8333-333333333333";
  const destinationId = "44444444-4444-4444-8444-444444444444";
  const operationId = "55555555-5555-4555-8555-555555555555";
  let operationStatus: "queued" | "running" | "paused" | "succeeded" | null = null;
  let resumed = false;
  let resumedPolls = 0;
  let submittedConfirmation: string | null = null;
  let groupReads = 0;
  let sourceLifecycle = "assigned";
  let destinationLifecycle = "assigned";
  let lastGroupLifecycle = "assigned";
  let released = false;
  let foreignInspectionStarted = false;
  let foreignMigrationStarted = false;
  let unraidEvidenceLoaded = false;
  const group = () => ({
    id: groupId,
    name: "Media",
    namespace_path: "/srv/hoardarr/media",
    purpose: "media",
    state: "active",
    policy: { placement: "preferred_then_available" },
    backends: [
      {
        id: sourceId,
        stable_identity: "disk:wwn:source",
        physical_disk_id: "66666666-6666-4666-8666-666666666666",
        storage_entity_id: null,
        namespace_path: "/srv/hoardarr/backends/source",
        role: "data",
        lifecycle_state: operationStatus === "succeeded" ? "retired" : sourceLifecycle,
      },
      {
        id: destinationId,
        stable_identity: "disk:wwn:destination",
        physical_disk_id: "77777777-7777-4777-8777-777777777777",
        storage_entity_id: null,
        namespace_path: "/srv/hoardarr/backends/destination",
        role: "data",
        lifecycle_state: destinationLifecycle,
      },
    ].filter((backend) => !(released && backend.id === sourceId)),
    events: released
      ? [{ id: "event-released", event_type: "backend_released_for_reuse", backend_id: sourceId, previous_state: "retired", resulting_state: "reuse_ready", reason: "operator release", occurred_at: now }]
      : operationStatus === "succeeded"
      ? [{ id: "event-retired", event_type: "backend_retired", backend_id: sourceId, previous_state: "read_only", resulting_state: "retired", reason: "verified drain completed", occurred_at: now }]
      : [],
  });
  const plan = {
    schema_version: 1,
    kind: "storage.drain",
    storage_group_id: groupId,
    storage_group_namespace: "/srv/hoardarr/media",
    source: { backend_id: sourceId, stable_identity: "disk:wwn:source", path: "/srv/hoardarr/backends/source", filesystem_device: 101, required_bytes: 48_234_567, health: "healthy", lifecycle_state: "preferred_write" },
    destinations: [{ backend_id: destinationId, stable_identity: "disk:wwn:destination", path: "/srv/hoardarr/backends/destination", filesystem_device: 202, free_bytes: 900_000_000_000, total_bytes: 1_000_000_000_000, health: "healthy" }],
    verification: { mode: "accurate", full_hashes: true, additional_read_pass: false },
    capacity: { required_bytes: 48_234_567, destination_free_bytes: 900_000_000_000, reserve_bytes: 1_073_741_824 },
    controls: {
      enforce_source_read_only: false,
      source_read_only_capability: { supported: true, currently_read_only: false, reason: "Exact disposable mount." },
      bandwidth_limit_mib_per_second: null,
      start_at: null,
      maintenance_window_minutes: null,
      maintenance_window_end: null,
    },
    open_use: { quality: "available", open_handles: 0, processes: [] },
    arr_activity: { quality: "available", active_writes: 0 },
    blockers: [],
    warnings: [],
    ready: true,
    phases: ["preflight", "remove_from_write_placement", "copy", "verify", "finalize", "reconcile_namespace"],
    plan_sha256: "a".repeat(64),
  };
  const operation = () => ({
    id: operationId,
    kind: "storage.drain",
    status: operationStatus ?? "queued",
    resource: { type: "storage_group", id: groupId },
    result: operationStatus === "succeeded" ? { namespace_path: "/srv/hoardarr/media" } : null,
  });

  await page.route("**/*", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value });
    if (pathname.endsWith("/setup/status")) return json({ configured: true, claim_available: false });
    if (pathname.endsWith("/auth/me")) return json({ csrf_token: "browser-csrf" });
    if (pathname.endsWith("/onboarding")) return json({ version: 1, steps: [], defaults: { experience: "guided", server: { hostname: "hoardarr", timezone: "UTC", dst_mode: "automatic" }, network: { mode: "single", interface_ids: [], addressing: "dhcp", addresses: [], gateway: null, dns_servers: [], vlan_id: null, mtu: 1500, bridge: { enabled: false, stp: true, prefer_rstp: true } }, ntp: { servers: ["pool.ntp.org"] }, discovery: { lldp: { enabled: true, mode: "rx_tx" }, cdp: { receive: true, smart_transmit: true } } }, apply_available: true });
    if (pathname.endsWith("/onboarding/network/interfaces")) return json({ items: [] });
    if (pathname.endsWith("/networking")) return json({ configuration: null, pending_confirmation: false, capabilities: { available: true, tools: {} }, interfaces: [], current: { hostname: "hoardarr", timezone: "UTC", addresses: {}, default_interface: null, default_gateway: null } });
    if (pathname.endsWith("/hardware/snapshots/latest")) return json({ title: "Not found" }, 404);
    if (pathname.endsWith("/storage/foreign/inspection/preview")) return json({ plan: {
      schema_version: 1,
      operation: "foreign.inspect_read_only",
      candidate_id: "foreign:archive",
      hardware_snapshot_id: "snapshot-foreign",
      hardware_snapshot_sha256: "d".repeat(64),
      device: { id: "wwn:archive", model: "Disposable archive disk", capacity_bytes: 8_000_000_000 },
      source: { kind: "whole_device", kernel_path_at_preview: "/dev/loop9", partition_number: null, filesystem_type: "xfs", filesystem_uuid: "archive-fs", filesystem_label: "Archive", signature_source: "wipefs", read_only_options: ["ro", "norecovery", "nodev", "nosuid", "noexec"] },
      limits: { maximum_entries: 100_000, maximum_extension_groups: 256, maximum_errors: 100 },
      access: "read_only",
      persistent_mount: false,
      automatic_activation: false,
      mutation_performed: false,
      plan_sha256: "e".repeat(64),
    } });
    if (pathname.endsWith("/storage/foreign/inspection")) {
      const body = request.postDataJSON() as { confirmation: string };
      expect(body.confirmation).toBe("INSPECT READ ONLY");
      foreignInspectionStarted = true;
      return json({ operation: { id: "foreign-operation", kind: "storage.foreign.inspect", status: "succeeded", resource: { type: "foreign_storage", id: "foreign:archive" }, result: { access: "read_only", persistent_mount: false, mutation_performed: false, inventory: { file_count: 24, total_bytes: 8192, read_errors: [] } } } }, 202);
    }
    if (pathname.endsWith("/storage/foreign/migration/preview")) return json({ plan: {
      schema_version: 1,
      operation: "foreign.migrate_files",
      candidate_id: "foreign:archive",
      hardware_snapshot_id: "snapshot-foreign",
      hardware_snapshot_sha256: "d".repeat(64),
      source_inventory_operation_id: "foreign-operation",
      source_inventory_sha256: "8".repeat(64),
      device: { id: "wwn:archive", model: "Disposable archive disk", capacity_bytes: 8_000_000_000 },
      device_binding_sha256: "7".repeat(64),
      source: { kind: "whole_device", kernel_path_at_preview: "/dev/loop9", partition_number: null, filesystem_type: "xfs", filesystem_uuid: "archive-fs", filesystem_label: "Archive", signature_source: "wipefs", read_only_options: ["ro", "norecovery", "nodev", "nosuid", "noexec"] },
      destination: { id: destinationId, backend_id: destinationId, storage_group_id: groupId, name: "Media", path: "/srv/hoardarr/backends/destination", stable_identity: "disk:wwn:destination", lifecycle_state: "preferred_write", device_number: 202, free_bytes: 900_000_000_000, free_bytes_at_preview: 900_000_000_000, reserve_bytes: 1_073_741_824 },
      inventory: { file_count: 24, total_bytes: 8192 },
      verification: { mode: "accurate", algorithm: "blake3" },
      collision_policy: "stop",
      source_access: "read_only",
      source_retained: true,
      parity_reuse_supported: false,
      plan_sha256: "6".repeat(64),
    } });
    if (pathname.endsWith("/storage/foreign/migration")) {
      const body = request.postDataJSON() as { confirmation: string };
      expect(body.confirmation).toBe("COPY AND VERIFY");
      foreignMigrationStarted = true;
      return json({ operation: { id: "foreign-migration", kind: "storage.foreign.migrate", status: "succeeded", resource: { type: "foreign_storage", id: "foreign:archive" }, result: { files_total: 24, files_copied: 24, files_verified: 24, files_reused: 0, bytes_copied: 8192, destination_path: "/srv/hoardarr/backends/destination", source_retained: true, parity_reused: false } } }, 202);
    }
    if (pathname.endsWith("/storage/foreign/stack-preview")) return json({ result: {
      candidate_id: "foreign:1234567890abcdef12345678",
      plan_sha256: "9".repeat(64),
      provider: "linux_md",
      identity: "4f6dbb74:8a9d60c1:0e9ff452:d42877bd",
      name: "media:0",
      layout: "raid6",
      members: [{ source: "/dev/loop11", role: 0 }, { source: "/dev/loop12", role: 1 }],
      completeness: { quality: "available", state: "incomplete", expected_members: 4, observed_members: 2, missing_members: 2 },
      health: { quality: "not_reported", state: null, reason: "Inactive MD member metadata does not prove current array health." },
      mountability: { quality: "temporarily_unavailable", state: "not_ready", reason: "All expected unique MD member identities were not observed." },
      activation_performed: false,
      mutation_performed: false,
    } });
    if (pathname.endsWith("/storage/foreign/unraid/evidence") && request.method() === "POST") {
      const body = request.postDataJSON() as { source: string; assignments: Array<{ slot: string; role: string; serial: string }> };
      expect(body.source).toBe("unraid_runtime_state");
      expect(body.assignments).toEqual(expect.arrayContaining([
        expect.objectContaining({ slot: "disk1", role: "data", serial: "ARCHIVE-DATA" }),
      ]));
      unraidEvidenceLoaded = true;
      return json({ item: { id: "unraid-evidence", document_sha256: "1".repeat(64) } }, 201);
    }
    if (pathname.endsWith("/storage/foreign/unraid/evidence") && request.method() === "DELETE") {
      unraidEvidenceLoaded = false;
      return json({ removed: true });
    }
    if (pathname.endsWith("/storage/foreign")) return json({
      snapshot: { id: "snapshot-foreign", captured_at: now, sha256: "d".repeat(64) },
      policy: { default_access: "read_only", automatic_mount: false, automatic_assembly: false, mutation_performed: false },
      unraid_evidence: unraidEvidenceLoaded ? { id: "unraid-evidence", source: "unraid_runtime_state", document_sha256: "1".repeat(64), captured_at: now, unraid_version: "7.1.4", assignment_count: 1, matched_assignment_count: 1, unmatched_slots: [], ambiguous_slots: [] } : null,
      migration_destinations: [{ id: destinationId, storage_group_id: groupId, name: "Media", path: "/srv/hoardarr/backends/destination", stable_identity: "disk:wwn:destination", lifecycle_state: "preferred_write", device_number: 202, free_bytes: 900_000_000_000 }],
      candidates: [{
        id: "foreign:archive",
        profile: "standalone_filesystem",
        profile_name: "Standalone filesystem",
        origin: { name: "Not reported", confidence: "unknown", reason: "Filesystem metadata cannot identify the previous system." },
        confidence: "high",
        state: "ready",
        members: [{ device_id: "wwn:archive", kernel_path: "/dev/loop9", model: "Disposable archive disk", serial: "ARCHIVE-DATA", wwn: "archive", capacity_bytes: 8_000_000_000, stable_identity: true, system_device: false, read_only: false, removable: false, mounted: false, mountpoints: [], signature_scan: { status: "complete", source: "wipefs", reason: null }, confidence: "high", signatures: [{ type: "xfs", usage: "filesystem", uuid: "archive-fs", label: "Archive", source: "wipefs" }], unraid: unraidEvidenceLoaded ? { role: "data", classification: "identified", slot: "disk1", reason: "The loaded assignment export matches this device by serial and WWN.", evidence_sha256: "1".repeat(64), parity_reuse_supported: false } : null }],
        filesystems: ["XFS"],
        signature_types: ["xfs"],
        capacity_bytes: 8_000_000_000,
        health: { quality: "not_reported", state: null, reason: "Filesystem metadata does not prove drive health." },
        warnings: [],
        blockers: [],
        modes: [{ id: "inspect_read_only", available: true, reason: "A bounded read-only inventory can be reviewed and queued." }],
        unraid: unraidEvidenceLoaded ? { role: "data", classification: "identified", slot: "disk1", reason: "The loaded assignment export matches this device by serial and WWN.", evidence_sha256: "1".repeat(64), parity_reuse_supported: false } : { role: "data", classification: "suspected", slot: null, reason: "A supported independently readable filesystem is compatible with an Unraid data disk, but does not prove its origin.", evidence_sha256: null, parity_reuse_supported: false },
        latest_inventory: foreignInspectionStarted ? { operation_id: "foreign-operation", completed_at: now, hardware_snapshot_sha256: "d".repeat(64), current_snapshot_match: true, filesystem: { type: "xfs", uuid: "archive-fs", label: "Archive" }, inventory: { file_count: 24, total_bytes: 8192, largest_file: { path: "Movies/Feature.mkv", bytes: 4096 }, oldest_mtime_unix: 1_700_000_000, newest_mtime_unix: 1_710_000_000, extension_distribution: [{ extension: ".mkv", files: 1 }], case_collision_count: 0, unicode_collision_count: 0, read_errors: [], truncated: false }, access: "read_only", persistent_mount: false, mutation_performed: false } : null,
        mutation_performed: false,
      }, {
        id: "foreign:1234567890abcdef12345678",
        profile: "linux_md",
        profile_name: "Linux MD array",
        origin: { name: "Generic Linux storage", confidence: "medium", reason: "Linux MD labels identify the storage technology, not a NAS vendor." },
        confidence: "high",
        state: "ready",
        members: [
          { device_id: "wwn:md-one", kernel_path: "/dev/loop11", model: "Disposable MD member", capacity_bytes: 1_000_000_000, stable_identity: true, system_device: false, read_only: false, removable: false, mounted: false, mountpoints: [], signature_scan: { status: "complete", source: "wipefs", reason: null }, confidence: "high", signatures: [{ type: "linux_raid_member", usage: "raid", uuid: "md-uuid", label: null, source: "wipefs" }] },
          { device_id: "wwn:md-two", kernel_path: "/dev/loop12", model: "Disposable MD member", capacity_bytes: 1_000_000_000, stable_identity: true, system_device: false, read_only: false, removable: false, mounted: false, mountpoints: [], signature_scan: { status: "complete", source: "wipefs", reason: null }, confidence: "high", signatures: [{ type: "linux_raid_member", usage: "raid", uuid: "md-uuid", label: null, source: "wipefs" }] },
        ],
        filesystems: [],
        signature_types: ["linux_raid_member"],
        capacity_bytes: 2_000_000_000,
        health: { quality: "not_reported", state: null, reason: "Inactive array labels do not prove health." },
        warnings: [],
        blockers: [],
        modes: [
          { id: "inspect_read_only", available: false, reason: "The stack is not a standalone filesystem." },
          { id: "preview_stack", available: true, reason: "Provider labels can be reviewed without assembly." },
        ],
        latest_inventory: null,
        mutation_performed: false,
      }],
      unrecognized_device_count: 0,
    });
    if (pathname.endsWith("/storage/groups") && request.method() === "GET") {
      const value = group();
      groupReads += 1;
      lastGroupLifecycle = value.backends.find((backend) => backend.id === sourceId)?.lifecycle_state ?? "released";
      return json({ items: [value] });
    }
    if (pathname.endsWith("/storage/disks")) return json({ items: released ? [{ id: "66666666-6666-4666-8666-666666666666", stable_identity: "wwn:source", kernel_path: "/dev/loop0", serial: "DISPOSABLE-SOURCE", wwn: "source", vendor: "Test", model: "Virtual disk", capacity_bytes: 1_000_000_000, media_type: "ssd", health_state: "healthy", lifecycle_state: "reuse_ready", last_seen_at: now }] : [] });
    if (pathname.endsWith("/storage/expansion")) return json({ schema_version: 1, hardware_snapshot_id: "lifecycle-snapshot", hardware_snapshot_sha256: "f".repeat(64), captured_at: now, storage_groups: [], available_disks: [], reserved_disks: [], detected_capabilities: { mergerfs: false, snapraid: false, zfs: false }, candidates: [], methodology: "Read-only test assessment." });
    const activationMatch = pathname.match(/\/storage\/groups\/[^/]+\/backends\/([^/]+)\/activation(?:\/preview)?$/);
    if (activationMatch && pathname.endsWith("/preview")) {
      const selected = activationMatch[1];
      const source = selected === sourceId;
      return json({ plan: {
        schema_version: 1,
        kind: "storage.backend.activate",
        storage_group_id: groupId,
        storage_group_namespace: "/srv/hoardarr/media",
        backend_id: selected,
        stable_identity: source ? "disk:wwn:source" : "disk:wwn:destination",
        lifecycle_state: "assigned",
        health: "healthy",
        evidence: { path: source ? "/srv/hoardarr/backends/source" : "/srv/hoardarr/backends/destination", filesystem_device: source ? 101 : 202, mount_source: source ? "/dev/loop0" : "/dev/loop1", exact_mount: true, identity_match: true, identity_basis: "disposable loop mount matches the registered stable device", total_bytes: 1_000_000_000, free_bytes: 900_000_000 },
        blockers: [],
        ready: true,
        plan_sha256: (source ? "b" : "c").repeat(64),
      } });
    }
    if (activationMatch) {
      if (activationMatch[1] === sourceId) sourceLifecycle = "active";
      if (activationMatch[1] === destinationId) destinationLifecycle = "active";
      return json({ item: group() });
    }
    if (pathname.endsWith(`/storage/groups/${groupId}/backends/${sourceId}/transition`)) {
      sourceLifecycle = "preferred_write";
      destinationLifecycle = "active";
      return json({ item: group() });
    }
    if (pathname.endsWith(`/storage/groups/${groupId}/drain/preview`)) return json({ plan });
    if (pathname.endsWith(`/storage/groups/${groupId}/drain`)) {
      const body = request.postDataJSON() as { confirmation: string };
      submittedConfirmation = body.confirmation;
      operationStatus = "queued";
      return json({ operation: operation(), replayed: false }, 202);
    }
    if (pathname.endsWith(`/storage/groups/${groupId}/backends/${sourceId}/retirement`)) {
      const body = request.postDataJSON() as { confirmation: string };
      expect(body.confirmation).toBe("RELEASE");
      released = true;
      return json({ item: group(), disk: { id: "66666666-6666-4666-8666-666666666666", lifecycle_state: "reuse_ready" } });
    }
    if (pathname.endsWith(`/operations/${operationId}/pause`)) {
      operationStatus = "paused";
      return json(operation(), 202);
    }
    if (pathname.endsWith(`/operations/${operationId}/resume`)) {
      operationStatus = "running";
      resumed = true;
      return json(operation(), 202);
    }
    if (pathname.endsWith(`/operations/${operationId}/progress`)) {
      if (resumed) resumedPolls += 1;
      if (resumedPolls >= 2) operationStatus = "succeeded";
      const completed = operationStatus === "succeeded";
      return json({
        operation_id: operationId,
        state: operationStatus,
        phase: completed ? "completed" : operationStatus === "paused" ? "paused" : "copying",
        completed_steps: completed ? 4 : 0,
        total_steps: 4,
        percent: completed ? 100 : operationStatus === "paused" ? 35 : 58,
        completed_actions: [],
        notices: [],
        current_action: completed ? null : { id: "Movies/Feature.mkv", type: "copying" },
        estimate: null,
        updated_at: Date.now() / 1000,
        files: { total: 4, copied: completed ? 4 : 2, verified: completed ? 4 : 0 },
        bytes: { total: 48_234_567, copied: completed ? 48_234_567 : 24_000_000 },
        report: completed ? { source_backend_id: sourceId, source_state: "retired", files_moved: 4, namespace_path: "/srv/hoardarr/media", namespace_preserved: true } : null,
      });
    }
    if (pathname.endsWith(`/operations/${operationId}`)) {
      if (resumed) operationStatus = "succeeded";
      return json(operation());
    }
    if (pathname.endsWith("/operations")) return json({ items: operationStatus ? [operation()] : [] });
    if (pathname.endsWith("/storage/mergerfs")) return json({ available: true, status: "configured", items: [] });
    if (pathname.endsWith("/storage/transfers/summary")) return json({ active: 0, pending: 0, failed: 0, completed: 0, bytes_moved: 0, current_throughput_bytes_per_second: 0, estimated_drain_seconds: null, hardlink_rate: null, copy_rate: null, seeding_retained_bytes: 0, recoverable_bytes: 0 });
    if (pathname.endsWith("/storage/logical")) return json({ items: [] });
    if (pathname.endsWith("/hardware/topology/plan-templates")) return json({ items: [] });
    if (pathname.endsWith("/hardware/topology/plans")) return json({ items: [] });
    if (pathname.endsWith("/storage/inventory")) return json({ captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] } });
    if (pathname.endsWith("/integrations") || pathname.endsWith("/wizards")) return json({ items: [] });
    if (pathname.endsWith("/system/overview")) return json({ captured_at: now, source: "live", system: { hostname: "hoardarr", application: "Hoardarr", version: "0.3.11", database_ready: true, booted_at: null, uptime_seconds: 60, cpu: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, boot_volume: null, temperatures: [] }, storage: { snapshot: null, drive_count: 2, raw_capacity_bytes: 2_000_000_000_000, health: "healthy", pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] } }, network: { interfaces: [], discovery: { status: "no_neighbors", source: null, captured_at: now, detail: null, neighbors: [] } }, activity: { operations: [] }, applications: { connections: [] }, alerts: [] });
    if (pathname.endsWith("/system/resources")) return json({ captured_at: now, processor: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, volumes: [], network: { interfaces: [] }, storage: { performance: null } });
    if (pathname.endsWith("/storage/telemetry")) return json({ captured_at: now, summary: { sample_seconds: null, writes_today_bytes: 0 }, drives: [], pools: [] });
    return route.continue();
  });
  return {
    confirmation: () => submittedConfirmation,
    groupReads: () => groupReads,
    lastGroupLifecycle: () => lastGroupLifecycle,
    released: () => released,
    foreignInspectionStarted: () => foreignInspectionStarted,
    foreignMigrationStarted: () => foreignMigrationStarted,
    unraidEvidenceLoaded: () => unraidEvidenceLoaded,
  };
}

test("drains and retires a Storage Group source through the real browser workflow", async ({ page }, testInfo) => {
  test.setTimeout(45_000);
  const observed = await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await expect(page.getByRole("heading", { name: "Storage", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Media", { exact: true }).getByText("/srv/hoardarr/media")).toBeVisible();
  await page.getByRole("button", { name: "Review activation" }).first().click();
  await expect(page.getByRole("heading", { name: "Review mounted storage" })).toBeVisible();
  await expect(page.getByText("Matches assigned storage")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("storage-lifecycle-activation.png"), fullPage: true });
  await page.getByRole("button", { name: "Activate verified storage" }).click();
  await page.getByRole("button", { name: "Review activation" }).first().click();
  await page.getByRole("button", { name: "Activate verified storage" }).click();
  await page.getByRole("button", { name: "Prefer new files here" }).first().click();
  await expect(page.getByText("Preferred for new files", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Preview drain" }).first().click();
  await expect(page.getByText("Drain preflight")).toBeVisible();
  await expect(page.getByText(/Source files are removed only after/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("storage-lifecycle-preflight.png"), fullPage: true });
  await page.getByLabel("Drain destructive confirmation").fill("I AGREE");
  await page.getByRole("button", { name: "Start durable drain" }).click();
  await expect(page.getByRole("heading", { name: "Drain and retire source" })).toBeVisible();
  expect(observed.confirmation()).toBe("I AGREE");
  await page.getByRole("button", { name: "Pause drain" }).click();
  await expect(page.getByRole("button", { name: "Resume drain" })).toBeVisible();
  await page.getByRole("button", { name: "Resume drain" }).click();
  await expect(page.getByText("Drain completed")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Moved and verified 4 files/)).toBeVisible();
  await expect.poll(observed.groupReads).toBeGreaterThan(1);
  await expect.poll(observed.lastGroupLifecycle).toBe("retired");
  await expect(page.getByTitle("Technical state: retired")).toBeVisible();
  await expect(page.getByLabel("Media", { exact: true }).getByText("/srv/hoardarr/media")).toBeVisible();
  await page.getByRole("button", { name: "Release retired disk" }).click();
  await expect(page.getByText(/does not erase, format, mount, or wipe/)).toBeVisible();
  await page.getByLabel("Release retired disk confirmation").fill("RELEASE");
  await page.getByRole("button", { name: "Release for reuse" }).click();
  await expect.poll(observed.released).toBe(true);
  await page.getByText("Recent lifecycle activity").click();
  await expect(page.getByText("backend released for reuse")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("storage-lifecycle-completed.png"), fullPage: true });
});

test("reviews and completes a bounded read-only foreign inventory in the real Storage UI", async ({ page }, testInfo) => {
  const observed = await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await page.getByText("Inspect storage from another system").click();
  await expect(page.getByText("Standalone filesystem")).toBeVisible();
  await expect(page.getByText("Not reported").first()).toBeVisible();
  await page.getByRole("button", { name: "Review read-only inspection" }).click();
  await expect(page.getByText("No storage configuration will change")).toBeVisible();
  await expect(page.getByText("100,000 entries")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("foreign-readonly-review.png"), fullPage: true });
  await page.getByRole("button", { name: "INSPECT READ ONLY" }).click();
  await expect(page.getByText("Read-only inventory completed")).toBeVisible();
  await expect(page.getByText(/24 files/)).toBeVisible();
  expect(observed.foreignInspectionStarted()).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("foreign-readonly-completed.png"), fullPage: true });
  await page.getByRole("button", { name: "Close report" }).click();
  await expect(page.getByText("Current inspection report")).toBeVisible();
  await expect(page.getByText("Movies/Feature.mkv", { exact: false })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("foreign-readonly-persisted-report.png"), fullPage: true });
});

test("reviews inactive storage-stack metadata without activating it", async ({ page }, testInfo) => {
  await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await page.getByText("Inspect storage from another system").click();
  await expect(page.getByText("Linux MD array", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review stack metadata" }).click();
  await expect(page.getByText("Storage stack was not activated")).toBeVisible();
  await expect(page.getByText("4f6dbb74:8a9d60c1:0e9ff452:d42877bd")).toBeVisible();
  await expect(page.getByText("2 of 4")).toBeVisible();
  await expect(page.getByText("Inactive MD member metadata does not prove current array health.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("foreign-stack-no-activation-preview.png"), fullPage: true });
});

test("copies a reviewed foreign disk into managed storage with the source retained", async ({ page }, testInfo) => {
  const observed = await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await page.getByText("Inspect storage from another system").click();
  await page.getByRole("button", { name: "Review read-only inspection" }).click();
  await page.getByRole("button", { name: "INSPECT READ ONLY" }).click();
  await expect(page.getByText("Read-only inventory completed")).toBeVisible();
  await page.getByRole("button", { name: "Close report" }).click();
  await page.getByRole("button", { name: "Plan verified copy" }).click();
  await expect(page.getByText("This copies files; it does not adopt or erase the source")).toBeVisible();
  await expect(page.getByLabel("Destination")).toHaveValue("44444444-4444-4444-8444-444444444444");
  await page.getByRole("button", { name: "Review copy plan" }).click();
  await expect(page.getByText("Source data stays untouched")).toBeVisible();
  await expect(page.getByText("Stop before replacing any existing file")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("foreign-migration-review.png"), fullPage: true });
  await page.getByRole("button", { name: "COPY AND VERIFY" }).click();
  await expect(page.getByText("Copy and verification completed")).toBeVisible();
  await expect(page.getByText(/source stayed read-only and remains unchanged/i)).toBeVisible();
  expect(observed.foreignMigrationStarted()).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("foreign-migration-completed.png"), fullPage: true });
});

test("loads stable Unraid assignment evidence and identifies a data disk in the real Storage UI", async ({ page }, testInfo) => {
  const observed = await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await page.getByText("Inspect storage from another system").click();
  await expect(page.getByText("No Unraid assignment export loaded")).toBeVisible();
  await expect(page.getByText(/Suspected only: data/)).toBeVisible();

  await page.getByLabel("Load Unraid assignment export").setInputFiles({
    name: "unraid-assignments.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({
      schema_version: 1,
      source: "unraid_runtime_state",
      captured_at: "2026-08-23T16:00:00Z",
      unraid_version: "7.1.4",
      assignments: [{ slot: "disk1", role: "data", serial: "ARCHIVE-DATA", wwn: "archive", capacity_bytes: 8_000_000_000, filesystem_type: "xfs" }],
    })),
  });

  await expect.poll(observed.unraidEvidenceLoaded).toBe(true);
  await expect(page.getByText("assignment evidence loaded")).toBeVisible();
  await expect(page.getByText("1 of 1")).toBeVisible();
  await expect(page.getByText(/Identified: data/)).toBeVisible();
  await expect(page.getByText(/Original slot: disk1/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("foreign-unraid-identified-data.png"), fullPage: true });
});
