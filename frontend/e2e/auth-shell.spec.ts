import { expect, test, type Page, type Route } from "@playwright/test";

async function configuredButSignedOut(page: Page): Promise<void> {
  await page.route("**/setup/status", (route) => route.fulfill({ json: { configured: true, claim_available: false } }));
  await page.route("**/auth/me", (route) => route.fulfill({
    status: 401,
    contentType: "application/problem+json",
    body: JSON.stringify({ title: "Not authenticated", status: 401 }),
  }));
}

async function authenticatedEmptyServer(page: Page): Promise<void> {
  await page.route("**/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const json = (value: unknown) => route.fulfill({ json: value });
    if (pathname.endsWith("/setup/status")) return json({ configured: true, claim_available: false });
    if (pathname.endsWith("/auth/me")) return json({ csrf_token: "browser-csrf" });
    if (pathname.endsWith("/onboarding")) return json({
      version: 1,
      steps: [],
      defaults: {
        experience: "guided",
        server: { hostname: "hoardarr", timezone: "UTC", dst_mode: "automatic" },
        network: { mode: "single", interface_ids: [], addressing: "dhcp", addresses: [], gateway: null, dns_servers: [], vlan_id: null, mtu: 1500, bridge: { enabled: false, stp: true, prefer_rstp: true } },
        ntp: { servers: ["pool.ntp.org"] },
        discovery: { lldp: { enabled: true, mode: "rx_tx" }, cdp: { receive: true, smart_transmit: true } },
      },
      apply_available: true,
    });
    if (pathname.endsWith("/onboarding/network/interfaces")) return json({ items: [] });
    if (pathname.endsWith("/networking")) return json({ configuration: null, pending_confirmation: false, capabilities: { available: true, tools: {} }, interfaces: [], current: { hostname: "hoardarr", timezone: "UTC", addresses: {}, default_interface: null, default_gateway: null } });
    if (pathname.endsWith("/hardware/snapshots/latest")) return route.fulfill({ status: 404, json: { title: "Not found" } });
    if (pathname.endsWith("/hardware/topology/expectation")) return json({ expectation: null, active_drifts: [], recent_events: [] });
    if (pathname.endsWith("/hardware/topology/plan-templates")) return json({ items: [] });
    if (pathname.endsWith("/hardware/topology/plans")) return json({ items: [] });
    if (pathname.endsWith("/storage/mergerfs")) return json({ available: true, status: "configured", items: [] });
    if (pathname.endsWith("/storage/groups") || pathname.endsWith("/storage/disks")) return json({ items: [] });
    if (pathname.endsWith("/storage/expansion")) return json({ schema_version: 1, hardware_snapshot_id: "none", hardware_snapshot_sha256: "0".repeat(64), captured_at: new Date().toISOString(), storage_groups: [], available_disks: [], reserved_disks: [], detected_capabilities: { mergerfs: false, snapraid: false, zfs: false }, candidates: [], methodology: "Read-only test assessment." });
    if (pathname.endsWith("/storage/logical")) return json({ items: [] });
    if (pathname.endsWith("/storage/foreign")) return json({
      snapshot: { id: "none", captured_at: new Date().toISOString(), sha256: "0".repeat(64) },
      policy: { default_access: "read_only", automatic_mount: false, automatic_assembly: false, mutation_performed: false },
      unraid_evidence: null,
      nas_evidence: null,
      migration_destinations: [],
      candidates: [],
      unrecognized_device_count: 0,
    });
    if (pathname.endsWith("/storage/inventory")) return json({ captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] } });
    if (pathname.endsWith("/integrations")) return json({ items: [] });
    if (pathname.endsWith("/backups/targets") || pathname.endsWith("/backups/runs")) return json({ items: [] });
    if (pathname.endsWith("/auth/tokens")) return json({ items: [] });
    if (pathname.endsWith("/addons")) return json({ items: [] });
    if (pathname.endsWith("/updates/status")) return json({ current_version: "0.3.11", latest_version: null, channel: "stable", metadata_sha256: null, last_checked_at: null, last_error: null, operation: null });
    if (pathname.endsWith("/wizards") || pathname.endsWith("/operations")) return json({ items: [] });
    if (pathname.endsWith("/system/overview")) return json({
      captured_at: new Date().toISOString(), source: "live",
      system: { hostname: "hoardarr", application: "Hoardarr", version: "0.3.11", database_ready: true, booted_at: null, uptime_seconds: 60, cpu: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, boot_volume: null, temperatures: [] },
      storage: { snapshot: null, drive_count: null, raw_capacity_bytes: null, health: null, pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] } },
      network: { interfaces: [], discovery: { status: "no_neighbors", source: null, captured_at: new Date().toISOString(), detail: null, neighbors: [] } },
      activity: { operations: [] }, applications: { connections: [] }, alerts: [],
    });
    if (pathname.endsWith("/system/resources")) return json({ captured_at: new Date().toISOString(), processor: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, volumes: [], network: { interfaces: [] }, storage: { performance: null } });
    if (pathname.endsWith("/storage/telemetry")) return json({ captured_at: new Date().toISOString(), summary: { sample_seconds: null, writes_today_bytes: 0 }, drives: [], pools: [] });
    if (pathname.endsWith("/storage/transfers/summary")) return json({ operations: [], tiers: [], summary: { queued_bytes: 0, running_bytes: 0, retained_for_seeding_bytes: 0, failed_operations: 0, observed_bytes_per_second: null, rate_sample_count: 0, estimated_queued_seconds: null, estimate_quality: "not_reported", estimate_methodology: "No measured transfer history is available." } });
    if (pathname.endsWith("/telemetry/catalog")) return json({
      items: [{ id: "io.read.bytes_per_second", name: "Read throughput", entity_types: ["drive"], unit: "bytes_per_second", kind: "raw", source: "Linux block counters", minimum_interval_seconds: 5, capability: null, retention_class: "recent", aggregation: "mean", availability: "When Linux reports block counters", formula: null, test_evidence: "backend test", entitled: true }],
      quality_states: ["available", "not_reported", "unsupported", "temporarily_unavailable", "stale", "estimated", "derived"],
      entitlements: { state: "unlicensed", capabilities: [], expires_at: null, license_id: null, detail: "Basic telemetry is active.", validated_at: new Date().toISOString(), cached: false, basic_metrics_available: true },
    });
    if (pathname.endsWith("/telemetry/entities")) return json({ items: [{ id: "entity-drive-1", entity_type: "drive", stable_id: "wwn:test", display_name: "Test SSD", labels: {}, topology: {}, first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }] });
    if (pathname.endsWith("/telemetry/current")) return json({ captured_at: new Date().toISOString(), items: [{ metric_id: "io.read.bytes_per_second", name: "Read throughput", entity: { id: "entity-drive-1", entity_type: "drive", stable_id: "wwn:test", display_name: "Test SSD", labels: {}, topology: {}, first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }, timestamp: new Date().toISOString(), value: 1048576, unit: "bytes_per_second", source: "Linux block counters", collection_interval_seconds: 5, quality: "available", raw: true, labels: {}, capability: null, error_code: null }], restricted_capabilities: [] });
    if (pathname.endsWith("/telemetry/alerts")) return json({ items: [] });
    if (pathname.endsWith("/telemetry/settings")) return json({ collection: { fast_interval_seconds: 5, device_interval_seconds: 300, hardware_interval_seconds: 900 }, history: { recent_resolution_seconds: 5, recent_retention_hours: 48, medium_resolution_seconds: 3600, medium_retention_days: 90, long_resolution_seconds: 86400, long_retention_days: 730, maximum_graph_points: 1200, maximum_series: 16, maximum_observations: 20000 }, storage: { database_bytes: 4096, oldest_raw_history: null, oldest_retained_history: null, entity_count: 1, estimated_bytes_per_day: 1024, estimate_method: "estimate", last_cleanup: null, next_cleanup: null, cleanup_batch_size: 10000 }, extended_history: { entitled: false, capability: "metrics.history.extended" } });
    if (pathname.endsWith("/telemetry/history")) return json({ entity: { id: "entity-drive-1", entity_type: "drive", stable_id: "wwn:test", display_name: "Test SSD", labels: {}, topology: {}, first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }, metric_id: "io.read.bytes_per_second", unit: "bytes_per_second", resolution: "raw", requested_resolution: "auto", source_resolution: "raw", aggregation_method: "raw samples", raw: true, points_returned: 1, displayed_points: 1, start: new Date(Date.now() - 3600000).toISOString(), end: new Date().toISOString(), points: [{ timestamp: new Date().toISOString(), value: 1048576, quality: "available", raw: true, interval_seconds: 5 }] });
    return route.continue();
  });
}

async function storageWizardServer(page: Page, options: { firstDriveContainsData?: boolean; resumeAfterFailure?: boolean; legacyFailedStatus?: boolean } = {}): Promise<void> {
  await authenticatedEmptyServer(page);
  const now = new Date().toISOString();
  const drives = Array.from({ length: 8 }, (_, index) => ({
    id: `serial:test:ssd-${index + 1}`,
    stable_identity: true,
    kernel_name: `sd${String.fromCharCode(98 + index)}`,
    kernel_path: `/dev/sd${String.fromCharCode(98 + index)}`,
    vendor: "TEST",
    model: "SSD-1TB",
    identity: { serial: `SSD-${index + 1}`, wwn: `5000c5000000000${index}` },
    capacity_bytes: 1_000_000_000_000,
    sector_sizes: { logical_bytes: 512, physical_bytes: 4096 },
    read_only: false,
    system_disk: false,
    connection: { transport: "sas", protocol: "sas", controller_address: "0000:01:00.0", enclosure_id: "test-shelf", slot: String(index + 1) },
    partitions: options.firstDriveContainsData && index === 0 ? [{ kernel_path: "/dev/sdb1", number: 1, filesystem: { type: "ext4", uuid: "11111111-2222-3333-4444-555555555555", label: "media-archive" } }] : [],
    signatures: options.firstDriveContainsData && index === 0 ? [{ type: "ext4", usage: "filesystem", offset: "0x438", uuid: "11111111-2222-3333-4444-555555555555", label: "media-archive" }] : [],
    signature_scan: { status: "complete", source: "wipefs", reason: null },
    discard: { granularity_bytes: 4096, max_bytes: 1_073_741_824 },
    maintenance_capabilities: {
      source: "sg_opcodes REPORT SUPPORTED OPERATION CODES",
      ata_secure_erase: false,
      nvme_block_erase: false,
      nvme_crypto_erase: false,
      scsi_block_erase: index === 0,
      scsi_crypto_erase: false,
      smart_self_test: {
        status: "available",
        short_minutes: 2,
        extended_minutes: 381,
        source: "smartctl -j -c",
      },
    },
  }));
  const snapshot = { id: "snap-storage", captured_at: now, sha256: "c".repeat(64), hardware: { disks: drives } };
  let revision = 0;
  let applied = false;
  let completed = false;
  let resumed = options.resumeAfterFailure !== true;
  let answers: Record<string, unknown> = {};
  const wizard = () => ({ id: "wizard-storage", revision, mode: "guided", status: completed ? "completed" : applied ? "applied" : "review", current_step: "storage", hardware_snapshot_id: snapshot.id, answers, plan_id: "plan-storage", created_at: now, updated_at: now });
  const planTemplate = {
    id: "plan-storage",
    revision: 4,
    sha256: "d".repeat(64),
    document: {
      apply_available: true,
      blockers: [],
      presentation_root: "/data",
      actions: { directories: [], connectivity: [] },
      storage: {
        topology: "individual",
        selected_devices: [{ id: drives[0].id, stable_identity: true, vendor: "TEST", model: "SSD-1TB", serial: "SSD-1", wwn: drives[0].identity.wwn, capacity_bytes: drives[0].capacity_bytes, logical_sector_bytes: 512, physical_sector_bytes: 4096, partitions: [], signatures: [] }],
        snapshot_binding: { snapshot_id: snapshot.id, snapshot_sha256: snapshot.sha256, selected_device_ids: [drives[0].id] },
        actions: [
          { action_id: `partition:${drives[0].id}`, type: "disk.partition_table.create", device_id: drives[0].id, table: "gpt", alignment_bytes: 1_048_576, destructive: true },
          { action_id: "storage-layout", type: "storage.layout.ensure", topology: "individual", device_ids: [drives[0].id], purpose: "media", destructive: false },
        ],
        risk: { destructive: true, approval_required: true, required_phrase: "I AGREE", message: "The selected drive will be formatted." },
        format: { filesystem: "ext4", partition_table: "gpt", alignment_bytes: 1_048_576, allocation_unit_bytes: 4096 },
        libraries: [{ name: "Movies", path: "/data/media/Movies" }],
        downloads: { hardlinks: "same_filesystem_only" },
        folders: ["/data/media/Movies"],
      },
    },
  };
  const currentPlan = () => {
    const storageAnswers = (answers.storage ?? {}) as Record<string, any>;
    const selectedIds = Array.isArray(storageAnswers.selected_device_ids)
      ? storageAnswers.selected_device_ids as string[]
      : [drives[0].id];
    const selectedDevices = selectedIds
      .map((id) => drives.find((drive) => drive.id === id))
      .filter((drive): drive is typeof drives[number] => drive !== undefined)
      .map((drive) => ({
        id: drive.id,
        stable_identity: true,
        vendor: drive.vendor,
        model: drive.model,
        serial: drive.identity.serial,
        wwn: drive.identity.wwn,
        capacity_bytes: drive.capacity_bytes,
        logical_sector_bytes: drive.sector_sizes.logical_bytes,
        physical_sector_bytes: drive.sector_sizes.physical_bytes,
        partitions: drive.partitions,
        signatures: drive.signatures,
      }));
    const expansion = storageAnswers.expansion;
    const topology = storageAnswers.topology ?? planTemplate.document.storage.topology;
    const preservesExistingData = storageAnswers.preserve_data === true;
    const intakeTests = (storageAnswers.intake_tests ?? {}) as Record<string, boolean>;
    const smartActions = [
      ...(intakeTests.identity ? [{ action_id: `test:identity:${selectedIds[0]}`, type: "drive.identity.verify", device_id: selectedIds[0], destructive: false }] : []),
      ...(intakeTests.smart_short ? [{ action_id: `test:smart_short:${selectedIds[0]}`, type: "drive.smart.short", device_id: selectedIds[0], destructive: false }] : []),
      ...(intakeTests.smart_extended ? [{ action_id: `test:smart_extended:${selectedIds[0]}`, type: "drive.smart.extended", device_id: selectedIds[0], destructive: false }] : []),
    ];
    return {
      ...planTemplate,
      document: {
        ...planTemplate.document,
        storage: {
          ...planTemplate.document.storage,
          topology,
          selected_devices: selectedDevices,
          snapshot_binding: {
            snapshot_id: snapshot.id,
            snapshot_sha256: snapshot.sha256,
            selected_device_ids: selectedIds,
          },
          ...(expansion ? { expansion } : {}),
          ...(topology === "test" ? {
            actions: smartActions,
            intake_tests: intakeTests,
            risk: { destructive: false, approval_required: false, required_phrase: null, message: "Drive checks do not format or repartition the drive." },
            folders: [],
          } : {}),
          ...(topology !== "test" && preservesExistingData ? {
            actions: [{ action_id: "storage-layout", type: "storage.layout.ensure", topology, device_ids: selectedIds, purpose: storageAnswers.purpose ?? "media", destructive: false }],
            risk: { destructive: false, approval_required: false, required_phrase: null, message: "The detected filesystem and existing files are preserved." },
          } : {}),
        },
      },
    };
  };
  const operationResult = () => {
    const storageAnswers = (answers.storage ?? {}) as Record<string, any>;
    const tests = (storageAnswers.intake_tests ?? {}) as Record<string, boolean>;
    const actionResults = [
      ...(tests.smart_short ? [{ action_id: `test:smart_short:${drives[0].id}`, device_id: drives[0].id, outcome: "passed", code: "smart_self_test_passed", message: "The SMART short self-test completed without a reported error." }] : []),
      ...(tests.smart_extended ? [{ action_id: `test:smart_extended:${drives[0].id}`, device_id: drives[0].id, outcome: "passed", code: "smart_self_test_passed", message: "The SMART extended self-test completed without a reported error." }] : []),
    ];
    return storageAnswers.topology === "test"
      ? { topology: "test", selected_device_ids: [drives[0].id], mountpoint: null, action_results: actionResults, notices: [] }
      : { mountpoint: "/data", namespace_reconciled: true };
  };

  await page.route("**/api/v1/hardware/snapshots/latest", (route) => route.fulfill({ json: snapshot }));
  await page.route("**/api/v1/hardware/snapshots/snap-storage", (route) => route.fulfill({ json: snapshot }));
  await page.route("**/api/v1/hardware/scans", (route) => route.fulfill({ status: 202, json: { operation: { id: "scan-refresh", kind: "hardware.scan", status: "succeeded", result: { snapshot_id: snapshot.id }, resource: { type: "hardware_snapshot", id: snapshot.id } } } }));
  await page.route("**/api/v1/wizards", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { items: applied ? [wizard()] : [] } });
    return route.fulfill({ status: 201, json: wizard() });
  });
  await page.route("**/api/v1/wizards/wizard-storage/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/plan/approve")) return route.fulfill({ status: 201, json: { status: { valid: true } } });
    if (pathname.endsWith("/apply")) {
      applied = true;
      return route.fulfill({ status: 202, json: { operation: { id: "op-storage", kind: "storage.apply", status: "queued", resource: { type: "wizard_session", id: "wizard-storage" } } } });
    }
    if (pathname.endsWith("/complete")) {
      completed = true;
      return route.fulfill({ json: wizard() });
    }
    if (pathname.endsWith("/plan")) {
      const plan = currentPlan();
      return route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, json: route.request().method() === "POST" ? { plan } : plan });
    }
    if (pathname.includes("/steps/")) {
      const body = route.request().postDataJSON() as { answers: Record<string, unknown> };
      const step = pathname.split("/steps/")[1];
      revision += 1;
      answers = { ...answers, [step]: body.answers };
      return route.fulfill({ json: { ...wizard(), current_step: step } });
    }
    return route.fulfill({ json: wizard() });
  });
  const operationDocument = () => resumed
    ? { id: "op-storage", kind: "storage.apply", status: "succeeded", resource: { type: "wizard_session", id: "wizard-storage" }, result: operationResult(), created_at: now, updated_at: now }
    : { id: "op-storage", kind: "storage.apply", status: options.legacyFailedStatus ? "failed" : "needs_attention", resource: { type: "wizard_session", id: "wizard-storage" }, error: { code: "storage_tool_missing", message: "A required storage tool is unavailable: setfattr." }, created_at: now, updated_at: now };
  await page.route("**/api/v1/operations", (route) => route.fulfill({ json: { items: applied ? [operationDocument()] : [] } }));
  await page.route("**/api/v1/operations/op-storage", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/resume")) {
      resumed = true;
      return route.fulfill({ status: 202, json: { ...operationDocument(), status: "queued", result: null } });
    }
    return route.fulfill({ json: operationDocument() });
  });
  await page.route("**/operations/op-storage/resume", (route) => {
    resumed = true;
    return route.fulfill({ status: 202, json: { ...operationDocument(), status: "queued", result: null } });
  });
  await page.route("**/api/v1/operations/op-storage/progress", (route) => route.fulfill({ json: resumed
    ? { operation_id: "op-storage", state: "succeeded", phase: "Storage build completed", completed_steps: 6, total_steps: 6, percent: 100, completed_actions: ["format", "mount"], notices: [{ code: "storage_build_resumed", message: "Storage execution resumed from its durable checkpoint." }], current_action: null, estimate: null, updated_at: Date.now() / 1000 }
    : { operation_id: "op-storage", state: "needs_attention", phase: "Building the selected storage layout", completed_steps: 4, total_steps: 6, percent: 66, completed_actions: ["format", "mount"], notices: [], current_action: { id: "layout", type: "storage.layout.apply" }, estimate: null, updated_at: Date.now() / 1000 } }));
  await page.route("**/api/v1/operations/op-storage/events", (route) => route.fulfill({ json: { items: resumed
    ? [{ sequence: 1, type: "operation.needs_attention", message: "setfattr was unavailable", data: {}, created_at: now }, { sequence: 2, type: "operation.resumed", message: "Storage build queued to resume from its durable checkpoint", data: {}, created_at: now }, { sequence: 3, type: "operation.succeeded", message: "Storage build completed", data: {}, created_at: now }]
    : [{ sequence: 1, type: "operation.needs_attention", message: "setfattr was unavailable", data: {}, created_at: now }] } }));
  await page.route("**/api/v1/storage/groups", (route) => route.fulfill({ json: { items: applied ? [{
    id: "media",
    name: "Media",
    namespace_path: "/data/media",
    purpose: "media",
    state: "active",
    policy: { placement: "most_free_space" },
    backends: [
      { id: "backend-1", stable_identity: "serial:SSD-1", physical_disk_id: drives[0].id, storage_entity_id: null, namespace_path: "/mnt/disk1", role: "data", lifecycle_state: "active" },
      { id: "backend-2", stable_identity: "serial:SSD-2", physical_disk_id: drives[1].id, storage_entity_id: null, namespace_path: "/mnt/disk2", role: "data", lifecycle_state: "preferred_write" },
    ],
    events: [{ id: "event-expansion", event_type: "namespace_reconciled", message: "Media namespace reconciled after expansion", created_at: now }],
  }] : [] } }));
  await page.route("**/api/v1/storage/maintenance/preview", async (route) => {
    const body = route.request().postDataJSON() as { device_id: string; method: string };
    const plan = {
      schema_version: 1,
      action: "wipe",
      options: { method: body.method, passes: 1, capability: true, capability_source: "sg_opcodes REPORT SUPPORTED OPERATION CODES", scope: "user_data_media" },
      device: { id: body.device_id, stable_identity: true, vendor: "TEST", model: "SSD-1TB", serial: "SSD-1", wwn: drives[0].identity.wwn, eui64: null, nguid: null, capacity_bytes: drives[0].capacity_bytes, logical_sector_bytes: 512, physical_sector_bytes: 4096 },
      device_binding_sha256: "e".repeat(64),
      hardware_snapshot_sha256: snapshot.sha256,
      destructive: true,
      advanced_only: true,
    };
    return route.fulfill({ json: { plan, plan_sha256: "f".repeat(64) } });
  });
  await page.route("**/api/v1/storage/maintenance", (route) => route.fulfill({ status: 202, json: { operation: { id: "op-maintenance", kind: "storage.maintenance", status: "queued", resource: { type: "drive", id: drives[0].id } } } }));
  await page.route("**/api/v1/operations/op-maintenance", (route) => route.fulfill({ json: { id: "op-maintenance", kind: "storage.maintenance", status: "succeeded", resource: { type: "drive", id: drives[0].id }, result: { sanitization_report: { method: "scsi_sanitize", scope: "user_data_media", capability_source: "sg_opcodes REPORT SUPPORTED OPERATION CODES", result: "succeeded", verification: { status: "command_completed", source: "Running SCSI block erase" } } } } }));
  await page.route("**/api/v1/operations/op-maintenance/progress", (route) => route.fulfill({ json: { operation_id: "op-maintenance", state: "succeeded", phase: "Drive maintenance completed", completed_steps: 1, total_steps: 1, percent: 100, completed_actions: ["maintenance:1"], notices: [], current_action: null, estimate: null, updated_at: Date.now() / 1000 } }));
  await page.route("**/api/v1/accounts/media", (route) => route.fulfill({ status: 201, json: { account: { username: "media", created: true }, credential: { password: "one-time-storage-password" } } }));
}

async function unconfiguredServer(page: Page): Promise<void> {
  await authenticatedEmptyServer(page);
  await page.unroute("**/*");
  let claimed = false;
  let fleetHardwareEnabled = true;
  let fleetCountry: string | null = "US";
  await page.route("**/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const json = (value: unknown) => route.fulfill({ json: value });
    if (pathname.endsWith("/setup/status")) return json({ configured: claimed, claim_available: !claimed });
    if (pathname.endsWith("/setup/claim")) {
      claimed = true;
      return route.fulfill({ status: 201, json: { csrf_token: "browser-csrf" } });
    }
    if (pathname.endsWith("/fleet-telemetry/settings")) {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as {
          hardware_enabled: boolean;
          country_code: string | null;
        };
        fleetHardwareEnabled = body.hardware_enabled;
        fleetCountry = body.country_code;
      }
      return json({
        anonymous_heartbeat: { required: true, enabled: true },
        hardware_enabled: fleetHardwareEnabled,
        enhanced_enabled: false,
        content_enabled: false,
        installation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        endpoint: "https://hoardarr.com/api/telemetry/v1",
        connection_status: "not_registered",
        credential_fingerprint: null,
        last_successful_upload: null,
        last_attempted_upload: null,
        last_error: null,
        schema_version: 1,
        country_code: fleetCountry,
        timezone: "America/New_York",
        location_detection_method: "os_timezone",
        location_confirmed: route.request().method() === "PUT",
        queued_records: 0,
        queued_bytes: 0,
        dead_letter_records: 0,
        by_status: {},
        limitations: "Local administrators can modify collected data.",
      });
    }
    if (pathname.endsWith("/onboarding")) return json({
      version: 1,
      steps: [],
      defaults: {
        experience: "guided",
        server: { hostname: "hoardarr", timezone: "UTC", dst_mode: "automatic" },
        network: { mode: "single", interface_ids: [], addressing: "dhcp", addresses: [], gateway: null, dns_servers: [], vlan_id: null, mtu: 1500, bridge: { enabled: false, stp: true, prefer_rstp: true } },
        ntp: { servers: ["pool.ntp.org"] },
        discovery: { lldp: { enabled: true, mode: "rx_tx" }, cdp: { receive: true, smart_transmit: true } },
      },
      apply_available: true,
    });
    if (pathname.endsWith("/onboarding/network/interfaces")) return json({ items: [{ id: "enp1s0", name: "enp1s0", model: "Test Ethernet", mac: "00:11:22:33:44:55", link: "up", speed_mbps: 1000, is_physical: true, warnings: [] }] });
    if (pathname.endsWith("/networking/plan")) return json({ plan: { apply_available: true, blockers: [], warnings: [], changed_components: ["server", "network", "ntp", "discovery"] }, sha256: "a".repeat(64) });
    if (pathname.endsWith("/networking/apply")) return json({ state: "pending_confirmation", token: "b".repeat(32), confirm_within_seconds: 120, changed_components: ["server", "network", "ntp", "discovery"] });
    if (pathname.endsWith("/networking/confirm")) return route.fulfill({ status: 204 });
    if (pathname.endsWith("/networking")) return json({ configuration: null, pending_confirmation: false, capabilities: { available: true, tools: {} }, interfaces: [], current: { hostname: "hoardarr", timezone: "UTC", addresses: {}, default_interface: "enp1s0", default_gateway: null } });
    if (pathname.endsWith("/hardware/snapshots/latest")) return route.fulfill({ status: 404, json: { title: "Not found" } });
    if (pathname.endsWith("/storage/mergerfs")) return json({ available: true, status: "configured", items: [] });
    if (pathname.endsWith("/storage/logical")) return json({ items: [] });
    if (pathname.endsWith("/storage/inventory")) return json({ captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] } });
    if (pathname.endsWith("/integrations")) return json({ items: [] });
    if (pathname.endsWith("/wizards") || pathname.endsWith("/operations")) return json({ items: [] });
    if (pathname.endsWith("/system/overview")) return json({
      captured_at: new Date().toISOString(), source: "live",
      system: { hostname: "hoardarr", application: "Hoardarr", version: "0.3.11", database_ready: true, booted_at: null, uptime_seconds: 60, cpu: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, boot_volume: null, temperatures: [] },
      storage: { snapshot: null, drive_count: null, raw_capacity_bytes: null, health: null, pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] } },
      network: { interfaces: [], discovery: { status: "no_neighbors", source: null, captured_at: new Date().toISOString(), detail: null, neighbors: [] } },
      activity: { operations: [] }, applications: { connections: [] }, alerts: [],
    });
    if (pathname.endsWith("/system/resources")) return json({ captured_at: new Date().toISOString(), processor: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, volumes: [], network: { interfaces: [] }, storage: { performance: null } });
    return route.continue();
  });
}

async function controllerRedundancyServer(page: Page) {
  await authenticatedEmptyServer(page);
  let state: "single" | "fully_redundant" | "failed_over" = "single";
  const storageId = "11111111-1111-4111-8111-111111111111";
  const path = (name: "a" | "b", active = true) => ({
    id: `${name === "a" ? "3" : "4"}3333333-3333-4333-8333-333333333333`,
    stable_path_identity: `fc:hba-${name}:target-${name}`,
    kernel_path: name === "a" ? "/dev/sdb" : "/dev/sdc",
    protocol: "fc",
    state: active ? "ready" : "failed",
    active,
    optimized: name === "a",
    controller: { id: `${name === "a" ? "5" : "6"}5555555-5555-4555-8555-555555555555`, stable_identity: `hba-${name}`, model: `Controller ${name.toUpperCase()}`, provider: "dm-multipath", state: { vendor: "TEST", firmware: "1.2.3" } },
    metadata: { negotiated_speed: "12 Gb/s", capable_speed: "12 Gb/s", hctl: name === "a" ? "2:0:0:1" : "3:0:0:1", target: `50:00:target-${name}`, initiator: `10:00:hba-${name}` },
  });
  const settings = { mode: "recommended", path_grouping_policy: "group_by_prio", path_selector: "service-time 0", failback: "followover", no_path_retry: "fail", polling_interval_seconds: 5, minimum_healthy_paths: 2, alert_on_reduced: true, alert_on_failover: true, alert_on_path_flapping: true, alert_on_total_loss: true };
  const storage = () => {
    const paths = state === "single" ? [path("a")] : state === "failed_over" ? [path("a", false), path("b")] : [path("a"), path("b")];
    return { id: storageId, name: "MediaPool", stable_identity: "wwn:naa.600a098000abc", filesystem_uuid: "22222222-2222-4222-8222-222222222222", mountpoint: "/media", presentation_device: state === "single" ? "/dev/sdb" : "/dev/mapper/naa.600a098000abc", topology_state: state, capacity_bytes: 8_000_000_000_000, transition_capability: { mode: state === "single" ? "brief_maintenance_required" : "online_supported", message: state === "single" ? "Adding redundancy requires a brief storage interruption." : "The map remains online." }, redundancy_settings: settings, redundancy_summary: { healthy_paths: paths.filter((item) => item.active).length, active_paths: paths.filter((item) => item.active).length, failed_paths: paths.filter((item) => !item.active).length, failovers_today: state === "failed_over" ? 1 : 0, last_failover: state === "failed_over" ? "2026-08-22T14:32:08Z" : null, time_degraded_seconds: state === "failed_over" ? 45 : 0 }, paths, available_paths: state === "single" ? [{ stable_path_identity: "fc:hba-b:target-b", kernel_path: "/dev/sdc", controller_identity: "hba-b", protocol: "fc" }] : [] };
  };
  const entity = (name: "a" | "b") => ({ id: `${name === "a" ? "7" : "8"}7777777-7777-4777-8777-777777777777`, entity_type: "storage_path", stable_id: `storage-path:fc:hba-${name}:target-${name}`, display_name: name === "a" ? "/dev/sdb" : "/dev/sdc", labels: { device: name === "a" ? "sdb" : "sdc" }, topology: { storage_entity_id: storageId }, first_seen_at: "2026-08-22T13:00:00Z", last_seen_at: "2026-08-22T15:00:00Z" });
  const failoverEvent = () => ({ id: "99999999-9999-4999-8999-999999999999", event_type: "controller_failover", path_id: path("a").id, controller_id: path("a").controller.id, operation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", previous_state: "fully_redundant", resulting_state: "failed_over", details: { active_path: "fc:hba-b:target-b" }, occurred_at: "2026-08-22T14:32:08Z" });
  await page.route("**/api/v1/storage/logical", (route) => route.fulfill({ json: { items: [storage()] } }));
  await page.route(`**/api/v1/storage/logical/${storageId}/redundancy/events`, (route) => route.fulfill({ json: { items: state === "failed_over" ? [failoverEvent()] : [] } }));
  await page.route("**/api/v1/storage/redundancy/preview", (route) => {
    const value = storage();
    const plan = { schema_version: 1, operation: "redundancy.add", storage_entity_id: storageId, logical_storage_identity: value.stable_identity, hardware_snapshot_sha256: "a".repeat(64), identity_binding_sha256: "b".repeat(64), before: { path_ids: [path("a").stable_path_identity], presentation_device: "/dev/sdb", mountpoint: "/media", device_mountpoint: "/media", filesystem_uuid: value.filesystem_uuid }, after: { path_ids: [path("a").stable_path_identity, path("b").stable_path_identity], presentation_device: "/dev/mapper/naa.600a098000abc", mountpoint: "/media", filesystem_uuid: value.filesystem_uuid, topology_state: "fully_redundant" }, selected_path: { stable_path_identity: path("b").stable_path_identity, kernel_path: "/dev/sdc", controller_identity: "hba-b", protocol: "fc" }, removed_path: null, policy: "recommended", settings, transition: { mode: "brief_maintenance_required", message: "Adding redundancy requires a brief storage interruption. Your data remains unchanged." }, managed_access_services: [{ id: "smb-media", protocol: "smb", name: "Media", path: "/media" }], destructive: false, format: false, copy_data: false, preserves: ["storage_entity_id", "filesystem_uuid", "mountpoint", "shares", "telemetry_history"], plan_sha256: "c".repeat(64) };
    return route.fulfill({ json: { plan, plan_sha256: plan.plan_sha256 } });
  });
  await page.route("**/api/v1/telemetry/entities?entity_type=storage_path", (route) => route.fulfill({ json: { items: state === "single" ? [entity("a")] : [entity("a"), entity("b")] } }));
  await page.route("**/api/v1/telemetry/current?entity_type=storage_path", (route) => {
    const entities = state === "single" ? [entity("a")] : [entity("a"), entity("b")];
    const items = entities.flatMap((item, index) => [["io.read.bytes_per_second", 100_000_000 + index * 25_000_000, "bytes_per_second"], ["io.write.bytes_per_second", 60_000_000, "bytes_per_second"], ["io.read.iops", 220, "operations_per_second"], ["io.write.iops", 140, "operations_per_second"], ["io.read.latency", state === "failed_over" ? 8.5 : 2.4, "milliseconds"], ["io.write.latency", 3.1, "milliseconds"]].map(([metric_id, value, unit]) => ({ metric_id, name: metric_id, entity: item, timestamp: "2026-08-22T15:00:00Z", value, unit, source: "Linux block counters", collection_interval_seconds: 5, quality: "available", raw: true, labels: {}, capability: null, error_code: null })));
    return route.fulfill({ json: { captured_at: "2026-08-22T15:00:00Z", items, restricted_capabilities: [] } });
  });
  await page.route("**/api/v1/telemetry/history?**", (route) => {
    const request = new URL(route.request().url());
    const entityId = request.searchParams.get("entity_id")!;
    const metricId = request.searchParams.get("metric_id")!;
    const item = entityId.startsWith("7") ? entity("a") : entity("b");
    const offset = entityId.startsWith("7") ? 0 : 18;
    return route.fulfill({ json: { entity: item, metric_id: metricId, unit: metricId.includes("latency") ? "milliseconds" : metricId.includes("iops") ? "operations_per_second" : "bytes_per_second", resolution: "raw", requested_resolution: "auto", source_resolution: "raw", aggregation_method: "raw samples", raw: true, points_returned: 4, displayed_points: 4, start: "2026-08-22T13:00:00Z", end: "2026-08-22T15:00:00Z", points: [{ timestamp: "2026-08-22T13:00:00Z", value: 5 + offset, quality: "available" }, { timestamp: "2026-08-22T14:00:00Z", value: 60 - offset, quality: "available" }, { timestamp: "2026-08-22T14:32:08Z", value: (state === "failed_over" ? 95 : 70) - offset, quality: "available" }, { timestamp: "2026-08-22T15:00:00Z", value: 20 + offset, quality: "available" }] } });
  });
  return {
    fullyRedundant: () => { state = "fully_redundant"; },
    failover: () => { state = "failed_over"; },
    recover: () => { state = "fully_redundant"; },
  };
}

test.describe("production sign-in shell", () => {
  test("uses ARR layout and functional password reveal in a real browser", async ({ page }) => {
    await configuredButSignedOut(page);
    await page.goto("/");

    await expect(page.getByText("SIGN IN TO CONTINUE")).toBeVisible();
    await expect(page.getByRole("img", { name: "Hoardarr" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Remember Me" })).toBeChecked();
    const password = page.getByPlaceholder("Password");
    await password.fill("browser-secret");
    await expect(password).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Show Password" }).click();
    await expect(password).toHaveAttribute("type", "text");
  });

  test("follows the browser light and dark color scheme", async ({ browser }) => {
    const light = await browser.newPage({ colorScheme: "light" });
    await configuredButSignedOut(light);
    await light.goto("/");
    const lightBackground = await light.locator(".auth-page").evaluate((element) => getComputedStyle(element).backgroundColor);

    const dark = await browser.newPage({ colorScheme: "dark" });
    await configuredButSignedOut(dark);
    await dark.goto("/");
    const darkBackground = await dark.locator(".auth-page").evaluate((element) => getComputedStyle(element).backgroundColor);

    expect(lightBackground).not.toBe(darkBackground);
    await light.close();
    await dark.close();
  });

  test("keeps local storage controls available when fleet telemetry is offline", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.route("**/api/v1/fleet-telemetry/settings", (route) => route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({ title: "Fleet service unavailable", status: 503 }),
    }));
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
    await expect(page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first()).toBeVisible();
  });

  test("configures visible HA-3 peer awareness without claiming automatic failover", async ({ page }, testInfo) => {
    await authenticatedEmptyServer(page);
    let saved = false;
    const handleHA = async (route: Route) => {
      const request = route.request();
      if (request.method() === "PUT") {
        const body = request.postDataJSON() as { local_node_id: string; peer_node_id: string; local_ip: string; peer_ip: string };
        expect(body).toMatchObject({ local_node_id: "hoardarr-a", peer_node_id: "hoardarr-b", local_ip: "10.81.200.251", peer_ip: "10.81.200.252" });
        saved = true;
      }
      return route.fulfill({ json: saved ? {
        configured: true, maturity_level: "HA-3", mode: "controlled_single_writer",
        local: { node_id: "hoardarr-a", name: "Hoardarr-A", fqdn: "hoardarr-a.local", ip: "10.81.200.251", role: "active" },
        peer: { node_id: "hoardarr-b", name: "Hoardarr-B", fqdn: "hoardarr-b.local", ip: "10.81.200.252", role: "passive", reachable: false, state: "unavailable", last_seen_at: null },
        service_ip: "10.81.200.253", current_owner_node_id: "hoardarr-a", synchronization_state: "unavailable", failover_readiness: "unknown", storage_ownership: "not_reported", automatic_failover: false, fencing_configured: false, updated_at: "2026-08-24T15:00:00Z",
        events: [{ id: "ha-event-1", event_type: "ha_configured", cause: null, previous_owner_node_id: null, resulting_owner_node_id: "hoardarr-a", detail: {}, occurred_at: "2026-08-24T15:00:00Z" }],
      } : { configured: false, maturity_level: "HA-2", mode: null, peer: null, events: [] } });
    };
    await page.route("**/api/v1/ha", handleHA);
    await page.route("**/api/v1/ha/configuration", handleHA);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Settings" }).click();
    await page.getByRole("button", { name: "Configure two nodes" }).click();
    await page.getByLabel("IP address", { exact: true }).fill("10.81.200.251");
    await page.getByLabel("Peer IP address").fill("10.81.200.252");
    await page.getByLabel("Floating/service IP (optional)").fill("10.81.200.253");
    await page.getByRole("button", { name: "Save node settings" }).click();
    await expect(page.getByText("HA-3 · Persistent peer awareness")).toBeVisible();
    await expect(page.getByText("Automatic failover is not configured")).toBeVisible();
    await expect(page.getByText("Hoardarr-B", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("ha-peer-awareness.png"), fullPage: true });
  });

  test("navigates the ARR shell and opens Guided storage with ordinary questions", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
    const storageNav = page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first();
    await expect(storageNav).toBeVisible();
    await storageNav.click();
    await expect(page.getByRole("heading", { name: "Storage", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Add storage", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Add storage" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Guided" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByText("Hoardarr finds drives before asking how they should be used. Identity comes from hardware, not a friendly nickname.")).toBeVisible();
    await expect(page.getByText("No discovery snapshot yet")).toBeVisible();
  });

  test("continues paired first-run setup through a real managed network apply", async ({ page }) => {
    await unconfiguredServer(page);
    await page.goto("/#setup=hsetup_test-token");
    await page.getByPlaceholder("Username").fill("owner");
    await page.getByPlaceholder("Password", { exact: true }).fill("x");
    await page.getByPlaceholder("Confirm Password").fill("x");
    await page.getByRole("button", { name: "Create Account" }).click();

    await expect(page.getByRole("dialog", { name: "Set up Hoardarr" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Name this server" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Help improve Hoardarr" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: /Share hardware and product telemetry/ })).toBeChecked();
    await expect(page.getByLabel("Telemetry country or region")).toHaveValue("US");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Connect to the network" })).toBeVisible();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("button", { name: "Apply and continue" })).toBeVisible();
    await page.getByRole("button", { name: "Apply and continue" }).click();
    await expect(page.getByRole("heading", { name: "Find and identify storage" })).toBeVisible();
  });

  test("exposes real Advanced storage controls without leaking them into Guided", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Add storage", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await dialog.getByRole("checkbox", { name: /Select SSD-1TB serial SSD-1/ }).check();
    await dialog.getByRole("button", { name: "Advanced settings" }).click();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Choose a storage layout" })).toBeVisible();
    await dialog.getByText("Add to a ZFS vdev").click();
    await expect(dialog.getByLabel("Protection layout")).toBeVisible();
    await expect(dialog.getByLabel("Drives per vdev")).toBeVisible();
    await expect(dialog.getByLabel("Sector alignment")).toBeVisible();
    await expect(dialog.getByLabel("Record size")).toBeVisible();
    await expect(dialog.getByLabel("Compression")).toBeVisible();
    await dialog.getByText("Add to a Linux RAID set").click();
    await expect(dialog.getByLabel("RAID level")).toBeVisible();
    await expect(dialog.getByLabel("Chunk size")).toBeVisible();
    await dialog.getByText("Add to SnapRAID").click();
    await expect(dialog.getByLabel("Parity drives")).toBeVisible();
    await dialog.getByText("Combine multiple protected pools").click();
    await expect(dialog.getByLabel("Component pool type")).toBeVisible();
    await expect(dialog.getByLabel("Drives per component")).toBeVisible();
    await expect(dialog.getByLabel("New-file placement")).toBeVisible();
    await dialog.getByText("Use as one drive").click();
    await expect(dialog.getByLabel("Filesystem", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel("Partition table")).toBeVisible();
    await expect(dialog.getByLabel("TRIM or discard")).toBeVisible();
  });

  test("opens a snapshot-bound expansion recommendation in the real storage wizard", async ({ page }) => {
    await storageWizardServer(page);
    await page.route("**/api/v1/storage/mergerfs", (route) => route.fulfill({ json: {
      available: true,
      status: "configured",
      items: [{ id: "mergerfs:0123456789abcdef", name: "media", mountpoint: "/data/media", source: "/mnt/disk1:/mnt/disk2", branches: ["/mnt/disk1", "/mnt/disk2"], options: ["category.create=mfs", "category.search=ff"], active: true, configured: true }],
    } }));
    await page.route("**/api/v1/storage/expansion", (route) => route.fulfill({ json: {
      schema_version: 1,
      hardware_snapshot_id: "snap-storage",
      hardware_snapshot_sha256: "c".repeat(64),
      captured_at: new Date().toISOString(),
      storage_groups: [{
        id: "media", name: "Media", namespace_path: "/data/media", purpose: "media", backend_count: 1,
        raw_capacity_bytes: 4_000_000_000_000,
        capacity: { total_bytes: 4_000_000_000_000, used_bytes: 1_000_000_000_000, free_bytes: 3_000_000_000_000, quality: "available", source: "statvfs Storage Group namespace" },
        distribution: { reported_members: 1, minimum_utilization_percent: 25, maximum_utilization_percent: 25, spread_percentage_points: null, methodology: "Maximum minus minimum utilization." },
        protection: { data_backends: 1, parity_backends: 0, summary: "No parity backend is configured in this Storage Group." },
        preferred_backend_id: "backend-1",
      }],
      available_disks: [1, 2].map((number) => ({
        id: `serial:test:ssd-${number}`,
        stable_identity: `serial:SSD-${number}`,
        kernel_path: `/dev/sd${number === 1 ? "b" : "c"}`,
        vendor: "TEST",
        model: "SSD-1TB",
        capacity_bytes: 1_000_000_000_000,
        media_type: "ssd",
        health: "available",
        existing_data: { state: "none_detected", detail: "No partition or filesystem signatures were reported by the complete scan." },
        eligible: true,
        blockers: [],
        warnings: [],
      })),
      reserved_disks: [],
      detected_capabilities: { mergerfs: true, snapraid: false, zfs: false },
      candidates: [{
        id: "0123456789abcdef01234567",
        kind: "add_mergerfs_member",
        disk_ids: ["serial:test:ssd-1", "serial:test:ssd-2"],
        storage_group_id: "media",
        storage_group_name: "Media",
        title: "Add capacity to Media",
        summary: "Add two independently readable members without changing the media namespace.",
        recommended: true,
        setup_mode: "expand",
        capacity: { raw_delta_bytes: 2_000_000_000_000, estimated_usable_delta_bytes: 2_000_000_000_000, methodology: "Sum of the two blank member capacities." },
        protection_impact: "No additional parity is created by this step.",
        future_expansion: "Additional members can be added later.",
        migration_work: "No existing media files need to move.",
        restrictions: ["Review SnapRAID parity capacity separately."],
        target: { provider: "mergerfs", instance_id: "mergerfs:0123456789abcdef", mountpoint: "/data/media" },
        configuration: { topology: "mergerfs" },
      }],
      methodology: "Read-only assessment bound to the latest hardware snapshot.",
    } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    const recommendation = page.getByRole("article", { name: "Add capacity to Media" });
    await expect(recommendation.getByText("Recommended")).toBeVisible();
    await recommendation.getByRole("button", { name: "Customize this plan" }).click();

    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByRole("heading", { name: "Choose a storage layout" })).toBeVisible();
    await expect(dialog.getByText("2 selected drives")).toBeVisible();
    await expect(dialog.locator(".selected-drives article")).toHaveCount(2);
    await expect(dialog.getByText("/dev/sdb", { exact: true })).toBeVisible();
    await expect(dialog.getByText("/dev/sdc", { exact: true })).toBeVisible();
  });

  test("applies a reviewed mergerFS expansion and reconciles the stable namespace", async ({ page }) => {
    await storageWizardServer(page);
    await page.route("**/api/v1/storage/mergerfs", (route) => route.fulfill({ json: {
      available: true,
      status: "configured",
      items: [{ id: "mergerfs:0123456789abcdef", name: "media", mountpoint: "/data/media", source: "/mnt/disk1:/mnt/disk2", branches: ["/mnt/disk1", "/mnt/disk2"], options: ["category.create=mfs", "category.search=ff"], active: true, configured: true }],
    } }));
    const candidateId = "1123456789abcdef01234567";
    await page.route("**/api/v1/storage/expansion", (route) => route.fulfill({ json: {
      schema_version: 1,
      hardware_snapshot_id: "snap-storage",
      hardware_snapshot_sha256: "c".repeat(64),
      captured_at: new Date().toISOString(),
      storage_groups: [{
        id: "media", name: "Media", namespace_path: "/data/media", purpose: "media", backend_count: 1,
        raw_capacity_bytes: 1_000_000_000_000,
        capacity: { total_bytes: 1_000_000_000_000, used_bytes: 250_000_000_000, free_bytes: 750_000_000_000, quality: "available", source: "statvfs Storage Group namespace" },
        distribution: { reported_members: 1, minimum_utilization_percent: 25, maximum_utilization_percent: 25, spread_percentage_points: null, methodology: "Maximum minus minimum utilization." },
        protection: { data_backends: 1, parity_backends: 0, summary: "No parity backend is configured in this Storage Group." },
        growth_forecast: { status: "insufficient_history", reason: "Seven days are required.", metric_entity_id: null },
        preferred_backend_id: "backend-1",
      }],
      available_disks: [1, 2].map((number) => ({
        id: `serial:test:ssd-${number}`,
        stable_identity: `serial:SSD-${number}`,
        kernel_path: `/dev/sd${number === 1 ? "b" : "c"}`,
        vendor: "TEST", model: "SSD-1TB", capacity_bytes: 1_000_000_000_000,
        media_type: "ssd", health: "healthy",
        existing_data: { state: "none_detected", detail: "Complete signature scan found no storage metadata." },
        eligible: true, blockers: [], warnings: [],
      })),
      reserved_disks: [],
      detected_capabilities: { mergerfs: true, snapraid: false, zfs: false, linux_md: true },
      tool_availability: { mergerfs: true, snapraid: false, zfs: false, linux_md: true },
      candidates: [{
        id: candidateId,
        kind: "add_mergerfs_member",
        disk_ids: ["serial:test:ssd-1", "serial:test:ssd-2"],
        storage_group_id: "media",
        storage_group_name: "Media",
        title: "Add capacity to Media",
        summary: "Add two independently readable members without changing the media namespace.",
        recommended: true,
        setup_mode: "expand",
        capacity: { raw_delta_bytes: 2_000_000_000_000, estimated_usable_delta_bytes: 2_000_000_000_000, methodology: "Sum of reviewed blank member capacities." },
        protection_impact: "No parity is added; current media remains online through the same namespace.",
        future_expansion: "Additional members can be added later.",
        migration_work: "Format and mount only the two new blank disks, update mergerFS, then reconcile /data/media.",
        restrictions: ["No existing member is reformatted."],
        target: { provider: "mergerfs", instance_id: "mergerfs:0123456789abcdef", mountpoint: "/data/media" },
        configuration: { topology: "mergerfs" },
      }],
      methodology: "Read-only assessment bound to the latest hardware snapshot.",
    } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("article", { name: "Add capacity to Media" }).getByRole("button", { name: "Customize this plan" }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    for (let step = 0; step < 4; step += 1) await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Review the exact plan" })).toBeVisible();
    await expect(dialog.getByLabel("Expansion plan binding")).toContainText(candidateId);
    await expect(dialog.getByLabel("Expansion plan binding")).toContainText("/data/media");
    await expect(dialog.locator(".selected-drives article")).toHaveCount(2);
    await dialog.getByRole("button", { name: "Continue to consent" }).click();
    await dialog.getByLabel('Type “I AGREE”').fill("I AGREE");
    await dialog.getByRole("button", { name: "Apply settings" }).click();
    await expect(dialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "100", { timeout: 15_000 });
    await page.goto("/?recovery=1");
    const recoveredDialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(recoveredDialog.getByRole("button", { name: "Create access credential" })).toBeVisible();
    await recoveredDialog.getByRole("button", { name: "Close storage change" }).click();
    await expect(recoveredDialog).toBeHidden();
    await expect(page.getByText("Storage is ready at /data.")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("dialog", { name: "Add storage" })).toBeHidden();
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await expect(page.getByText("Media", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("/data/media", { exact: true }).first()).toBeVisible();
  });

  test("opens a reviewed RAIDZ2 expansion candidate with its exact geometry", async ({ page }) => {
    await storageWizardServer(page);
    const disks = [1, 2, 3, 4].map((number) => ({
      id: `serial:test:ssd-${number}`,
      stable_identity: `serial:SSD-${number}`,
      kernel_path: `/dev/sd${String.fromCharCode(97 + number)}`,
      vendor: "TEST",
      model: "SSD-1TB",
      capacity_bytes: 1_000_000_000_000,
      media_type: "ssd",
      health: "healthy",
      existing_data: { state: "none_detected", detail: "Complete scan found no signatures." },
      eligible: true,
      blockers: [],
      warnings: [],
    }));
    await page.route("**/api/v1/storage/expansion", (route) => route.fulfill({ json: {
      schema_version: 1,
      hardware_snapshot_id: "snap-storage",
      hardware_snapshot_sha256: "c".repeat(64),
      captured_at: new Date().toISOString(),
      storage_groups: [],
      available_disks: disks,
      reserved_disks: [],
      detected_capabilities: { mergerfs: false, snapraid: false, zfs: true },
      candidates: [{
        id: "abcdef0123456789abcdef01",
        kind: "new_zfs_raidz2",
        disk_ids: disks.map((disk) => disk.id),
        storage_group_id: null,
        storage_group_name: null,
        title: "Create a 4-drive protected ZFS pool",
        summary: "Use RAIDZ2 so the pool tolerates two drive failures.",
        recommended: true,
        setup_mode: "advanced",
        capacity: { raw_delta_bytes: 4_000_000_000_000, estimated_usable_delta_bytes: 2_000_000_000_000, methodology: "Smallest capacity multiplied by two data columns." },
        protection_impact: "Can tolerate two drive failures in this vdev.",
        future_expansion: "Capacity grows by adding another complete vdev.",
        migration_work: "Create after immutable review and exact approval.",
        restrictions: ["All four disks become dedicated ZFS members."],
        target: null,
        configuration: { topology: "zfs", vdev_type: "raidz2", vdev_width: 4 },
      }],
      methodology: "Read-only assessment bound to the latest hardware snapshot.",
    } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    const candidate = page.getByRole("article", { name: "Create a 4-drive protected ZFS pool" });
    await candidate.getByRole("button", { name: "Customize this plan" }).click();

    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.locator('input[name="storage-role"][value="zfs"]')).toBeChecked();
    await expect(dialog.getByLabel("Protection layout")).toHaveValue("raidz2");
    await expect(dialog.getByLabel("Drives per vdev")).toHaveValue("4");
    await expect(dialog.locator(".selected-drives article")).toHaveCount(4);
  });

  test("opens a reviewed Linux MD expansion candidate with its exact geometry", async ({ page }) => {
    await storageWizardServer(page);
    const disks = [1, 2, 3, 4].map((number) => ({
      id: `serial:test:ssd-${number}`,
      stable_identity: `serial:SSD-${number}`,
      kernel_path: `/dev/sd${String.fromCharCode(97 + number)}`,
      vendor: "TEST",
      model: "SSD-1TB",
      capacity_bytes: 1_000_000_000_000,
      media_type: "ssd",
      health: "healthy",
      existing_data: { state: "none_detected", detail: "Complete scan found no signatures." },
      eligible: true,
      blockers: [],
      warnings: [],
    }));
    await page.route("**/api/v1/storage/expansion", (route) => route.fulfill({ json: {
      schema_version: 1,
      hardware_snapshot_id: "snap-storage",
      hardware_snapshot_sha256: "c".repeat(64),
      captured_at: new Date().toISOString(),
      storage_groups: [],
      available_disks: disks,
      reserved_disks: [],
      detected_capabilities: { mergerfs: false, snapraid: false, zfs: false, linux_md: false },
      tool_availability: { mergerfs: false, snapraid: false, zfs: false, linux_md: true },
      candidates: [{
        id: "bcdef0123456789abcdef012",
        kind: "new_linux_md_raid10",
        disk_ids: disks.map((disk) => disk.id),
        storage_group_id: null,
        storage_group_name: null,
        title: "Create a 4-drive Linux RAID10 array",
        summary: "Create one Linux software RAID device from matched blank drives.",
        recommended: false,
        setup_mode: "advanced",
        capacity: { raw_delta_bytes: 4_000_000_000_000, estimated_usable_delta_bytes: 2_000_000_000_000, methodology: "Smallest member multiplied by two data columns." },
        protection_impact: "Can tolerate one member failure.",
        future_expansion: "Future changes follow mdadm reshape rules.",
        migration_work: "Create after immutable review and exact approval.",
        restrictions: ["All four disks become dedicated array members."],
        target: null,
        configuration: { topology: "raid", md_level: "raid10", member_count: 4 },
      }],
      methodology: "Read-only assessment bound to the latest hardware snapshot.",
    } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("article", { name: "Create a 4-drive Linux RAID10 array" }).getByRole("button", { name: "Customize this plan" }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.locator('input[name="storage-role"][value="raid"]')).toBeChecked();
    await expect(dialog.getByLabel("RAID level")).toHaveValue("raid10");
    await expect(dialog.locator(".selected-drives article")).toHaveCount(4);
  });

  test("shows real SMART support and drive-reported durations before a drive check", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByLabel("Actions for /dev/sdb").click();
    await page.getByRole("menuitem", { name: /Run drive checks/i }).click();

    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByRole("heading", { name: "Choose drive checks" })).toBeVisible();
    await dialog.getByRole("checkbox", { name: "SMART short self-test" }).check();
    await dialog.getByRole("checkbox", { name: "SMART extended self-test" }).check();
    await expect(dialog.getByText("Supported · drive-reported estimate 2 min")).toBeVisible();
    await expect(dialog.getByText("Supported · drive-reported estimate 381 min")).toBeVisible();
    await expect(dialog.getByText("smartctl -j -c")).toBeVisible();
  });

  test("runs a short SMART self-test from the drive action and preserves its result in Activity", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByLabel("Actions for /dev/sdb").click();
    await expect(page.getByRole("menuitem", { name: /Run Long Test/ })).toBeEnabled();
    await page.getByRole("menuitem", { name: /Run Short Test/ }).click();

    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByRole("heading", { name: "Choose drive checks" })).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "SMART short self-test" })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "SMART extended self-test" })).not.toBeChecked();
    await expect(dialog.getByText("Supported · drive-reported estimate 2 min")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Review the exact plan" })).toBeVisible();
    await expect(dialog.getByText("drive.smart.short", { exact: true })).toBeVisible();
    await expect(dialog.getByText("No destructive approval is required", { exact: true }).first()).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByLabel('Type “I AGREE”')).toHaveCount(0);
    await dialog.getByRole("button", { name: "Apply settings" }).click();
    await expect(dialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "100");

    await page.reload();
    const recoveredDialog = page.getByRole("dialog", { name: "Add storage" });
    await recoveredDialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "Activity" }).click();
    await expect(page.getByLabel("SMART self-test history")).toContainText("Short");
    await expect(page.getByLabel("SMART self-test history")).toContainText("The SMART short self-test completed without a reported error.");
  });

  test("binds the drive-reported long SMART test into a durable non-destructive plan", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByLabel("Actions for /dev/sdb").click();
    await page.getByRole("menuitem", { name: /Run Long Test/ }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByRole("checkbox", { name: "SMART extended self-test" })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: "SMART short self-test" })).not.toBeChecked();
    await expect(dialog.getByText("Supported · drive-reported estimate 381 min")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("drive.smart.extended", { exact: true })).toBeVisible();
    await expect(dialog.getByText("No destructive approval is required", { exact: true }).first()).toBeVisible();
  });

  test("replaces a degraded Linux MD member with elevated existing-data safeguards", async ({ page }) => {
    await storageWizardServer(page);
    await page.route("**/api/v1/storage/inventory", (route) => route.fulfill({ json: {
      captured_from: "live_host",
      topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] },
      active_operations: [],
      pools: { status: "configured", items: [{
        id: "md:media", name: "media", type: "Linux MD", status: "degraded", mountpoint: "/data/media", members: 3, degraded: true,
        configuration: { quality: "available", level: "raid5", member_paths: ["/dev/sdb1", "/dev/sdc1"], config_sha256: "a".repeat(64) },
      }] },
      shares: { status: "configured", items: [{ id: "smb:media", name: "Media", protocol: "SMB", path: "/data/media" }] },
      controllers: { status: "Not reported", items: [], unavailable: [] }, enclosures: { status: "Not reported", items: [], unavailable: [] },
    } }));
    const replacementPlan = {
      schema_version: 1, kind: "array_replacement", provider: "linux_md", target_id: "md:media", target_name: "media",
      target_identity: "md-uuid:11111111:22222222:33333333:44444444", configuration_sha256: "a".repeat(64), level: "raid5", member_count: 3, degraded: true,
      old_member_path: null, minimum_capacity_bytes: 1_000_000_000_000,
      device: { id: "serial:test:ssd-1", stable_identity: true, vendor: "TEST", model: "SSD-1TB", serial: "SSD-1", wwn: "5000c50000000000", eui64: null, nguid: null, capacity_bytes: 1_000_000_000_000, logical_sector_bytes: 512, physical_sector_bytes: 4096 },
      device_binding_sha256: "b".repeat(64), hardware_snapshot_sha256: "c".repeat(64),
      existing_data: { detected: true, partition_count: 1, signature_types: ["ext4"], scan_status: "complete" }, destructive: true,
    };
    await page.route("**/api/v1/storage/arrays/replacements/preview", (route) => route.fulfill({ json: { plan: replacementPlan, plan_sha256: "d".repeat(64) } }));
    await page.route("**/api/v1/storage/arrays/replacements", (route) => route.fulfill({ status: 202, json: { operation: { id: "op-replacement", kind: "storage.array.replace", status: "queued", resource: { type: "linux_md", id: "md:media" } }, replayed: false } }));
    await page.route("**/api/v1/operations/op-replacement", (route) => route.fulfill({ json: { id: "op-replacement", kind: "storage.array.replace", status: "succeeded", resource: { type: "linux_md", id: "md:media" }, result: { target_identity: replacementPlan.target_identity, replacement_device_id: replacementPlan.device.id } } }));
    await page.route("**/api/v1/operations/op-replacement/progress", (route) => route.fulfill({ json: { operation_id: "op-replacement", state: "succeeded", phase: "Linux MD recovery verified", completed_steps: 4, total_steps: 4, percent: 100, completed_actions: ["identity", "add", "rebuild", "verify"], notices: [], current_action: null, estimate: null, updated_at: Date.now() / 1000 } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    const panel = page.locator("section.card").filter({ has: page.getByRole("heading", { name: "Replace a ZFS or Linux MD disk" }) });
    await panel.getByLabel("Array replacement drive").selectOption("serial:test:ssd-1");
    await panel.getByRole("button", { name: "Review array replacement" }).click();
    await expect(panel.getByText("Existing data detected on the replacement")).toBeVisible();
    await expect(panel.getByText(/1 partition and ext4 signatures/)).toBeVisible();
    await expect(panel.getByText("md-uuid:11111111:22222222:33333333:44444444")).toBeVisible();
    await panel.getByLabel("Array replacement confirmation").fill("I AGREE");
    await panel.getByRole("button", { name: "Start durable array replacement" }).click();
    await expect(panel.getByRole("progressbar", { name: "Array replacement progress" })).toHaveAttribute("aria-valuenow", "100");
    await expect(panel.getByText("Array replacement completed")).toBeVisible();
  });

  test("onboards a data-bearing disk without formatting and reconciles its stable namespace", async ({ page }) => {
    await storageWizardServer(page, { firstDriveContainsData: true });
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();

    await expect(page.getByRole("row", { name: /\/dev\/sdb Stable identity/ })).toContainText("1 partition; 1 recognized signature");
    await expect(page.getByRole("row", { name: /\/dev\/sdb Stable identity/ })).toContainText("signatures: ext4");
    await page.getByLabel("Actions for /dev/sdb").click();
    await page.getByRole("menuitem", { name: /Import existing data/i }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByRole("heading", { name: "Check drive condition" })).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Tell us how the drives will be used" })).toBeVisible();
    await expect(dialog.locator('input[name="preserve"][value="yes"]')).toBeChecked();

    for (let step = 0; step < 5; step += 1) await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Review the exact plan" })).toBeVisible();
    await expect(dialog.getByText("Preserve ext4", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Preserve/import", { exact: true })).toBeVisible();
    await expect(dialog.getByText("disk.partition_table.create", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText("filesystem.create", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText("storage.layout.ensure", { exact: true })).toBeVisible();
    await expect(dialog.getByText("No destructive approval is required", { exact: true }).first()).toBeVisible();

    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByLabel('Type “I AGREE”')).toHaveCount(0);
    await dialog.getByRole("button", { name: "Apply settings" }).click();
    await expect(dialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "100");
    await page.reload();
    const recoveredDialog = page.getByRole("dialog", { name: "Add storage" });
    await recoveredDialog.getByRole("button", { name: "Create access credential" }).click();
    await recoveredDialog.getByRole("button", { name: "Show generated password" }).click();
    await recoveredDialog.getByRole("button", { name: "I saved this password" }).click();
    await recoveredDialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByText("Storage is ready at /data.")).toBeVisible();
    await expect(page.getByText("/data/media", { exact: true }).first()).toBeVisible();
  });

  test("reviews and applies only a hardware-reported SCSI sanitization method", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByLabel("Actions for /dev/sdb").click();
    await page.getByRole("menuitem", { name: /Erase or decommission/i }).click();

    const dialog = page.getByRole("dialog", { name: "Erase or convert a drive" });
    await expect(dialog.getByRole("option", { name: /NVMe block erase/ })).toHaveAttribute(
      "disabled",
      "",
    );
    await dialog.getByLabel("Method").selectOption("scsi_sanitize");
    await expect(dialog.getByText(/Support was reported by sg_opcodes/)).toBeVisible();
    await dialog.getByRole("button", { name: "Review plan" }).click();
    await expect(dialog.getByText("user_data_media")).toBeVisible();
    await dialog.getByLabel('Type “I AGREE”').fill("I AGREE");
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog.getByText("Sanitization report")).toBeVisible();
    await expect(dialog.getByText(/command_completed/)).toBeVisible();
  });

  test("reserves and releases an expansion disk through the real Storage UI", async ({ page }) => {
    await storageWizardServer(page);
    const disk = {
      id: "serial:test:ssd-1",
      stable_identity: "serial:SSD-1",
      kernel_path: "/dev/sdb",
      vendor: "TEST",
      model: "SSD-1TB",
      capacity_bytes: 1_000_000_000_000,
      media_type: "ssd",
      health: "available",
      existing_data: { state: "none_detected", detail: "No signatures were found." },
      eligible: true,
      blockers: [],
      warnings: [],
    };
    let reserved = false;
    const assessment = () => ({
      schema_version: 1,
      hardware_snapshot_id: "snap-storage",
      hardware_snapshot_sha256: "c".repeat(64),
      captured_at: new Date().toISOString(),
      storage_groups: [],
      available_disks: reserved ? [] : [disk],
      reserved_disks: reserved ? [disk] : [],
      detected_capabilities: { mergerfs: false, snapraid: false, zfs: false },
      candidates: reserved ? [] : [{
        id: "reserve-candidate",
        kind: "new_storage_group",
        disk_ids: [disk.id],
        storage_group_id: null,
        storage_group_name: null,
        title: "Create a separate storage location",
        summary: "Keep this disk independent.",
        recommended: true,
        setup_mode: "configure",
        capacity: { raw_delta_bytes: disk.capacity_bytes, estimated_usable_delta_bytes: disk.capacity_bytes, methodology: "Raw capacity before filesystem overhead." },
        protection_impact: "A single disk has no drive-failure protection.",
        future_expansion: "It can become a combined member later.",
        migration_work: "No disk changes occur until approval.",
        restrictions: [],
        target: null,
        configuration: { topology: "individual" },
      }],
      methodology: "Read-only assessment; no changes were made.",
    });
    await page.route("**/api/v1/storage/expansion", (route) => route.fulfill({ json: assessment() }));
    await page.route("**/api/v1/storage/disks/*/reservation", (route) => {
      const body = route.request().postDataJSON() as { action: "reserve" | "release" };
      reserved = body.action === "reserve";
      return route.fulfill({ json: { item: { ...disk, lifecycle_state: reserved ? "reserved" : "discovered", last_seen_at: new Date().toISOString() } } });
    });

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Reserve for later" }).click();
    await expect(page.getByRole("heading", { name: "Reserved for later" })).toBeVisible();
    await page.getByRole("button", { name: "Release disk" }).click();
    await expect(page.getByRole("button", { name: "Reserve for later" })).toBeVisible();
  });

  test("creates and edits a future topology through the real Storage UI", async ({ page }, testInfo) => {
    await storageWizardServer(page);
    const template = {
      id: "generic-dual-path-shelf",
      name: "Dual-path 24-bay shelf",
      description: "Two planned controller paths to one generic twenty-four-bay disk shelf.",
      controller_count: 2,
      enclosures: [{ id: "shelf-1", label: "Disk shelf 1", bay_count: 24 }],
    };
    let plan: Record<string, any> | null = null;
    await page.route("**/api/v1/hardware/topology/plan-templates", (route) => route.fulfill({ json: { items: [template] } }));
    await page.route("**/api/v1/hardware/topology/plans", async (route) => {
      if (route.request().method() === "GET") return route.fulfill({ json: { items: plan ? [plan] : [] } });
      plan = {
        id: "plan-browser",
        name: "Future media shelf",
        template_id: template.id,
        revision: 0,
        plan: {
          schema_version: 1,
          chassis: { id: "host", label: "Hoardarr host" },
          controllers: [
            { id: "controller-1", label: "Controller A", state: "planned" },
            { id: "controller-2", label: "Controller B", state: "planned" },
          ],
          enclosures: [{ ...template.enclosures[0], controller_ids: ["controller-1", "controller-2"] }],
          changes: [],
          notes: "",
        },
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      return route.fulfill({ status: 201, json: { plan } });
    });
    await page.route("**/api/v1/hardware/topology/plans/plan-browser", async (route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as { revision: number; name: string; plan: Record<string, unknown> };
        plan = { ...plan!, name: body.name, revision: body.revision + 1, plan: body.plan, updated_at: new Date().toISOString() };
        return route.fulfill({ json: { plan } });
      }
      return route.fulfill({ json: { removed: true } });
    });

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByText("Plan future chassis and drive changes", { exact: true }).click();
    await expect(page.getByText("No future layout has been planned")).toBeVisible();
    await page.getByLabel("Plan name").fill("Future media shelf");
    await page.getByRole("button", { name: "Create planning layout" }).click();
    await expect(page.getByLabel("Future media shelf planned topology")).toBeVisible();
    await expect(page.getByText("Controller A")).toBeVisible();
    await expect(page.getByText("Controller B")).toBeVisible();
    await page.getByLabel("Bay").fill("4");
    await page.getByLabel("Capacity (TB, optional)").fill("20");
    await page.getByRole("button", { name: "Add to plan" }).click();
    await expect(page.getByText("20 TB planned", { exact: true })).toBeVisible();
    await testInfo.attach("planned-topology", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  });

  test("renders the sanitized LSI and NetApp physical topology with honest bay confidence", async ({ page }, testInfo) => {
    await storageWizardServer(page);
    const node = (id: string, kind: string, label: string, extra: Record<string, unknown> = {}) => ({ id, kind, label, status: "healthy", ...extra });
    const links: Array<Record<string, unknown>> = [];
    const link = (source: string, target: string, negotiated = 6, capable = 12, protocol = "SAS") => links.push({ id: `${source}->${target}`, source, target, protocol, negotiated_speed_gbps: negotiated, capable_speed_gbps: capable });
    const nodes = [
      node("controller:2308", "controller", "LSI SAS2308", { address: "0000:18:00.0", driver: "mpt2sas", protocol: "SAS", negotiated_speed_gbps: 6, capable_speed_gbps: 6 }),
      node("host:6", "sas_host", "SAS host 6", { address: "host6", protocol: "SAS" }),
      node("port:6", "port", "HBA port 6:0", { address: "port-6:0", protocol: "SAS" }),
      node("phy:6", "phy", "PHY 3", { address: "phy-6:3", protocol: "SAS", sas_address: "0x500605b000000003", phy_identifier: "3", minimum_speed_gbps: 1.5, negotiated_speed_gbps: 3, capable_speed_gbps: 6, invalid_dwords: 14, disparity_errors: 3, loss_of_sync: 1, reset_problems: 1 }),
      node("expander:424", "expander", "DS424IOM6 expander", { address: "500a098000000424", protocol: "SAS", sas_address: "500a098000000424", smp_quality: "available", smp_source: "smp_discover --summary --dsn", smp_phy_count: 24, smp_attached_phy_count: 2 }),
      node("enclosure:424", "enclosure", "NETAPP DS424IOM6", { address: "500a098000000424", protocol: "SAS" }),
      node("path:sata", "path", "SATA path through IOM6", { protocol: "SATA", target_port_identifier: "5000c50000000001", target_port_identifier_type: "naa" }),
      node("drive:sata", "drive", "SEAGATE archive disk", { serial: "SAN-SATA-0001", stable_identity: "wwn:5000c50000000001", path: "/dev/sdb", slot: "03", mapping_source: "SES slot SAS address ↔ device SAS address", mapping_confidence: "high", mapping_last_confirmed_at: "2026-08-23T12:00:00Z", controller_id: "controller:2308", enclosure_id: "enclosure:424", protocol: "SATA", capacity_bytes: 8_000_000_000_000, health_status: "healthy", negotiated_speed_gbps: 3, capable_speed_gbps: 6, temperature_c: 34, identity_evidence_quality: "available", identity_evidence_source: "sysfs vpd_pg83" }),
      node("controller:3008", "controller", "LSI SAS3008", { address: "0000:81:00.0", driver: "mpt3sas", protocol: "SAS", negotiated_speed_gbps: 6, capable_speed_gbps: 12 }),
      node("host:12", "sas_host", "SAS host 12", { address: "host12", protocol: "SAS" }),
      node("port:12", "port", "HBA port 12:0", { address: "port-12:0", protocol: "SAS" }),
      node("phy:12", "phy", "PHY 1", { address: "phy-12:1", protocol: "SAS", sas_address: "0x500605b000000012", phy_identifier: "1", negotiated_speed_gbps: 6, capable_speed_gbps: 12, invalid_dwords: 0, disparity_errors: 0, loss_of_sync: 0, reset_problems: 0 }),
      node("expander:224", "expander", "DS224IOM6 expander", { address: "500a098000000224", protocol: "SAS", sas_address: "500a098000000224", smp_quality: "temporarily_unavailable", smp_source: "smp_discover" }),
      node("enclosure:224", "enclosure", "NETAPP DS224IOM6", { address: "500a098000000224", protocol: "SAS" }),
      node("path:sas", "path", "SAS path with inferred slot", { protocol: "SAS", target_port_identifier: "5000c50000000002", target_port_identifier_type: "naa" }),
      node("drive:sas", "drive", "SEAGATE media disk", { serial: "SAN-SAS-0002", stable_identity: "wwn:5000c50000000002", path: "/dev/sdc", slot: "04", mapping_source: "stable H:C:T:L topology", mapping_confidence: "medium", mapping_last_confirmed_at: "2026-08-23T12:00:00Z", controller_id: "controller:3008", enclosure_id: "enclosure:224", protocol: "SAS", capacity_bytes: 4_000_000_000_000, health_status: "healthy", negotiated_speed_gbps: 6, capable_speed_gbps: 12, temperature_c: null, identity_evidence_quality: "available", identity_evidence_source: "SCSI VPD page 83" }),
      node("path:unknown", "path", "SAS path without trusted slot", { protocol: "SAS" }),
      node("drive:unknown", "drive", "SAS disk with unknown bay", { serial: "SAN-SAS-0004", stable_identity: "wwn:5000c50000000004", path: "/dev/sdd", slot: null, mapping_source: null, mapping_confidence: "unknown", mapping_last_confirmed_at: null, controller_id: "controller:3008", enclosure_id: "enclosure:224", protocol: "SAS", capacity_bytes: 2_000_000_000_000, health_status: "unknown", negotiated_speed_gbps: 6, capable_speed_gbps: 12, temperature_c: null, identity_evidence_quality: "available", identity_evidence_source: "SCSI VPD page 83" }),
      node("pool:media", "pool", "MediaPool", { path: "/data/media", protocol: "Logical", pool_type: "mergerFS" }),
      node("filesystem:media", "filesystem", "Media filesystem", { path: "/data/media", protocol: "Logical", filesystem_type: "ext4" }),
      node("share:media", "share", "Media", { path: "/data/media", protocol: "Logical" }),
    ];
    link("controller:2308", "host:6", 6, 6); link("host:6", "port:6", 6, 6); link("port:6", "phy:6", 3, 6); link("phy:6", "expander:424", 3, 6); link("expander:424", "enclosure:424", 6, 6); link("enclosure:424", "path:sata", 3, 6, "SATA"); link("path:sata", "drive:sata", 3, 6, "SATA");
    link("controller:3008", "host:12", 6, 12); link("host:12", "port:12", 6, 12); link("port:12", "phy:12", 6, 12); link("phy:12", "expander:224", 6, 12); link("expander:224", "enclosure:224", 6, 12); link("enclosure:224", "path:sas", 6, 12); link("path:sas", "drive:sas", 6, 12); link("enclosure:224", "path:unknown", 6, 12); link("path:unknown", "drive:unknown", 6, 12); link("drive:sas", "pool:media", 6, 12, "Logical"); link("pool:media", "filesystem:media", 6, 12, "Logical"); link("filesystem:media", "share:media", 6, 12, "Logical");
    await page.route("**/api/v1/storage/inventory", (route) => route.fulfill({ json: {
      captured_from: "sanitized_lsi_netapp_fixture",
      topology: {
        status: "available", nodes, links, direct_attached_drive_ids: [],
        enclosures: [
          { id: "enclosure:424", label: "NETAPP DS424IOM6", vendor: "NETAPP", model: "DS424IOM6", address: "500a098000000424", status: "healthy", protocols: ["SAS", "SATA"], controller_ids: ["controller:2308"], bays: [{ slot: "03", drive_id: "drive:sata", status: "OK", locate: false, fault: false, mapping_source: "SES slot SAS address ↔ device SAS address", mapping_confidence: "high", mapping_last_confirmed_at: "2026-08-23T12:00:00Z" }] },
          { id: "enclosure:224", label: "NETAPP DS224IOM6", vendor: "NETAPP", model: "DS224IOM6", address: "500a098000000224", status: "healthy", protocols: ["SAS"], controller_ids: ["controller:3008"], bays: [{ slot: "04", drive_id: "drive:sas", status: "OK", locate: false, fault: false, mapping_source: "stable H:C:T:L topology", mapping_confidence: "medium", mapping_last_confirmed_at: "2026-08-23T12:00:00Z" }, { slot: null, drive_id: "drive:unknown", status: "OK", locate: null, fault: null, mapping_source: null, mapping_confidence: "unknown", mapping_last_confirmed_at: null }] },
        ],
      },
      active_operations: [], pools: { status: "configured", items: [] }, shares: { status: "configured", items: [] }, controllers: { status: "available", items: [], unavailable: [] }, enclosures: { status: "available", items: [], unavailable: [] },
    } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await expect(page.getByText("LSI SAS2308", { exact: true })).toBeVisible();
    await expect(page.getByText("LSI SAS3008", { exact: true })).toBeVisible();
    await expect(page.getByText("NETAPP DS424IOM6", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Bay 03 · Confirmed")).toBeVisible();
    await expect(page.getByText("Bay 04 · Inferred")).toBeVisible();
    await expect(page.getByText("Bay — · Not reported")).toBeVisible();
    const slowLink = page.locator(".inline-notice.warning:visible").filter({ hasText: "This link is operating at 3 Gb/s" }).first();
    await slowLink.scrollIntoViewIfNeeded();
    await expect(slowLink).toBeVisible();
    const phyCounters = page.locator("dt:visible").filter({ hasText: "Invalid DWORDs" }).first();
    await phyCounters.scrollIntoViewIfNeeded();
    await expect(phyCounters).toBeVisible();
    await expect(page.getByText("14", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("MediaPool", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Media filesystem", { exact: true }).first()).toBeVisible();
    await testInfo.attach("sanitized-lsi-netapp-live-topology", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  });

  test("guides a Plex user from four drives to protected media and download folders", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Add storage", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    for (const serial of ["SSD-1", "SSD-2", "SSD-3", "SSD-4"]) {
      await dialog.getByRole("checkbox", { name: new RegExp(`Select SSD-1TB serial ${serial}`) }).check();
    }
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Check drive condition" })).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("What will you store?")).toBeVisible();
    await expect(dialog.getByText("Media libraries", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Yes, one large location — Recommended")).toBeVisible();
    await expect(dialog.getByText("Protect against one drive failure — Recommended")).toBeVisible();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("Recommended for your setup")).toBeVisible();
    await expect(dialog.getByText("Flexible protected media storage").first()).toBeVisible();
    await expect(dialog.getByText(/Estimated usable/)).toBeVisible();
    await dialog.getByRole("button", { name: "Use recommended setup" }).click();
    await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByText("Which media server do you use?")).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: /Plex/ })).toBeChecked();
    await expect(dialog.locator(".library-name strong", { hasText: "Movies" })).toBeVisible();
    await expect(dialog.locator(".library-name strong", { hasText: "TV" })).toBeVisible();
    await expect(dialog.getByText("How do you download?")).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: /Torrents/ })).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: /Usenet/ })).toBeChecked();
  });

  test("recovers a durable apply after refresh and reveals a generated password only until confirmation", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Add storage", exact: true }).click();
    let dialog = page.getByRole("dialog", { name: "Add storage" });
    await dialog.getByRole("checkbox", { name: /Select SSD-1TB serial SSD-1/ }).check();
    for (let step = 0; step < 7; step += 1) await dialog.getByRole("button", { name: "Continue" }).click();
    await expect(dialog.getByRole("heading", { name: "Review the exact plan" })).toBeVisible();
    await dialog.getByRole("button", { name: "Continue to consent" }).click();
    await dialog.getByLabel('Type “I AGREE”').fill("I AGREE");
    await dialog.getByRole("button", { name: "Apply settings" }).click();
    await expect(dialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "100");

    await page.reload();
    dialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(dialog.getByText("100%", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Create access credential" }).click();
    const password = dialog.getByLabel("Generated media account password");
    await expect(password).toHaveAttribute("type", "password");
    await expect(dialog.getByRole("button", { name: "Close", exact: true })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Show generated password" }).click();
    await expect(password).toHaveAttribute("type", "text");
    await expect(password).toHaveValue("one-time-storage-password");
    await dialog.getByRole("button", { name: "I saved this password" }).click();
    await expect(password).toHaveCount(0);
    await expect(dialog.getByText("Password saved and removed")).toBeVisible();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Storage", level: 1 })).toBeVisible();
  });

  test("resumes a storage build from its durable needs-attention checkpoint", async ({ page }) => {
    await storageWizardServer(page, { resumeAfterFailure: true, legacyFailedStatus: true });
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Add storage", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Add storage" });
    await dialog.getByRole("checkbox", { name: /Select SSD-1TB serial SSD-1/ }).check();
    for (let step = 0; step < 7; step += 1) await dialog.getByRole("button", { name: "Continue" }).click();
    await dialog.getByRole("button", { name: "Continue to consent" }).click();
    await dialog.getByLabel('Type “I AGREE”').fill("I AGREE");
    await dialog.getByRole("button", { name: "Apply settings" }).click();

    await expect(dialog.getByText("A required storage tool is unavailable: setfattr.").first()).toBeVisible();
    await page.goto("/?recovery=1");
    const recoveredDialog = page.getByRole("dialog", { name: "Add storage" });
    await expect(recoveredDialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "66");
    await recoveredDialog.getByText("Operation details").click();
    await expect(recoveredDialog.getByText("setfattr was unavailable")).toBeVisible();
    await expect(recoveredDialog.getByRole("button", { name: "Resume from safe checkpoint" }).first()).toBeVisible();
    await recoveredDialog.getByRole("button", { name: "Resume from safe checkpoint" }).first().click();
    await expect(recoveredDialog.getByRole("progressbar", { name: "Storage build progress" })).toHaveAttribute("aria-valuenow", "100", { timeout: 15_000 });
    await expect(recoveredDialog.getByText("Storage build completed", { exact: true }).first()).toBeVisible();
    await expect(recoveredDialog.getByText("Storage execution resumed from its durable checkpoint.")).toBeVisible();
  });

  test("renders live storage analytics and explains the metric source", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Analytics" }).click();
    await expect(page.getByRole("heading", { name: "Storage Analytics" })).toBeVisible();
    await expect(page.getByText("1 MiB/s")).toBeVisible();
    await page.getByText("About this metric").first().click();
    await expect(page.getByText("Linux block counters", { exact: true }).first()).toBeVisible();
    await page.getByLabel("Graph").selectOption("bars");
    await expect(page.locator(".graph-bars rect")).toHaveCount(1);
  });

  test("creates and verifies a real remote-backup target workflow", async ({ page }) => {
    await authenticatedEmptyServer(page);
    let savedTarget: Record<string, unknown> | null = null;
    await page.route("**/api/v1/backups/targets", async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ json: { items: savedTarget ? [savedTarget] : [] } });
      }
      const input = route.request().postDataJSON() as Record<string, unknown>;
      savedTarget = {
        id: "target-browser",
        name: input.name,
        provider: input.provider,
        endpoint_url: input.endpoint_url,
        region: input.region,
        bucket: input.bucket,
        prefix: input.prefix,
        force_path_style: true,
        verify_tls: true,
        allow_private_network: false,
        allow_insecure_http: false,
        bandwidth_limit_mib: null,
        schedule: { enabled: false },
        credential_fingerprint: "redacted-fingerprint",
        status: "untested",
        last_tested_at: null,
        last_success_at: null,
        error: null,
        enabled: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      return route.fulfill({ status: 201, json: savedTarget });
    });
    await page.route("**/api/v1/backups/targets/target-browser/test", (route) => {
      savedTarget = { ...savedTarget, status: "available", last_tested_at: new Date().toISOString() };
      return route.fulfill({ status: 202, json: { operation: { id: "backup-test-browser", kind: "backup.target.test", status: "succeeded" }, replayed: false } });
    });

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Settings" }).click();
    await expect(page.getByRole("heading", { name: "Remote backups" })).toBeVisible();
    await page.getByRole("button", { name: "Add backup target" }).click();
    const backups = page.locator("section").filter({ has: page.getByRole("heading", { name: "Remote backups" }) });
    await backups.getByLabel("Name", { exact: true }).fill("Home MinIO");
    await backups.getByLabel("Endpoint", { exact: true }).fill("https://minio.example:9000");
    await backups.getByLabel("Bucket").fill("hoardarr-backups");
    await backups.getByLabel("Access key").fill("access-key");
    await backups.getByLabel("Secret key").fill("secret-value");
    await backups.getByRole("button", { name: "Save target" }).click();
    await expect(backups.getByText("Home MinIO")).toBeVisible();
    await expect(backups.getByRole("button", { name: "Back up now" })).toBeDisabled();
    await backups.getByRole("button", { name: "Test connection" }).click();
    await expect(backups.getByText(/Status: available/)).toBeVisible();
    await expect(backups.getByRole("button", { name: "Back up now" })).toBeEnabled();
    await expect(backups.locator('input[value="secret-value"]')).toHaveCount(0);
  });

  test("shows durable ARR write activity used by storage lifecycle coordination", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.route("**/api/v1/integrations", (route) => route.fulfill({ json: { items: [{
      id: "11111111-1111-4111-8111-111111111111",
      name: "Sonarr",
      expected_product: "sonarr",
      discovered_product: "sonarr",
      product_version: "4.0.0",
      base_url: "http://sonarr:8989",
      status: "connected",
      capabilities: ["activity"],
      state: {
        active_writes: 2,
        activity_observed_at: "2026-08-23T16:00:00Z",
        activity: { quality: "available", active_writes: 2, downloading: 1, importing: 1, pending: 3, stalled: 0 },
      },
      last_checked_at: "2026-08-23T16:00:00Z",
    }] } }));
    await page.goto("/");
    await page.getByRole("button", { name: "Applications" }).click();
    await expect(page.getByRole("heading", { name: "Applications", exact: true })).toBeVisible();
    await expect(page.getByText("Storage active")).toBeVisible();
    await expect(page.getByText(/1 downloading · 1 importing · 3 pending/)).toBeVisible();
  });

  test("shows read-only Plex library observability without inventing capacity", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.route("**/api/v1/integrations", (route) => route.fulfill({ json: { items: [{
      id: "33333333-3333-4333-8333-333333333333",
      name: "Plex",
      expected_product: "plex",
      discovered_product: "plex",
      product_version: "1.42.0",
      base_url: "http://plex:32400",
      status: "connected",
      capabilities: ["media_libraries"],
      state: { libraries: [{ id: "movies", name: "Movies", media_type: "movie", paths: ["/data/media/Movies"], item_count: 4020, capacity_bytes: null, quality: "available" }] },
      last_checked_at: "2026-08-23T16:00:00Z",
    }] } }));
    await page.goto("/");
    await page.getByRole("button", { name: "Applications" }).click();
    await expect(page.getByText("4,020 items")).toBeVisible();
    await expect(page.getByText(/Storage Group not reported/)).toBeVisible();
    await expect(page.getByText("/data/media/Movies")).toBeVisible();
    await expect(page.getByText(/does not modify media libraries/)).toBeVisible();
  });

  test("reviews a real download-tier plan and retains a torrent source for seeding", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.route("**/api/v1/storage/groups", (route) => route.fulfill({ json: { items: [{
      id: "group-media",
      name: "Media",
      namespace_path: "/data/media",
      purpose: "media",
      state: "active",
      policy: {},
      backends: [
        { id: "landing", stable_identity: "wwn:fast", physical_disk_id: "disk-fast", storage_entity_id: null, namespace_path: "/data/downloads", role: "landing", lifecycle_state: "preferred_write" },
        { id: "media", stable_identity: "wwn:media", physical_disk_id: "disk-media", storage_entity_id: null, namespace_path: "/data/media", role: "data", lifecycle_state: "active" },
      ],
      events: [],
    }] } }));
    const plan = {
      workload: "torrent",
      source: "/data/downloads/completed/example.mkv",
      destination: "/data/media/Movies/example.mkv",
      source_identity: "dev:11",
      destination_identity: "dev:22",
      same_filesystem: false,
      method: "copy",
      retain_until: "seeding_complete",
      cleanup: true,
      required_bytes: 1_073_741_824,
      completed_steps: ["download_complete"],
      sha256: "a".repeat(64),
    };
    await page.route("**/api/v1/storage/transfers/preview", (route) => route.fulfill({ json: { plan, plan_sha256: "b".repeat(64) } }));
    await page.route("**/api/v1/storage/transfers/summary", (route) => route.fulfill({ json: {
      queue: {
        queued_count: 0, queued_bytes: 0, running_count: 0, running_planned_bytes: 0,
        retained_for_seeding_count: 0, retained_for_seeding_bytes: 0, failed_count: 0,
        observed_bytes_per_second: null, rate_sample_count: 0, estimated_queued_seconds: null,
        estimate_methodology: "Not reported until at least three measured copy or move transfers complete.",
      },
      tiers: [],
    } }));
    await page.route("**/api/v1/storage/transfers", (route) => route.fulfill({ json: { operation: { id: "transfer-1", kind: "storage.transfer", status: "succeeded", result: { state: "retained" } } } }));
    await page.route("**/api/v1/storage/transfers/transfer-1/cleanup", (route) => route.fulfill({ json: { operation: { id: "cleanup-1", kind: "storage.transfer.cleanup", status: "queued" } } }));

    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await expect(page.getByRole("heading", { name: "Download & landing tier" })).toBeVisible();
    await page.getByRole("button", { name: "Review transfer" }).click();
    await expect(page.getByText(/different filesystems; a hardlink is not possible/)).toBeVisible();
    await expect(page.getByText("1.07 GB")).toBeVisible();
    await page.getByRole("button", { name: "Start durable transfer" }).click();
    await expect(page.getByText("Imported and retained for seeding")).toBeVisible();
    await page.getByRole("button", { name: "Seeding complete — clean up source" }).click();
    await expect(page.getByText("Post-seeding cleanup")).toBeVisible();
  });

  test("productizes controller redundancy from single path through failover and recovery", async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const controls = await controllerRedundancyServer(page);
    const shot = async (name: string) => page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
    await page.goto("/");
    await expect(page.getByText("Storage redundancy", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /MediaPool/ }).click();
    await expect(page.getByText("1 / 1 paths healthy")).toBeVisible();
    await shot("controller-redundancy-single-path");

    await page.getByRole("button", { name: "Add redundant path" }).last().click();
    const dialog = page.getByRole("dialog", { name: "Add storage redundancy" });
    await expect(dialog.getByText("hba-b")).toBeVisible();
    await shot("controller-redundancy-second-path-detected");
    await dialog.getByRole("button", { name: "Review change" }).click();
    await expect(dialog.getByText("Will not change")).toBeVisible();
    await expect(dialog.getByText(/brief storage interruption/i)).toBeVisible();
    await shot("controller-redundancy-add-review");
    await dialog.getByRole("button", { name: "Close controller settings" }).click();

    controls.fullyRedundant();
    await expect(page.getByText("2 / 2 paths healthy")).toBeVisible({ timeout: 8_000 });
    await shot("controller-redundancy-fully-redundant");
    await page.getByRole("button", { name: "Controllers & paths" }).click();
    await expect(page.getByText("Controller B")).toBeVisible();
    await shot("controller-redundancy-path-detail");

    await page.getByRole("button", { name: "Performance" }).click();
    await expect(page.getByRole("img", { name: "Read throughput by controller path" })).toBeVisible();
    await shot("controller-redundancy-graphs");
    await page.getByRole("button", { name: "Advanced settings" }).click();
    await expect(page.getByText("Exact resulting settings")).toBeVisible();
    await shot("controller-redundancy-settings");

    controls.failover();
    await expect(page.getByText("Storage has failed over")).toBeVisible({ timeout: 8_000 });
    await shot("controller-redundancy-failed-over");
    await page.getByRole("button", { name: "Performance" }).click();
    await expect(page.locator(".failover-marker")).toHaveCount(6, { timeout: 8_000 });
    await shot("controller-redundancy-failover-graphs");
    await page.getByRole("button", { name: "Events" }).click();
    await expect(page.getByText("controller failover")).toBeVisible({ timeout: 8_000 });
    await shot("controller-redundancy-events");

    controls.recover();
    await expect(page.getByText("2 / 2 paths healthy")).toBeVisible({ timeout: 8_000 });
    await shot("controller-redundancy-recovered");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Controllers & paths" }).click();
    await expect(page.getByText("Controller A")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await shot("controller-redundancy-mobile");
    await page.emulateMedia({ colorScheme: "dark" });
    await shot("controller-redundancy-mobile-dark");
  });

  test("repeated analytics navigation releases polling and stabilizes browser heap", async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await authenticatedEmptyServer(page);
    let currentRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/telemetry/current")) currentRequests += 1;
    });
    await page.goto("/");
    await page.requestGC();
    const heap = () => page.evaluate(() => (
      performance as Performance & { memory?: { usedJSHeapSize: number } }
    ).memory?.usedJSHeapSize ?? null);
    const initialHeap = await heap();
    const ranges = ["0.083333", "1", "24", "168", "720"];
    let warmHeap: number | null = null;
    for (let index = 0; index < 200; index += 1) {
      await page.getByRole("button", { name: "Analytics" }).click();
      await expect(page.getByRole("heading", { name: "Storage Analytics" })).toBeVisible();
      await page.getByLabel("Time range").selectOption(ranges[index % ranges.length]);
      await page.getByRole("button", { name: "Overview" }).click();
      await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
      if (index === 49) {
        await page.requestGC();
        warmHeap = await heap();
      }
    }
    await page.waitForTimeout(250);
    const requestsAfterLeaving = currentRequests;
    await page.waitForTimeout(5_250);
    expect(currentRequests).toBe(requestsAfterLeaving);
    await page.requestGC();
    const finalHeap = await heap();
    console.info("analytics-memory", JSON.stringify({ initialHeap, warmHeap, finalHeap, visits: 200, rangeChanges: 200, currentRequests }));
    await testInfo.attach("analytics-memory.json", {
      body: JSON.stringify({ initialHeap, warmHeap, finalHeap, visits: 200, rangeChanges: 200, currentRequests }),
      contentType: "application/json",
    });
    if (initialHeap !== null && finalHeap !== null) {
      expect(finalHeap - initialHeap).toBeLessThan(12 * 1024 * 1024);
    }
    if (warmHeap !== null && finalHeap !== null) {
      expect(finalHeap - warmHeap).toBeLessThan(12 * 1024 * 1024);
    }
  });
});
