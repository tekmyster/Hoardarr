import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiProblemMessage, drivesFromSnapshot, HARDWARE_SCAN_TIMEOUT_MS } from "./client";
import { demoSnapshot } from "../demo/fixture";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("hardware snapshot normalization", () => {
  it("uses stable identity and health provenance from detector disks", () => {
    const drives = drivesFromSnapshot({
      id: "snapshot-1",
      captured_at: "2026-08-17T17:20:00Z",
      sha256: "a".repeat(64),
      hardware: {
        disks: [{
          id: "serial:cisco:ssd-240g:stp26501raw",
          kernel_path: "/dev/sdb",
          stable_identity: true,
          volatile_locator: true,
          vendor: "CISCO",
          model: "SSD-240G",
          firmware_revision: "V01",
          identity: { serial: "STP26501RAW", wwn: null },
          capacity_bytes: 240_057_409_536,
          sector_sizes: { logical_bytes: 512, physical_bytes: 4096 },
          connection: { transport: "usb", protocol: "uas", presentation: "hyperv-scsi", controller_address: "usb-1:2" },
          partitions: [{ kernel_name: "sdb1", kernel_path: "/dev/sdb1", start_bytes: 1_048_576, size_bytes: 239_000_000_000, filesystem: { type: "ntfs" } }],
          signatures: [{ type: "gpt", usage: "partition_table" }],
          signature_scan: { status: "partial", source: "udev", reason: "A full on-media scan has not run." },
          health: {
            power_on_hours: {
              status: "conflicting",
              value: null,
              confidence: "conflicting",
              source: "smartctl and OS observations",
              captured_at: "2026-08-17T17:20:00Z",
              transport: "usb/uas -> hyperv-scsi",
              reason: "Raw SMART and translated counters disagree.",
              observations: [{
                value: 8,
                unit: "hours",
                source: "windows-storage-reliability-counter",
                captured_at: "2026-08-17T17:20:00Z",
                transport: "usb/uas -> hyperv-scsi",
                confidence: "low",
                qualifies_as_lifetime: false,
                reason: "Translated counter was not corroborated by raw SMART.",
              }],
            },
          },
        }],
      },
    });

    expect(drives).toHaveLength(1);
    expect(drives[0]).toMatchObject({
      id: "serial:cisco:ssd-240g:stp26501raw",
      path: "/dev/sdb",
      model: "SSD-240G V01",
      serial: "STP26501RAW",
      capacityBytes: 240_057_409_536,
      sector: { logical: 512, physical: 4096 },
      connection: { bus: "USB", transport: "usb/uas/hyperv-scsi" },
      stableIdentity: true,
      selectable: true,
      signatureScan: { status: "partial", source: "udev" },
    });
    expect(drives[0].partitions[0]).toMatchObject({ path: "/dev/sdb1", filesystem: "ntfs" });
    expect(drives[0].signatures).toEqual(["gpt"]);
    expect(drives[0].metrics[0]).toMatchObject({
      available: false,
      value: null,
      provenance: { confidence: "unreliable", source: "smartctl and OS observations" },
    });
    expect(drives[0].observations[0]).toMatchObject({
      value: 8,
      unit: "hours",
      qualifiesAsLifetime: false,
      provenance: { source: "windows-storage-reliability-counter", confidence: "low" },
    });
  });

  it("does not invent sector geometry and blocks drives without stable identity", () => {
    const drives = drivesFromSnapshot({
      id: "snapshot-unknown",
      captured_at: "2026-08-17T17:20:00Z",
      sha256: "b".repeat(64),
      hardware: { disks: [{ id: "kernel:sdc", kernel_path: "/dev/sdc", stable_identity: false, identity: {}, sector_sizes: { logical_bytes: null, physical_bytes: null }, partitions: [], signatures: [], signature_scan: { status: "unavailable", reason: "No scan ran.", source: "sysfs" } }] },
    });

    expect(drives[0].sector).toEqual({ logical: null, physical: null });
    expect(drives[0].selectable).toBe(false);
    expect(drives[0].selectionBlockers.join(" ")).toMatch(/stable hardware identity/i);
    expect(drives[0].signatureScan).toMatchObject({ status: "unavailable", reason: "No scan ran." });
  });

  it("excludes the physical disk that contains the running operating system", () => {
    const drives = drivesFromSnapshot({
      id: "snapshot-system-disk",
      captured_at: "2026-08-21T12:00:00Z",
      sha256: "c".repeat(64),
      hardware: { disks: [
        { id: "serial:system", system_disk: true, stable_identity: true, identity: { serial: "SYSTEM" } },
        { id: "serial:data", system_disk: false, stable_identity: true, identity: { serial: "DATA" } },
      ] },
    });

    expect(drives.map((drive) => drive.id)).toEqual(["serial:data"]);
  });

  it("collapses multiple controller paths to one logical device", () => {
    const shared = {
      id: "wwn:naa.600a098000abc",
      stable_identity: true,
      identity: { serial: "LUN7", wwn: "naa.600a098000abc" },
      capacity_bytes: 8_000_000_000_000,
      sector_sizes: { logical_bytes: 512, physical_bytes: 4096 },
      partitions: [],
      signatures: [],
    };
    const drives = drivesFromSnapshot({
      id: "snapshot-multipath",
      captured_at: "2026-08-21T12:00:00Z",
      sha256: "d".repeat(64),
      hardware: { disks: [
        { ...shared, kernel_path: "/dev/sdb", connection: { protocol: "fc", controller_address: "hba-a" } },
        { ...shared, kernel_path: "/dev/sdc", connection: { protocol: "fc", controller_address: "hba-b" } },
      ] },
    });

    expect(drives).toHaveLength(1);
    expect(drives[0].alternatePaths).toEqual(["/dev/sdb", "/dev/sdc"]);
    expect(drives[0].selectable).toBe(false);
    expect(drives[0].selectionBlockers.join(" ")).toMatch(/one logical device/i);
  });

  it("preserves already-normalized demo health observations", () => {
    const [drive] = drivesFromSnapshot(demoSnapshot);
    expect(drive.observations).toHaveLength(2);
    expect(drive.observations[0]).toMatchObject({
      name: "translated_power_on_hours",
      value: 8,
      qualifiesAsLifetime: false,
      provenance: { source: "windows-storage-reliability-counter", confidence: "low" },
    });
  });

  it("formats bounded validation details and allows the backend scan ceiling", () => {
    expect(HARDWARE_SCAN_TIMEOUT_MS).toBeGreaterThanOrEqual(310_000);
    expect(apiProblemMessage({
      detail: "The request is invalid.",
      errors: [{ location: ["body", "network", "addresses", "0"], message: "Value must include a prefix.\u0000" }],
    }, 422)).toBe("The request is invalid. body.network.addresses.0: Value must include a prefix.");
  });

  it("requests the lightweight live resource endpoint", async () => {
    const reading = {
      captured_at: "2026-08-20T15:00:00Z",
      source: "live",
      cpu: { used_percent: 12, logical_processors: 8, physical_cores: 4 },
      memory: { total_bytes: 16_000, available_bytes: 8_000, used_bytes: 8_000, used_percent: 50 },
      storage: {
        system_volume: { mountpoint: "/", total_bytes: 100_000, used_bytes: 30_000, free_bytes: 70_000, used_percent: 30 },
        performance: {
          captured_at: "2026-08-20T15:00:00Z", source: "linux_block_counters",
          summary: { read_bytes_per_second: 1, write_bytes_per_second: 2, read_iops: 3, write_iops: 4, read_wait_ms: 5, write_wait_ms: 6, utilization_percent: 7, writes_today_bytes: 8, sample_seconds: 2 },
          drives: [], pools: [],
        },
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(reading), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.resourceUsage()).resolves.toEqual(reading);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/system\/resources$/);
  });

  it("lists live mergerFS instances without adding sample choices", async () => {
    const inventory = {
      available: true,
      status: "configured",
      items: [{
        id: "mergerfs:0123456789abcdef",
        name: "media",
        mountpoint: "/mnt/media",
        source: "/mnt/disk1:/mnt/disk2",
        branches: ["/mnt/disk1", "/mnt/disk2"],
        options: ["allow_other", "category.create=mfs"],
        active: true,
        configured: true,
      }],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(inventory), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.mergerfsInventory()).resolves.toEqual(inventory);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/storage\/mergerfs$/);
  });

  it("submits a media account password only to the dedicated account endpoint", async () => {
    const result = {
      account: { username: "media", created: true, password_updated: true, smb_enabled: true, shell_login: false },
      credential: { generated: false, password: null, display_once: false },
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.provisionMediaAccount({
      username: "media",
      credential_mode: "provide",
      password: "x",
    })).resolves.toEqual(result);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/accounts\/media$/);
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      username: "media",
      credential_mode: "provide",
      password: "x",
    });
  });

  it("submits the bounded drain preflight contract without starting an operation", async () => {
    const plan = { kind: "storage.drain", schema_version: 1, ready: false, plan_sha256: "a".repeat(64) };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ plan }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const input = {
      source_backend_id: "11111111-1111-4111-8111-111111111111",
      destination_backend_ids: ["22222222-2222-4222-8222-222222222222"],
      verification_mode: "accurate" as const,
      reserve_bytes: 1_073_741_824,
      enforce_source_read_only: true,
      bandwidth_limit_mib_per_second: 64,
      start_at: "2026-08-24T02:00:00.000Z",
      maintenance_window_minutes: 120,
    };

    await expect(api.previewStorageGroupDrain("33333333-3333-4333-8333-333333333333", input))
      .resolves.toEqual(plan);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(
      /\/api\/v1\/storage\/groups\/33333333-3333-4333-8333-333333333333\/drain\/preview$/,
    );
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(request.method).toBe("POST");
    expect(JSON.parse(String(request.body))).toEqual(input);
  });

  it("starts, pauses, and resumes a durable storage drain", async () => {
    const plan = {
      kind: "storage.drain",
      schema_version: 1,
      storage_group_id: "33333333-3333-4333-8333-333333333333",
      plan_sha256: "a".repeat(64),
    } as never;
    const operation = { id: "44444444-4444-4444-8444-444444444444", kind: "storage.drain", status: "queued" };
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ operation }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.startStorageGroupDrain(plan)).resolves.toEqual(operation);
    await api.pauseOperation(operation.id);
    await api.resumeOperation(operation.id);
    const startRequest = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(startRequest.body))).toEqual({
      plan,
      plan_sha256: "a".repeat(64),
      confirmation: "I AGREE",
    });
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/operations\/.*\/pause$/);
    expect(String(fetchMock.mock.calls[2][0])).toMatch(/\/operations\/.*\/resume$/);
  });

  it("restores a browser session and uses its renewed request token", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        user: { id: "user-1", username: "owner", is_admin: true },
        auth_type: "session",
        scopes: ["admin"],
        csrf_token: "hc_restored-token",
      }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        token: { id: "key-1", name: "Test", scopes: ["read"], created_at: "2026-08-20T00:00:00Z", expires_at: null, last_used_at: null },
        secret: "hak_test",
      }), { status: 201, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.resumeSession();
    await api.createApiKey({ name: "Test", scopes: ["read"] });

    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/auth\/me$/);
    const mutation = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(mutation.headers).get("X-CSRF-Token")).toBe("hc_restored-token");
  });

  it("treats a missing latest hardware snapshot as an empty inventory", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      title: "Not found",
      detail: "No hardware snapshot is available.",
    }), { status: 404, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.latestHardwareSnapshot()).resolves.toBeNull();
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/v1\/hardware\/snapshots\/latest$/);
  });

  it("fetches the exact snapshot identified by the completed scan operation", async () => {
    const snapshot = {
      id: "snapshot-from-this-operation",
      captured_at: "2026-08-17T17:20:00Z",
      sha256: "a".repeat(64),
      hardware: { disks: [] },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        operation: {
          id: "operation-1",
          status: "succeeded",
          resource: { type: "hardware_snapshot", id: snapshot.id },
          result: { snapshot_id: snapshot.id },
          error: null,
        },
      }), { status: 202, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.discoverHardware()).resolves.toEqual(snapshot);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/hardware\/snapshots\/snapshot-from-this-operation$/);
    expect(String(fetchMock.mock.calls[1][0])).not.toMatch(/\/latest$/);
  });

  it("surfaces a needs-attention operation's backend message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      operation: {
        id: "operation-2",
        status: "needs_attention",
        resource: null,
        result: null,
        error: { code: "worker_interrupted", message: "Worker stopped before the outcome was known" },
      },
    }), { status: 202, headers: { "Content-Type": "application/json" } })));

    await expect(api.discoverHardware()).rejects.toThrow("Worker stopped before the outcome was known");
  });
});
