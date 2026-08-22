import { expect, test, type Page } from "@playwright/test";

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
    if (pathname.endsWith("/storage/mergerfs")) return json({ available: true, status: "configured", items: [] });
    if (pathname.endsWith("/storage/inventory")) return json({ captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] } });
    if (pathname.endsWith("/integrations")) return json({ items: [] });
    if (pathname.endsWith("/wizards") || pathname.endsWith("/operations")) return json({ items: [] });
    if (pathname.endsWith("/system/overview")) return json({
      captured_at: new Date().toISOString(), source: "live",
      system: { hostname: "hoardarr", application: "Hoardarr", version: "0.3.10", database_ready: true, booted_at: null, uptime_seconds: 60, cpu: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, boot_volume: null, temperatures: [] },
      storage: { snapshot: null, drive_count: null, raw_capacity_bytes: null, health: null, pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] } },
      network: { interfaces: [], discovery: { status: "no_neighbors", source: null, captured_at: new Date().toISOString(), detail: null, neighbors: [] } },
      activity: { operations: [] }, applications: { connections: [] }, alerts: [],
    });
    if (pathname.endsWith("/system/resources")) return json({ captured_at: new Date().toISOString(), processor: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, volumes: [], network: { interfaces: [] }, storage: { performance: null } });
    if (pathname.endsWith("/storage/telemetry")) return json({ captured_at: new Date().toISOString(), summary: { sample_seconds: null, writes_today_bytes: 0 }, drives: [], pools: [] });
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

async function storageWizardServer(page: Page): Promise<void> {
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
    partitions: [],
    signatures: [],
    signature_scan: { status: "complete", source: "wipefs", reason: null },
    discard: { granularity_bytes: 4096, max_bytes: 1_073_741_824 },
  }));
  const snapshot = { id: "snap-storage", captured_at: now, sha256: "c".repeat(64), hardware: { disks: drives } };
  let revision = 0;
  let applied = false;
  let answers: Record<string, unknown> = {};
  const wizard = () => ({ id: "wizard-storage", revision, mode: "guided", status: applied ? "applied" : "review", current_step: "storage", hardware_snapshot_id: snapshot.id, answers, plan_id: "plan-storage", created_at: now, updated_at: now });
  const plan = {
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
    if (pathname.endsWith("/complete")) return route.fulfill({ json: { ...wizard(), status: "completed" } });
    if (pathname.endsWith("/plan")) return route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, json: route.request().method() === "POST" ? { plan } : plan });
    if (pathname.includes("/steps/")) {
      const body = route.request().postDataJSON() as { answers: Record<string, unknown> };
      const step = pathname.split("/steps/")[1];
      revision += 1;
      answers = { ...answers, [step]: body.answers };
      return route.fulfill({ json: { ...wizard(), current_step: step } });
    }
    return route.fulfill({ json: wizard() });
  });
  await page.route("**/api/v1/operations", (route) => route.fulfill({ json: { items: applied ? [{ id: "op-storage", kind: "storage.apply", status: "succeeded", resource: { type: "wizard_session", id: "wizard-storage" }, result: { mountpoint: "/data" } }] : [] } }));
  await page.route("**/api/v1/operations/op-storage", (route) => route.fulfill({ json: { id: "op-storage", kind: "storage.apply", status: "succeeded", resource: { type: "wizard_session", id: "wizard-storage" }, result: { mountpoint: "/data" } } }));
  await page.route("**/api/v1/operations/op-storage/progress", (route) => route.fulfill({ json: { operation_id: "op-storage", state: "succeeded", phase: "Storage build completed", completed_steps: 6, total_steps: 6, percent: 100, completed_actions: ["format", "mount"], notices: [], current_action: null, estimate: null, updated_at: Date.now() / 1000 } }));
  await page.route("**/api/v1/operations/op-storage/events", (route) => route.fulfill({ json: { items: [{ sequence: 1, type: "operation.succeeded", message: "Storage build completed", data: {}, created_at: now }] } }));
  await page.route("**/api/v1/accounts/media", (route) => route.fulfill({ status: 201, json: { account: { username: "media", created: true }, credential: { password: "one-time-storage-password" } } }));
}

async function unconfiguredServer(page: Page): Promise<void> {
  await authenticatedEmptyServer(page);
  await page.unroute("**/*");
  let claimed = false;
  await page.route("**/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const json = (value: unknown) => route.fulfill({ json: value });
    if (pathname.endsWith("/setup/status")) return json({ configured: claimed, claim_available: !claimed });
    if (pathname.endsWith("/setup/claim")) {
      claimed = true;
      return route.fulfill({ status: 201, json: { csrf_token: "browser-csrf" } });
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
    if (pathname.endsWith("/networking/apply")) return json({ state: "pending_confirmation", token: "b".repeat(32), changed_components: ["server", "network", "ntp", "discovery"] });
    if (pathname.endsWith("/networking/confirm")) return route.fulfill({ status: 204 });
    if (pathname.endsWith("/networking")) return json({ configuration: null, pending_confirmation: false, capabilities: { available: true, tools: {} }, interfaces: [], current: { hostname: "hoardarr", timezone: "UTC", addresses: {}, default_interface: "enp1s0", default_gateway: null } });
    if (pathname.endsWith("/hardware/snapshots/latest")) return route.fulfill({ status: 404, json: { title: "Not found" } });
    if (pathname.endsWith("/storage/mergerfs")) return json({ available: true, status: "configured", items: [] });
    if (pathname.endsWith("/storage/inventory")) return json({ captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] } });
    if (pathname.endsWith("/integrations")) return json({ items: [] });
    if (pathname.endsWith("/wizards") || pathname.endsWith("/operations")) return json({ items: [] });
    if (pathname.endsWith("/system/overview")) return json({
      captured_at: new Date().toISOString(), source: "live",
      system: { hostname: "hoardarr", application: "Hoardarr", version: "0.3.10", database_ready: true, booted_at: null, uptime_seconds: 60, cpu: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, boot_volume: null, temperatures: [] },
      storage: { snapshot: null, drive_count: null, raw_capacity_bytes: null, health: null, pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] } },
      network: { interfaces: [], discovery: { status: "no_neighbors", source: null, captured_at: new Date().toISOString(), detail: null, neighbors: [] } },
      activity: { operations: [] }, applications: { connections: [] }, alerts: [],
    });
    if (pathname.endsWith("/system/resources")) return json({ captured_at: new Date().toISOString(), processor: { used_percent: 1, logical_processors: 2, physical_cores: 1 }, memory: { total_bytes: 1024, available_bytes: 512, used_bytes: 512, used_percent: 50 }, volumes: [], network: { interfaces: [] }, storage: { performance: null } });
    return route.continue();
  });
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

  test("navigates the ARR shell and opens Guided storage with ordinary questions", async ({ page }) => {
    await authenticatedEmptyServer(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
    const storageNav = page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first();
    await expect(storageNav).toBeVisible();
    await storageNav.click();
    await expect(page.getByRole("heading", { name: "Storage", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Add storage" }).click();
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
    await page.getByRole("button", { name: "Add storage" }).click();
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

  test("recovers a durable apply after refresh and reveals a generated password only until confirmation", async ({ page }) => {
    await storageWizardServer(page);
    await page.goto("/");
    await page.locator('nav[aria-label="Primary navigation"] button').filter({ hasText: "Storage" }).first().click();
    await page.getByRole("button", { name: "Add storage" }).click();
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
