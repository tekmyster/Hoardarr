export type WizardMode = "guided" | "advanced";
export type NetworkMode = "single" | "active_passive" | "lacp";
export type DaylightSavingMode = "automatic" | "standard_time";
export type StorageRole =
  | "individual"
  | "mergerfs"
  | "download-cache"
  | "block"
  | "import"
  | "test"
  | "zfs"
  | "raid"
  | "snapraid"
  | "mixed";

export interface SetupStatus {
  configured: boolean;
  claim_available: boolean;
}

export interface MergerFsInstance {
  id: string;
  name: string;
  mountpoint: string;
  source: string;
  branches: string[];
  options: string[];
  active: boolean;
  configured: boolean;
}

export interface MergerFsInventory {
  available: boolean;
  status: "configured" | "available_not_configured" | "unavailable";
  items: MergerFsInstance[];
}

export interface StorageInventory {
  captured_from: "live_host";
  topology: StorageTopology;
  active_operations: Array<{
    operation_id: string;
    status: "queued" | "running";
    selected_device_ids: string[];
    created_at: string;
    updated_at: string;
  }>;
  pools: {
    status: "configured" | "not_configured";
    items: Array<{
      id: string;
      name: string;
      type: string;
      status: string;
      total_bytes: number | null;
      used_bytes: number | null;
      free_bytes: number | null;
      members: number | null;
      mountpoint: string | null;
      branches?: string[];
      device_names?: string[];
      pool_guid?: string | null;
      degraded?: boolean;
      maintenance?: string;
      progress_percent?: number | "Not reported";
      parity_fresh?: boolean | "Not reported";
      unsynced_items?: number | "Not reported";
      bad_blocks?: number | "Not reported";
      last_sync?: string;
      configuration?: {
        quality?: "available" | "temporarily_unavailable" | "unsupported";
        data_disks?: Array<{ name: string; path: string }>;
        parity_disks?: Array<{ level: number; path: string }>;
        content_files?: string[];
        config_sha256: string | null;
        errors?: string[];
        vdev_type?: "mirror" | "raidz1" | "raidz2" | "raidz3" | null;
        member_paths?: string[];
        member_capacities?: Record<string, number>;
        array_path?: string;
        array_uuid?: string | null;
        level?: "raid1" | "raid5" | "raid6" | "raid10";
        raid_disks?: number;
      };
    }>;
  };
  shares: {
    status: "configured" | "not_configured";
    items: Array<{ id: string; name: string; protocol: string; path: string | null }>;
  };
  controllers: {
    status: string;
    items: Array<{
      id: string | number;
      provider: string;
      model: string;
      serial?: string;
      health: string;
      drives?: Array<Record<string, unknown>>;
    }>;
    unavailable: Array<{ provider: string; status: string }>;
  };
  enclosures: {
    status: string;
    items: Array<{
      id: string;
      provider: string;
      descriptor: string;
      health: string;
      path: string;
      slots: Array<{ slot: string | number; status: string; identify: boolean | "Not reported"; fault: boolean | "Not reported" }>;
      temperature_c: number | "Not reported";
      fan_rpm: number | "Not reported";
      fan_count: number | "Not reported";
      power_supplies: string[] | "Not reported";
      voltages: number[] | "Not reported";
      locate: boolean;
      fault: boolean;
      expanders: string[] | "Not reported";
    }>;
    unavailable: Array<{ provider: string; path?: string; status: string }>;
  };
}

export type StorageTopologyProtocol = "SAS" | "SATA" | "FC" | "FCoE" | "NVMe" | "USB" | "SCSI" | "Logical";

export interface StorageTopologyNode {
  id: string;
  kind: "controller" | "sas_host" | "port" | "phy" | "expander" | "path" | "enclosure" | "drive" | "pool" | "filesystem" | "share";
  label: string;
  address?: string | null;
  bus?: string | null;
  driver?: string | null;
  protocol?: StorageTopologyProtocol;
  status?: string;
  vendor?: string | null;
  model?: string | null;
  serial?: string;
  stable_identity?: string;
  path?: string | null;
  slot?: string | null;
  mapping_source?: string | null;
  mapping_confidence?: "high" | "medium" | "low" | "unknown";
  mapping_last_confirmed_at?: string | null;
  controller_id?: string | null;
  enclosure_id?: string | null;
  pool_type?: string | null;
  filesystem_type?: string | null;
  path_components?: string[];
  capacity_bytes?: number;
  used_bytes?: number | null;
  usable_bytes?: number | null;
  health_status?: "healthy" | "warning" | "critical" | "unknown";
  smart_available?: boolean;
  temperature_c?: number | null;
  capable_speed_gbps?: number | null;
  negotiated_speed_gbps?: number | null;
  minimum_speed_gbps?: number | null;
  sas_address?: string | null;
  phy_identifier?: string | null;
  invalid_dwords?: number | null;
  disparity_errors?: number | null;
  loss_of_sync?: number | null;
  reset_problems?: number | null;
  target_port_identifier?: string | null;
  target_port_identifier_type?: string | null;
  identity_evidence_quality?: string | null;
  identity_evidence_source?: string | null;
  identity_evidence_conflict?: boolean;
  smp_quality?: string | null;
  smp_source?: string | null;
  smp_phy_count?: number;
  smp_attached_phy_count?: number;
  smp_phys?: Array<{
    phy_id: number;
    routing: string | null;
    state: string | null;
    negotiated_rate_gbps: number | null;
    attached_sas_address: string | null;
    attached_phy_id: number | null;
    attached_details: string | null;
    device_slot_number: number | null;
  }>;
  system_disk?: boolean;
}

export interface StorageTopologyLink {
  id: string;
  source: string;
  target: string;
  protocol: StorageTopologyProtocol;
  capable_speed_gbps: number | null;
  negotiated_speed_gbps: number | null;
}

export interface StorageEnclosure {
  id: string;
  label: string;
  vendor: string | null;
  model: string | null;
  address: string;
  status: string;
  protocols: StorageTopologyProtocol[];
  controller_ids: string[];
  bays: Array<{
    slot: string | null;
    drive_id: string | null;
    status?: string | null;
    locate?: boolean | null;
    fault?: boolean | null;
    mapping_source?: string | null;
    mapping_confidence?: "high" | "medium" | "low" | "unknown";
    mapping_last_confirmed_at?: string | null;
  }>;
}

export interface StorageTopology {
  status: "available" | "not_available";
  nodes: StorageTopologyNode[];
  links: StorageTopologyLink[];
  enclosures: StorageEnclosure[];
  direct_attached_drive_ids: string[];
}

export interface TopologyExpectationDocument {
  id: string;
  name: string;
  source_snapshot_id: string;
  expected: {
    schema_version: number;
    source_snapshot_id: string;
    source_snapshot_sha256: string;
    nodes: Array<Record<string, unknown>>;
  };
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TopologyDriftEventDocument {
  id: string;
  expectation_id: string;
  snapshot_id: string;
  kind: string;
  severity: "critical" | "warning" | "info";
  entity_type: string;
  entity_id: string;
  message: string;
  expected: Record<string, unknown>;
  observed: Record<string, unknown>;
  state: "active" | "resolved";
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
}

export interface TopologyExpectationStatus {
  expectation: TopologyExpectationDocument | null;
  active_drifts: TopologyDriftEventDocument[];
  recent_events: TopologyDriftEventDocument[];
}

export interface TopologyPlanTemplate {
  id: "generic-8-bay" | "generic-12-bay" | "generic-24-bay-shelf" | "generic-dual-path-shelf";
  name: string;
  description: string;
  controller_count: number;
  enclosures: Array<{ id: string; label: string; bay_count: number }>;
}

export interface TopologyPlanChange {
  id: string;
  kind: "disk_addition" | "disk_retirement";
  label: string;
  enclosure_id: string | null;
  slot: number | null;
  capacity_bytes: number | null;
  stable_device_id: string | null;
}

export interface TopologyPlanDocument {
  id: string;
  name: string;
  template_id: TopologyPlanTemplate["id"];
  revision: number;
  plan: {
    schema_version: 1;
    chassis: { id: string; label: string };
    controllers: Array<{ id: string; label: string; state: "existing" | "planned" }>;
    enclosures: Array<{ id: string; label: string; bay_count: number; controller_ids: string[] }>;
    changes: TopologyPlanChange[];
    notes: string;
  };
  created_at: string;
  updated_at: string;
}

export interface ApiKeyDocument {
  id: string;
  name: string;
  scopes: Array<"read" | "operate" | "admin">;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface MediaAccountProvisionResult {
  account: {
    username: string;
    created: boolean;
    password_updated: boolean;
    smb_enabled: boolean;
    shell_login: false;
  };
  credential: {
    generated: boolean;
    password: string | null;
    display_once: boolean;
  };
}

export interface OperationDocument {
  id: string;
  kind: string;
  status: "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled" | "needs_attention";
  resource?: { type: string; id: string } | null;
  result?: { snapshot_id?: string; [key: string]: unknown } | null;
  error?: { code?: string; message?: string; detail?: string } | null;
  not_before?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface HAEventDocument {
  id: string;
  event_type: string;
  cause: string | null;
  previous_owner_node_id: string | null;
  resulting_owner_node_id: string | null;
  detail: Record<string, unknown>;
  occurred_at: string;
}

export interface HAStatusDocument {
  configured: boolean;
  maturity_level: "HA-2" | "HA-3";
  mode: "controlled_single_writer" | null;
  local?: { node_id: string; name: string; fqdn: string; ip: string; role: "active" | "passive" };
  peer: { node_id: string; name: string; fqdn: string; ip: string; role: "active" | "passive"; reachable: boolean; state: "healthy" | "stale" | "unavailable"; last_seen_at: string | null } | null;
  service_ip?: string | null;
  current_owner_node_id?: string | null;
  synchronization_state?: "not_configured" | "in_sync" | "synchronizing" | "stale" | "unavailable";
  failover_readiness?: "ready" | "not_ready" | "unknown";
  storage_ownership?: "serving" | "standby" | "unavailable" | "not_reported";
  automatic_failover?: boolean;
  fencing_configured?: boolean;
  updated_at?: string;
  events: HAEventDocument[];
}

export interface HAConfigurationInput {
  local_node_id: string;
  local_name: string;
  local_fqdn: string;
  local_ip: string;
  local_role: "active" | "passive";
  peer_node_id: string;
  peer_name: string;
  peer_fqdn: string;
  peer_ip: string;
  peer_role: "active" | "passive";
  service_ip?: string | null;
}

export type ConnectivityProtocol = "smb" | "nfs" | "iscsi" | "fcoe";

export interface ConnectivityCapabilities {
  service_available: boolean;
  protocols: Record<ConnectivityProtocol, { available: boolean; installed?: boolean; online?: boolean }>;
  tools: Record<string, boolean>;
  modules?: Record<string, boolean>;
  fcoe_interfaces_detected?: boolean;
  fcoe_interfaces?: Array<{
    name: string;
    driver: string;
    mac: string;
    state: string;
    speed_mbps: number | null;
    target_wwpn: string;
    dcb_owner: "host" | "firmware";
    online: boolean;
  }>;
}

export interface ConnectivityServiceDocument {
  id: string;
  protocol: ConnectivityProtocol;
  name: string;
  config: Record<string, unknown>;
  status: "pending" | "active" | "error" | "removing";
  state: Record<string, unknown>;
  error: { code?: string; message?: string } | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectivityServiceInput {
  protocol: ConnectivityProtocol;
  name: string;
  path?: string;
  read_only?: boolean;
  browseable?: boolean;
  valid_users?: string[];
  write_users?: string[];
  read_users?: string[];
  acl_entries?: Array<{
    kind: "user" | "group";
    name: string;
    role: "administrator" | "media_application" | "media_user";
  }>;
  inherit_acl?: boolean;
  clients?: string[];
  backing_path?: string;
  size_bytes?: number;
  target_iqn?: string;
  portal_ips?: string[];
  initiator_iqns?: string[];
  chap_enabled?: boolean;
  chap_username?: string;
  chap_password?: string;
  generate_chap_password?: boolean;
  interfaces?: string[];
  fcoe_mode?: "fabric" | "vn2vn";
  dcb_mode?: "auto" | "host" | "firmware" | "none";
  auto_vlan?: boolean;
  fip_responder?: boolean;
  initiator_wwpns?: string[];
}

export interface OperationEvent {
  sequence: number;
  type: string;
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface StorageOperationProgress {
  operation_id: string;
  state: string;
  phase: string;
  completed_steps: number;
  total_steps: number;
  percent: number;
  completed_actions: string[];
  notices: Array<{ action_id?: string; device_id?: string; code: string; message: string }>;
  action_results?: Array<{
    action_id: string;
    device_id?: string;
    outcome: "passed" | "failed" | "skipped";
    code: string;
    message: string;
    test_kind?: "short" | "extended";
    started_at?: number;
    finished_at?: number;
  }>;
  current_action: {
    id?: string;
    type?: string;
    number?: number;
    count?: number;
    progress?: {
      kind: string;
      device: string;
      test_kind?: "short" | "extended";
      state?: string;
      processed_bytes?: number;
      total_bytes?: number;
      percent: number;
      elapsed_seconds: number;
      bytes_per_second?: number;
      estimated_seconds_remaining: number | null;
      expected_finish_at?: number | null;
    };
  } | null;
  estimate: {
    scope: string;
    estimated_seconds_remaining: number;
    estimated_completion_at: number;
    remaining_bytes: number | null;
  } | null;
  updated_at: number | null;
  files?: { total: number; copied: number; verified: number; reused?: number };
  bytes?: { total: number; copied: number };
  report?: Record<string, unknown> | null;
  sanitization_report?: Record<string, unknown> | null;
}

export interface NetworkInterface {
  id: string;
  name: string;
  mac: string;
  speed_mbps: number | null;
  link: "up" | "down" | "unknown";
  driver: string | null;
  model: string | null;
  warnings?: string[];
  selected?: boolean;
}

export interface MetricProvenance {
  source: string;
  capturedAt: string;
  transport: string;
  confidence: "high" | "medium" | "low" | "unreliable";
  detail?: string;
}

export interface DriveMetric {
  name: string;
  label: string;
  value: string | number | null;
  unit?: string;
  available: boolean;
  provenance: MetricProvenance;
}

export interface DriveObservation {
  name: string;
  label: string;
  value: string | number | null;
  unit?: string;
  qualifiesAsLifetime: boolean;
  reason?: string;
  provenance: MetricProvenance;
}

export interface DriveTestResult {
  id: string;
  label: string;
  status: "passed" | "failed" | "unavailable" | "not-run";
  summary: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface DrivePartition {
  kernelName: string | null;
  path: string | null;
  startBytes: number | null;
  sizeBytes: number | null;
  filesystem: string | null;
}

export interface SignatureScan {
  status: "complete" | "partial" | "unavailable";
  reason: string | null;
  source: string | null;
}

export interface Drive {
  id: string;
  path: string;
  model: string;
  vendor: string;
  serial: string;
  wwn: string | null;
  capacityBytes: number;
  rotational?: boolean | null;
  stableIdentity: boolean;
  readOnly: boolean;
  selectable: boolean;
  selectionBlockers: string[];
  connection: {
    bus: string;
    transport: string;
    bridge?: string;
  };
  sector: {
    logical: number | null;
    physical: number | null;
  };
  signatures: string[];
  partitions: DrivePartition[];
  signatureScan: SignatureScan;
  location: string;
  removable: boolean;
  alternatePaths?: string[];
  healthStatus: "healthy" | "warning" | "critical" | "unknown";
  metrics: DriveMetric[];
  observations: DriveObservation[];
  tests: DriveTestResult[];
  smartSelfTest?: {
    status: "available" | "unsupported" | "not_reported";
    shortMinutes: number | null;
    extendedMinutes: number | null;
    source: string;
  };
  maintenanceCapabilities?: {
    source: string;
    ataSecureErase: boolean;
    nvmeBlockErase: boolean;
    nvmeCryptoErase: boolean;
    scsiBlockErase: boolean;
    scsiCryptoErase: boolean;
  };
}

export interface HardwareSnapshot {
  id: string;
  captured_at: string;
  sha256: string;
  hardware: unknown;
}

export interface DeviceMaintenancePlan {
  schema_version: 1;
  action: "wipe" | "sector_conversion";
  options: Record<string, unknown>;
  device: Record<string, unknown>;
  device_binding_sha256: string;
  hardware_snapshot_sha256: string;
  destructive: true;
  advanced_only: boolean;
}

export interface SnapraidReplacementPlan {
  schema_version: 1;
  kind: "snapraid_replacement";
  pool_name: string;
  data_name: string;
  old_path: string;
  replacement_mount: string;
  filesystem: "ext4" | "xfs" | "btrfs";
  config_sha256: string;
  device: {
    id: string;
    stable_identity: boolean;
    vendor: string | null;
    model: string | null;
    serial: string | null;
    wwn: string | null;
    eui64: string | null;
    nguid: string | null;
    capacity_bytes: number;
    logical_sector_bytes: number | null;
    physical_sector_bytes: number | null;
  };
  device_binding_sha256: string;
  hardware_snapshot_sha256: string;
  existing_data: {
    detected: boolean;
    partition_count: number;
    signature_types: string[];
    scan_status: "complete" | "partial" | "unavailable";
  };
  destructive: true;
}

export interface ArrayReplacementPlan {
  schema_version: 1;
  kind: "array_replacement";
  provider: "zfs" | "linux_md";
  target_id: string;
  target_name: string;
  target_identity: string;
  configuration_sha256: string;
  level: string;
  member_count: number;
  degraded: boolean;
  old_member_path: string | null;
  minimum_capacity_bytes: number | null;
  device: SnapraidReplacementPlan["device"];
  device_binding_sha256: string;
  hardware_snapshot_sha256: string;
  existing_data: SnapraidReplacementPlan["existing_data"];
  destructive: true;
}

export interface StorageControllerPathDocument {
  id: string;
  stable_path_identity: string;
  kernel_path: string;
  protocol: string;
  state: string;
  active: boolean;
  optimized: boolean | null;
  controller: {
    id: string;
    stable_identity: string;
    model: string | null;
    provider?: string;
    state?: Record<string, unknown>;
  } | null;
  metadata?: Record<string, unknown>;
}

export interface StorageRedundancySettings {
  mode: "recommended" | "custom";
  path_grouping_policy: "failover" | "group_by_prio" | "multibus";
  path_selector: "service-time 0" | "round-robin 0" | "queue-length 0";
  failback: "immediate" | "manual" | "followover";
  no_path_retry: "fail" | "queue" | "queue_30";
  polling_interval_seconds: number;
  minimum_healthy_paths: number;
  alert_on_reduced: boolean;
  alert_on_failover: boolean;
  alert_on_path_flapping: boolean;
  alert_on_total_loss: boolean;
}

export interface StorageRedundancyEventDocument {
  id: string;
  event_type: string;
  path_id: string | null;
  controller_id: string | null;
  operation_id: string | null;
  previous_state: string | null;
  resulting_state: string;
  details: Record<string, unknown>;
  occurred_at: string;
}

export interface LogicalStorageDocument {
  id: string;
  name: string;
  stable_identity: string;
  filesystem_uuid: string | null;
  mountpoint: string;
  presentation_device: string;
  topology_state: "single_path" | "fully_redundant" | "reduced_redundancy" | "failed_over" | "no_path" | string;
  capacity_bytes: number;
  node_name?: string | null;
  storage_scope?: "local" | "external_shared" | string;
  ownership_mode?: string | null;
  ownership_state?: "serving" | "standby" | "unavailable" | "restarting" | string | null;
  peer_node?: string | null;
  transition_capability?: { mode: "online_supported" | "brief_maintenance_required" | "automatic_conversion_unsupported"; message: string };
  redundancy_settings?: StorageRedundancySettings;
  redundancy_summary?: {
    healthy_paths: number;
    active_paths: number;
    failed_paths: number;
    failovers_today: number;
    last_failover: string | null;
    time_degraded_seconds: number;
  };
  paths: StorageControllerPathDocument[];
  available_paths?: Array<{
    stable_path_identity: string;
    kernel_path: string;
    controller_identity: string;
    protocol: string;
  }>;
}

export interface PhysicalDiskDocument {
  id: string;
  stable_identity: string;
  kernel_path: string | null;
  serial: string | null;
  wwn: string | null;
  vendor: string | null;
  model: string | null;
  capacity_bytes: number | null;
  media_type: string | null;
  health_state: string;
  lifecycle_state: string;
  last_seen_at: string;
}

export interface StorageGroupDocument {
  id: string;
  name: string;
  namespace_path: string;
  purpose: string;
  state: string;
  policy: Record<string, unknown>;
  backends: Array<{
    id: string;
    stable_identity: string;
    physical_disk_id: string | null;
    storage_entity_id: string | null;
    namespace_path: string | null;
    role: string;
    lifecycle_state: string;
  }>;
  events: Array<{
    id: string;
    event_type: string;
    backend_id: string | null;
    previous_state: string | null;
    resulting_state: string;
    reason: string | null;
    occurred_at: string;
  }>;
}

export interface StorageVolumeCapability {
  support: "supported" | "unsupported" | "not_reported";
  availability: "available" | "temporarily_unavailable" | "unsupported" | "not_reported";
  source: "provider_baseline" | "provider_observation";
  constraints: Record<string, unknown>;
}

export interface StorageVolumeDocument {
  id: string;
  stable_identity: string;
  name: string;
  provider: string;
  resource_type: "filesystem" | "dataset" | "zvol" | "logical_volume" | "lun";
  provider_resource_id: string;
  presentation: "file" | "block";
  parent_storage_entity_id: string | null;
  mountpoint: string | null;
  device_path: string | null;
  filesystem_type: string | null;
  filesystem_uuid: string | null;
  size_bytes: number | null;
  allocated_bytes: number | null;
  lifecycle_state: string;
  config: Record<string, unknown>;
  capabilities: Record<string, StorageVolumeCapability>;
  capabilities_detected_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface StorageVolumePlan {
  schema_version: 1;
  kind: "storage.volume.create";
  mode: "guided" | "advanced";
  name: string;
  purpose: "media" | "downloads" | "archive" | "backup" | "general" | "vm";
  provider: "zfs";
  resource_type: "dataset" | "zvol";
  provider_resource_id: string;
  presentation: "file" | "block";
  parent: {
    pool_id: string;
    pool_name: string;
    pool_guid: string;
    free_bytes_at_preview: number;
  };
  size_bytes: number | null;
  properties: Record<string, string | boolean>;
  blockers: Array<{ code: string; message: string }>;
  ready: boolean;
  explanation: string;
  plan_sha256: string;
}

export interface StorageBackendActivationPlan {
  schema_version: 1;
  kind: "storage.backend.activate";
  storage_group_id: string;
  storage_group_namespace: string;
  backend_id: string;
  stable_identity: string;
  lifecycle_state: "assigned";
  health: string;
  evidence: {
    path: string;
    filesystem_device: number;
    mount_source: string;
    exact_mount: true;
    identity_match: boolean;
    identity_basis: string;
    total_bytes: number;
    free_bytes: number;
  };
  blockers: Array<{ code: string; message: string }>;
  ready: boolean;
  plan_sha256: string;
}

export interface TierTransferPlan {
  workload: "torrent" | "usenet";
  source: string;
  destination: string;
  source_identity: string;
  destination_identity: string;
  same_filesystem: boolean;
  method: "copy" | "move" | "hardlink";
  retain_until: "seeding_complete" | "manual" | "never" | "import_complete";
  cleanup: boolean;
  required_bytes: number;
  completed_steps: string[];
  sha256: string;
}

export interface TierTransferSummary {
  queue: {
    queued_count: number;
    running_count: number;
    failed_count: number;
    queued_bytes: number;
    running_planned_bytes: number;
    retained_for_seeding_count: number;
    retained_for_seeding_bytes: number;
    observed_bytes_per_second: number | null;
    rate_sample_count: number;
    estimated_queued_seconds: number | null;
    estimate_quality: "available" | "estimated" | "not_reported";
    estimate_methodology: string;
  };
  tiers: Array<{
    storage_group_id: string;
    storage_group_name: string;
    backend_id: string;
    role: "cache" | "landing";
    path: string | null;
    quality: "available" | "not_reported" | "temporarily_unavailable";
    total_bytes: number | null;
    used_bytes: number | null;
    free_bytes: number | null;
  }>;
}

export interface StorageDrainPlan {
  schema_version: 1;
  kind: "storage.drain";
  storage_group_id: string;
  storage_group_namespace: string;
  source: {
    backend_id: string;
    stable_identity: string;
    path: string;
    filesystem_device: number;
    required_bytes: number;
    health: string;
    lifecycle_state: string;
  };
  destinations: Array<{
    backend_id: string;
    stable_identity: string;
    path: string;
    filesystem_device: number;
    free_bytes: number;
    total_bytes: number;
    health: string;
  }>;
  verification: { mode: "fast" | "accurate" | "paranoid"; full_hashes: boolean; additional_read_pass: boolean; algorithm?: "sha256" | "blake3" };
  capacity: { required_bytes: number; destination_free_bytes: number; reserve_bytes: number };
  controls: {
    enforce_source_read_only: boolean;
    source_read_only_capability: { supported: boolean; currently_read_only: boolean | null; reason: string };
    bandwidth_limit_mib_per_second: number | null;
    io_priority?: "normal" | "background" | "idle";
    start_at: string | null;
    maintenance_window_minutes: number | null;
    maintenance_window_end: string | null;
  };
  blockers: Array<{ code: string; message: string }>;
  warnings: Array<{ code: string; message: string }>;
  ready: boolean;
  phases: string[];
  plan_sha256: string;
}

export interface StorageExpansionDisk {
  id: string;
  stable_identity: string;
  kernel_path: string | null;
  vendor: string | null;
  model: string | null;
  capacity_bytes: number | null;
  media_type: string;
  health: string;
  existing_data: { state: "detected" | "none_detected" | "unknown"; detail: string };
  eligible: boolean;
  blockers: string[];
  warnings: string[];
}

export interface StorageExpansionTarget {
  provider: "mergerfs" | "zfs";
  instance_id: string;
  mountpoint: string;
}

export interface ForeignStorageAssessment {
  snapshot: { id: string; captured_at: string; sha256: string };
  policy: {
    default_access: "read_only";
    automatic_mount: false;
    automatic_assembly: false;
    mutation_performed: false;
  };
  unraid_evidence: UnraidEvidenceSummary | null;
  nas_evidence: NASEvidenceSummary | null;
  migration_destinations: ForeignMigrationDestination[];
  candidates: Array<{
    id: string;
    profile: "standalone_filesystem" | "linux_md" | "lvm" | "zfs" | "unraid_unknown";
    profile_name: string;
    origin: { name: string; confidence: "unknown" | "low" | "medium" | "high"; reason: string };
    confidence: "unknown" | "low" | "medium" | "high";
    state: "ready" | "degraded-review" | "blocked";
    members: Array<{
      device_id: string;
      kernel_path: string | null;
      model: string;
      capacity_bytes: number | null;
      stable_identity: boolean;
      system_device: boolean;
      read_only: boolean;
      removable: boolean;
      connection?: { transport: string | null; protocol: string | null };
      external?: boolean;
      mounted: boolean;
      mountpoints: string[];
      signature_scan: { status: string | null; source: string | null; reason: string | null };
      confidence: "unknown" | "low" | "medium" | "high";
      signatures: Array<{
        type: string;
        usage: string | null;
        uuid: string | null;
        label: string | null;
        source: string;
      }>;
      unraid: UnraidRoleClassification | null;
      nas_origin?: NASOriginClassification | null;
    }>;
    filesystems: string[];
    signature_types: string[];
    capacity_bytes: number | null;
    health: { quality: string; state: string | null; reason: string };
    archive_intake?: {
      state: "discovered_external" | null;
      default_access: "read_only";
      reason: string;
    };
    warnings: string[];
    blockers: string[];
    modes: Array<{ id: string; available: boolean; reason: string }>;
    unraid?: UnraidRoleClassification | null;
    nas_origin?: NASOriginClassification | null;
    latest_inventory: ForeignInventoryReport | null;
    mutation_performed: false;
  }>;
  unrecognized_device_count: number;
}

export interface ForeignMigrationDestination {
  id: string;
  storage_group_id: string;
  name: string;
  path: string;
  stable_identity: string;
  lifecycle_state: "active" | "preferred_write";
  device_number: number;
  free_bytes: number;
}

export interface ForeignMigrationPlan {
  schema_version: 1 | 2;
  operation: "foreign.migrate_files";
  candidate_id: string;
  hardware_snapshot_id: string;
  hardware_snapshot_sha256: string;
  source_inventory_operation_id: string;
  source_inventory_sha256: string;
  device: ForeignInspectionPlan["device"];
  device_binding_sha256: string;
  source: ForeignInspectionPlan["source"];
  destination: ForeignMigrationDestination & {
    backend_id: string;
    free_bytes_at_preview: number;
    reserve_bytes: number;
  };
  inventory: { file_count: number; total_bytes: number };
  selection?: {
    mode: "full" | "selected_folders" | "filtered";
    include_paths: string[];
    include_extensions: string[];
    include_globs: string[];
    exclude_globs: string[];
    capacity_upper_bound_bytes: number;
    exact_selected_bytes_at_review: number | null;
  };
  verification: { mode: "fast" | "accurate"; algorithm: "size_mtime" | "blake3" };
  collision_policy: "stop" | "reuse_identical";
  source_access: "read_only";
  source_retained: true;
  parity_reuse_supported: false;
  plan_sha256: string;
}

export interface ForeignInventoryReport {
  operation_id: string;
  completed_at: string;
  hardware_snapshot_sha256: string;
  current_snapshot_match: boolean;
  filesystem: { type: string; uuid: string | null; label: string | null } | null;
  inventory: {
    file_count: number;
    directory_count?: number;
    total_bytes: number;
    largest_file: { path: string; bytes: number } | null;
    oldest_mtime_unix: number | null;
    newest_mtime_unix: number | null;
    extension_distribution: Array<{ extension: string; files: number }>;
    case_collision_count: number;
    unicode_collision_count: number;
    read_errors: Array<unknown>;
    permission_anomalies?: Record<string, number>;
    top_level_entries?: Array<{ name: string; type: "directory" | "file"; bytes?: number }>;
    truncated: boolean;
  };
  access: "read_only";
  persistent_mount: false;
  mutation_performed: false;
}

export interface UnraidRoleClassification {
  role: "data" | "parity" | "unknown";
  classification: "identified" | "suspected" | "unknown";
  slot: string | null;
  reason: string;
  evidence_sha256: string | null;
  parity_reuse_supported: false;
}

export interface UnraidEvidenceSummary {
  id: string;
  source: "unraid_runtime_state";
  document_sha256: string;
  captured_at: string;
  unraid_version: string | null;
  assignment_count: number;
  matched_assignment_count: number;
  unmatched_slots: string[];
  ambiguous_slots: string[];
}

export interface UnraidEvidenceInput {
  schema_version: 1;
  source: "unraid_runtime_state";
  captured_at: string;
  unraid_version?: string | null;
  assignments: Array<{
    slot: string;
    role: "data" | "parity";
    serial: string;
    wwn?: string | null;
    eui64?: string | null;
    nguid?: string | null;
    capacity_bytes?: number | null;
    filesystem_type?: string | null;
  }>;
}

export interface NASOriginClassification {
  platform: "synology" | "qnap" | "generic_linux_nas";
  platform_name: string;
  classification: "identified";
  members: string[];
  evidence_sha256: string;
  reason: string;
}

export interface NASEvidenceSummary {
  id: string;
  source: "nas_runtime_state";
  document_sha256: string;
  captured_at: string;
  platform: "synology" | "qnap" | "generic_linux_nas";
  platform_name: string;
  platform_marker: "synology_runtime" | "qnap_runtime" | "linux_runtime";
  product_version: string | null;
  member_count: number;
  matched_member_count: number;
  unmatched_members: string[];
  ambiguous_members: string[];
}

export interface NASEvidenceInput {
  schema_version: 1;
  source: "nas_runtime_state";
  captured_at: string;
  platform: "synology" | "qnap" | "generic_linux_nas";
  platform_marker: "synology_runtime" | "qnap_runtime" | "linux_runtime";
  product_version?: string | null;
  members: Array<{
    member: string;
    serial: string;
    wwn?: string | null;
    eui64?: string | null;
    nguid?: string | null;
    capacity_bytes?: number | null;
  }>;
}

export interface ForeignInspectionPlan {
  schema_version: 1;
  operation: "foreign.inspect_read_only";
  candidate_id: string;
  hardware_snapshot_id: string;
  hardware_snapshot_sha256: string;
  device: { id: string; model: string | null; capacity_bytes: number | null; [key: string]: unknown };
  source: {
    kind: "whole_device" | "partition";
    kernel_path_at_preview: string;
    partition_number: number | null;
    filesystem_type: string;
    filesystem_uuid: string | null;
    filesystem_label: string | null;
    signature_source: string;
    read_only_options: string[];
  };
  limits: { maximum_entries: number; maximum_extension_groups: number; maximum_errors: number };
  access: "read_only";
  persistent_mount: false;
  automatic_activation: false;
  mutation_performed: false;
  plan_sha256: string;
}

export interface ForeignStackPreviewResult {
  candidate_id: string;
  plan_sha256: string;
  provider: "linux_md" | "lvm" | "zfs";
  identity: string;
  name: string;
  layout: string;
  members: Array<Record<string, unknown>>;
  completeness: {
    quality: "available" | "not_reported";
    state: "complete" | "incomplete" | "not_reported";
    expected_members: number | null;
    observed_members: number;
    missing_members: number | null;
  };
  health: { quality: string; state: string | null; reason: string };
  mountability: { quality: string; state: string; reason: string };
  activation_performed: false;
  mutation_performed: false;
}

export interface StorageExpansionSelection {
  candidate_id: string;
  kind: string;
  storage_group_id: string | null;
  hardware_snapshot_sha256: string;
  disk_ids: string[];
  target: StorageExpansionTarget | null;
  configuration: {
    topology?: string;
    vdev_type?: string;
    vdev_width?: number;
    snapraid_role?: "data" | "parity";
    snapraid_instance_id?: string;
    snapraid_config_sha256?: string;
    zfs_pool_guid?: string;
    zfs_config_sha256?: string;
    zfs_vdev_count?: number;
    md_level?: "raid1" | "raid5" | "raid6" | "raid10";
    member_count?: number;
  };
}

export interface StorageExpansionAssessment {
  schema_version: 1;
  hardware_snapshot_id: string;
  hardware_snapshot_sha256: string;
  captured_at: string;
  storage_groups: Array<{
    id: string;
    name: string;
    namespace_path: string;
    purpose: string;
    backend_count: number;
    raw_capacity_bytes: number | null;
    capacity: {
      total_bytes: number | null;
      used_bytes: number | null;
      free_bytes: number | null;
      quality: "available" | "not_reported";
      source: string | null;
    };
    distribution: {
      reported_members: number;
      minimum_utilization_percent: number | null;
      maximum_utilization_percent: number | null;
      spread_percentage_points: number | null;
      methodology: string;
    };
    protection: { data_backends: number; parity_backends: number; summary: string };
    growth_forecast: {
      status: "available" | "stable_or_declining" | "insufficient_history" | "not_reported";
      reason: string | null;
      metric_entity_id: string | null;
      data_points?: number;
      history_days?: number;
      growth_bytes_per_day?: number | null;
      projected?: Record<string, { days: number; date: string } | null>;
      methodology?: string;
    };
    preferred_backend_id: string | null;
  }>;
  available_disks: StorageExpansionDisk[];
  reserved_disks: StorageExpansionDisk[];
  detected_capabilities: { mergerfs: boolean; snapraid: boolean; zfs: boolean; linux_md?: boolean };
  tool_availability?: { mergerfs: boolean; snapraid: boolean; zfs: boolean; linux_md?: boolean };
  candidates: Array<{
    id: string;
    kind: string;
    disk_ids: string[];
    storage_group_id: string | null;
    storage_group_name: string | null;
    title: string;
    summary: string;
    recommended: boolean;
    setup_mode: "configure" | "import" | "expand" | "cache" | "advanced";
    capacity: {
      raw_delta_bytes: number;
      estimated_usable_delta_bytes: number | null;
      methodology: string;
    };
    protection_impact: string;
    future_expansion: string;
    migration_work: string;
    restrictions: string[];
    target: StorageExpansionTarget | null;
    configuration: {
      topology?: string;
      vdev_type?: string;
      vdev_width?: number;
      snapraid_role?: "data" | "parity";
      snapraid_instance_id?: string;
      snapraid_config_sha256?: string;
      md_level?: "raid1" | "raid5" | "raid6" | "raid10";
      member_count?: number;
    };
  }>;
  methodology: string;
}

export interface StorageRedundancyPlan {
  schema_version: 1;
  operation: "redundancy.add" | "redundancy.remove" | "redundancy.replace" | "redundancy.configure";
  storage_entity_id: string;
  logical_storage_identity: string;
  hardware_snapshot_sha256: string;
  identity_binding_sha256: string;
  before: {
    path_ids: string[];
    presentation_device: string;
    mountpoint: string;
    device_mountpoint: string;
    filesystem_uuid: string | null;
  };
  after: {
    path_ids: string[];
    presentation_device: string;
    mountpoint: string;
    filesystem_uuid: string | null;
    topology_state: string;
  };
  selected_path: {
    stable_path_identity: string;
    kernel_path: string;
    controller_identity?: string;
    protocol?: string;
  };
  removed_path?: {
    stable_path_identity: string;
    kernel_path: string;
  } | null;
  policy: "recommended" | "failover" | "multibus" | "group_by_prio";
  settings: StorageRedundancySettings;
  transition: { mode: "online_supported" | "brief_maintenance_required" | "automatic_conversion_unsupported"; message: string };
  managed_access_services?: Array<{ id: string; protocol: "smb" | "nfs"; name: string; path: string }>;
  destructive: false;
  format: false;
  copy_data: false;
  preserves: string[];
  plan_sha256: string;
}

export interface WizardDocument {
  id: string;
  workflow?: string;
  workflow_version?: number;
  revision: number;
  mode: WizardMode;
  status: string;
  current_step: string;
  hardware_snapshot_id: string | null;
  answers: Record<string, unknown>;
  plan_id: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface PlanDocument {
  id: string;
  sha256: string;
  revision: number;
  document: {
    apply_available?: boolean;
    blockers?: Array<{ code: string; message: string }>;
    actions?: Record<string, unknown[]>;
    summary?: Record<string, number>;
    storage?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface OnboardingDefaults {
  version: number;
  steps: string[];
  defaults: {
    experience: WizardMode;
    server: {
      hostname: string;
      timezone: string;
      dst_mode: DaylightSavingMode;
    };
    network: {
      mode: NetworkMode | "bridge";
      interface_ids: string[];
      addressing: "dhcp" | "static";
      addresses: string[];
      gateway: string | null;
      dns_servers: string[];
      vlan_id: number | null;
      mtu: number;
      bridge: { enabled: boolean; stp: boolean; prefer_rstp: boolean };
    };
    ntp: { servers: string[] };
    discovery: {
      lldp: { enabled: boolean; mode: "rx_tx" | "receive_only" };
      cdp: { receive: boolean; smart_transmit: boolean };
    };
  };
  apply_available: boolean;
}

export interface NetworkPlanResponse {
  plan: {
    apply_available: boolean;
    changed_components?: string[];
    blockers: Array<{ code: string; message: string }>;
    warnings: Array<{ code: string; message: string }>;
    [key: string]: unknown;
  };
  sha256: string;
}

export interface ManagedNetworkStatus {
  configuration: Record<string, unknown> | null;
  pending_confirmation: boolean;
  capabilities: { available: boolean; tools: Record<string, boolean> };
  interfaces: Array<Record<string, unknown>>;
  current: {
    hostname: string;
    timezone: string;
    addresses: Record<string, string[]>;
    default_interface: string | null;
    default_gateway: string | null;
  };
}

export interface ManagedNetworkApplyResult {
  state: "pending_confirmation";
  token: string;
  confirm_within_seconds: number;
  changed_components: string[];
}

export interface ApiProblemBody {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  message?: string;
  code?: string;
  errors?: Array<Record<string, unknown>>;
}

export interface LibraryChoice {
  id: string;
  label: string;
  contentType: string;
  app: string;
  selected: boolean;
  source: "recommended" | "detected" | "user";
}

export interface ProcessorUsage {
  used_percent: number | null;
  logical_processors: number | null;
  physical_cores: number | null;
}

export interface MemoryUsage {
  total_bytes: number | null;
  available_bytes: number | null;
  used_bytes: number | null;
  used_percent: number | null;
}

export interface VolumeUsage {
  mountpoint: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
}

export interface ResourceUsageDocument {
  captured_at: string;
  source: "live";
  cpu: ProcessorUsage;
  memory: MemoryUsage;
  network: {
    interfaces: Array<{
      name: string;
      up: boolean | null;
      bytes_received: number | null;
      bytes_sent: number | null;
    }>;
  };
  storage: {
    system_volume: VolumeUsage | null;
      performance: StorageTelemetryDocument | null;
  };
}

export interface StoragePerformanceMetrics {
  read_bytes_per_second: number | null;
  write_bytes_per_second: number | null;
  read_iops: number | null;
  write_iops: number | null;
  read_wait_ms: number | null;
  write_wait_ms: number | null;
  utilization_percent: number | null;
}

export interface StorageTelemetryDocument {
  captured_at: string;
  source: "linux_block_counters";
  summary: StoragePerformanceMetrics & {
    writes_today_bytes: number;
    sample_seconds: number | null;
  };
  drives: Array<{
    id: string;
    device: string;
    device_name: string;
    model: string;
    serial: string | null;
    rotational: boolean | null;
    system_disk: boolean;
    pool_ids: string[];
    metrics: StoragePerformanceMetrics;
    writes_today_bytes: number;
    os_write_bytes_since_boot: number;
    endurance: {
      lifetime_writes_bytes: number | null;
      remaining_percent: number | null;
      source: string | null;
    };
  }>;
  pools: Array<{
    id: string;
    name: string;
    type: string;
    writes_today_bytes: number | null;
    metrics: StoragePerformanceMetrics | null;
    device_names: string[];
    status: "available" | "not_reported";
  }>;
}

export interface OverviewDocument {
  captured_at: string;
  source: "live";
  system: {
    hostname: string | null;
    application: string;
    version: string;
    database_ready: boolean;
    booted_at: string | null;
    uptime_seconds: number | null;
    cpu: ProcessorUsage;
    memory: MemoryUsage;
    boot_volume: VolumeUsage | null;
    temperatures: Array<{
      source: string;
      label: string;
      current_c: number | null;
      high_c: number | null;
      critical_c: number | null;
    }>;
  };
  storage: {
    snapshot: { id: string; captured_at: string; source: string } | null;
    drive_count: number | null;
    raw_capacity_bytes: number | null;
    health: { healthy: number; warning: number; critical: number; unknown: number } | null;
    pools: { status: string; items: Array<Record<string, unknown>> };
    shares: { status: string; items: Array<Record<string, unknown>> };
  };
  network: {
    interfaces: Array<{
      name: string;
      up: boolean | null;
      speed_mbps: number | null;
      mtu: number | null;
      bytes_received: number | null;
      bytes_sent: number | null;
      errors_received: number | null;
      errors_sent: number | null;
      drops_received: number | null;
      drops_sent: number | null;
    }>;
    discovery: {
      status: "available" | "no_neighbors" | "tool_unavailable" | "unavailable";
      source: string | null;
      captured_at: string;
      detail: string | null;
      neighbors: Array<{
        local_interface: string;
        protocol: string;
        protocol_variant: string;
        device_name: string | null;
        chassis_id: string | null;
        port_id: string | null;
        port_description: string | null;
        management_addresses: string[];
        system_description: string | null;
        age: string | null;
        ttl_seconds: number | null;
      }>;
    };
  };
  activity: {
    operations: Array<{
      id: string;
      kind: string;
      status: string;
      created_at: string;
      updated_at: string;
    }>;
  };
  applications: {
    connections: Array<{
      id: string;
      name: string;
      adapter: string;
      status: string;
      product_version: string | null;
      last_checked_at: string | null;
    }>;
  };
  alerts: Array<{
    severity: "critical" | "warning" | "info";
    message: string;
    source: string;
    operation_id?: string;
  }>;
}

export type MetricQuality = "available" | "not_reported" | "unsupported" | "temporarily_unavailable" | "stale" | "estimated" | "derived";

export interface MetricDefinition {
  id: string;
  name: string;
  entity_types: string[];
  unit: string;
  kind: "raw" | "derived";
  source: string;
  minimum_interval_seconds: number;
  capability: string | null;
  retention_class: string;
  aggregation: string;
  availability: string;
  formula: string | null;
  test_evidence: string;
  implementation_status?: string;
  physical_validation?: "pending" | "not_required";
  entitled: boolean;
}

export interface MetricEntity {
  id: string;
  entity_type: string;
  stable_id: string;
  display_name: string;
  labels: Record<string, string>;
  topology: Record<string, string>;
  first_seen_at: string;
  last_seen_at: string;
}

export interface MetricSampleDocument {
  metric_id: string;
  name: string;
  entity: MetricEntity;
  timestamp: string;
  value: number | string | null;
  unit: string;
  source: string;
  collection_interval_seconds: number;
  quality: MetricQuality;
  raw: boolean;
  labels: Record<string, string>;
  capability: string | null;
  error_code: string | null;
}

export interface EntitlementDocument {
  state: string;
  capabilities: string[];
  expires_at: string | null;
  license_id: string | null;
  detail: string;
  validated_at: string;
  cached: boolean;
  basic_metrics_available: boolean;
}

export interface MetricCatalogDocument {
  items: MetricDefinition[];
  quality_states: MetricQuality[];
  entitlements: EntitlementDocument;
}

export interface CurrentMetricsDocument {
  captured_at: string;
  items: MetricSampleDocument[];
  restricted_capabilities: string[];
}

export interface MetricHistoryDocument {
  entity: MetricEntity | null;
  metric_id: string;
  unit: string;
  resolution: "raw" | "hour" | "day";
  requested_resolution?: "auto" | "raw" | "hour" | "day";
  source_resolution?: "raw" | "hour" | "day";
  aggregation_method?: string;
  raw?: boolean;
  points_returned?: number;
  displayed_points?: number;
  available_points?: number;
  maximum_points?: number;
  start: string;
  end: string;
  points: Array<{
    timestamp: string;
    value: number | string | null;
    quality: MetricQuality;
    minimum?: number | null;
    maximum?: number | null;
    p50?: number | null;
    p95?: number | null;
    p99?: number | null;
    sample_count?: number;
    interval_seconds?: number;
    raw?: boolean;
    first?: number | string | null;
    last?: number | string | null;
    transition_count?: number;
    states?: string[];
  }>;
}

export interface TelemetrySettingsDocument {
  collection: { fast_interval_seconds: number; device_interval_seconds: number; hardware_interval_seconds: number };
  history: { recent_resolution_seconds: number; recent_retention_hours: number; medium_resolution_seconds: number; medium_retention_days: number; long_resolution_seconds: number; long_retention_days: number; maximum_graph_points: number; maximum_series: number; maximum_observations: number };
  storage: { database_bytes: number; oldest_raw_history: string | null; oldest_retained_history: string | null; entity_count: number; estimated_bytes_per_day: number; estimate_method: string; last_cleanup: string | null; next_cleanup: string | null; cleanup_batch_size: number };
  extended_history: { entitled: boolean; capability: string };
}

export interface FleetTelemetrySettingsDocument {
  anonymous_heartbeat: { required: true; enabled: true };
  hardware_enabled: boolean;
  enhanced_enabled: boolean;
  content_enabled: boolean;
  installation_id: string;
  endpoint: string;
  connection_status: string;
  credential_fingerprint: string | null;
  last_successful_upload: string | null;
  last_attempted_upload: string | null;
  last_error: { code?: string; message?: string } | null;
  schema_version: number;
  country_code: string | null;
  timezone: string;
  location_detection_method: "os_timezone" | "locale" | "network" | "manual";
  location_confirmed: boolean;
  queued_records: number;
  queued_bytes: number;
  dead_letter_records: number;
  by_status: Record<string, { records: number; bytes: number }>;
  limitations: string;
}

export interface FleetPendingDocument {
  schema_version: number;
  field_groups: Record<string, string>;
  items: Array<{
    id: string;
    message_type: "heartbeat" | "inventory" | "event" | "observation";
    telemetry_level: 0 | 1 | 2 | 3;
    schema_version: number;
    payload: Record<string, unknown>;
    status: "queued" | "retrying" | "dead_letter";
    attempt_count: number;
    last_error: { code?: string; message?: string } | null;
    created_at: string;
  }>;
}

export interface MetricAlertDocument {
  id: string;
  entity: MetricEntity;
  metric_id: string;
  severity: "info" | "warning" | "critical";
  state: "active" | "resolved";
  lifecycle_state: "active" | "acknowledged" | "suppressed" | "cleared";
  trigger_value: number | null;
  threshold: Record<string, unknown>;
  topology: Record<string, string>;
  details: Record<string, unknown>;
  started_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  suppressed_until: string | null;
  suppressed_by: string | null;
  suppression_reason: string | null;
  runbook: {
    id: string;
    title: string;
    summary: string;
    actions: string[];
    evidence: string[];
  } | null;
}

export interface WebhookEndpointDocument {
  id: string;
  name: string;
  url: string;
  event_types: string[];
  allow_localhost: boolean;
  verify_tls: boolean;
  enabled: boolean;
  status: "not_tested" | "testing" | "healthy" | "degraded";
  secret_configured: true;
  secret_fingerprint: string;
  last_success_at: string | null;
  last_error: { code?: string; message?: string } | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookDeliveryDocument {
  id: string;
  endpoint_id: string;
  event_id: string;
  event_type: string;
  status: "queued" | "delivering" | "retrying" | "delivered" | "failed";
  attempt_count: number;
  next_attempt_at: string;
  response_status: number | null;
  last_error: { code?: string; message?: string } | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TelemetryForecastDocument {
  status: "available" | "insufficient_history" | "stable_or_declining";
  methodology: string;
  samples?: number;
  history_days?: number;
  growth_bytes_per_day?: number | null;
  consumption_percent_per_day?: number | null;
  projected?: Record<string, { days: number; date: string }>;
  projected_exhaustion?: { days: number; date: string } | null;
  confidence?: string;
}

export interface LatencyAnalyticsDocument {
  metric_id: string;
  samples: number;
  minimum: number | null;
  maximum: number | null;
  mean: number | null;
  median: number | null;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  status: "available" | "insufficient_history";
  methodology: string;
}

export interface UpdateStatusDocument {
  current_version: string;
  latest_version: string | null;
  channel: string;
  metadata_sha256: string | null;
  last_checked_at: string | null;
  last_error: { code: string; message: string } | null;
  operation: OperationDocument | null;
}

export interface UpdateCheckDocument {
  current_version: string;
  latest_version: string;
  channel: string;
  compatible: boolean;
  blockers: Array<{ code: string; message: string }>;
  metadata_sha256: string;
  required_free_bytes: number;
}

export interface AddonDocument {
  id: string;
  name: string;
  version: string;
  state: string;
  privileges: string[];
  ui: Array<{ slot: string; module: string }>;
  last_error: { code: string; message: string } | null;
}

export type RemoteBackupProvider =
  | "aws_s3"
  | "minio"
  | "cloudflare_r2"
  | "wasabi"
  | "backblaze_b2"
  | "generic_s3";

export interface RemoteBackupTargetDocument {
  id: string;
  name: string;
  provider: RemoteBackupProvider;
  endpoint_url: string | null;
  region: string;
  bucket: string;
  prefix: string;
  force_path_style: boolean;
  verify_tls: boolean;
  allow_private_network: boolean;
  allow_insecure_http: boolean;
  bandwidth_limit_mib: number | null;
  schedule: { enabled: boolean; [key: string]: unknown };
  credential_fingerprint: string;
  status: string;
  last_tested_at: string | null;
  last_success_at: string | null;
  error: { code?: string; message?: string; [key: string]: unknown } | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface RemoteBackupRunDocument {
  id: string;
  target_id: string;
  backup_kind: "control_plane";
  object_key: string | null;
  artifact_sha256: string | null;
  artifact_size_bytes: number | null;
  status: string;
  phase: string;
  report: Record<string, unknown>;
  error: { code?: string; message?: string; detail?: string; [key: string]: unknown } | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export type IntegrationProduct = "sonarr" | "radarr" | "lidarr" | "readarr" | "whisparr" | "prowlarr" | "plex" | "jellyfin" | "emby";

export interface IntegrationDocument {
  id: string;
  name: string;
  expected_product: IntegrationProduct;
  discovered_product: string | null;
  product_version: string | null;
  base_url: string;
  status: string;
  capabilities: string[];
  state: Record<string, unknown>;
  last_checked_at: string | null;
}
