import { demoInterfaces, demoOnboarding, demoPlan, demoSetupStatus, demoSnapshot, demoWizard } from "../demo/fixture";
import type {
  ApiProblemBody,
  ApiKeyDocument,
  AddonDocument,
  IntegrationDocument,
  IntegrationProduct,
  CurrentMetricsDocument,
  EntitlementDocument,
  ConnectivityCapabilities,
  ConnectivityServiceDocument,
  ConnectivityServiceInput,
  Drive,
  DeviceMaintenancePlan,
  ForeignStorageAssessment,
  ForeignInspectionPlan,
  HardwareSnapshot,
  MergerFsInventory,
  MetricAlertDocument,
  MetricCatalogDocument,
  MetricEntity,
  MetricHistoryDocument,
  MetricSampleDocument,
  LatencyAnalyticsDocument,
  LogicalStorageDocument,
  MediaAccountProvisionResult,
  NetworkInterface,
  ManagedNetworkApplyResult,
  ManagedNetworkStatus,
  NetworkPlanResponse,
  OnboardingDefaults,
  OperationDocument,
  OperationEvent,
  OverviewDocument,
  PlanDocument,
  PhysicalDiskDocument,
  ResourceUsageDocument,
  SetupStatus,
  SnapraidReplacementPlan,
  ArrayReplacementPlan,
  StorageOperationProgress,
  StorageBackendActivationPlan,
  StorageInventory,
  StorageDrainPlan,
  StorageExpansionAssessment,
  StorageGroupDocument,
  StorageRedundancyPlan,
  StorageRedundancyEventDocument,
  StorageRedundancySettings,
  StorageTelemetryDocument,
  TelemetryForecastDocument,
  TelemetrySettingsDocument,
  TierTransferPlan,
  TierTransferSummary,
  TopologyExpectationDocument,
  TopologyExpectationStatus,
  TopologyPlanDocument,
  TopologyPlanTemplate,
  UpdateCheckDocument,
  UpdateStatusDocument,
  WizardDocument,
  WizardMode,
} from "../types";
import { createIdempotencyKey } from "./idempotency";

const apiBase = (import.meta.env.VITE_HOARDARR_API_BASE ?? "").replace(/\/$/, "");
// Demo fixtures are a development-only capability. A production build cannot
// be switched back to sample data by a stale or injected environment value.
export const demoMode = import.meta.env.DEV && import.meta.env.VITE_HOARDARR_DEMO === "true";
export const HARDWARE_SCAN_TIMEOUT_MS = 310_000;

function browserCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  const match = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem?: ApiProblemBody;

  constructor(status: number, message: string, problem?: ApiProblemBody) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalPositiveNumber(value: unknown): number | null {
  const parsed = optionalNumber(value);
  return parsed !== null && parsed > 0 ? parsed : null;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function safeProblemText(value: unknown, maximum = 180): string {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, maximum);
}

export function apiProblemMessage(problem: ApiProblemBody | undefined, status: number): string {
  const base = safeProblemText(problem?.detail)
    || safeProblemText(problem?.message)
    || safeProblemText(problem?.title)
    || `API request failed (${status}).`;
  const details = (problem?.errors ?? []).slice(0, 8).flatMap((rawError) => {
    const error = record(rawError);
    const message = safeProblemText(error.message);
    if (!message) return [];
    const rawLocation = Array.isArray(error.location) ? error.location : [];
    const location = rawLocation
      .map((part) => safeProblemText(String(part), 48))
      .filter(Boolean)
      .join(".");
    return [`${location ? `${location}: ` : ""}${message}`];
  });
  return details.length ? `${base} ${details.join(" ")}` : base;
}

function normalizeDrive(raw: unknown, index: number): Drive {
  const item = record(raw);
  const identity = record(item.identity);
  const connection = record(item.connection);
  const sector = record(item.sector ?? item.sectors ?? item.sector_sizes);
  const health = record(item.health);
  const powerOnHours = record(health.power_on_hours);
  const powerObservations = Array.isArray(powerOnHours.observations) ? powerOnHours.observations : [];
  const suppliedObservations = Array.isArray(item.observations) ? item.observations : [];
  const metrics = Array.isArray(item.metrics) ? item.metrics : [];
  const tests = Array.isArray(item.tests) ? item.tests : [];
  const rawPartitions = Array.isArray(item.partitions) ? item.partitions : [];
  const signatureScan = record(item.signatureScan ?? item.signature_scan);
  const maintenance = record(item.maintenance_capabilities ?? item.maintenanceCapabilities);
  const smartSelfTest = record(maintenance.smart_self_test ?? maintenance.smartSelfTest);
  const serial = text(item.serial ?? item.serial_number ?? identity.serial, "Not reported");
  const stableIdentity = item.stable_identity === true || item.stableIdentity === true;
  const readOnly = item.read_only === true || item.readOnly === true;
  const selectionBlockers = [
    ...(!stableIdentity ? ["No stable hardware identity was reported. A serial, WWN, EUI-64, or NGUID is required before this drive can enter a storage plan."] : []),
    ...(readOnly ? ["The operating system reports this drive as read-only."] : []),
  ];
  const rawConfidence = text(powerOnHours.confidence, "unavailable");
  const rawHealthStatus = text(item.health_status ?? health.status ?? health.overall).toLowerCase().replace("_", " ");
  const healthStatus: Drive["healthStatus"] = ["healthy", "good", "ok", "passed", "pass"].includes(rawHealthStatus)
    ? "healthy"
    : ["warning", "warn", "degraded", "prefail", "pre-fail"].includes(rawHealthStatus)
      ? "warning"
      : ["critical", "failed", "fail", "bad"].includes(rawHealthStatus)
        ? "critical"
        : health.passed === true
          ? "healthy"
          : health.passed === false ? "critical" : "unknown";
  const powerMetric: Drive["metrics"][number] | null = Object.keys(powerOnHours).length ? {
    name: "power_on_hours",
    label: "Power-on hours",
    value: typeof powerOnHours.value === "number" ? powerOnHours.value : null,
    unit: text(powerOnHours.unit, "hours"),
    available: powerOnHours.status === "available" && typeof powerOnHours.value === "number",
    provenance: {
      source: text(powerOnHours.source, "Not reported"),
      capturedAt: text(powerOnHours.captured_at, new Date(0).toISOString()),
      transport: text(powerOnHours.transport, text(connection.transport, "Unknown")),
      confidence: ["high", "medium", "low"].includes(rawConfidence) ? rawConfidence as "high" | "medium" | "low" : "unreliable",
      detail: text(powerOnHours.reason) || undefined,
    },
  } : null;
  const transportParts = [connection.transport, connection.protocol, connection.presentation]
    .map((part) => text(part))
    .filter(Boolean);
  const slot = text(connection.slot);
  const enclosure = text(connection.enclosure_id);
  return {
    id: text(item.id ?? item.stable_id, `drive-${serial}-${index}`),
    path: text(item.path ?? item.device ?? item.kernel_path, "Not reported"),
    model: [text(item.model, "Not reported"), text(item.firmware_revision)].filter(Boolean).join(" "),
    vendor: text(item.vendor, ""),
    serial,
    wwn: text(item.wwn ?? identity.wwn ?? identity.eui64 ?? identity.nguid) || null,
    capacityBytes: number(item.capacityBytes ?? item.capacity_bytes ?? item.size_bytes),
    rotational: typeof item.rotational === "boolean" ? item.rotational : null,
    stableIdentity,
    readOnly,
    selectable: selectionBlockers.length === 0,
    selectionBlockers,
    connection: {
      bus: text(connection.bus ?? item.bus ?? connection.transport, "Unknown").toUpperCase(),
      transport: transportParts.length ? transportParts.join("/") : text(item.transport, "Unknown"),
      bridge: text(connection.bridge) || undefined,
    },
    sector: {
      logical: optionalPositiveNumber(sector.logical ?? sector.logical_bytes ?? item.logical_sector_size),
      physical: optionalPositiveNumber(sector.physical ?? sector.physical_bytes ?? item.physical_sector_size),
    },
    signatures: Array.isArray(item.signatures) ? item.signatures.map((signature) => {
      if (typeof signature === "string") return signature;
      const value = record(signature);
      return text(value.type ?? value.name ?? value.kind, "Unidentified signature");
    }) : [],
    partitions: rawPartitions.map((rawPartition) => {
      const partition = record(rawPartition);
      const filesystem = record(partition.filesystem);
      return {
        kernelName: text(partition.kernelName ?? partition.kernel_name) || null,
        path: text(partition.path ?? partition.kernel_path) || null,
        startBytes: optionalNumber(partition.startBytes ?? partition.start_bytes),
        sizeBytes: optionalNumber(partition.sizeBytes ?? partition.size_bytes),
        filesystem: text(partition.filesystemType ?? partition.filesystem_type ?? filesystem.type) || null,
      };
    }),
    signatureScan: {
      status: ["complete", "partial", "unavailable"].includes(text(signatureScan.status))
        ? text(signatureScan.status) as "complete" | "partial" | "unavailable"
        : "unavailable",
      reason: text(signatureScan.reason) || null,
      source: text(signatureScan.source) || null,
    },
    location: text(item.location ?? item.enclosure_slot) || (enclosure || slot ? `${enclosure || "enclosure"}${slot ? ` / slot ${slot}` : ""}` : text(connection.controller_address, "Not reported")),
    removable: Boolean(item.removable ?? item.volatile_locator) || text(connection.transport).toLowerCase() === "usb",
    healthStatus,
    metrics: metrics.length ? metrics as Drive["metrics"] : powerMetric ? [powerMetric] : [],
    observations: (suppliedObservations.length ? suppliedObservations : powerObservations).map((rawObservation, observationIndex) => {
      const observation = record(rawObservation);
      const provenance = record(observation.provenance);
      const confidence = text(observation.confidence, "unavailable");
      const provenanceConfidence = text(provenance.confidence, confidence);
      return {
        name: text(observation.name, `power_on_hours_observation_${observationIndex + 1}`),
        label: text(observation.label, "Power-on hours evidence"),
        value: typeof observation.value === "number" || typeof observation.value === "string" ? observation.value : null,
        unit: text(observation.unit) || undefined,
        qualifiesAsLifetime: observation.qualifies_as_lifetime === true || observation.qualifiesAsLifetime === true,
        reason: text(observation.reason) || undefined,
        provenance: {
          source: text(provenance.source, text(observation.source, "Not reported")),
          capturedAt: text(provenance.capturedAt ?? provenance.captured_at, text(observation.captured_at, text(powerOnHours.captured_at, new Date(0).toISOString()))),
          transport: text(provenance.transport, text(observation.transport, text(powerOnHours.transport, text(connection.transport, "Unknown")))),
          confidence: ["high", "medium", "low"].includes(provenanceConfidence) ? provenanceConfidence as "high" | "medium" | "low" : "unreliable",
          detail: text(provenance.detail, text(observation.reason)) || undefined,
        },
      };
    }),
    tests: tests as Drive["tests"],
    smartSelfTest: {
      status: ["available", "unsupported", "not_reported"].includes(text(smartSelfTest.status))
        ? text(smartSelfTest.status) as NonNullable<Drive["smartSelfTest"]>["status"]
        : "not_reported",
      shortMinutes: optionalPositiveNumber(smartSelfTest.short_minutes ?? smartSelfTest.shortMinutes),
      extendedMinutes: optionalPositiveNumber(smartSelfTest.extended_minutes ?? smartSelfTest.extendedMinutes),
      source: text(smartSelfTest.source, "Not reported"),
    },
    maintenanceCapabilities: {
      source: text(maintenance.source, "Not reported"),
      ataSecureErase: maintenance.ata_secure_erase === true,
      nvmeBlockErase: maintenance.nvme_block_erase === true,
      nvmeCryptoErase: maintenance.nvme_crypto_erase === true,
      scsiBlockErase: maintenance.scsi_block_erase === true,
      scsiCryptoErase: maintenance.scsi_crypto_erase === true,
    },
  };
}

export function drivesFromSnapshot(snapshot: HardwareSnapshot): Drive[] {
  const hardware = record(snapshot.hardware);
  const storage = record(hardware.storage);
  const raw = hardware.disks ?? hardware.drives ?? hardware.block_devices ?? storage.drives ?? [];
  if (!Array.isArray(raw)) return [];
  const drives = raw
    .filter((item) => {
      const drive = record(item);
      return drive.system_disk !== true && drive.systemDisk !== true;
    })
    .map(normalizeDrive);
  const grouped = new Map<string, Drive[]>();
  for (const drive of drives) {
    const key = drive.wwn ? `wwn:${drive.wwn.toLowerCase()}` : drive.id;
    grouped.set(key, [...(grouped.get(key) ?? []), drive]);
  }
  return [...grouped.values()].map((paths) => {
    if (paths.length === 1) return paths[0];
    const mapped = paths.find((drive) => drive.path.startsWith("/dev/mapper/"));
    const canonical = mapped ?? paths[0];
    const selectionBlockers = mapped
      ? canonical.selectionBlockers
      : [
          ...canonical.selectionBlockers,
          "Multiple controller paths reach this storage. Import or manage it as one logical device before changing its contents.",
        ];
    return {
      ...canonical,
      alternatePaths: paths.map((drive) => drive.path),
      selectable: mapped ? canonical.selectable : false,
      selectionBlockers,
    };
  });
}

class HoardarrApi {
  private csrfToken: string | null = null;
  private demoRevision = 0;

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const method = (init.method ?? "GET").toUpperCase();
    const csrfToken = browserCookie("hoardarr_csrf") ?? browserCookie("__Host-hoardarr_csrf") ?? this.csrfToken;
    if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      headers.set("X-CSRF-Token", csrfToken);
    }
    let response: Response;
    try {
      response = await fetch(`${apiBase}/api/v1${path}`, {
        ...init,
        credentials: "include",
        headers,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError(0, error instanceof Error ? `Cannot reach the Hoardarr API: ${error.message}` : "Cannot reach the Hoardarr API.");
    }
    if (!response.ok) {
      let problem: ApiProblemBody | undefined;
      try {
        problem = (await response.json()) as ApiProblemBody;
      } catch {
        problem = undefined;
      }
      throw new ApiError(response.status, apiProblemMessage(problem, response.status), problem);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async setupStatus(): Promise<SetupStatus> {
    if (demoMode) return demoSetupStatus;
    return this.request<SetupStatus>("/setup/status");
  }

  async overview(): Promise<OverviewDocument> {
    if (demoMode) {
      throw new ApiError(503, "Live Overview data is unavailable because this browser session is not connected to the Hoardarr API.");
    }
    return this.request<OverviewDocument>("/system/overview");
  }

  async resourceUsage(): Promise<ResourceUsageDocument> {
    if (demoMode) {
      throw new ApiError(503, "Live resource usage is unavailable because this browser session is not connected to the Hoardarr API.");
    }
    return this.request<ResourceUsageDocument>("/system/resources");
  }

  async mergerfsInventory(): Promise<MergerFsInventory> {
    if (demoMode) return { available: false, status: "unavailable", items: [] };
    return this.request<MergerFsInventory>("/storage/mergerfs");
  }

  async storageInventory(): Promise<StorageInventory> {
    if (demoMode) return { captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [], pools: { status: "not_configured", items: [] }, shares: { status: "not_configured", items: [] }, controllers: { status: "Not reported", items: [], unavailable: [] }, enclosures: { status: "Not reported", items: [], unavailable: [] } };
    return this.request<StorageInventory>("/storage/inventory");
  }

  async storageTelemetry(): Promise<StorageTelemetryDocument> {
    if (demoMode) throw new ApiError(503, "Live storage performance is unavailable in demo mode.");
    return this.request<StorageTelemetryDocument>("/storage/telemetry");
  }

  async logicalStorage(): Promise<LogicalStorageDocument[]> {
    if (demoMode) return [];
    const result = await this.request<{ items: LogicalStorageDocument[] }>("/storage/logical");
    return result.items;
  }

  async previewTierTransfer(input: {
    workload: "torrent" | "usenet";
    source: string;
    destination: string;
    method: "auto" | "copy" | "move" | "hardlink";
    retain_until?: "seeding_complete" | "manual" | "never" | "import_complete";
    cleanup: boolean;
    completed_steps: string[];
  }): Promise<{ plan: TierTransferPlan; plan_sha256: string }> {
    return this.request("/storage/transfers/preview", { method: "POST", body: JSON.stringify(input) });
  }

  async tierTransferSummary(): Promise<TierTransferSummary> {
    return this.request<TierTransferSummary>("/storage/transfers/summary");
  }

  async applyTierTransfer(plan: TierTransferPlan, planSha256: string): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>("/storage/transfers", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ plan, plan_sha256: planSha256, confirmation: "APPLY" }),
    });
    return result.operation;
  }

  async cleanupTierTransfer(operationId: string): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(`/storage/transfers/${encodeURIComponent(operationId)}/cleanup`, {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ confirmation: "APPLY" }),
    });
    return result.operation;
  }

  async storageGroups(signal?: AbortSignal): Promise<StorageGroupDocument[]> {
    if (demoMode) return [];
    const result = await this.request<{ items: StorageGroupDocument[] }>("/storage/groups", { signal });
    return result.items;
  }

  async registeredDisks(signal?: AbortSignal): Promise<PhysicalDiskDocument[]> {
    if (demoMode) return [];
    const result = await this.request<{ items: PhysicalDiskDocument[] }>("/storage/disks", { signal });
    return result.items;
  }

  async storageExpansion(signal?: AbortSignal): Promise<StorageExpansionAssessment> {
    return this.request<StorageExpansionAssessment>("/storage/expansion", { signal });
  }

  async foreignStorage(signal?: AbortSignal): Promise<ForeignStorageAssessment> {
    return this.request<ForeignStorageAssessment>("/storage/foreign", { signal });
  }

  async previewForeignInspection(candidateId: string): Promise<ForeignInspectionPlan> {
    const result = await this.request<{ plan: ForeignInspectionPlan }>(
      "/storage/foreign/inspection/preview",
      { method: "POST", body: JSON.stringify({ candidate_id: candidateId }) },
    );
    return result.plan;
  }

  async startForeignInspection(plan: ForeignInspectionPlan): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(
      "/storage/foreign/inspection",
      {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({
          plan,
          plan_sha256: plan.plan_sha256,
          confirmation: "INSPECT READ ONLY",
        }),
      },
    );
    return result.operation;
  }

  async setDiskReservation(
    diskId: string,
    action: "reserve" | "release",
  ): Promise<PhysicalDiskDocument> {
    const result = await this.request<{ item: PhysicalDiskDocument }>(
      `/storage/disks/${encodeURIComponent(diskId)}/reservation`,
      { method: "POST", body: JSON.stringify({ action }) },
    );
    return result.item;
  }

  async createStorageGroup(input: {
    name: string;
    namespace_path: string;
    purpose: "media" | "downloads" | "archive" | "backup" | "general";
  }): Promise<StorageGroupDocument> {
    const result = await this.request<{ item: StorageGroupDocument }>("/storage/groups", {
      method: "POST",
      body: JSON.stringify(input),
    });
    return result.item;
  }

  async assignStorageGroupDisk(
    groupId: string,
    physicalDiskId: string,
    namespacePath?: string,
  ): Promise<StorageGroupDocument> {
    const result = await this.request<{ item: StorageGroupDocument }>(
      `/storage/groups/${encodeURIComponent(groupId)}/backends`,
      {
        method: "POST",
        body: JSON.stringify({
          physical_disk_id: physicalDiskId,
          namespace_path: namespacePath || undefined,
          role: "data",
        }),
      },
    );
    return result.item;
  }

  async previewStorageGroupDrain(
    groupId: string,
    input: {
      source_backend_id: string;
      destination_backend_ids: string[];
      verification_mode: "fast" | "accurate" | "paranoid";
      reserve_bytes: number;
      enforce_source_read_only: boolean;
      bandwidth_limit_mib_per_second: number | null;
      io_priority?: "normal" | "background" | "idle";
      start_at: string | null;
      maintenance_window_minutes: number | null;
    },
  ): Promise<StorageDrainPlan> {
    const result = await this.request<{ plan: StorageDrainPlan }>(
      `/storage/groups/${encodeURIComponent(groupId)}/drain/preview`,
      { method: "POST", body: JSON.stringify(input) },
    );
    return result.plan;
  }

  async startStorageGroupDrain(plan: StorageDrainPlan): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(
      `/storage/groups/${encodeURIComponent(plan.storage_group_id)}/drain`,
      {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({
          plan,
          plan_sha256: plan.plan_sha256,
          confirmation: "I AGREE",
        }),
      },
    );
    return result.operation;
  }

  async transitionStorageBackend(
    groupId: string,
    backendId: string,
    targetState: "active" | "preferred_write",
  ): Promise<StorageGroupDocument> {
    const result = await this.request<{ item: StorageGroupDocument }>(
      `/storage/groups/${encodeURIComponent(groupId)}/backends/${encodeURIComponent(backendId)}/transition`,
      { method: "POST", body: JSON.stringify({ target_state: targetState }) },
    );
    return result.item;
  }

  async previewStorageBackendActivation(
    groupId: string,
    backendId: string,
  ): Promise<StorageBackendActivationPlan> {
    const result = await this.request<{ plan: StorageBackendActivationPlan }>(
      `/storage/groups/${encodeURIComponent(groupId)}/backends/${encodeURIComponent(backendId)}/activation/preview`,
      { method: "POST", body: "{}" },
    );
    return result.plan;
  }

  async activateStorageBackend(
    plan: StorageBackendActivationPlan,
  ): Promise<StorageGroupDocument> {
    const result = await this.request<{ item: StorageGroupDocument }>(
      `/storage/groups/${encodeURIComponent(plan.storage_group_id)}/backends/${encodeURIComponent(plan.backend_id)}/activation`,
      {
        method: "POST",
        body: JSON.stringify({
          plan_sha256: plan.plan_sha256,
          reason: "Mounted storage identity reviewed and verified.",
        }),
      },
    );
    return result.item;
  }

  async releaseRetiredStorageBackend(
    groupId: string,
    backendId: string,
    reason?: string,
  ): Promise<{ item: StorageGroupDocument; disk: PhysicalDiskDocument }> {
    return this.request(
      `/storage/groups/${encodeURIComponent(groupId)}/backends/${encodeURIComponent(backendId)}/retirement`,
      {
        method: "POST",
        body: JSON.stringify({
          action: "release_for_reuse",
          confirmation: "RELEASE",
          reason: reason || undefined,
        }),
      },
    );
  }

  async previewStorageRedundancy(input: {
    storage_entity_id: string;
    action: "add" | "remove" | "replace" | "configure";
    path_identity?: string;
    remove_path_identity?: string;
    policy?: "recommended" | "failover" | "multibus" | "group_by_prio";
    settings?: StorageRedundancySettings;
  }): Promise<{ plan: StorageRedundancyPlan; plan_sha256: string }> {
    return this.request("/storage/redundancy/preview", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async storageRedundancyEvents(
    storageEntityId: string,
    signal?: AbortSignal,
  ): Promise<StorageRedundancyEventDocument[]> {
    const result = await this.request<{ items: StorageRedundancyEventDocument[] }>(
      `/storage/logical/${encodeURIComponent(storageEntityId)}/redundancy/events`,
      { signal },
    );
    return result.items;
  }

  async applyStorageRedundancy(
    plan: StorageRedundancyPlan,
    planSha256: string,
  ): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>("/storage/redundancy", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ plan, plan_sha256: planSha256, confirmation: "APPLY" }),
    });
    return result.operation;
  }

  async metricCatalog(signal?: AbortSignal): Promise<MetricCatalogDocument> {
    return this.request<MetricCatalogDocument>("/telemetry/catalog", { signal });
  }

  async metricEntities(entityType?: string, signal?: AbortSignal): Promise<MetricEntity[]> {
    const query = entityType ? `?entity_type=${encodeURIComponent(entityType)}` : "";
    const result = await this.request<{ items: MetricEntity[] }>(`/telemetry/entities${query}`, { signal });
    return result.items;
  }

  async currentMetrics(filters: { metricId?: string; entityType?: string; entityId?: string } = {}, signal?: AbortSignal): Promise<CurrentMetricsDocument> {
    const query = new URLSearchParams();
    if (filters.metricId) query.append("metric_id", filters.metricId);
    if (filters.entityType) query.set("entity_type", filters.entityType);
    if (filters.entityId) query.set("entity_id", filters.entityId);
    return this.request<CurrentMetricsDocument>(`/telemetry/current${query.size ? `?${query}` : ""}`, { signal });
  }

  async metricHistory(input: { entityId: string; metricId: string; start: string; end: string; resolution?: "auto" | "raw" | "hour" | "day"; maximumPoints?: number; signal?: AbortSignal }): Promise<MetricHistoryDocument> {
    const query = new URLSearchParams({
      entity_id: input.entityId,
      metric_id: input.metricId,
      start: input.start,
      end: input.end,
      resolution: input.resolution ?? "auto",
    });
    if (input.maximumPoints) query.set("limit", String(input.maximumPoints));
    return this.request<MetricHistoryDocument>(`/telemetry/history?${query}`, { signal: input.signal });
  }

  async telemetrySettings(signal?: AbortSignal): Promise<TelemetrySettingsDocument> {
    return this.request<TelemetrySettingsDocument>("/telemetry/settings", { signal });
  }

  async telemetryEntitlements(): Promise<EntitlementDocument> {
    return this.request<EntitlementDocument>("/telemetry/entitlements");
  }

  async metricAlerts(state: "active" | "resolved" | "all" = "active", signal?: AbortSignal): Promise<MetricAlertDocument[]> {
    const result = await this.request<{ items: MetricAlertDocument[] }>(`/telemetry/alerts?state=${state}`, { signal });
    return result.items;
  }

  async topMetrics(metricId: string, direction: "highest" | "lowest" = "highest", signal?: AbortSignal): Promise<MetricSampleDocument[]> {
    const query = new URLSearchParams({ metric_id: metricId, direction });
    const result = await this.request<{ items: MetricSampleDocument[] }>(`/telemetry/top?${query}`, { signal });
    return result.items;
  }

  async capacityForecast(entityId: string, signal?: AbortSignal): Promise<{ forecast: TelemetryForecastDocument }> {
    return this.request(`/telemetry/analytics/capacity/${encodeURIComponent(entityId)}`, { signal });
  }

  async enduranceForecast(entityId: string, signal?: AbortSignal): Promise<{ forecast: TelemetryForecastDocument }> {
    return this.request(`/telemetry/analytics/endurance/${encodeURIComponent(entityId)}`, { signal });
  }

  async latencyAnalytics(entityId: string, metricId: "io.read.latency" | "io.write.latency", signal?: AbortSignal): Promise<LatencyAnalyticsDocument> {
    const query = new URLSearchParams({ metric_id: metricId });
    return this.request(`/telemetry/analytics/latency/${encodeURIComponent(entityId)}?${query}`, { signal });
  }

  async telemetryAnomalies(signal?: AbortSignal): Promise<Array<Record<string, unknown>>> {
    const result = await this.request<{ items: Array<Record<string, unknown>> }>("/telemetry/analytics/anomalies", { signal });
    return result.items;
  }

  async previewDeviceMaintenance(input: {
    device_id: string;
    action: "wipe" | "sector_conversion";
    method?: "quick" | "metadata_clear" | "hdd_overwrite" | "ata_secure_erase" | "nvme_sanitize" | "nvme_crypto_erase" | "scsi_sanitize" | "scsi_crypto_erase";
    passes?: number;
    target_logical_bytes?: 512 | 4096;
  }): Promise<{ plan: DeviceMaintenancePlan; plan_sha256: string }> {
    return this.request("/storage/maintenance/preview", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async applyDeviceMaintenance(
    plan: DeviceMaintenancePlan,
    planSha256: string,
  ): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>("/storage/maintenance", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ plan, plan_sha256: planSha256, confirmation: "I AGREE" }),
    });
    return result.operation;
  }

  async previewSnapraidReplacement(input: {
    pool_name: string;
    data_name: string;
    replacement_device_id: string;
    filesystem: "ext4" | "xfs" | "btrfs";
  }): Promise<{ plan: SnapraidReplacementPlan; plan_sha256: string }> {
    return this.request("/storage/snapraid/replacements/preview", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async applySnapraidReplacement(
    plan: SnapraidReplacementPlan,
    planSha256: string,
  ): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(
      "/storage/snapraid/replacements",
      {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({ plan, plan_sha256: planSha256, confirmation: "I AGREE" }),
      },
    );
    return result.operation;
  }

  async previewArrayReplacement(input: {
    target_id: string;
    old_member_path: string | null;
    replacement_device_id: string;
  }): Promise<{ plan: ArrayReplacementPlan; plan_sha256: string }> {
    return this.request("/storage/arrays/replacements/preview", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async applyArrayReplacement(
    plan: ArrayReplacementPlan,
    planSha256: string,
  ): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(
      "/storage/arrays/replacements",
      {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({ plan, plan_sha256: planSha256, confirmation: "I AGREE" }),
      },
    );
    return result.operation;
  }

  async connectivityCapabilities(): Promise<ConnectivityCapabilities> {
    return this.request<ConnectivityCapabilities>("/connectivity/capabilities");
  }

  async connectivityServices(): Promise<ConnectivityServiceDocument[]> {
    const result = await this.request<{ items: ConnectivityServiceDocument[] }>("/connectivity");
    return result.items;
  }

  async createConnectivityService(input: ConnectivityServiceInput): Promise<{
    service: ConnectivityServiceDocument;
    operation: OperationDocument;
    generated_password?: string;
  }> {
    return this.request("/connectivity", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify(input),
    });
  }

  async updateConnectivityService(id: string, input: ConnectivityServiceInput): Promise<{
    service: ConnectivityServiceDocument;
    operation: OperationDocument;
    generated_password?: string;
  }> {
    return this.request(`/connectivity/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify(input),
    });
  }

  async deleteConnectivityService(id: string, deleteBackingData: boolean): Promise<{
    service: ConnectivityServiceDocument;
    operation: OperationDocument;
  }> {
    return this.request(`/connectivity/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ confirmation: "I AGREE", delete_backing_data: deleteBackingData }),
    });
  }

  async provisionMediaAccount(input: {
    username: string;
    credential_mode: "generate" | "provide";
    password?: string;
  }): Promise<MediaAccountProvisionResult> {
    if (demoMode) throw new ApiError(409, "Media accounts cannot be created in demonstration mode.");
    return this.request<MediaAccountProvisionResult>("/accounts/media", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async claimSetup(input: { token: string; username: string; password: string }): Promise<void> {
    if (demoMode) return;
    const result = await this.request<{ csrf_token: string }>("/setup/claim", {
      method: "POST",
      body: JSON.stringify(input),
    });
    this.csrfToken = result.csrf_token;
  }

  async login(input: { username: string; password: string; remember_me?: boolean }): Promise<void> {
    if (demoMode) return;
    const result = await this.request<{ csrf_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    });
    this.csrfToken = result.csrf_token;
  }

  async resumeSession(): Promise<void> {
    if (demoMode) return;
    const result = await this.request<{ csrf_token: string | null }>("/auth/me");
    this.csrfToken = result.csrf_token;
  }

  async apiKeys(): Promise<ApiKeyDocument[]> {
    if (demoMode) return [];
    const result = await this.request<{ items: ApiKeyDocument[] }>("/auth/tokens");
    return result.items;
  }

  async createApiKey(input: { name: string; scopes: Array<"read" | "operate" | "admin"> }): Promise<{ key: ApiKeyDocument; secret: string }> {
    if (demoMode) throw new ApiError(409, "API keys cannot be created in demonstration mode.");
    const result = await this.request<{ token: ApiKeyDocument; secret: string }>("/auth/tokens", {
      method: "POST",
      body: JSON.stringify({ ...input, expires_at: null }),
    });
    return { key: result.token, secret: result.secret };
  }

  async deleteApiKey(id: string): Promise<void> {
    if (demoMode) return;
    await this.request(`/auth/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  async onboarding(): Promise<OnboardingDefaults> {
    if (demoMode) return demoOnboarding;
    return this.request<OnboardingDefaults>("/onboarding");
  }

  async networkInterfaces(): Promise<NetworkInterface[]> {
    if (demoMode) return demoInterfaces;
    const result = await this.request<{ items: Array<Record<string, unknown>> }>("/onboarding/network/interfaces");
    return result.items.map((item) => ({
      id: text(item.id ?? item.name),
      name: text(item.name, "Unnamed interface"),
      mac: text(item.mac ?? item.mac_address, "Not reported"),
      speed_mbps: typeof item.speed_mbps === "number" ? item.speed_mbps : null,
      link: text(item.operational_state, item.carrier === true ? "up" : item.carrier === false ? "down" : "unknown") as NetworkInterface["link"],
      driver: text(item.driver) || null,
      model: text(item.model) || null,
      warnings: Array.isArray(item.warnings) ? item.warnings.map(String) : [],
    }));
  }

  async previewNetworkPlan(payload: Record<string, unknown>): Promise<NetworkPlanResponse> {
    if (demoMode) {
      return {
        plan: {
          apply_available: false,
          warnings: [],
          blockers: [{ code: "network_privileged_executor_not_implemented", message: "Network changes remain review-only on this build." }],
          payload,
        },
        sha256: "6fd6e2f65c9222876ff8d23548fdd3ac2e1a5f077b7440207dd64270d79ca23f",
      };
    }
    return this.request<NetworkPlanResponse>("/onboarding/network/plan", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async networkingStatus(): Promise<ManagedNetworkStatus> {
    return this.request<ManagedNetworkStatus>("/networking");
  }

  async planManagedNetwork(configuration: Record<string, unknown>, changedComponents: string[]): Promise<NetworkPlanResponse> {
    return this.request<NetworkPlanResponse>("/networking/plan", {
      method: "POST",
      body: JSON.stringify({ configuration, changed_components: changedComponents }),
    });
  }

  async applyManagedNetwork(configuration: Record<string, unknown>, planSha256: string, changedComponents: string[]): Promise<ManagedNetworkApplyResult> {
    return this.request<ManagedNetworkApplyResult>("/networking/apply", {
      method: "POST",
      body: JSON.stringify({ configuration, changed_components: changedComponents, plan_sha256: planSha256, confirmation: "APPLY" }),
    });
  }

  async confirmManagedNetwork(token: string): Promise<void> {
    await this.request("/networking/confirm", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  }

  async discoverHardware(): Promise<HardwareSnapshot> {
    if (demoMode) return demoSnapshot;
    const started = await this.request<{ operation: OperationDocument }>("/hardware/scans", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({}),
    });
    const deadline = Date.now() + HARDWARE_SCAN_TIMEOUT_MS;
    let operation = started.operation;
    while (["queued", "running"].includes(operation.status) && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 750));
      operation = await this.request<OperationDocument>(`/operations/${operation.id}`);
    }
    if (["queued", "running"].includes(operation.status)) {
      throw new ApiError(409, `Hardware scan ${operation.id} is still ${operation.status} after ${HARDWARE_SCAN_TIMEOUT_MS / 1000} seconds. Review it in Activity before starting another scan; its outcome is not known.`);
    }
    if (operation.status === "needs_attention") {
      const detail = operation.error?.message ?? operation.error?.detail ?? "The scan outcome could not be determined.";
      throw new ApiError(409, `Hardware scan needs attention: ${detail} Review operation ${operation.id} in Activity before trusting or repeating the scan.`);
    }
    if (operation.status !== "succeeded") {
      throw new ApiError(409, operation.error?.message ?? operation.error?.detail ?? `Hardware scan ended with status ${operation.status}.`);
    }
    const snapshotId = operation.result?.snapshot_id;
    if (!snapshotId) {
      throw new ApiError(409, "Hardware scan completed without identifying its exact snapshot.");
    }
    if (operation.resource && (operation.resource.type !== "hardware_snapshot" || operation.resource.id !== snapshotId)) {
      throw new ApiError(409, "Hardware scan returned inconsistent snapshot identities.");
    }
    return this.request<HardwareSnapshot>(`/hardware/snapshots/${encodeURIComponent(snapshotId)}`);
  }

  async latestHardwareSnapshot(): Promise<HardwareSnapshot | null> {
    if (demoMode) return demoSnapshot;
    try {
      return await this.request<HardwareSnapshot>("/hardware/snapshots/latest");
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  }

  async topologyExpectation(): Promise<TopologyExpectationStatus> {
    return this.request<TopologyExpectationStatus>("/hardware/topology/expectation");
  }

  async saveTopologyExpectation(snapshotId: string, name: string): Promise<TopologyExpectationDocument> {
    const result = await this.request<{ expectation: TopologyExpectationDocument }>("/hardware/topology/expectations", {
      method: "POST",
      body: JSON.stringify({ snapshot_id: snapshotId, name, confirmation: "SAVE" }),
    });
    return result.expectation;
  }

  async removeTopologyExpectation(expectationId: string): Promise<void> {
    await this.request(`/hardware/topology/expectations/${encodeURIComponent(expectationId)}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: "REMOVE" }),
    });
  }

  async topologyPlanTemplates(): Promise<TopologyPlanTemplate[]> {
    const result = await this.request<{ items: TopologyPlanTemplate[] }>("/hardware/topology/plan-templates");
    return result.items;
  }

  async topologyPlans(): Promise<TopologyPlanDocument[]> {
    const result = await this.request<{ items: TopologyPlanDocument[] }>("/hardware/topology/plans");
    return result.items;
  }

  async createTopologyPlan(name: string, templateId: TopologyPlanTemplate["id"]): Promise<TopologyPlanDocument> {
    const result = await this.request<{ plan: TopologyPlanDocument }>("/hardware/topology/plans", {
      method: "POST",
      body: JSON.stringify({ name, template_id: templateId }),
    });
    return result.plan;
  }

  async updateTopologyPlan(plan: TopologyPlanDocument): Promise<TopologyPlanDocument> {
    const result = await this.request<{ plan: TopologyPlanDocument }>(`/hardware/topology/plans/${encodeURIComponent(plan.id)}`, {
      method: "PUT",
      body: JSON.stringify({ revision: plan.revision, name: plan.name, plan: plan.plan }),
    });
    return result.plan;
  }

  async removeTopologyPlan(planId: string): Promise<void> {
    await this.request(`/hardware/topology/plans/${encodeURIComponent(planId)}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: "REMOVE" }),
    });
  }

  async locateDrive(deviceId: string, enabled: boolean, durationSeconds = 300): Promise<{ operation: OperationDocument; automatic_clear: OperationDocument | null }> {
    return this.request<{ operation: OperationDocument; automatic_clear: OperationDocument | null }>("/hardware/locate", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ device_id: deviceId, enabled, duration_seconds: durationSeconds }),
    });
  }

  async startWizard(mode: WizardMode, snapshotId: string): Promise<WizardDocument> {
    if (demoMode) return demoWizard(mode);
    return this.request<WizardDocument>("/wizards", {
      method: "POST",
      body: JSON.stringify({ workflow: "storage_setup", mode, hardware_snapshot_id: snapshotId }),
    });
  }

  async listWizards(): Promise<WizardDocument[]> {
    if (demoMode) return [];
    const result = await this.request<{ items: WizardDocument[] }>("/wizards");
    return result.items;
  }

  async readWizard(wizardId: string): Promise<WizardDocument> {
    if (demoMode) return demoWizard("guided");
    return this.request<WizardDocument>(`/wizards/${encodeURIComponent(wizardId)}`);
  }

  async saveWizardStep(wizard: WizardDocument, step: string, answers: Record<string, unknown>): Promise<WizardDocument> {
    if (demoMode) {
      this.demoRevision += 1;
      return { ...wizard, revision: this.demoRevision, current_step: step, answers: { ...wizard.answers, [step]: answers } };
    }
    return this.request<WizardDocument>(`/wizards/${wizard.id}/steps/${encodeURIComponent(step)}`, {
      method: "PUT",
      body: JSON.stringify({ revision: wizard.revision, answers }),
    });
  }

  async createPlan(wizard: WizardDocument): Promise<PlanDocument> {
    if (demoMode) return demoPlan(wizard);
    const result = await this.request<{ plan: PlanDocument }>(`/wizards/${wizard.id}/plan`, {
      method: "POST",
      body: JSON.stringify({ revision: wizard.revision }),
    });
    return result.plan;
  }

  async readPlan(wizardId: string): Promise<PlanDocument> {
    return this.request<PlanDocument>(`/wizards/${encodeURIComponent(wizardId)}/plan`);
  }

  async completeWizard(wizardId: string): Promise<WizardDocument> {
    return this.request<WizardDocument>(`/wizards/${encodeURIComponent(wizardId)}/complete`, {
      method: "POST",
    });
  }

  async refreshPlan(wizard: WizardDocument): Promise<{ wizard: WizardDocument; plan: PlanDocument; hardware_snapshot: HardwareSnapshot }> {
    if (demoMode) return { wizard, plan: await demoPlan(wizard), hardware_snapshot: demoSnapshot };
    return this.request<{ wizard: WizardDocument; plan: PlanDocument; hardware_snapshot: HardwareSnapshot }>(`/wizards/${wizard.id}/plan/refresh`, {
      method: "POST",
      body: JSON.stringify({ revision: wizard.revision }),
    });
  }

  async recordConsent(wizard: WizardDocument, plan: PlanDocument, snapshotSha256: string, acknowledgement: string, driveIds: string[]): Promise<void> {
    if (demoMode) return;
    await this.request(`/wizards/${wizard.id}/plan/approve`, {
      method: "POST",
      body: JSON.stringify({
        revision: wizard.revision,
        plan_sha256: plan.sha256,
        hardware_snapshot_sha256: snapshotSha256,
        selected_device_ids: driveIds,
        confirmation: acknowledgement,
      }),
    });
  }

  async startStorageApply(wizard: WizardDocument): Promise<OperationDocument> {
    if (demoMode) {
      return { id: "demo-storage-apply", kind: "storage.apply", status: "succeeded" };
    }
    const result = await this.request<{ operation: OperationDocument }>(
      `/wizards/${encodeURIComponent(wizard.id)}/apply`,
      { method: "POST", headers: { "Idempotency-Key": createIdempotencyKey() } },
    );
    return result.operation;
  }

  async operation(operationId: string): Promise<OperationDocument> {
    return this.request<OperationDocument>(`/operations/${encodeURIComponent(operationId)}`);
  }

  async listOperations(): Promise<OperationDocument[]> {
    const result = await this.request<{ items: OperationDocument[] }>("/operations");
    return result.items;
  }

  async operationEvents(operationId: string): Promise<OperationEvent[]> {
    const result = await this.request<{ items: OperationEvent[] }>(
      `/operations/${encodeURIComponent(operationId)}/events`,
    );
    return result.items;
  }

  async storageOperationProgress(operationId: string): Promise<StorageOperationProgress> {
    return this.request<StorageOperationProgress>(
      `/operations/${encodeURIComponent(operationId)}/progress`,
    );
  }

  async pauseOperation(operationId: string): Promise<OperationDocument> {
    return this.request<OperationDocument>(
      `/operations/${encodeURIComponent(operationId)}/pause`,
      { method: "POST" },
    );
  }

  async resumeOperation(operationId: string): Promise<OperationDocument> {
    return this.request<OperationDocument>(
      `/operations/${encodeURIComponent(operationId)}/resume`,
      { method: "POST" },
    );
  }

  async cancelWizard(wizard: WizardDocument): Promise<void> {
    if (demoMode) return;
    await this.request(`/wizards/${wizard.id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ revision: wizard.revision }),
    });
  }

  async updateStatus(): Promise<UpdateStatusDocument> {
    return this.request<UpdateStatusDocument>("/updates/status");
  }

  async checkUpdates(): Promise<UpdateCheckDocument> {
    return this.request<UpdateCheckDocument>("/updates/check", { method: "POST" });
  }

  async applyUpdate(metadataSha256: string): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>("/updates/apply", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ metadata_sha256: metadataSha256, confirmation: "APPLY" }),
    });
    return result.operation;
  }

  async addons(): Promise<AddonDocument[]> {
    const result = await this.request<{ items: AddonDocument[] }>("/addons");
    return result.items;
  }

  async integrations(): Promise<IntegrationDocument[]> {
    const result = await this.request<{ items: IntegrationDocument[] }>("/integrations");
    return result.items;
  }

  async createIntegration(input: {
    name: string;
    product: IntegrationProduct;
    base_url: string;
    api_key: string;
    verify_tls: boolean;
    allow_localhost: boolean;
  }): Promise<{ integration: IntegrationDocument; operation: OperationDocument }> {
    return this.request("/integrations", {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify(input),
    });
  }

  async refreshIntegration(id: string): Promise<OperationDocument> {
    const result = await this.request<{ operation: OperationDocument }>(`/integrations/${encodeURIComponent(id)}/refresh`, {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
    });
    return result.operation;
  }

  async changeAddon(id: string, action: "enable" | "disable" | "remove"): Promise<void> {
    await this.request(`/addons/${encodeURIComponent(id)}/lifecycle`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
  }
}

export const api = new HoardarrApi();
