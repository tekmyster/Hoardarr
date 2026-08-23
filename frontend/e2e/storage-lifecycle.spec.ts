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
  let lastGroupLifecycle = "preferred_write";
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
        lifecycle_state: operationStatus === "succeeded" ? "retired" : "preferred_write",
      },
      {
        id: destinationId,
        stable_identity: "disk:wwn:destination",
        physical_disk_id: "77777777-7777-4777-8777-777777777777",
        storage_entity_id: null,
        namespace_path: "/srv/hoardarr/backends/destination",
        role: "data",
        lifecycle_state: "preferred_write",
      },
    ],
    events: operationStatus === "succeeded"
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
    if (pathname.endsWith("/storage/groups") && request.method() === "GET") {
      const value = group();
      groupReads += 1;
      lastGroupLifecycle = value.backends[0].lifecycle_state;
      return json({ items: [value] });
    }
    if (pathname.endsWith("/storage/disks")) return json({ items: [] });
    if (pathname.endsWith(`/storage/groups/${groupId}/drain/preview`)) return json({ plan });
    if (pathname.endsWith(`/storage/groups/${groupId}/drain`)) {
      const body = request.postDataJSON() as { confirmation: string };
      submittedConfirmation = body.confirmation;
      operationStatus = "queued";
      return json({ operation: operation(), replayed: false }, 202);
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
    if (pathname.endsWith("/storage/logical")) return json({ items: [] });
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
  };
}

test("drains and retires a Storage Group source through the real browser workflow", async ({ page }, testInfo) => {
  test.setTimeout(45_000);
  const observed = await storageLifecycleServer(page);
  await page.goto("/");
  await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
  await expect(page.getByRole("heading", { name: "Storage", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Media", { exact: true }).getByText("/srv/hoardarr/media")).toBeVisible();
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
  await page.screenshot({ path: testInfo.outputPath("storage-lifecycle-completed.png"), fullPage: true });
});
