import { useEffect, useMemo, useState } from "react";
import { api, ApiError, demoMode, drivesFromSnapshot } from "./api/client";
import { AppShell, WizardFrame, type AppPage } from "./components/AppShell";
import { AuthenticationPage, type AuthenticationInput } from "./components/AuthenticationPage";
import { ActivityPage } from "./components/ActivityPage";
import { ApplicationsPage } from "./components/ApplicationsPage";
import { HealthPage } from "./components/HealthPage";
import { AnalyticsPage } from "./components/AnalyticsPage";
import { ConnectivityPage } from "./components/ConnectivityPage";
import { OverviewDashboard } from "./components/OverviewDashboard";
import { EyeIcon, OneTimePassword } from "./components/OneTimePassword";
import { SettingsPage } from "./components/SettingsPage";
import { StoragePage, type DriveAction, type SavedStorageDraft, type StorageAction } from "./components/StoragePage";
import { StorageOperationNotices, StorageProgressDetails } from "./components/StorageProgressDetails";
import { StorageWizardDialog } from "./components/StorageWizardDialog";
import { Card, ChoiceCard, Field, Notice, SourceBadge, Spinner, StatusBadge } from "./components/ui";
import { gatewayForPayload, normalizeServerNameInput, serverSettingsError, supportedTimeZones, timeZoneLabel, timeZoneOffsetLabel, timeZoneUsesDaylightSaving, uiDefaultsFromOnboarding } from "./onboarding";
import { actionDestructiveLabel, detectedFilesystems, driveMayContainData, exactConsentAccepted, existingDataSummary, filesystemRecommendation, hasKnownSectorGeometry, humanCapacity, isImportedNtfs, isUsbRaidOverride, layoutChoicesForDrive, recommendStorage, sectorGeometryAssessment, selectPortableSystem, storageChoiceNeedsSectorGeometry, storageRoleLabel, toggleNetworkInterfaceSelection, type ProtectionPreference } from "./policy";
import type {
  Drive,
  DaylightSavingMode,
  FleetTelemetrySettingsDocument,
  HardwareSnapshot,
  IntegrationDocument,
  IntegrationProduct,
  LibraryChoice,
  MergerFsInventory,
  NetworkInterface,
  NetworkMode,
  NetworkPlanResponse,
  OperationDocument,
  OperationEvent,
  PlanDocument,
  SetupStatus,
  StorageRole,
  StorageExpansionSelection,
  StorageInventory,
  StorageOperationProgress,
  WizardDocument,
  WizardMode,
} from "./types";

const STEPS = [
  "Server",
  "Network",
  "Find storage",
  "Check drives",
  "Choose use",
  "Storage layout",
  "Libraries",
  "File access",
  "Storage Access",
  "Review",
  "Finish",
] as const;
const STORAGE_CHANGE_STEPS = STEPS.slice(2);

const INITIAL_TIMEZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const INITIAL_NTP_SERVERS = "time.cloudflare.com, time.google.com";
const TIME_ZONE_OPTIONS = supportedTimeZones(INITIAL_TIMEZONE);
const ACCESS_PROTOCOLS = ["SSH", "HTTP", "HTTPS", "SMB", "NFS", "iSCSI", "FCoE"] as const;
type NetworkComponent = "server" | "network" | "ntp" | "discovery" | "syslog" | "snmp" | "traps" | "access_rules";

const LIBRARY_DEFAULTS: LibraryChoice[] = [
  { id: "movies", label: "Movies", contentType: "movies", app: "Radarr", selected: true, source: "recommended" },
  { id: "tv", label: "TV", contentType: "series", app: "Sonarr", selected: true, source: "recommended" },
  { id: "music", label: "Music", contentType: "music", app: "Lidarr", selected: true, source: "recommended" },
  { id: "photos", label: "Photos", contentType: "photos", app: "Immich", selected: true, source: "recommended" },
  { id: "books", label: "Books", contentType: "books", app: "Readarr", selected: true, source: "recommended" },
  { id: "audiobooks", label: "Audiobooks", contentType: "audiobooks", app: "Readarr", selected: true, source: "recommended" },
];

function messageFromError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The request could not be completed.";
}

function speedLabel(speed: number | null): string {
  if (speed === null) return "Unknown speed";
  if (speed >= 1000) return `${speed / 1000} Gb/s`;
  return `${speed} Mb/s`;
}

function smartTestCapabilityLabel(drive: Drive, kind: "short" | "extended"): string {
  const capability = drive.smartSelfTest;
  if (!capability || capability.status === "not_reported") {
    return "Not reported — Hoardarr will verify support before starting";
  }
  if (capability.status === "unsupported") return "Not supported by the detected drive/connection";
  const minutes = kind === "short" ? capability.shortMinutes : capability.extendedMinutes;
  return minutes ? `Supported · drive-reported estimate ${minutes} min` : "Supported · duration not reported";
}

function checkboxToggle(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

export default function App() {
  const [activeStep, setActiveStep] = useState(0);
  const [activePage, setActivePage] = useState<AppPage>("Overview");
  const [focusedStorageId, setFocusedStorageId] = useState<string | null>(null);
  const [storageAction, setStorageAction] = useState<StorageAction | null>(null);
  const [firstRunSetup, setFirstRunSetup] = useState(false);
  const [mode, setMode] = useState<WizardMode>("guided");
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [authenticated, setAuthenticated] = useState(false);
  const [interfaces, setInterfaces] = useState<NetworkInterface[]>([]);
  const [snapshot, setSnapshot] = useState<HardwareSnapshot | null>(null);
  const [wizard, setWizard] = useState<WizardDocument | null>(null);
  const [savedWizards, setSavedWizards] = useState<WizardDocument[]>([]);
  const [plan, setPlan] = useState<PlanDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [consentRecorded, setConsentRecorded] = useState(false);
  const [storageOperation, setStorageOperation] = useState<OperationDocument | null>(null);
  const [storageProgress, setStorageProgress] = useState<StorageOperationProgress | null>(null);
  const [storageEvents, setStorageEvents] = useState<OperationEvent[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationDocument[]>([]);
  const [fleetSettings, setFleetSettings] = useState<FleetTelemetrySettingsDocument | null>(null);
  const [fleetCountry, setFleetCountry] = useState("");
  const [fleetHardwareEnabled, setFleetHardwareEnabled] = useState(true);

  const [serverName, setServerName] = useState("hoardarr");
  const [timezone, setTimezone] = useState(INITIAL_TIMEZONE);
  const [dstMode, setDstMode] = useState<DaylightSavingMode>("automatic");
  const [ntpServers, setNtpServers] = useState(INITIAL_NTP_SERVERS);

  const [networkMode, setNetworkMode] = useState<NetworkMode>("single");
  const [selectedInterfaces, setSelectedInterfaces] = useState<string[]>([]);
  const [addressing, setAddressing] = useState<"dhcp" | "static">("dhcp");
  const [address, setAddress] = useState("");
  const [gateway, setGateway] = useState("");
  const [dns, setDns] = useState("1.1.1.1, 9.9.9.9");
  const [vlan, setVlan] = useState("");
  const [mtu, setMtu] = useState("1500");
  const [lldp, setLldp] = useState(true);
  const [lldpMode, setLldpMode] = useState<"rx_tx" | "receive_only">("rx_tx");
  const [cdpReceive, setCdpReceive] = useState(true);
  const [cdpSmart, setCdpSmart] = useState(true);
  const [bridge, setBridge] = useState(false);
  const [networkPlan, setNetworkPlan] = useState<NetworkPlanResponse | null>(null);
  const [networkExecutorReady, setNetworkExecutorReady] = useState(false);
  const [networkConfirmationPending, setNetworkConfirmationPending] = useState(false);
  const [networkChangedComponents, setNetworkChangedComponents] = useState<NetworkComponent[]>([]);
  const [syslogEnabled, setSyslogEnabled] = useState(false);
  const [syslogServer, setSyslogServer] = useState("");
  const [syslogTransport, setSyslogTransport] = useState<"udp" | "tcp">("udp");
  const [syslogPort, setSyslogPort] = useState("514");
  const [snmpEnabled, setSnmpEnabled] = useState(false);
  const [snmpCommunity, setSnmpCommunity] = useState("");
  const [snmpManagers, setSnmpManagers] = useState("10.81.0.0/16");
  const [snmpLocation, setSnmpLocation] = useState("");
  const [snmpContact, setSnmpContact] = useState("");
  const [trapsEnabled, setTrapsEnabled] = useState(false);
  const [trapServer, setTrapServer] = useState("");
  const [trapPort, setTrapPort] = useState("162");
  const [trapCommunity, setTrapCommunity] = useState("");

  const [selectedDriveIds, setSelectedDriveIds] = useState<string[]>([]);
  const [testIdentity, setTestIdentity] = useState(true);
  const [testSurfaceRead, setTestSurfaceRead] = useState(true);
  const [testSmartShort, setTestSmartShort] = useState(false);
  const [testSmartExtended, setTestSmartExtended] = useState(false);
  const [testDestructive, setTestDestructive] = useState(false);
  const [destructiveTestAck, setDestructiveTestAck] = useState("");

  const [preserveData, setPreserveData] = useState(false);
  const [preserveDataTouched, setPreserveDataTouched] = useState(false);
  const [purpose, setPurpose] = useState("media");
  const [protectionPreference, setProtectionPreference] = useState<ProtectionPreference>("one");
  const [oneLargeLocation, setOneLargeLocation] = useState(true);
  const [easyExpansion, setEasyExpansion] = useState(true);
  const [portability, setPortability] = useState<string[]>(["linux"]);
  const [snapshots, setSnapshots] = useState(false);
  const [encryption, setEncryption] = useState("none");
  const [storageRole, setStorageRole] = useState<StorageRole>("individual");
  const [mergerFsInventory, setMergerFsInventory] = useState<MergerFsInventory | null>(null);
  const [storageInventory, setStorageInventory] = useState<StorageInventory | null>(null);
  const [expansionSelection, setExpansionSelection] = useState<StorageExpansionSelection | null>(null);
  const [mergerFsTarget, setMergerFsTarget] = useState("");
  const [mergerFsName, setMergerFsName] = useState("combined-storage");
  const [mergerFsMountpoint, setMergerFsMountpoint] = useState("/mnt/combined-storage");
  const [mergerFsCreatePolicy, setMergerFsCreatePolicy] = useState<"mfs" | "epmfs">("mfs");
  const [mergerFsSearchPolicy, setMergerFsSearchPolicy] = useState<"ff" | "all">("ff");
  const [arrayName, setArrayName] = useState("media");
  const [zfsVdevType, setZfsVdevType] = useState<"mirror" | "raidz1" | "raidz2" | "raidz3">("raidz2");
  const [zfsVdevWidth, setZfsVdevWidth] = useState(4);
  const [zfsAshift, setZfsAshift] = useState(12);
  const [zfsRecordsize, setZfsRecordsize] = useState("1M");
  const [zfsCompression, setZfsCompression] = useState("lz4");
  const [mdLevel, setMdLevel] = useState<"raid1" | "raid5" | "raid6" | "raid10">("raid6");
  const [mdChunkKib, setMdChunkKib] = useState(512);
  const [snapraidParityCount, setSnapraidParityCount] = useState(1);
  const [mixedComponentType, setMixedComponentType] = useState<"zfs" | "raid">("zfs");
  const [mixedComponentWidth, setMixedComponentWidth] = useState(4);
  const [usbOverrideAck, setUsbOverrideAck] = useState("");
  const [formatFilesystem, setFormatFilesystem] = useState<string | null>(null);
  const [formatPartitionTable, setFormatPartitionTable] = useState<"gpt" | "mbr">("gpt");
  const [formatAlignmentBytes, setFormatAlignmentBytes] = useState(1_048_576);
  const [formatAllocationUnitBytes, setFormatAllocationUnitBytes] = useState<number | null>(null);
  const [formatNoatime, setFormatNoatime] = useState(true);
  const [formatTrimMode, setFormatTrimMode] = useState<"conditional" | "periodic" | "continuous" | "disabled">("conditional");

  const [libraries, setLibraries] = useState<LibraryChoice[]>(LIBRARY_DEFAULTS);
  const [mediaServers, setMediaServers] = useState<string[]>(["Plex"]);
  const [torrentDownloads, setTorrentDownloads] = useState(true);
  const [usenetDownloads, setUsenetDownloads] = useState(true);
  const [newLibraryName, setNewLibraryName] = useState("");
  const [newLibraryType, setNewLibraryType] = useState("series");
  const [newLibraryApp, setNewLibraryApp] = useState("Sonarr");

  const [serviceUsername, setServiceUsername] = useState("media");
  const [serviceCredentialMode, setServiceCredentialMode] = useState<"generate" | "provide">("generate");
  const [servicePassword, setServicePassword] = useState("");
  const [servicePasswordConfirmation, setServicePasswordConfirmation] = useState("");
  const [showServicePassword, setShowServicePassword] = useState(false);
  const [showServicePasswordConfirmation, setShowServicePasswordConfirmation] = useState(false);
  const [generatedServicePassword, setGeneratedServicePassword] = useState<string | null>(null);
  const [generatedPasswordCopyConfirmed, setGeneratedPasswordCopyConfirmed] = useState(false);
  const [provisionedServiceUsername, setProvisionedServiceUsername] = useState<string | null>(null);
  const [consentPhrase, setConsentPhrase] = useState("");
  const [connectivitySkipped, setConnectivitySkipped] = useState(false);
  const [smbEnabled, setSmbEnabled] = useState(true);
  const [nfsEnabled, setNfsEnabled] = useState(false);
  const [iscsiEnabled, setIscsiEnabled] = useState(false);
  const [fcoeEnabled, setFcoeEnabled] = useState(false);
  const [shareName, setShareName] = useState("data");
  const [sharePath, setSharePath] = useState("/data");
  const [accessRules, setAccessRules] = useState<Array<{ id: number; source: string; destination: string; protocol: typeof ACCESS_PROTOCOLS[number]; action: "allow" | "deny" }>>([]);

  useEffect(() => {
    let current = true;
    void (async () => {
      try {
        const result = await api.setupStatus();
        if (result.configured) {
          try {
            await api.resumeSession();
            await loadAuthenticatedData(false);
          } catch (caught) {
            if (!(caught instanceof ApiError && caught.status === 401) && current) {
              setError(messageFromError(caught));
            }
          }
        }
        if (current) setSetupStatus(result);
      } catch (caught) {
        if (current) setError(messageFromError(caught));
      }
    })();
    return () => { current = false; };
  }, []);

  useEffect(() => {
    if (!storageOperation || !["queued", "running"].includes(storageOperation.status)) return;
    const operationId = storageOperation.id;
    let cancelled = false;
    let timer: number | undefined;

    async function poll(): Promise<void> {
      try {
        const [operation, progress, events] = await Promise.all([
          api.operation(operationId),
          api.storageOperationProgress(operationId),
          api.operationEvents(operationId),
        ]);
        if (cancelled) return;
        setStorageOperation(operation);
        setStorageProgress(progress);
        setStorageEvents(events);
        if (operation.status === "succeeded") {
          setError(null);
          if (storageRole === "test") {
            setStatus("Drive checks completed. Review the recorded results, then close.");
            return;
          }
          setStatus("Storage was built and verified successfully. Preparing file-access credentials…");
          try {
            await provisionServiceAccountAtFinish();
          } catch (caught) {
            if (!cancelled) setError(`Storage is ready, but the file-access credential could not be created: ${messageFromError(caught)}`);
          }
          return;
        }
        if (["failed", "cancelled", "needs_attention"].includes(operation.status)) {
          setError(operation.error?.detail ?? operation.error?.message ?? `Storage ended with status ${operation.status}.`);
          setStatus(null);
          return;
        }
        timer = window.setTimeout(() => void poll(), 1000);
      } catch (caught) {
        if (cancelled) return;
        setError(`Storage is still running, but its progress could not be refreshed: ${messageFromError(caught)}`);
        timer = window.setTimeout(() => void poll(), 3000);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [storageOperation?.id, storageOperation?.status]);

  const drives = useMemo(() => snapshot ? drivesFromSnapshot(snapshot) : [], [snapshot]);
  const activeReservedDriveIds = useMemo(
    () => new Set(storageInventory?.active_operations.flatMap((item) => item.selected_device_ids) ?? []),
    [storageInventory],
  );
  const selectedDrives = useMemo(() => drives.filter((drive) => selectedDriveIds.includes(drive.id)), [drives, selectedDriveIds]);
  const recommendation = useMemo(() => recommendStorage({
    drives: selectedDrives,
    purpose,
    preserveData,
    oneLargeLocation,
    protection: protectionPreference,
    easyExpansion,
  }), [selectedDrives, purpose, preserveData, oneLargeLocation, protectionPreference, easyExpansion]);
  const selectedInterfaceSummary = useMemo(() => selectedInterfaces.map((id) => {
    const item = interfaces.find((candidate) => candidate.id === id);
    return item ? `${item.name} (${item.id})` : id;
  }).join(", ") || "None selected", [interfaces, selectedInterfaces]);
  const firstDrive = selectedDrives[0] ?? drives[0];
  const filesystem = filesystemRecommendation(portability, purpose);
  const selectedFilesystem = (formatFilesystem ?? filesystem.filesystem).toLowerCase();
  const selectedAllocationUnitBytes = formatAllocationUnitBytes ?? (selectedFilesystem === "exfat" ? 131_072 : filesystem.allocationUnitBytes);
  const layoutChoices = layoutChoicesForDrive(firstDrive, mode, preserveData, selectedDriveIds.length);
  const importedFilesystems = detectedFilesystems(selectedDrives);
  const importingNtfs = isImportedNtfs(preserveData, selectedDrives);
  const planRisk = objectValue(objectValue(plan?.document.storage).risk);
  const planNeedsApproval = planRisk.approval_required === true;
  const planDeclaredNonDestructive = planRisk.destructive === false && planRisk.approval_required === false;

  useEffect(() => {
    if (preserveDataTouched || !selectedDrives.length) return;
    const shouldPreserve = selectedDrives.some(driveMayContainData);
    setPreserveData(shouldPreserve);
    if (shouldPreserve) setStorageRole(selectedDrives.length === 1 ? "import" : "mergerfs");
  }, [preserveDataTouched, selectedDrives]);

  function applyRecommendedLayout(): void {
    setStorageRole(recommendation.role);
    if (recommendation.role === "mergerfs" && mergerFsInventory?.items.length === 0) setMergerFsTarget("create");
    if (recommendation.parityCount) setSnapraidParityCount(recommendation.parityCount);
    if (recommendation.zfsVdevType) {
      setZfsVdevType(recommendation.zfsVdevType);
      setZfsVdevWidth(selectedDriveIds.length);
    }
    setPlan(null);
  }

  async function loadAuthenticatedData(firstRun: boolean): Promise<void> {
    const [onboarding, foundInterfaces, managedNetwork, latestSnapshot, foundMergerFs, foundStorage, foundWizards, foundOperations, foundIntegrations, foundFleetSettings] = await Promise.all([
        api.onboarding(),
        api.networkInterfaces(),
        api.networkingStatus(),
        api.latestHardwareSnapshot(),
        api.mergerfsInventory(),
        api.storageInventory(),
        api.listWizards(),
        api.listOperations(),
        api.integrations(),
        // Fleet telemetry is operationally independent from local storage.
        // A central-service outage or an older backend must never prevent the
        // appliance shell, Health, or storage controls from loading.
        api.fleetTelemetrySettings().catch(() => null),
    ]);
    const defaults = uiDefaultsFromOnboarding(onboarding);
    setMode((current) => current === "advanced" ? current : defaults.experience);
    setServerName((current) => current === "hoardarr" ? defaults.hostname : current);
    setTimezone((current) => current === INITIAL_TIMEZONE ? firstRun ? INITIAL_TIMEZONE : defaults.timezone : current);
    setDstMode(defaults.dstMode);
    setNetworkMode(defaults.networkMode);
    setAddressing(defaults.addressing);
    setAddress(defaults.address);
    setGateway(defaults.gateway);
    setDns(defaults.dnsServers.join(", "));
    setVlan(defaults.vlanId);
    setMtu(defaults.mtu);
    setBridge(defaults.experience === "advanced" && defaults.bridge);
    setNtpServers((current) => current === INITIAL_NTP_SERVERS ? defaults.ntpServers.join(", ") : current);
    setLldp(defaults.lldpEnabled);
    setLldpMode(defaults.lldpMode);
    setCdpReceive(defaults.cdpReceive);
    setCdpSmart(defaults.cdpReceive && defaults.cdpSmartTransmit);
    setNetworkExecutorReady(managedNetwork.capabilities.available);
    setNetworkConfirmationPending(managedNetwork.pending_confirmation);
    if (managedNetwork.configuration) {
      const applied = managedNetwork.configuration;
      const host = objectValue(applied.host);
      const appliedNetwork = objectValue(host.network);
      const appliedNtp = objectValue(host.ntp);
      const discovery = objectValue(host.discovery);
      const appliedLldp = objectValue(discovery.lldp);
      const appliedCdp = objectValue(discovery.cdp);
      const appliedSyslog = objectValue(applied.syslog);
      const appliedSnmp = objectValue(applied.snmp);
      const appliedTraps = objectValue(applied.traps);
      const destinations = Array.isArray(appliedTraps.destinations) ? appliedTraps.destinations.map(objectValue) : [];
      const rules = Array.isArray(applied.access_rules) ? applied.access_rules.map(objectValue) : [];
      setNetworkMode(["single", "active_passive", "lacp"].includes(String(appliedNetwork.mode)) ? appliedNetwork.mode as NetworkMode : "single");
      setBridge(appliedNetwork.mode === "bridge");
      setSelectedInterfaces(stringArray(appliedNetwork.interface_ids));
      setAddressing(appliedNetwork.addressing === "static" ? "static" : "dhcp");
      setAddress(stringArray(appliedNetwork.addresses)[0] ?? "");
      setGateway(typeof appliedNetwork.gateway === "string" ? appliedNetwork.gateway : "");
      setDns(stringArray(appliedNetwork.dns_servers).join(", "));
      setVlan(typeof appliedNetwork.vlan_id === "number" ? String(appliedNetwork.vlan_id) : "");
      setMtu(typeof appliedNetwork.mtu === "number" ? String(appliedNetwork.mtu) : "1500");
      setNtpServers(stringArray(appliedNtp.servers).join(", "));
      setLldp(appliedLldp.enabled !== false);
      setLldpMode(appliedLldp.mode === "receive_only" ? "receive_only" : "rx_tx");
      setCdpReceive(appliedCdp.receive === true);
      setCdpSmart(appliedCdp.smart_transmit === true);
      setSyslogEnabled(appliedSyslog.enabled === true);
      setSyslogServer(typeof appliedSyslog.server === "string" ? appliedSyslog.server : "");
      setSyslogTransport(appliedSyslog.transport === "tcp" ? "tcp" : "udp");
      setSyslogPort(typeof appliedSyslog.port === "number" ? String(appliedSyslog.port) : "514");
      setSnmpEnabled(appliedSnmp.enabled === true);
      setSnmpCommunity(typeof appliedSnmp.community === "string" ? appliedSnmp.community : "");
      setSnmpManagers(stringArray(appliedSnmp.allowed_managers).join(", "));
      setSnmpLocation(typeof appliedSnmp.location === "string" ? appliedSnmp.location : "");
      setSnmpContact(typeof appliedSnmp.contact === "string" ? appliedSnmp.contact : "");
      setTrapsEnabled(appliedTraps.enabled === true);
      setTrapServer(typeof destinations[0]?.server === "string" ? destinations[0].server as string : "");
      setTrapPort(typeof destinations[0]?.port === "number" ? String(destinations[0].port) : "162");
      setTrapCommunity(typeof destinations[0]?.community === "string" ? destinations[0].community as string : "");
      const protocolLabels: Record<string, typeof ACCESS_PROTOCOLS[number]> = { ssh: "SSH", http: "HTTP", https: "HTTPS", smb: "SMB", nfs: "NFS", iscsi: "iSCSI", fcoe: "FCoE" };
      setAccessRules(rules.map((rule, index) => ({
        id: Date.now() + index,
        source: rule.source === "this_server" ? "This server" : typeof rule.source === "string" ? rule.source : "Any",
        destination: rule.destination === "this_server" ? "This server" : typeof rule.destination === "string" ? rule.destination : "This server",
        protocol: protocolLabels[String(rule.protocol)] ?? "HTTPS",
        action: rule.action === "deny" ? "deny" : "allow",
      })));
    } else {
      setServerName(managedNetwork.current.hostname);
      if (managedNetwork.current.timezone) setTimezone(managedNetwork.current.timezone);
    }
    const availableIds = new Set(foundInterfaces.map((item) => item.id));
    const matchedInterfaces = defaults.selectedInterfaces.filter((id) => availableIds.has(id));
    const defaultInterfaces = defaults.networkMode === "single" || defaults.bridge ? matchedInterfaces.slice(0, 1) : matchedInterfaces;
    if (!managedNetwork.configuration) {
      const liveDefault = managedNetwork.current.default_interface;
      if (liveDefault && availableIds.has(liveDefault)) setSelectedInterfaces([liveDefault]);
      else if (defaultInterfaces.length) setSelectedInterfaces(defaultInterfaces);
      else if (foundInterfaces.length) setSelectedInterfaces([foundInterfaces[0].id]);
      else setSelectedInterfaces([]);
    }
    setInterfaces(foundInterfaces);
    setSnapshot(latestSnapshot);
    setMergerFsInventory(foundMergerFs);
    setStorageInventory(foundStorage);
    setIntegrations(foundIntegrations);
    if (foundFleetSettings) {
      setFleetSettings(foundFleetSettings);
      setFleetCountry(foundFleetSettings.country_code ?? "");
      setFleetHardwareEnabled(foundFleetSettings.hardware_enabled);
    }
    let recoverableStorage = foundOperations.find((item) => {
      if (item.kind !== "storage.apply" || !["queued", "running", "succeeded", "needs_attention"].includes(item.status)) return false;
      const related = foundWizards.find((candidate) => candidate.id === item.resource?.id);
      return related !== undefined && related.status !== "completed" && related.status !== "cancelled";
    });
    let preloadedRecoveryProgress: StorageOperationProgress | null = null;
    if (!recoverableStorage) {
      for (const candidate of foundOperations) {
        if (candidate.kind !== "storage.apply" || candidate.status !== "failed") continue;
        const related = foundWizards.find((wizardCandidate) => wizardCandidate.id === candidate.resource?.id);
        if (!related || related.status === "completed" || related.status === "cancelled") continue;
        const checkpoint = await api.storageOperationProgress(candidate.id).catch(() => null);
        if (checkpoint?.state !== "needs_attention") continue;
        recoverableStorage = { ...candidate, status: "needs_attention" };
        preloadedRecoveryProgress = checkpoint;
        break;
      }
    }
    if (recoverableStorage) {
      const recoveredWizard = foundWizards.find((candidate) => candidate.id === recoverableStorage.resource?.id);
      if (recoveredWizard) {
        const recoveredPlan = await api.readPlan(recoveredWizard.id);
        const recoveredStorage = objectValue(recoveredWizard.answers.storage);
        const recoveredAccount = objectValue(recoveredStorage.service_account);
        const recoveredDownloads = objectValue(recoveredStorage.downloads);
        const recoveredTopology = stringValue(recoveredStorage.topology, "individual");
        setWizard(recoveredWizard);
        setPlan(recoveredPlan);
        setMode(recoveredWizard.mode);
        setStorageAction("add");
        setActiveStep(10);
        setSelectedDriveIds(stringArray(recoveredStorage.selected_device_ids));
        setStorageRole(recoveredTopology === "cache" ? "download-cache" : isStorageRole(recoveredTopology) ? recoveredTopology : "individual");
        setPurpose(stringValue(recoveredStorage.purpose, "media"));
        setPreserveData(booleanValue(recoveredStorage.preserve_data, false));
        setPortability(stringArray(recoveredStorage.portable_systems));
        setSnapshots(booleanValue(recoveredStorage.snapshots, false));
        setEncryption(stringValue(recoveredStorage.encryption, "none"));
        setLibraries(libraryChoices(recoveredStorage.libraries));
        setTorrentDownloads(booleanValue(recoveredDownloads.torrents, true));
        setUsenetDownloads(booleanValue(recoveredDownloads.usenet, true));
        setServiceUsername(stringValue(recoveredAccount.username, "media"));
        setServiceCredentialMode(recoveredAccount.credential_mode === "provide_separately" ? "provide" : "generate");
        setStorageOperation(recoverableStorage);
        const fallbackProgress: StorageOperationProgress = {
          operation_id: recoverableStorage.id,
          state: recoverableStorage.status,
          phase: recoverableStorage.status === "succeeded"
            ? "Storage build completed"
            : recoverableStorage.status === "needs_attention"
              ? "Storage build stopped at a durable checkpoint"
              : "Reconnecting to storage activity",
          completed_steps: recoverableStorage.status === "succeeded" ? 1 : 0,
          total_steps: 1,
          percent: recoverableStorage.status === "succeeded" ? 100 : 0,
          completed_actions: [],
          notices: [],
          current_action: null,
          estimate: null,
          updated_at: null,
        };
        const [recoveredProgress, recoveredEvents] = await Promise.all([
          preloadedRecoveryProgress
            ? Promise.resolve(preloadedRecoveryProgress)
            : api.storageOperationProgress(recoverableStorage.id).catch(() => fallbackProgress),
          api.operationEvents(recoverableStorage.id).catch(() => [] as OperationEvent[]),
        ]);
        setStorageProgress(recoveredProgress);
        setStorageEvents(recoveredEvents);
        setActivePage("Storage");
        setStatus(
          recoverableStorage.status === "succeeded"
            ? "Storage completed. Finish the file-access credential to close setup."
            : recoverableStorage.status === "needs_attention"
              ? "Storage stopped safely. Resume it from the last verified checkpoint after correcting the reported problem."
              : "Reconnected to the running storage setup.",
        );
      }
    }
    const mutableWizards = foundWizards.filter((item) =>
      (item.status === "draft" || item.status === "review")
      && item.id !== recoverableStorage?.resource?.id
    );
    const explicitlySaved = mutableWizards.filter((item) => Object.hasOwn(item.answers, "draft_ui"));
    const latestReview = mutableWizards.find((item) => Object.hasOwn(item.answers, "storage") && !Object.hasOwn(item.answers, "draft_ui"));
    setSavedWizards(latestReview ? [...explicitlySaved, latestReview] : explicitlySaved);
    setMergerFsTarget((current) => current || (foundMergerFs.items.length ? "" : "create"));
    setAuthenticated(true);
  }

  const savedStorageDrafts = useMemo<SavedStorageDraft[]>(() => savedWizards.map((saved) => {
    const draft = objectValue(saved.answers.draft_ui);
    const selected = wizardSelectedDeviceIds(saved);
    const selectedHardware = selected.map((id) => drives.find((drive) => drive.id === id));
    const missing = selected.filter((_id, index) => !selectedHardware[index]);
    const blocked = selectedHardware.filter((drive): drive is Drive => Boolean(drive && (!drive.selectable || activeReservedDriveIds.has(drive.id))));
    const reservedElsewhere = new Set(savedWizards.filter((other) => other.id !== saved.id).flatMap(wizardSelectedDeviceIds));
    const conflicts = selected.filter((id) => reservedElsewhere.has(id));
    const action = draft.action === "move" || draft.action === "change" ? draft.action : "add";
    return {
      id: saved.id,
      savedAt: saved.updated_at ?? saved.created_at ?? "",
      mode: saved.mode,
      action,
      selectedDriveIds: selected,
      selectedDriveLabels: selectedHardware.filter((drive): drive is Drive => Boolean(drive)).map((drive) => `${drive.path} (${drive.serial})`),
      available: missing.length === 0 && blocked.length === 0 && conflicts.length === 0,
      unavailableReason: missing.length ? `${missing.length} selected drive${missing.length === 1 ? " is" : "s are"} no longer detected.` : blocked.length ? `${blocked.map((drive) => drive.path).join(", ")} ${blocked.length === 1 ? "is" : "are"} no longer free for planning.` : conflicts.length ? `${conflicts.length} selected drive${conflicts.length === 1 ? " is" : "s are"} also reserved by another saved draft. Discard one draft before continuing.` : null,
    };
  }), [activeReservedDriveIds, drives, savedWizards]);

  function mergerFsAnswer(): Record<string, unknown> {
    if (!mergerFsInventory) throw new Error("Combined storage discovery has not completed.");
    if (!mergerFsTarget) throw new Error("Choose an existing combined storage instance or create a new one.");
    if (mergerFsTarget !== "create") {
      const existing = mergerFsInventory.items.find((item) => item.id === mergerFsTarget);
      if (!existing) throw new Error("The selected combined storage instance is no longer available. Choose it again.");
      return { mode: "existing", instance_id: existing.id, name: existing.name, mountpoint: existing.mountpoint };
    }
    if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(mergerFsName)) {
      throw new Error("Use 1–64 lowercase letters, numbers, dots, dashes, or underscores for the combined storage name.");
    }
    if (!/^\/(mnt|srv|data)\/.+/.test(mergerFsMountpoint) || mergerFsMountpoint.includes("..")) {
      throw new Error("Use a combined storage path beneath /mnt, /srv, or /data without .. segments.");
    }
    return {
      mode: "create",
      name: mergerFsName,
      mountpoint: mergerFsMountpoint,
      create_policy: mergerFsCreatePolicy,
      search_policy: mergerFsSearchPolicy,
    };
  }

  function layoutOptionsAnswer(topology: StorageRole): Record<string, unknown> | null {
    if (!/^[a-z][a-z0-9_-]{0,62}$/.test(arrayName)) {
      throw new Error("Use a lower-case storage name containing letters, numbers, dashes, or underscores.");
    }
    if (topology === "zfs") {
      const minimum = { mirror: 2, raidz1: 3, raidz2: 4, raidz3: 5 }[zfsVdevType];
      if (zfsVdevWidth < minimum || selectedDriveIds.length % zfsVdevWidth !== 0) {
        throw new Error(`Choose a vdev width of at least ${minimum} that divides the ${selectedDriveIds.length} selected drives evenly.`);
      }
      const vdevs = [];
      for (let index = 0; index < selectedDriveIds.length; index += zfsVdevWidth) {
        vdevs.push({ type: zfsVdevType, device_ids: selectedDriveIds.slice(index, index + zfsVdevWidth) });
      }
      const existingZfsMountpoint = expansionSelection?.kind === "add_zfs_vdev"
        && expansionSelection.target?.provider === "zfs"
        ? expansionSelection.target.mountpoint
        : "/data";
      return {
        name: arrayName,
        vdevs,
        ashift: zfsAshift,
        recordsize: zfsRecordsize,
        compression: zfsCompression,
        mountpoint: existingZfsMountpoint,
        scrub_schedule: "monthly",
        snapshots: { enabled: snapshots, retention: snapshots ? 12 : 0 },
      };
    }
    if (topology === "raid") {
      if (!new Set(["ext4", "xfs", "btrfs"]).has(selectedFilesystem)) {
        throw new Error("Linux RAID requires ext4, XFS, or Btrfs.");
      }
      return {
        name: arrayName,
        level: mdLevel,
        device_ids: selectedDriveIds,
        filesystem: selectedFilesystem,
        mountpoint: "/data",
        chunk_kib: mdChunkKib,
        metadata: "1.2",
      };
    }
    if (topology === "snapraid") {
      if (snapraidParityCount < 1 || snapraidParityCount > 6 || snapraidParityCount >= selectedDriveIds.length) {
        throw new Error("Choose one to six parity drives and leave at least one data drive.");
      }
      const parity = selectedDriveIds.slice(0, snapraidParityCount);
      return {
        name: arrayName,
        data: selectedDriveIds.slice(snapraidParityCount),
        parity,
        mountpoint: "/data",
        sync_schedule: "daily",
        scrub_schedule: "weekly",
        scrub_percent: 12,
      };
    }
    if (topology === "mixed") {
      if (mixedComponentWidth < 2 || selectedDriveIds.length % mixedComponentWidth !== 0 || selectedDriveIds.length / mixedComponentWidth < 2) {
        throw new Error("Choose a component width that divides the selected drives into at least two pools.");
      }
      const components = [];
      for (let index = 0; index < selectedDriveIds.length; index += mixedComponentWidth) {
        const number = index / mixedComponentWidth + 1;
        const deviceIds = selectedDriveIds.slice(index, index + mixedComponentWidth);
        const name = `${arrayName}_${number}`;
        const mountpoint = `/mnt/hoardarr/${arrayName}-${number}`;
        if (mixedComponentType === "zfs") {
          const minimum = { mirror: 2, raidz1: 3, raidz2: 4, raidz3: 5 }[zfsVdevType];
          if (mixedComponentWidth < minimum) throw new Error(`${zfsVdevType.toUpperCase()} needs at least ${minimum} drives per component pool.`);
          components.push({
            topology: "zfs",
            device_ids: deviceIds,
            options: {
              name,
              vdevs: [{ type: zfsVdevType, device_ids: deviceIds }],
              ashift: zfsAshift,
              recordsize: zfsRecordsize,
              compression: zfsCompression,
              mountpoint,
              scrub_schedule: "monthly",
              snapshots: { enabled: snapshots, retention: snapshots ? 12 : 0 },
            },
          });
        } else {
          components.push({
            topology: "raid",
            device_ids: deviceIds,
            options: { name, level: mdLevel, device_ids: deviceIds, filesystem: selectedFilesystem, mountpoint, chunk_kib: mdChunkKib, metadata: "1.2" },
          });
        }
      }
      return { name: arrayName, components, mountpoint: "/data", create_policy: mergerFsCreatePolicy, search_policy: mergerFsSearchPolicy };
    }
    return null;
  }

  function storageDraftPayload(): Record<string, unknown> {
    return {
      schema: 1,
      action: storageAction ?? "add",
      active_step: Math.min(activeStep, 8),
      selected_device_ids: selectedDriveIds,
      tests: {
        identity: testIdentity,
        surface_read: testSurfaceRead,
        smart_short: testSmartShort,
        smart_extended: testSmartExtended,
        destructive: testDestructive,
      },
      preserve_data: preserveData,
      purpose,
      guided_preferences: {
        protection: protectionPreference,
        one_large_location: oneLargeLocation,
        easy_expansion: easyExpansion,
      },
      portability,
      snapshots,
      encryption,
      storage_role: storageRole,
      array: {
        name: arrayName,
        zfs_vdev_type: zfsVdevType,
        zfs_vdev_width: zfsVdevWidth,
        zfs_ashift: zfsAshift,
        zfs_recordsize: zfsRecordsize,
        zfs_compression: zfsCompression,
        md_level: mdLevel,
        md_chunk_kib: mdChunkKib,
        snapraid_parity_count: snapraidParityCount,
        mixed_component_type: mixedComponentType,
        mixed_component_width: mixedComponentWidth,
      },
      mergerfs: {
        target: mergerFsTarget,
        name: mergerFsName,
        mountpoint: mergerFsMountpoint,
        create_policy: mergerFsCreatePolicy,
        search_policy: mergerFsSearchPolicy,
      },
      expansion_selection: expansionSelection,
      format: {
        filesystem: formatFilesystem,
        partition_table: formatPartitionTable,
        alignment_bytes: formatAlignmentBytes,
        allocation_unit_bytes: formatAllocationUnitBytes,
        noatime: formatNoatime,
        trim_mode: formatTrimMode,
      },
      libraries,
      media_servers: mediaServers,
      downloads: { torrents: torrentDownloads, usenet: usenetDownloads },
      service_username: serviceUsername,
      account_mode: serviceCredentialMode,
      connectivity: {
        skipped: connectivitySkipped,
        smb: smbEnabled,
        nfs: nfsEnabled,
        iscsi: iscsiEnabled,
        fcoe: fcoeEnabled,
        share_name: shareName,
        share_path: sharePath,
      },
    };
  }

  async function authenticateAndLoad(input: AuthenticationInput): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      if (!setupStatus) throw new Error("The server is still starting. Try again in a moment.");
      const firstRun = !setupStatus.configured;
      if (firstRun) {
        if (!setupStatus.claim_available) throw new Error("This setup link has expired. Run hoardarr setup again and open the new link.");
        if (!input.setupCode) throw new Error("Open the one-time setup link provided by the server.");
        await api.claimSetup({ token: input.setupCode, username: input.username, password: input.password });
        setSetupStatus({ configured: true, claim_available: false });
      } else {
        await api.login({ username: input.username, password: input.password, remember_me: input.rememberMe ?? true });
      }
      await loadAuthenticatedData(firstRun);
      if (firstRun) {
        setFirstRunSetup(true);
        setStorageAction("add");
        setActiveStep(0);
      }
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  function networkPayload(): Record<string, unknown> {
    return {
      experience: mode,
      server: { hostname: serverName.trim(), timezone, dst_mode: dstMode },
      network: {
        mode: bridge && mode === "advanced" ? "bridge" : networkMode,
        interface_ids: selectedInterfaces,
        addressing,
        addresses: addressing === "static" ? [address.trim()] : [],
        gateway: gatewayForPayload(addressing, gateway),
        dns_servers: dns.split(",").map((item) => item.trim()).filter(Boolean),
        vlan_id: vlan ? Number(vlan) : null,
        mtu: Number(mtu),
        bridge: { enabled: mode === "advanced" && bridge, stp: bridge, prefer_rstp: bridge },
      },
      ntp: { servers: ntpServers.split(",").map((item) => item.trim()).filter(Boolean) },
      discovery: { lldp: { enabled: lldp, mode: lldpMode }, cdp: { receive: cdpReceive, smart_transmit: cdpReceive && cdpSmart } },
    };
  }

  function managedNetworkPayload(): Record<string, unknown> {
    return {
      host: networkPayload(),
      syslog: {
        enabled: syslogEnabled,
        server: syslogServer.trim() || null,
        transport: syslogTransport,
        port: Number(syslogPort),
      },
      snmp: {
        enabled: snmpEnabled,
        community: snmpCommunity || null,
        allowed_managers: snmpManagers.split(",").map((item) => item.trim()).filter(Boolean),
        location: snmpLocation,
        contact: snmpContact,
      },
      traps: {
        enabled: trapsEnabled,
        destinations: trapsEnabled ? [{
          server: trapServer.trim(),
          port: Number(trapPort),
          community: trapCommunity || snmpCommunity,
        }] : [],
      },
      access_rules: accessRules.map((rule) => ({
        source: rule.source.trim() || "any",
        destination: rule.destination.trim(),
        protocol: rule.protocol.toLowerCase(),
        action: rule.action,
      })),
    };
  }

  function applicationAnswers(): Record<string, unknown> {
    return {
      selected_integration_ids: integrations
        .filter((item) => item.status === "connected")
        .map((item) => item.id),
      root_folder_paths: {},
      remote_path_mappings: [],
    };
  }

  function applyApplicationRecommendations(value: {
    product: IntegrationProduct;
    media: boolean;
    torrents: boolean;
    usenet: boolean;
  }): void {
    const libraryId: Partial<Record<IntegrationProduct, string>> = {
      sonarr: "tv",
      radarr: "movies",
      lidarr: "music",
      readarr: "books",
    };
    const selectedId = libraryId[value.product];
    if (selectedId) {
      setLibraries((items) => items.map((item) => item.id === selectedId
        ? { ...item, selected: value.media }
        : item));
    }
    setTorrentDownloads(value.torrents);
    setUsenetDownloads(value.usenet);
  }

  async function refreshHardware(): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus("Scanning controllers, enclosures, and drives…");
    try {
      const found = await api.discoverHardware();
      const foundStorage = await api.storageInventory();
      const foundDrives = drivesFromSnapshot(found);
      setSnapshot(found);
      setStorageInventory(foundStorage);
      const selectableDrives = foundDrives.filter((drive) => drive.selectable);
      setSelectedDriveIds(selectableDrives.length === 1 ? [selectableDrives[0].id] : []);
      if (wizard && (wizard.status === "draft" || wizard.status === "review")) {
        await api.cancelWizard(wizard);
        setWizard(null);
      }
      setPlan(null);
      setConsentPhrase("");
      setStatus(`Discovery completed. ${foundDrives.length} drive${foundDrives.length === 1 ? "" : "s"} found.`);
    } catch (caught) {
      setStatus(null);
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function ensureWizard(foundSnapshot: HardwareSnapshot | null = snapshot): Promise<WizardDocument> {
    if (wizard) return wizard;
    if (!foundSnapshot) throw new Error("Run storage discovery before continuing.");
    const created = await api.startWizard(mode, foundSnapshot.id);
    setWizard(created);
    return created;
  }

  async function saveStep(step: string, answers: Record<string, unknown>): Promise<WizardDocument> {
    const current = await ensureWizard();
    const updated = await api.saveWizardStep(current, step, answers);
    setWizard(updated);
    setPlan(null);
    setConsentPhrase("");
    return updated;
  }

  function validateInterfaceSelection(): void {
    const minimum = bridge && mode === "advanced" ? 1 : networkMode === "single" ? 1 : 2;
    if (selectedInterfaces.length < minimum) {
      throw new Error(networkMode === "single" ? "Select one network interface." : "Select at least two interfaces for redundancy.");
    }
    if ((networkMode === "single" || bridge) && selectedInterfaces.length !== 1) {
      throw new Error("Select exactly one network interface for this connection mode.");
    }
    if (addressing === "static" && !address.trim()) throw new Error("Enter the static address with its network prefix. The gateway is optional.");
  }

  function connectivityPayload(): Record<string, unknown> {
    if (connectivitySkipped) return { skip: true, services: [] };
    const selectedProtocols = [
      ...(smbEnabled ? ["smb"] : []),
      ...(nfsEnabled ? ["nfs"] : []),
      ...(iscsiEnabled ? ["iscsi"] : []),
      ...(fcoeEnabled ? ["fcoe"] : []),
    ];
    if (!selectedProtocols.length) throw new Error("Choose at least one connection method or select Set up connectivity later.");
    if (!/^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$/.test(shareName)) throw new Error("Use a predictable 1–64 character share name.");
    if (!/^\/(data|mnt|srv)(?:\/|$)/.test(sharePath) || sharePath.includes("..")) throw new Error("Use a share path beneath /data, /mnt, or /srv without .. segments.");
    return {
      skip: false,
      services: selectedProtocols.map((protocol) => ({
        protocol,
        name: shareName,
        path: sharePath,
        read_only: false,
      })),
    };
  }

  async function advance(): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (activeStep === 0) {
        const validationMessage = serverSettingsError(serverName, timezone, ntpServers);
        if (validationMessage) throw new Error(validationMessage);
        if (firstRunSetup && fleetSettings) {
          const savedFleetSettings = await api.saveFleetTelemetrySettings({
            hardware_enabled: fleetHardwareEnabled,
            enhanced_enabled: false,
            content_enabled: false,
            country_code: fleetCountry.trim().toUpperCase() || null,
            timezone,
          });
          setFleetSettings(savedFleetSettings);
          setFleetCountry(savedFleetSettings.country_code ?? "");
        }
        setNetworkPlan(null);
      } else if (activeStep === 1) {
        validateInterfaceSelection();
        const configuration = managedNetworkPayload();
        const changed: NetworkComponent[] = ["server", "network", "ntp", "discovery"];
        const preview = await api.planManagedNetwork(configuration, changed);
        if (!networkPlan || networkPlan.sha256 !== preview.sha256) {
          setNetworkPlan(preview);
          if (!preview.plan.apply_available) {
            throw new Error(preview.plan.blockers?.[0]?.message ?? "Required networking tools are unavailable.");
          }
          setStatus("Review the network settings, then select Apply and continue.");
          return;
        }
        if (!preview.plan.apply_available) {
          throw new Error(preview.plan.blockers?.[0]?.message ?? "Required networking tools are unavailable.");
        }
        const pending = await api.applyManagedNetwork(configuration, preview.sha256, changed);
        setNetworkConfirmationPending(true);
        await api.confirmManagedNetwork(pending.token);
        setNetworkConfirmationPending(false);
        setNetworkPlan(null);
        setStatus("Network settings applied.");
      } else if (activeStep === 2) {
        if (!snapshot) throw new Error("Run storage discovery before continuing.");
        if (!selectedDriveIds.length) throw new Error("Select at least one drive.");
        const blockedSelections = selectedDrives.filter((drive) => !drive.selectable);
        if (blockedSelections.length) throw new Error(`Remove unselectable drive(s): ${blockedSelections.map((drive) => drive.path).join(", ")}.`);
        await ensureWizard();
      } else if (activeStep === 3) {
        if (testDestructive && !exactConsentAccepted(destructiveTestAck)) throw new Error('Type the exact words “I AGREE” before selecting a destructive drive test.');
        if (storageRole === "test") {
          const storageUpdated = await saveStep("storage", {
            selected_device_ids: selectedDriveIds,
            topology: "test",
            purpose: "general",
            preserve_data: true,
            portable_systems: ["linux"],
            snapshots: false,
            encryption: "none",
            libraries: [],
            custom_libraries: [],
            service_account: {
              username: serviceUsername,
              credential_mode: "provide_separately",
            },
            intake_tests: {
              identity: testIdentity,
              full_surface_read: testSurfaceRead,
              smart_short: testSmartShort,
              smart_extended: testSmartExtended,
              destructive_write_read: testDestructive,
            },
            downloads: { torrents: false, usenet: false },
          });
          const layoutUpdated = await api.saveWizardStep(storageUpdated, "layout", {
            work_path: "/data/work",
            downloads_path: "/data/downloads",
            media_path: "/data/media",
          });
          const applicationsUpdated = await api.saveWizardStep(
            layoutUpdated,
            "applications",
            applicationAnswers(),
          );
          const connectivityUpdated = await api.saveWizardStep(
            applicationsUpdated,
            "connectivity",
            { skip: true, services: [] },
          );
          setWizard(connectivityUpdated);
          setPlan(await api.createPlan(connectivityUpdated));
          setActiveStep(9);
          return;
        }
      } else if (activeStep === 4) {
        if (!portability.length) throw new Error("Choose Windows, macOS, or Hoardarr-managed Linux storage.");
        if (mode === "guided") applyRecommendedLayout();
      } else if (activeStep === 5) {
        if (storageRole === "mergerfs") mergerFsAnswer();
        if (storageRole === "zfs" || storageRole === "raid" || storageRole === "snapraid" || storageRole === "mixed") layoutOptionsAnswer(storageRole);
        const unsupportedGeometry = selectedDrives.filter((drive) => !hasKnownSectorGeometry(drive));
        if (unsupportedGeometry.length && storageChoiceNeedsSectorGeometry({ preserveData, topology: storageRole, encryption })) {
          throw new Error(`This storage choice requires a geometry-dependent write, but sector geometry is not write-compatible: ${unsupportedGeometry.map((drive) => `${drive.path} (${drive.serial}): ${sectorGeometryAssessment(drive).message}`).join(" ")} Choose Keep or import with an individual or combined-storage layout and no encryption, or correct the sector format outside Hoardarr.`);
        }
        if (selectedDrives.some((drive) => isUsbRaidOverride(drive, storageRole)) && !exactConsentAccepted(usbOverrideAck)) {
          throw new Error('Type the exact words “I AGREE” to override the USB array safety policy.');
        }
      } else if (activeStep === 6) {
        const selected = libraries.filter((library) => library.selected);
        if (!selected.length) throw new Error("Select at least one library.");
      } else if (activeStep === 7) {
        validateServiceAccount();
      } else if (activeStep === 8) {
        validateServiceAccount();
        const connectivity = connectivityPayload();
        const selected = libraries.filter((library) => library.selected);
        const topology = storageRole === "download-cache" ? "cache" : storageRole;
        const standardNames = new Set(LIBRARY_DEFAULTS.map((library) => library.label));
        const appNames = (display: string) => display.split("+").map((name) => name.trim().toLowerCase()).map((name) => name === "file share" ? "none" : name);
        const storageUpdated = await saveStep("storage", {
          selected_device_ids: selectedDriveIds,
          topology,
          purpose,
          preserve_data: preserveData,
          portable_systems: portability.includes("none") ? ["linux"] : portability,
          snapshots,
          encryption,
          libraries: selected.filter((library) => standardNames.has(library.label)).map((library) => library.label),
          custom_libraries: selected.filter((library) => !standardNames.has(library.label)).map((library) => ({
            name: library.label,
            content_type: library.contentType === "movies-and-series" ? "both" : library.contentType,
            applications: appNames(library.app),
          })),
          service_account: { username: serviceUsername, credential_mode: serviceCredentialMode === "generate" ? "generate" : "provide_separately" },
          intake_tests: {
            identity: testIdentity,
            full_surface_read: testSurfaceRead,
            smart_short: testSmartShort,
            smart_extended: testSmartExtended,
            destructive_write_read: testDestructive,
          },
          downloads: { torrents: torrentDownloads, usenet: usenetDownloads },
          ...(topology === "mergerfs" ? { mergerfs: mergerFsAnswer() } : {}),
          ...(expansionSelection ? { expansion: expansionSelection } : {}),
          ...(topology === "zfs" || topology === "raid" || topology === "snapraid" || topology === "mixed" ? { layout_options: layoutOptionsAnswer(topology) } : {}),
          ...(mode === "advanced" ? { format_options: {
            filesystem: selectedFilesystem,
            partition_table: formatPartitionTable,
            alignment_bytes: formatAlignmentBytes,
            allocation_unit_bytes: selectedAllocationUnitBytes,
            noatime: formatNoatime,
            trim_mode: formatTrimMode,
          } } : {}),
          ...(usbOverrideAck ? { advanced_usb_acknowledgement: usbOverrideAck } : {}),
        });
        const storageRoot = expansionSelection?.kind === "add_zfs_vdev"
          && expansionSelection.target?.provider === "zfs"
          ? expansionSelection.target.mountpoint
          : "/data";
        const layoutUpdated = await api.saveWizardStep(storageUpdated, "layout", {
          work_path: `${storageRoot}/work`,
          downloads_path: `${storageRoot}/downloads`,
          media_path: `${storageRoot}/media`,
        });
        const applicationsUpdated = await api.saveWizardStep(
          layoutUpdated,
          "applications",
          applicationAnswers(),
        );
        const connectivityUpdated = await api.saveWizardStep(applicationsUpdated, "connectivity", connectivity);
        setWizard(connectivityUpdated);
        const createdPlan = await api.createPlan(connectivityUpdated);
        setPlan(createdPlan);
        setGeneratedServicePassword(null);
        setGeneratedPasswordCopyConfirmed(false);
        setProvisionedServiceUsername(null);
      } else if (activeStep === 9) {
        if (!plan) throw new Error("Create a current review plan before continuing.");
      }
      setActiveStep((step) => Math.min(step + 1, STEPS.length - 1));
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitConsent(): Promise<void> {
    if (!wizard || !plan) return;
    if (planNeedsApproval && !consentRecorded && !exactConsentAccepted(consentPhrase)) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (planNeedsApproval && !consentRecorded) {
        if (!snapshot) throw new Error("The bound hardware snapshot is unavailable.");
        await api.recordConsent(wizard, plan, snapshot.sha256, consentPhrase, selectedDriveIds);
        setConsentRecorded(true);
      }
      const operation = await api.startStorageApply(wizard);
      setStorageOperation(operation);
      setStorageProgress({
        operation_id: operation.id,
        state: operation.status,
        phase: operation.status === "queued" ? "Waiting for the storage worker" : "Starting",
        completed_steps: 0,
        total_steps: 0,
        percent: 0,
        completed_actions: [],
        notices: [],
        current_action: null,
        estimate: null,
        updated_at: null,
      });
      setStorageEvents([]);
      setStatus(`Storage build ${operation.id} was queued. No success will be shown until the executor finishes.`);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function resumeStorageBuild(): Promise<void> {
    if (!storageOperation || storageOperation.status !== "needs_attention") return;
    setBusy(true);
    setError(null);
    setStatus("Checking the durable storage checkpoint…");
    try {
      const operation = await api.resumeOperation(storageOperation.id);
      setStorageOperation(operation);
      setStorageProgress((current) => current ? {
        ...current,
        state: operation.status,
        phase: "Waiting for the storage executor to resume",
      } : null);
      setStatus("Storage execution was queued to resume from its last safe checkpoint.");
    } catch (caught) {
      setStatus(null);
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function refreshStaleStoragePlan(): Promise<void> {
    if (!wizard) return;
    setBusy(true);
    setError(null);
    setStatus("Refreshing storage identities…");
    try {
      const refreshed = await api.refreshPlan(wizard);
      const refreshedDrives = drivesFromSnapshot(refreshed.hardware_snapshot);
      const refreshedIds = new Set(refreshedDrives.map((drive) => drive.id));
      const missing = selectedDriveIds.filter((driveId) => !refreshedIds.has(driveId));
      if (missing.length) {
        throw new Error("One or more selected drives no longer match the saved identities. Return to Find storage and select the drives again.");
      }
      setSnapshot(refreshed.hardware_snapshot);
      setWizard(refreshed.wizard);
      setPlan(refreshed.plan);
      setConsentPhrase("");
      setConsentRecorded(false);
      setStorageOperation(null);
      setStorageProgress(null);
      setStorageEvents([]);
      setActiveStep(9);
      setStatus("Storage identities refreshed. Review the replacement plan before continuing.");
    } catch (caught) {
      setStatus(null);
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  function resetStorageDraftState(): void {
    setActiveStep(2);
    setMode("guided");
    setSelectedDriveIds([]);
    setTestIdentity(true);
    setTestSurfaceRead(true);
    setTestSmartShort(false);
    setTestSmartExtended(false);
    setTestDestructive(false);
    setDestructiveTestAck("");
    setPreserveData(false);
    setPreserveDataTouched(false);
    setPurpose("media");
    setProtectionPreference("one");
    setOneLargeLocation(true);
    setEasyExpansion(true);
    setPortability(["linux"]);
    setSnapshots(false);
    setEncryption("none");
    setStorageRole("individual");
    setExpansionSelection(null);
    setMergerFsTarget(mergerFsInventory?.items.length ? "" : "create");
    setMergerFsName("combined-storage");
    setMergerFsMountpoint("/mnt/combined-storage");
    setMergerFsCreatePolicy("mfs");
    setMergerFsSearchPolicy("ff");
    setUsbOverrideAck("");
    setFormatFilesystem(null);
    setFormatPartitionTable("gpt");
    setFormatAlignmentBytes(1_048_576);
    setFormatAllocationUnitBytes(null);
    setFormatNoatime(true);
    setFormatTrimMode("conditional");
    setLibraries(LIBRARY_DEFAULTS.map((library) => ({ ...library })));
    setMediaServers(["Plex"]);
    setTorrentDownloads(true);
    setUsenetDownloads(true);
    setNewLibraryName("");
    setNewLibraryType("series");
    setNewLibraryApp("Sonarr");
    setServiceUsername("media");
    setServiceCredentialMode("generate");
    setServicePassword("");
    setServicePasswordConfirmation("");
    setShowServicePassword(false);
    setShowServicePasswordConfirmation(false);
    setGeneratedServicePassword(null);
    setGeneratedPasswordCopyConfirmed(false);
    setProvisionedServiceUsername(null);
    setConnectivitySkipped(false);
    setSmbEnabled(true);
    setNfsEnabled(false);
    setIscsiEnabled(false);
    setFcoeEnabled(false);
    setShareName("data");
    setSharePath("/data");
    setConsentPhrase("");
    setConsentRecorded(false);
    setWizard(null);
    setPlan(null);
    setStorageOperation(null);
    setStorageProgress(null);
    setStorageEvents([]);
  }

  async function handleCancel(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      if (wizard) await api.cancelWizard(wizard);
      if (wizard) setSavedWizards((items) => items.filter((item) => item.id !== wizard.id));
      resetStorageDraftState();
      setStorageAction(null);
      setFirstRunSetup(false);
      setStatus("The draft storage change was discarded. Its answers were reset and no storage changes were applied.");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function saveStorageDraftForLater(): Promise<void> {
    if (generatedServicePassword) {
      setError("Copy the generated password before leaving this page. Hoardarr cannot save credentials in a draft.");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const current = await ensureWizard();
      const saved = await api.saveWizardStep(current, "draft_ui", storageDraftPayload());
      setWizard(saved);
      setPlan(null);
      setConsentPhrase("");
      setConsentRecorded(false);
      setActiveStep(Math.min(activeStep, 8));
      setServicePassword("");
      setServicePasswordConfirmation("");
      setSavedWizards((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
      setStorageAction(null);
      setFirstRunSetup(false);
      const savedAt = saved.updated_at ? new Date(saved.updated_at).toLocaleString() : "just now";
      setStatus(`Storage draft saved ${savedAt}. Passwords and consent phrases were not saved.`);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function resumeStorageDraft(draftId: string): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const saved = await api.readWizard(draftId);
      if (saved.status !== "draft" && saved.status !== "review") throw new Error("This saved storage change is no longer available.");
      const draft = objectValue(saved.answers.draft_ui);
      const selected = wizardSelectedDeviceIds(saved);
      const reservedElsewhere = new Set(savedWizards.filter((other) => other.id !== saved.id).flatMap(wizardSelectedDeviceIds));
      const conflicts = selected.filter((id) => reservedElsewhere.has(id));
      if (conflicts.length) throw new Error("The saved change cannot continue because one or more selected drives are reserved by another saved draft. Discard one of the drafts first.");
      if (!Object.hasOwn(saved.answers, "draft_ui") && Object.hasOwn(saved.answers, "storage")) {
        const refreshed = await api.refreshPlan(saved);
        const currentDrives = drivesFromSnapshot(refreshed.hardware_snapshot);
        const currentIds = new Set(currentDrives.map((drive) => drive.id));
        if (selected.some((id) => !currentIds.has(id))) throw new Error("One or more selected drives no longer match the saved identities. Start a new storage change and select the drives again.");
        const storedStorage = objectValue(refreshed.wizard.answers.storage);
        const storedTests = objectValue(storedStorage.intake_tests);
        const storedAccount = objectValue(storedStorage.service_account);
        const storedDownloads = objectValue(storedStorage.downloads);
        const storedConnectivity = objectValue(refreshed.wizard.answers.connectivity);
        const services = Array.isArray(storedConnectivity.services) ? storedConnectivity.services.map(objectValue) : [];
        const protocols = new Set(services.map((service) => stringValue(service.protocol, "")));
        const firstService = services[0] ?? {};
        const storedTopology = stringValue(storedStorage.topology, "individual");
        setExpansionSelection(expansionSelectionValue(storedStorage.expansion));
        setSnapshot(refreshed.hardware_snapshot);
        setWizard(refreshed.wizard);
        setPlan(refreshed.plan);
        setMode(refreshed.wizard.mode);
        setStorageAction("add");
        setActiveStep(9);
        setSelectedDriveIds(selected);
        setTestIdentity(booleanValue(storedTests.identity, true));
        setTestSurfaceRead(booleanValue(storedTests.full_surface_read, true));
        setTestSmartShort(booleanValue(storedTests.smart_short, false));
        setTestSmartExtended(booleanValue(storedTests.smart_extended, false));
        setTestDestructive(booleanValue(storedTests.destructive_write_read, false));
        setPreserveData(booleanValue(storedStorage.preserve_data, false));
        setPreserveDataTouched(true);
        setPurpose(stringValue(storedStorage.purpose, "media"));
        setPortability(stringArray(storedStorage.portable_systems));
        setSnapshots(booleanValue(storedStorage.snapshots, false));
        setEncryption(stringValue(storedStorage.encryption, "none"));
        setStorageRole(storedTopology === "cache" ? "download-cache" : isStorageRole(storedTopology) ? storedTopology : "individual");
        setTorrentDownloads(booleanValue(storedDownloads.torrents, true));
        setUsenetDownloads(booleanValue(storedDownloads.usenet, true));
        setServiceUsername(stringValue(storedAccount.username, "media"));
        setServiceCredentialMode(storedAccount.credential_mode === "provide_separately" ? "provide" : "generate");
        setConnectivitySkipped(booleanValue(storedConnectivity.skip, false));
        setSmbEnabled(protocols.has("smb"));
        setNfsEnabled(protocols.has("nfs"));
        setIscsiEnabled(protocols.has("iscsi"));
        setFcoeEnabled(protocols.has("fcoe"));
        setShareName(stringValue(firstService.name, "data"));
        setSharePath(stringValue(firstService.path, "/data"));
        setConsentPhrase("");
        setConsentRecorded(false);
        setStatus("Storage identities refreshed. Review the replacement plan before continuing.");
        return;
      }
      const currentSnapshot = await api.discoverHardware();
      const currentDrives = drivesFromSnapshot(currentSnapshot);
      setSnapshot(currentSnapshot);
      const selectedHardware = selected.map((id) => currentDrives.find((drive) => drive.id === id));
      const missing = selected.filter((_id, index) => !selectedHardware[index]);
      const blocked = selectedHardware.filter((drive): drive is Drive => Boolean(drive && (!drive.selectable || activeReservedDriveIds.has(drive.id))));
      if (missing.length || blocked.length) throw new Error("The saved change cannot continue because one or more selected drives are missing or no longer free. Scan storage and review the draft again.");

      const replacement = await api.startWizard(saved.mode, currentSnapshot.id);
      const rebound = await api.saveWizardStep(replacement, "draft_ui", draft);
      await api.cancelWizard(saved);
      setSavedWizards((items) => [rebound, ...items.filter((item) => item.id !== saved.id && item.id !== rebound.id)]);
      setWizard(rebound);
      setPlan(null);
      setMode(saved.mode);
      setStorageAction(draft.action === "move" || draft.action === "change" ? draft.action : "add");
      setActiveStep(integerInRange(draft.active_step, 2, 8, 2));
      setSelectedDriveIds(selected);
      const tests = objectValue(draft.tests);
      setTestIdentity(booleanValue(tests.identity, true));
      setTestSurfaceRead(booleanValue(tests.surface_read, true));
      setTestSmartShort(booleanValue(tests.smart_short, false));
      setTestSmartExtended(booleanValue(tests.smart_extended, false));
      setTestDestructive(booleanValue(tests.destructive, false));
      setDestructiveTestAck("");
      setPreserveData(booleanValue(draft.preserve_data, false));
      setPreserveDataTouched(true);
      setPurpose(stringValue(draft.purpose, "media"));
      const guidedPreferences = objectValue(draft.guided_preferences);
      const savedProtection = stringValue(guidedPreferences.protection, "one");
      setProtectionPreference(savedProtection === "none" || savedProtection === "two" ? savedProtection : "one");
      setOneLargeLocation(booleanValue(guidedPreferences.one_large_location, true));
      setEasyExpansion(booleanValue(guidedPreferences.easy_expansion, true));
      setPortability(stringArray(draft.portability).length ? stringArray(draft.portability) : ["linux"]);
      setSnapshots(booleanValue(draft.snapshots, false));
      setEncryption(stringValue(draft.encryption, "none"));
      const savedRole = stringValue(draft.storage_role, "individual");
      setStorageRole(isStorageRole(savedRole) ? savedRole : "individual");
      const array = objectValue(draft.array);
      setArrayName(stringValue(array.name, "media"));
      setMixedComponentType(array.mixed_component_type === "raid" ? "raid" : "zfs");
      setMixedComponentWidth(numberValue(array.mixed_component_width, 4));
      const mergerfs = objectValue(draft.mergerfs);
      setExpansionSelection(expansionSelectionValue(draft.expansion_selection));
      setMergerFsTarget(stringValue(mergerfs.target, mergerFsInventory?.items.length ? "" : "create"));
      setMergerFsName(stringValue(mergerfs.name, "combined-storage"));
      setMergerFsMountpoint(stringValue(mergerfs.mountpoint, "/mnt/combined-storage"));
      setMergerFsCreatePolicy(mergerfs.create_policy === "epmfs" ? "epmfs" : "mfs");
      setMergerFsSearchPolicy(mergerfs.search_policy === "all" ? "all" : "ff");
      setUsbOverrideAck("");
      const format = objectValue(draft.format);
      setFormatFilesystem(typeof format.filesystem === "string" ? format.filesystem : null);
      setFormatPartitionTable(format.partition_table === "mbr" ? "mbr" : "gpt");
      setFormatAlignmentBytes(numberValue(format.alignment_bytes, 1_048_576));
      setFormatAllocationUnitBytes(typeof format.allocation_unit_bytes === "number" ? format.allocation_unit_bytes : null);
      setFormatNoatime(booleanValue(format.noatime, true));
      const trimMode = stringValue(format.trim_mode, "conditional");
      setFormatTrimMode(isTrimMode(trimMode) ? trimMode : "conditional");
      setLibraries(libraryChoices(draft.libraries));
      setMediaServers(stringArray(draft.media_servers));
      const downloads = objectValue(draft.downloads);
      setTorrentDownloads(booleanValue(downloads.torrents, true));
      setUsenetDownloads(booleanValue(downloads.usenet, true));
      setServiceUsername(stringValue(draft.service_username, "media"));
      setServiceCredentialMode(draft.account_mode === "provide" ? "provide" : "generate");
      const connectivity = objectValue(draft.connectivity);
      setConnectivitySkipped(booleanValue(connectivity.skipped, false));
      setSmbEnabled(booleanValue(connectivity.smb, true));
      setNfsEnabled(booleanValue(connectivity.nfs, false));
      setIscsiEnabled(booleanValue(connectivity.iscsi, false));
      setFcoeEnabled(booleanValue(connectivity.fcoe, false));
      setShareName(stringValue(connectivity.share_name, "data"));
      setSharePath(stringValue(connectivity.share_path, "/data"));
      setServicePassword("");
      setServicePasswordConfirmation("");
      setGeneratedServicePassword(null);
      setGeneratedPasswordCopyConfirmed(false);
      setProvisionedServiceUsername(null);
      setConsentPhrase("");
      setConsentRecorded(false);
      setStatus(draft.account_mode === "provide" ? "Draft restored after a fresh drive scan. Re-enter the media account password before continuing; passwords are never stored in drafts." : "Draft restored after a fresh scan confirmed that its selected drives are still available.");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function discardSavedStorageDraft(draftId: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.readWizard(draftId);
      await api.cancelWizard(saved);
      setSavedWizards((items) => items.filter((item) => item.id !== draftId));
      if (wizard?.id === draftId) resetStorageDraftState();
      setStatus("The saved storage draft was discarded. No storage changes were applied.");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function releaseWizardForNewStorageWork(): Promise<void> {
    if (!wizard) return;
    if (wizard.status === "draft" || wizard.status === "review") {
      await api.cancelWizard(wizard);
      return;
    }
    if (storageOperation?.status === "succeeded") {
      await api.completeWizard(wizard.id);
      return;
    }
    throw new Error("The current storage operation must finish or be reviewed in Activity before another storage change can start.");
  }

  async function openStorageAction(action: StorageAction): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus(null);
    setConsentRecorded(false);
    try {
      await releaseWizardForNewStorageWork();
      resetStorageDraftState();
      setStorageAction(action);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function openDriveAction(action: DriveAction, driveId: string | string[], expansion?: StorageExpansionSelection): Promise<void> {
    const requestedSmartTest = action === "smart_short" ? "short" : action === "smart_long" ? "long" : null;
    const effectiveAction: DriveAction = requestedSmartTest ? "test" : action;
    const driveIds = Array.isArray(driveId) ? driveId : [driveId];
    if (driveIds.some((id) => activeReservedDriveIds.has(id))) {
      setError("This drive is already reserved by a queued or running storage operation. Open Activity to review it.");
      return;
    }
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await releaseWizardForNewStorageWork();
      resetStorageDraftState();
      setPlan(null);
      setConsentPhrase("");
      setConsentRecorded(false);
      setSelectedDriveIds(driveIds);
      setUsbOverrideAck("");
      setExpansionSelection(expansion ?? null);

      if (effectiveAction === "advanced") {
        setMode("advanced");
        setActiveStep(expansion ? 5 : 2);
        if (expansion?.configuration.topology === "zfs") {
          setStorageRole("zfs");
          const type = expansion.configuration.vdev_type;
          if (type === "mirror" || type === "raidz1" || type === "raidz2" || type === "raidz3") {
            setZfsVdevType(type);
          }
          setZfsVdevWidth(expansion.configuration.vdev_width ?? driveIds.length);
          if (expansion.target?.provider === "zfs") {
            setArrayName(expansion.target.instance_id.replace(/^zfs:/, ""));
          }
        }
        if (expansion?.configuration.topology === "raid") {
          const level = expansion.configuration.md_level;
          if (level === "raid1" || level === "raid5" || level === "raid6" || level === "raid10") {
            setMdLevel(level);
          }
        }
      } else {
        setMode("guided");
        setActiveStep(effectiveAction === "configure" ? 2 : effectiveAction === "expand" ? 5 : 3);
      }
      if (effectiveAction === "test") {
        setStorageRole("test");
        if (requestedSmartTest) {
          setTestIdentity(true);
          setTestSurfaceRead(false);
          setTestSmartShort(requestedSmartTest === "short");
          setTestSmartExtended(requestedSmartTest === "long");
          setTestDestructive(false);
        }
      }
      if (effectiveAction === "import" || effectiveAction === "test") setPreserveData(true);
      if (effectiveAction === "configure" || effectiveAction === "cache" || effectiveAction === "expand") setPreserveData(false);
      if (effectiveAction !== "test") {
        const plannedTopology = expansion?.configuration.topology;
        setStorageRole(
          plannedTopology && isStorageRole(plannedTopology)
            ? plannedTopology
            : effectiveAction === "cache" ? "download-cache" : effectiveAction === "import" ? "import" : effectiveAction === "expand" ? "mergerfs" : "individual",
        );
      }
      if (effectiveAction === "expand") {
        const target = expansion?.target?.instance_id;
        if (expansion && !mergerFsInventory?.items.some((item) => item.id === target)) {
          throw new Error("The recommended combined-storage target is no longer detected. Refresh expansion choices.");
        }
        setMergerFsTarget(target ?? mergerFsInventory?.items[0]?.id ?? "create");
      }
      setStorageAction("add");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function closeCompletedStorageAction(): Promise<void> {
    if (storageOperation?.status !== "succeeded") return;
    setBusy(true);
    setError(null);
    const mountpoint = typeof storageOperation.result?.mountpoint === "string"
      ? storageOperation.result.mountpoint
      : "/data";
    try {
      if (wizard) await api.completeWizard(wizard.id);
      const [found, foundMergerFs, foundStorage] = await Promise.all([
        api.discoverHardware(),
        api.mergerfsInventory(),
        api.storageInventory(),
      ]);
      setSnapshot(found);
      setMergerFsInventory(foundMergerFs);
      setStorageInventory(foundStorage);
      if (wizard) setSavedWizards((items) => items.filter((item) => item.id !== wizard.id));
      resetStorageDraftState();
      setStorageOperation(null);
      setStorageProgress(null);
      setStorageEvents([]);
      setStorageAction(null);
      setFirstRunSetup(false);
      setActivePage("Storage");
      setStatus(storageRole === "test" ? "Drive checks completed." : `Storage is ready at ${mountpoint}.`);
    } catch (caught) {
      setStorageAction(null);
      setActivePage("Storage");
      setStatus(`Storage was built at ${mountpoint}, but the follow-up inventory scan needs attention.`);
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  function minimizeStorageActivity(): void {
    if (generatedServicePassword) {
      setError("Copy the generated password before leaving this page. Hoardarr cannot show it again later.");
      return;
    }
    setStorageAction(null);
    setActivePage("Activity");
    setStatus(storageOperation && ["queued", "running"].includes(storageOperation.status)
      ? "The storage build is continuing in the background."
      : "The storage operation is available in Activity.");
  }

  async function changeMode(nextMode: WizardMode): Promise<void> {
    if (nextMode === mode) return;
    setBusy(true);
    setError(null);
    try {
      if (wizard) {
        await api.cancelWizard(wizard);
        const replacement = snapshot ? await api.startWizard(nextMode, snapshot.id) : null;
        setWizard(replacement);
      }
      if (nextMode === "guided" && (storageRole === "raid" || storageRole === "mixed" || ((storageRole === "snapraid" || storageRole === "zfs") && selectedDrives.some((drive) => drive.connection.bus.toLowerCase() === "usb")) || (storageRole === "zfs" && selectedDriveIds.length < 2))) setStorageRole("individual");
      if (nextMode === "guided") {
        setSnapshots(false);
        setEncryption("none");
        setTestDestructive(false);
        setDestructiveTestAck("");
        setBridge(false);
        setNfsEnabled(false);
        setIscsiEnabled(false);
        setFcoeEnabled(false);
      }
      setMode(nextMode);
      setUsbOverrideAck("");
      setPlan(null);
      setConsentPhrase("");
      setStatus("The draft plan was reset because the storage settings level changed.");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  function goBack(): void {
    setError(null);
    if (activeStep >= 8) {
      setPlan(null);
      setConsentPhrase("");
    }
    setActiveStep((step) => Math.max(0, step - 1));
  }

  function addLibrary(): void {
    const name = newLibraryName.trim();
    if (!name) {
      setError("Name the library before adding it.");
      return;
    }
    setLibraries((items) => [...items, {
      id: `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${items.length + 1}`,
      label: name,
      contentType: newLibraryType,
      app: newLibraryApp,
      selected: true,
      source: "user",
    }]);
    setNewLibraryName("");
    setError(null);
  }

  function changeServiceUsername(value: string): void {
    setServiceUsername(value.toLowerCase().replace(/[^a-z0-9_-]/g, ""));
    setProvisionedServiceUsername(null);
    setGeneratedServicePassword(null);
    setGeneratedPasswordCopyConfirmed(false);
  }

  function changeServiceCredentialMode(value: "generate" | "provide"): void {
    setServiceCredentialMode(value);
    setServicePassword("");
    setServicePasswordConfirmation("");
    setShowServicePassword(false);
    setShowServicePasswordConfirmation(false);
    setGeneratedServicePassword(null);
    setGeneratedPasswordCopyConfirmed(false);
    setProvisionedServiceUsername(null);
  }

  function validateServiceAccount(): void {
    if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(serviceUsername)) throw new Error("Use a lower-case media service username beginning with a letter or underscore.");
    if (serviceCredentialMode === "provide") {
      if (!servicePassword) throw new Error("Enter a password for the media application account.");
      if (servicePassword !== servicePasswordConfirmation) throw new Error("The media application passwords do not match.");
    }
  }

  async function provisionServiceAccountAtFinish(): Promise<void> {
    validateServiceAccount();
    const result = await api.provisionMediaAccount({
      username: serviceUsername,
      credential_mode: serviceCredentialMode,
      ...(serviceCredentialMode === "provide" ? { password: servicePassword } : {}),
    });
    if (serviceCredentialMode === "generate" && !result.credential.password) {
      throw new Error("The account was created, but the generated password was not returned. Finish setup again to replace it with a new password.");
    }
    setProvisionedServiceUsername(result.account.username);
    setGeneratedServicePassword(result.credential.password);
    setGeneratedPasswordCopyConfirmed(false);
    setServicePassword("");
    setServicePasswordConfirmation("");
    setShowServicePassword(false);
    setShowServicePasswordConfirmation(false);
    setStatus(result.account.created ? `Media application account “${result.account.username}” was created.` : `The password for media application account “${result.account.username}” was updated.`);
  }

  function confirmGeneratedPasswordSaved(): void {
    setGeneratedServicePassword(null);
    setGeneratedPasswordCopyConfirmed(true);
    setError(null);
    setStatus("Password saving was confirmed. Hoardarr removed it from this page and will never display it again.");
  }

  function renderServer() {
    const observesDst = timeZoneUsesDaylightSaving(timezone);
    const dstDefaultDescription = observesDst
      ? "This region changes its clock seasonally."
      : "This region does not currently change its clock seasonally.";
    return (
      <>
        <Card title="What should this server be called?" description="This name identifies the server on your network and in the Hoardarr interface.">
          <div className="server-settings-grid">
            <Field label="Server Name" source="Hoardarr default" hint="Use letters, numbers, dots, or hyphens. Example: media-storage"><input autoComplete="off" spellCheck={false} maxLength={253} value={serverName} onChange={(event) => setServerName(normalizeServerNameInput(event.target.value))} /></Field>
            <div className="setting-preview" aria-live="polite"><span>It will appear as</span><strong>{serverName.trim().toLowerCase() || "server-name"}</strong></div>
          </div>
        </Card>
        <Card title="Set the correct time" description="Accurate time keeps scheduled checks, alerts, security records, and certificates working properly.">
          <div className="automatic-setting">
            <span className="automatic-setting-icon" aria-hidden="true">✓</span>
            <div><strong>Set date and time automatically</strong><span>Hoardarr will keep the clock synchronized for you.</span></div>
            <span className="recommended-badge">Recommended</span>
          </div>
          <div className="form-grid server-time-grid">
            <Field label="Time Zone" source="This browser" hint={`Current regional offset: ${timeZoneOffsetLabel(timezone)}`}><select value={timezone} onChange={(event) => { setTimezone(event.target.value); setDstMode("automatic"); }}>{TIME_ZONE_OPTIONS.map((item) => <option value={item} key={item}>{timeZoneLabel(item)}</option>)}</select></Field>
            {timezone && <Field label="Daylight Saving Time" source="Selected region" hint={`${dstDefaultDescription} Automatic is the region default.`}><select value={dstMode} onChange={(event) => setDstMode(event.target.value as DaylightSavingMode)}><option value="automatic">{observesDst ? "Automatic — follow regional DST rules" : "Automatic — no regional DST changes"}</option><option value="standard_time">Standard time year-round</option></select></Field>}
            <Field label="NTP Server" source="Hoardarr recommended" hint="The default is ready to use. Separate multiple server names or addresses with commas."><input autoComplete="off" spellCheck={false} value={ntpServers} onChange={(event) => setNtpServers(event.target.value)} /></Field>
          </div>
        </Card>
        {firstRunSetup && fleetSettings && <Card title="Help improve Hoardarr" description="Review the anonymous product telemetry used to understand real home-storage hardware and improve compatibility.">
          <Notice tone="info" title="A minimal anonymous heartbeat is always sent">It contains a random installation ID, Hoardarr version, telemetry schema, platform family, and time. It does not contain disks, applications, paths, filenames, or hardware identity.</Notice>
          <label className="check-line"><input type="checkbox" checked={fleetHardwareEnabled} onChange={(event) => setFleetHardwareEnabled(event.target.checked)} /><span><strong>Share hardware and product telemetry</strong><small>Recommended and enabled by default. Sends hardware models, capacities, health summaries, storage layouts, and detected product names. It never sends full serials, paths, URLs, usernames, passwords, or file contents.</small></span></label>
          <div className="form-grid two-columns">
            <Field label="Country / Region" source={fleetSettings.location_confirmed ? "Previously confirmed" : "Suggested from server timezone"} hint="Optional two-letter code. Leave blank if the suggestion is uncertain."><input aria-label="Telemetry country or region" value={fleetCountry} maxLength={2} onChange={(event) => setFleetCountry(event.target.value.replace(/[^A-Za-z]/g, "").toUpperCase())} /></Field>
            <Field label="Telemetry timezone" source="Server setup"><input value={timezone} disabled /></Field>
          </div>
          <p className="settings-help">You can inspect every queued payload, opt out of hardware telemetry, or reset the random identity later under Settings → Telemetry &amp; Privacy.</p>
        </Card>}
      </>
    );
  }

  function selectNetworkMode(next: NetworkMode): void {
    setNetworkMode(next);
    const required = next === "single" ? 1 : 2;
    setSelectedInterfaces(interfaces.slice(0, required).map((item) => item.id));
    setNetworkPlan(null);
    markNetworkChanged("network");
  }

  function toggleNetworkInterface(interfaceId: string): void {
    setSelectedInterfaces((values) => toggleNetworkInterfaceSelection(values, interfaceId, networkMode === "single" || bridge));
    setNetworkPlan(null);
    markNetworkChanged("network");
  }

  function markNetworkChanged(...components: NetworkComponent[]): void {
    setNetworkChangedComponents((current) => Array.from(new Set([...current, ...components])));
    setNetworkPlan(null);
  }

  function renderNetwork() {
    const preview = networkPlan?.plan;
    const previewWarnings = preview?.warnings ?? [];
    const previewBlockers = preview?.blockers ?? [];
    return (
      <>
        <Card title="How should this server stay connected?" description="Choose the behavior. Hoardarr will prepare the technical settings for review.">
          <div className="choice-grid">
            <ChoiceCard name="network-mode" value="single" checked={networkMode === "single"} label="Use one connection" description="The simplest setup. Select one network port." onChange={() => selectNetworkMode("single")} />
            <ChoiceCard name="network-mode" value="active_passive" checked={networkMode === "active_passive"} label="Keep a backup connection" description="One port carries traffic; another takes over if it fails. No special switch setup is normally required." onChange={() => selectNetworkMode("active_passive")} />
            <ChoiceCard name="network-mode" value="lacp" checked={networkMode === "lacp"} label="Use a switch-managed port group" description="LACP provides link redundancy and distributes multiple flows. The switch ports must already be configured together." warning="Do not select this unless the switch is configured for LACP." onChange={() => selectNetworkMode("lacp")} />
          </div>
        </Card>
        <Card title="Network ports" description="Port names and hardware addresses are shown so selection remains predictable.">
          {interfaces.length ? <div className="interface-list">{interfaces.map((item) => (
            <label className={`interface-row ${selectedInterfaces.includes(item.id) ? "selected" : ""}`} key={item.id}>
              <input type="checkbox" checked={selectedInterfaces.includes(item.id)} onChange={() => toggleNetworkInterface(item.id)} />
              <span className="interface-main"><strong>{item.name}</strong><span>{item.model ?? "Unidentified network controller"}</span><code>{item.mac}</code>{item.warnings?.map((warning) => <small className="hardware-warning" key={warning}>{warning}</small>)}</span>
              <span><StatusBadge status={item.link} /> <strong>{speedLabel(item.speed_mbps)}</strong></span>
            </label>
          ))}</div> : <p>No network ports were returned by the server.</p>}
        </Card>
        <Card title="Addressing and discovery">
          <div className="form-grid two-columns">
            <Field label="Address assignment" source="Hoardarr default"><select value={addressing} onChange={(event) => { setAddressing(event.target.value as "dhcp" | "static"); markNetworkChanged("network"); }}><option value="dhcp">Automatic (DHCP)</option><option value="static">Static address</option></select></Field>
            <Field label="MTU" source="Hoardarr default"><input inputMode="numeric" value={mtu} onChange={(event) => { setMtu(event.target.value); markNetworkChanged("network"); }} /></Field>
            {addressing === "static" && <><Field label="Address and prefix"><input placeholder="192.0.2.10/24" value={address} onChange={(event) => { setAddress(event.target.value); markNetworkChanged("network"); }} /></Field><Field label="Gateway" hint="Optional. Leave empty for an isolated network with no default route."><input placeholder="192.0.2.1" value={gateway} onChange={(event) => { setGateway(event.target.value); markNetworkChanged("network"); }} /></Field></>}
            <Field label="DNS servers"><input value={dns} onChange={(event) => { setDns(event.target.value); markNetworkChanged("network"); }} /></Field>
            <Field label="VLAN ID" hint="Leave empty for untagged traffic."><input inputMode="numeric" value={vlan} onChange={(event) => { setVlan(event.target.value); markNetworkChanged("network"); }} /></Field>
          </div>
          <div className="check-stack">
            <label><input type="checkbox" checked={lldp} onChange={(event) => { setLldp(event.target.checked); markNetworkChanged("discovery"); }} /><span><strong>Participate in LLDP</strong><small>{lldpMode === "rx_tx" ? "Advertise this server and learn its switch port." : "Learn the switch port without advertising this server."}</small></span></label>
            <label><input type="checkbox" checked={cdpReceive} onChange={(event) => { const enabled = event.target.checked; setCdpReceive(enabled); setCdpSmart(enabled); markNetworkChanged("discovery"); }} /><span><strong>Participate in Cisco Discovery Protocol</strong><small>Advertise this server and learn its Nexus switch port.</small></span></label>
          </div>
          {mode === "advanced" && lldp && <div className="advanced-panel"><h3>Advanced: LLDP direction</h3><Field label="LLDP mode"><select value={lldpMode} onChange={(event) => { setLldpMode(event.target.value as "rx_tx" | "receive_only"); markNetworkChanged("discovery"); }}><option value="rx_tx">Receive and transmit</option><option value="receive_only">Receive only</option></select></Field></div>}
          {mode === "advanced" && <div className="advanced-panel"><h3>Advanced: Linux bridge</h3><label className="check-line"><input type="checkbox" checked={bridge} onChange={(event) => { const enabled = event.target.checked; setBridge(enabled); if (enabled) setSelectedInterfaces((items) => items.slice(0, 1)); markNetworkChanged("network"); }} />Create a bridge for guest networking</label>{bridge && <Notice tone="warning" title="Switch protection is still required">Hoardarr will enable host spanning tree, prefer RSTP, and stage a two-minute rollback. Configure spanning tree and edge/BPDU protections correctly on the physical switch. Bond physical uplinks first; one bridge cannot directly contain multiple physical uplinks.</Notice>}</div>}
          {networkPlan && (preview?.apply_available
            ? <Notice tone="success" title="Ready to apply">Review the warnings below, then apply the settings.</Notice>
            : <Notice tone="danger" title="Network settings cannot be applied">A required host tool is unavailable.</Notice>)}
          {networkPlan && <div className="plan-hash"><span>Network plan SHA-256</span><code>{networkPlan.sha256}</code></div>}
          {previewWarnings.map((warning, index) => <Notice key={String(warning.code ?? index)} tone="warning" title={String(warning.code ?? "Network warning")}>{String(warning.message ?? "Review this network warning.")}</Notice>)}
          {previewBlockers.map((blocker, index) => <Notice key={String(blocker.code ?? index)} tone="warning" title={String(blocker.code ?? "Network apply blocker")}>{String(blocker.message ?? "Network apply is blocked.")}</Notice>)}
        </Card>
      </>
    );
  }

  async function previewStandaloneNetwork(): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (!networkChangedComponents.length) {
        setStatus("No settings have changed.");
        return;
      }
      validateInterfaceSelection();
      const preview = await api.planManagedNetwork(managedNetworkPayload(), networkChangedComponents);
      setNetworkPlan(preview);
      setStatus("Network settings are ready to apply.");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function applyStandaloneNetwork(): Promise<void> {
    if (!networkPlan?.plan.apply_available) return;
    setBusy(true);
    setError(null);
    setStatus("Applying network settings…");
    try {
      validateInterfaceSelection();
      const configuration = managedNetworkPayload();
      const currentPlan = await api.planManagedNetwork(configuration, networkChangedComponents);
      if (currentPlan.sha256 !== networkPlan.sha256) {
        setNetworkPlan(currentPlan);
        throw new Error("Settings changed. Review the updated plan, then apply again.");
      }
      const pending = await api.applyManagedNetwork(configuration, currentPlan.sha256, networkChangedComponents);
      setNetworkConfirmationPending(true);
      await api.confirmManagedNetwork(pending.token);
      setNetworkConfirmationPending(false);
      setNetworkPlan(null);
      setNetworkChangedComponents([]);
      setStatus("Network settings applied.");
    } catch (caught) {
      setError(messageFromError(caught));
      setStatus("If the server became unreachable, the previous network settings will return automatically.");
    } finally {
      setBusy(false);
    }
  }

  function renderNetworkingPage() {
    return <div className="networking-page">
      {error && <Notice tone="danger" title="Networking request failed">{error}</Notice>}
      {status && <Notice tone="info" title="Networking status">{status}</Notice>}
      {networkConfirmationPending && <Notice tone="warning" title="Waiting for confirmation">The previous settings will return automatically if this browser cannot reconnect.</Notice>}
      {!networkExecutorReady && <Notice tone="danger" title="Networking tools are unavailable">Install the required host packages before applying changes.</Notice>}
      {renderNetwork()}
      <Card title="Time and monitoring">
        <div className="network-service-list">
          <section><h3>NTP</h3><Field label="Time servers" hint="Separate multiple names or addresses with commas."><input autoComplete="off" spellCheck={false} value={ntpServers} onChange={(event) => { setNtpServers(event.target.value); markNetworkChanged("ntp"); }} /></Field></section>
          <section><label className="check-line"><input type="checkbox" checked={syslogEnabled} onChange={(event) => { setSyslogEnabled(event.target.checked); markNetworkChanged("syslog"); }} />Send logs to another server</label>{syslogEnabled && <div className="form-grid three-columns"><Field label="Syslog server"><input value={syslogServer} onChange={(event) => { setSyslogServer(event.target.value); markNetworkChanged("syslog"); }} /></Field><Field label="Transport"><select value={syslogTransport} onChange={(event) => { setSyslogTransport(event.target.value as "udp" | "tcp"); markNetworkChanged("syslog"); }}><option value="udp">UDP</option><option value="tcp">TCP</option></select></Field><Field label="Port"><input inputMode="numeric" value={syslogPort} onChange={(event) => { setSyslogPort(event.target.value); markNetworkChanged("syslog"); }} /></Field></div>}</section>
          <section><label className="check-line"><input type="checkbox" checked={snmpEnabled} onChange={(event) => { setSnmpEnabled(event.target.checked); markNetworkChanged("snmp"); }} />Enable SNMP monitoring</label>{snmpEnabled && <div className="form-grid two-columns"><Field label="Read-only community"><input type="password" value={snmpCommunity} onChange={(event) => { setSnmpCommunity(event.target.value); markNetworkChanged("snmp"); }} /></Field><Field label="Allowed managers" hint="Comma separated networks"><input value={snmpManagers} onChange={(event) => { setSnmpManagers(event.target.value); markNetworkChanged("snmp"); }} /></Field><Field label="Location"><input value={snmpLocation} onChange={(event) => { setSnmpLocation(event.target.value); markNetworkChanged("snmp"); }} /></Field><Field label="Contact"><input value={snmpContact} onChange={(event) => { setSnmpContact(event.target.value); markNetworkChanged("snmp"); }} /></Field></div>}</section>
          <section><label className="check-line"><input type="checkbox" checked={trapsEnabled} onChange={(event) => { setTrapsEnabled(event.target.checked); markNetworkChanged("traps"); }} />Send SNMP traps</label>{trapsEnabled && <div className="form-grid three-columns"><Field label="Trap server"><input value={trapServer} onChange={(event) => { setTrapServer(event.target.value); markNetworkChanged("traps"); }} /></Field><Field label="Port"><input inputMode="numeric" value={trapPort} onChange={(event) => { setTrapPort(event.target.value); markNetworkChanged("traps"); }} /></Field><Field label="Community"><input type="password" value={trapCommunity} placeholder={snmpCommunity ? "Use SNMP community" : "Community"} onChange={(event) => { setTrapCommunity(event.target.value); markNetworkChanged("traps"); }} /></Field></div>}</section>
        </div>
      </Card>
      <Card title="Access list" description="Rules are evaluated from top to bottom. No rules means unrestricted host access.">
        {accessRules.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Source</th><th>Destination</th><th>Protocol</th><th>Action</th><th /></tr></thead><tbody>{accessRules.map((rule) => <tr key={rule.id}>
          <td><input aria-label={`Source for rule ${rule.id}`} value={rule.source} onChange={(event) => { setAccessRules((items) => items.map((item) => item.id === rule.id ? { ...item, source: event.target.value } : item)); markNetworkChanged("access_rules"); }} placeholder="Any, This server, or a network" /></td>
          <td><input aria-label={`Destination for rule ${rule.id}`} value={rule.destination} onChange={(event) => { setAccessRules((items) => items.map((item) => item.id === rule.id ? { ...item, destination: event.target.value } : item)); markNetworkChanged("access_rules"); }} placeholder="This server or a network" /></td>
          <td><select aria-label={`Protocol for rule ${rule.id}`} value={rule.protocol} onChange={(event) => { setAccessRules((items) => items.map((item) => item.id === rule.id ? { ...item, protocol: event.target.value as typeof ACCESS_PROTOCOLS[number] } : item)); markNetworkChanged("access_rules"); }}>{ACCESS_PROTOCOLS.map((protocol) => <option key={protocol}>{protocol}</option>)}</select></td>
          <td><select aria-label={`Action for rule ${rule.id}`} value={rule.action} onChange={(event) => { setAccessRules((items) => items.map((item) => item.id === rule.id ? { ...item, action: event.target.value as "allow" | "deny" } : item)); markNetworkChanged("access_rules"); }}><option value="allow">Allow</option><option value="deny">Deny</option></select></td>
          <td><button type="button" className="button button-secondary" onClick={() => { setAccessRules((items) => items.filter((item) => item.id !== rule.id)); markNetworkChanged("access_rules"); }}>Remove</button></td>
        </tr>)}</tbody></table></div> : <div className="empty-state compact-empty"><h3>No Hoardarr access rules</h3><p>The existing host firewall has not been interpreted or changed.</p></div>}
        <button type="button" className="button button-secondary" onClick={() => { setAccessRules((items) => [...items, { id: Date.now(), source: "any", destination: "This server", protocol: "HTTPS", action: "allow" }]); markNetworkChanged("access_rules"); }}>Add rule</button>
      </Card>
      <div className="page-actions"><button type="button" className="button button-secondary" onClick={() => void previewStandaloneNetwork()} disabled={busy || !networkExecutorReady}>{busy ? "Checking…" : "Review changes"}</button>{networkPlan?.plan.apply_available && <button type="button" className="button button-primary" onClick={() => void applyStandaloneNetwork()} disabled={busy}>{busy ? "Applying…" : "Apply settings"}</button>}</div>
    </div>;
  }

  function renderDiscovery() {
    return (
      <Card title="Storage discovery" description="Hoardarr finds drives before asking how they should be used. Identity comes from hardware, not a friendly nickname." actions={<button type="button" className="button button-secondary" onClick={() => void refreshHardware()} disabled={busy}>{busy ? "Scanning…" : snapshot ? "Scan again" : "Scan for storage"}</button>}>
        {!snapshot && !busy && <div className="empty-state"><span aria-hidden="true">▤</span><h3>No discovery snapshot yet</h3><p>Scan controllers, enclosures, direct-attached drives, and USB storage.</p></div>}
        {busy && !snapshot && <Spinner label="Reading hardware inventory…" />}
        {snapshot && <>
          <div className="snapshot-meta"><SourceBadge>Hardware scan {new Date(snapshot.captured_at).toLocaleString()}</SourceBadge><code>Snapshot {snapshot.id}</code></div>
          {drives.length ? <div className="table-scroll"><table className="data-table drive-table"><thead><tr><th scope="col"><span className="sr-only">Select</span></th><th scope="col">Device</th><th scope="col">Model</th><th scope="col">Stable identity</th><th scope="col">Capacity</th><th scope="col">Sector geometry</th><th scope="col">Connection</th><th scope="col">Location</th><th scope="col">Existing data</th></tr></thead><tbody>{drives.map((drive, index) => { const eligibilityId = `drive-eligibility-${index}`; const reserved = activeReservedDriveIds.has(drive.id); return <tr key={`${drive.id}-${index}`} className={selectedDriveIds.includes(drive.id) ? "selected-row" : ""}><td><input type="checkbox" aria-label={`Select ${drive.model} serial ${drive.serial}`} aria-describedby={eligibilityId} checked={selectedDriveIds.includes(drive.id)} disabled={!drive.selectable || reserved} onChange={() => setSelectedDriveIds((values) => checkboxToggle(values, drive.id))} /></td><td><code>{drive.path}</code>{reserved ? <small id={eligibilityId} className="hardware-warning">Reserved by an active storage build</small> : <DriveEligibility drive={drive} id={eligibilityId} />}</td><td><strong>{drive.vendor} {drive.model}</strong></td><td><span className="stacked"><code>{drive.serial}</code><code>{drive.wwn ?? "WWN not reported"}</code><StatusBadge status={drive.stableIdentity ? "Stable" : "Unstable"} /></span></td><td>{humanCapacity(drive.capacityBytes)}</td><td><SectorGeometry drive={drive} /></td><td><span className="stacked"><strong>{drive.connection.bus}</strong><span>{drive.connection.transport}</span></span></td><td>{drive.location}</td><td><ExistingData drive={drive} /></td></tr>; })}</tbody></table></div> : <Notice tone="warning" title="No drives found">Check controller support, cabling, passthrough, and whether the operating system can see the device.</Notice>}
        </>}
      </Card>
    );
  }

  function renderTests() {
    const plannedChecks = [
      testIdentity && ["identity", "Identity, sector size, and existing signatures"],
      testSurfaceRead && ["surface-read", "Full surface read"],
      testSmartShort && ["smart-short", "SMART short self-test"],
      testSmartExtended && ["smart-extended", "SMART extended self-test"],
      testDestructive && ["destructive-write-read", "Destructive write/read qualification"],
    ].filter(Boolean) as string[][];
    return (
      <>
        <Card title="Selected drives" description="Every planned check remains tied to these stable identities."><SelectedDriveSummary drives={selectedDrives} /></Card>
        <Card title="Choose drive checks" description="Recommended non-destructive checks are already selected. This step only plans checks; it does not execute them.">
          <div className="test-options">
            <CheckOption checked={testIdentity} onChange={setTestIdentity} title="Identity, sector size, and existing signatures" detail="Confirm serial/WWN, connection path, 512/4K geometry, partitions, filesystems, and RAID metadata." recommended />
            <CheckOption checked={testSurfaceRead} onChange={setTestSurfaceRead} title="Full surface read" detail="Read every addressable block without writing to the drive." recommended />
            <CheckOption checked={testSmartShort} onChange={setTestSmartShort} title="SMART short self-test" detail="Run only when the drive and transport expose the command." />
            <CheckOption checked={testSmartExtended} onChange={setTestSmartExtended} title="SMART extended self-test" detail="May take hours. Hoardarr records whether the command is actually supported." />
          </div>
          {(testSmartShort || testSmartExtended) && <div className="table-scroll"><table className="data-table"><thead><tr><th>Drive</th><th>Short self-test</th><th>Extended self-test</th><th>Capability source</th></tr></thead><tbody>{selectedDrives.map((drive) => <tr key={drive.id}><td>{drive.model} <code>{drive.serial}</code></td><td>{smartTestCapabilityLabel(drive, "short")}</td><td>{smartTestCapabilityLabel(drive, "extended")}</td><td>{drive.smartSelfTest?.source ?? "Not reported"}</td></tr>)}</tbody></table></div>}
          {mode === "advanced" && <div className="advanced-panel"><CheckOption checked={testDestructive} onChange={setTestDestructive} title="Destructive write/read qualification" detail="Write test patterns across the device, then verify them. This erases every byte." />{testDestructive && <><Notice tone="danger" title="ARE YOU SURE?">This test destroys all partitions, filesystems, files, and recovery data on the selected drives. Type <strong>I AGREE</strong> exactly to make it eligible for the plan.</Notice><Field label='Type “I AGREE”'><input value={destructiveTestAck} onChange={(event) => setDestructiveTestAck(event.target.value)} autoComplete="off" /></Field></>}</div>}
        </Card>
        <Card title="Planned drive checks — not yet run" description="These checks run when you apply the immutable plan. No pass or fail result exists until the storage worker completes them.">
          <div className="table-scroll"><table className="data-table"><thead><tr><th>Check</th><th>Status</th><th>Selected drives</th></tr></thead><tbody>{plannedChecks.map(([id, label]) => <tr key={id}><td>{label}</td><td><StatusBadge status="Planned (not run)" /></td><td>{selectedDrives.length}</td></tr>)}</tbody></table></div>
        </Card>
        <Card title="Discovery health evidence" description="These values and observations came from the read-only hardware scan, not from the planned intake tests. A value without a trustworthy source is shown as Not reported.">
          {selectedDrives.map((drive) => <div className="drive-results" key={drive.id}><h3>{drive.model} <code>{drive.serial}</code></h3><h4>Health metrics</h4>{drive.metrics.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Metric</th><th>Value</th><th>Source</th><th>Captured</th><th>Transport</th><th>Confidence</th></tr></thead><tbody>{drive.metrics.map((metric) => <tr key={metric.name}><td>{metric.label}</td><td>{metric.available ? `${metric.value}${metric.unit ? ` ${metric.unit}` : ""}` : <strong>Not reported</strong>}</td><td>{metric.provenance.source}{metric.provenance.detail && <small className="cell-detail">{metric.provenance.detail}</small>}</td><td>{new Date(metric.provenance.capturedAt).toLocaleString()}</td><td>{metric.provenance.transport}</td><td><StatusBadge status={metric.provenance.confidence} /></td></tr>)}</tbody></table></div> : <p>No health metrics were exposed by this connection path.</p>}{drive.observations.length > 0 && <><h4>Supporting observations</h4><div className="table-scroll"><table className="data-table"><thead><tr><th>Observation</th><th>Value</th><th>Lifetime metric?</th><th>Source</th><th>Captured</th><th>Transport</th><th>Confidence</th></tr></thead><tbody>{drive.observations.map((observation) => <tr key={observation.name}><td>{observation.label}{observation.reason && <small className="cell-detail">{observation.reason}</small>}</td><td>{observation.value === null ? "Not reported" : `${observation.value}${observation.unit ? ` ${observation.unit}` : ""}`}</td><td>{observation.qualifiesAsLifetime ? "Yes" : "No"}</td><td>{observation.provenance.source}</td><td>{new Date(observation.provenance.capturedAt).toLocaleString()}</td><td>{observation.provenance.transport}</td><td><StatusBadge status={observation.provenance.confidence} /></td></tr>)}</tbody></table></div></>}</div>)}
        </Card>
      </>
    );
  }

  function renderPurpose() {
    const detectedData = selectedDrives.filter(driveMayContainData);
    return (
      <>
        {detectedData.length > 0 && <Notice tone="warning" title="These drives may already contain data">{detectedData.length} selected drive{detectedData.length === 1 ? " has" : "s have"} partitions, filesystem signatures, or incomplete scan evidence. Hoardarr selected the non-formatting choice. Choose “Nothing needs to be kept” only when you are certain.</Notice>}
        <Card title="What is on these drives now?"><div className="choice-grid"><ChoiceCard name="preserve" value="yes" checked={preserveData} label="Keep or import existing data" description="Preserve the current filesystems and inspect them before making storage available." onChange={() => { setPreserveDataTouched(true); setPreserveData(true); setStorageRole(selectedDriveIds.length === 1 ? "import" : "mergerfs"); }} /><ChoiceCard name="preserve" value="no" checked={!preserveData} label="Nothing needs to be kept" description="The reviewed plan may replace partitions and filesystems after final consent." warning={detectedData.length ? "Hoardarr detected possible existing data. Formatting would erase it." : undefined} onChange={() => { setPreserveDataTouched(true); setPreserveData(false); if (storageRole === "import") setStorageRole("individual"); }} /></div></Card>
        <Card title="What will you store?" description="This determines the directory layout and safe defaults."><div className="choice-grid"><ChoiceCard name="purpose" value="media" checked={purpose === "media"} label="Media libraries" description="Movies, TV, music, photos, books, and audiobooks." onChange={() => setPurpose("media")} /><ChoiceCard name="purpose" value="general" checked={purpose === "general"} label="Files and folders" description="General shared storage for documents and other files." onChange={() => setPurpose("general")} /><ChoiceCard name="purpose" value="archive" checked={purpose === "archive"} label="Archive and important files" description="Long-lived data with a conservative storage policy." onChange={() => setPurpose("archive")} /><ChoiceCard name="purpose" value="downloads" checked={purpose === "downloads"} label="Downloads and temporary work" description="High-write workspace for torrent or Usenet activity." onChange={() => setPurpose("downloads")} /></div></Card>
        {selectedDriveIds.length > 1 && <>
          <Card title="Do you want one large storage location?" description="Plex and the ARR applications normally work best with one stable media path."><div className="choice-grid"><ChoiceCard name="one-location" value="yes" checked={oneLargeLocation} label="Yes, one large location — Recommended" description="All selected capacity appears under one media path." onChange={() => setOneLargeLocation(true)} /><ChoiceCard name="one-location" value="no" checked={!oneLargeLocation} label="Keep the drives separate" description="Each drive gets its own storage location." onChange={() => setOneLargeLocation(false)} /></div></Card>
          <Card title="Do you want protection from a drive failure?" description="Protection uses some capacity but can keep protected media available after a drive fails."><div className="choice-grid"><ChoiceCard name="protection" value="one" checked={protectionPreference === "one"} label="Protect against one drive failure — Recommended" description="Reserve enough capacity to recover after one selected drive fails." onChange={() => setProtectionPreference("one")} />{selectedDriveIds.length >= 4 && <ChoiceCard name="protection" value="two" checked={protectionPreference === "two"} label="Protect against two drive failures" description="Uses more capacity for additional protection." onChange={() => setProtectionPreference("two")} />}<ChoiceCard name="protection" value="none" checked={protectionPreference === "none"} label="No drive-failure protection" description="Use the most capacity and rely on another backup if a drive fails." onChange={() => setProtectionPreference("none")} /></div></Card>
          <Card title="Will you add more drives later?"><div className="choice-grid"><ChoiceCard name="expansion" value="yes" checked={easyExpansion} label="Yes, make expansion easy — Recommended" description="Prefer layouts that accept another drive without rebuilding the existing media." onChange={() => setEasyExpansion(true)} /><ChoiceCard name="expansion" value="no" checked={!easyExpansion} label="Probably not" description="Prefer a fixed protected group when it better matches the selected drives." onChange={() => setEasyExpansion(false)} /></div></Card>
        </>}
        <Card title="Will the drive be connected directly to another computer?" description="Choose every operating system that must read the drive without Hoardarr serving it. The Hoardarr-only choice is mutually exclusive."><div className="check-stack"><label><input type="checkbox" checked={portability.includes("windows")} onChange={() => setPortability((values) => selectPortableSystem(values, "windows"))} /><span><strong>Windows</strong><small>Use a Windows-readable filesystem and naming rules.</small></span></label><label><input type="checkbox" checked={portability.includes("macos")} onChange={() => setPortability((values) => selectPortableSystem(values, "macos"))} /><span><strong>macOS</strong><small>Use exFAT when macOS portability is selected without Windows.</small></span></label><label><input type="checkbox" checked={portability.length === 1 && portability[0] === "linux"} onChange={() => setPortability((values) => values.length === 1 && values[0] === "linux" ? [] : selectPortableSystem(values, "linux"))} /><span><strong>No, Hoardarr will always manage it</strong><small>Use a Linux-native format; clients access files through Hoardarr.</small></span></label></div></Card>
        <Card title="History and encryption">
          <div className="setting-summary"><div><span>Snapshots</span><strong>{snapshots ? "Enabled" : "Not needed"}</strong></div>{mode === "advanced" && <button type="button" className="text-button" onClick={() => setSnapshots((value) => !value)}>Change</button>}</div>
          <div className="setting-summary"><div><span>Encryption</span><strong>{encryption === "none" ? "Not encrypted" : encryption}</strong></div>{mode === "advanced" && <select aria-label="Encryption method" value={encryption} onChange={(event) => setEncryption(event.target.value)}><option value="none">No encryption</option><option value="luks2">LUKS2</option><option value="bitlocker">BitLocker (externally managed)</option></select>}</div>
          {mode === "guided" && <p className="advanced-hint">Snapshots and encryption methods—LUKS2 or externally managed BitLocker—are available in Advanced.</p>}
        </Card>
      </>
    );
  }

  function renderLayout() {
    const override = selectedDrives.some((drive) => isUsbRaidOverride(drive, storageRole));
    const unsupportedGeometry = selectedDrives.filter((drive) => !hasKnownSectorGeometry(drive));
    const geometryDependentWrite = storageChoiceNeedsSectorGeometry({ preserveData, topology: storageRole, encryption });
    const backendOwnsFilesystem = storageRole === "zfs" || (storageRole === "mixed" && mixedComponentType === "zfs");
    return (
      <>
        {mode === "guided" && <Card title="Recommended for your setup" description={recommendation.summary}>
          <div className="recommendation-head"><div><span>{selectedDriveIds.length} selected drive{selectedDriveIds.length === 1 ? "" : "s"}</span><strong>{recommendation.title}</strong></div><SourceBadge>Recommended</SourceBadge></div>
          <div className="review-grid"><ReviewLine label="Raw capacity" value={humanCapacity(recommendation.rawCapacityBytes)} /><ReviewLine label="Estimated usable" value={recommendation.usableCapacityBytes === null ? "Not calculated" : humanCapacity(recommendation.usableCapacityBytes)} /><ReviewLine label="Protection" value={recommendation.protection} /><ReviewLine label="Adding drives later" value={recommendation.expansion} /></div>
          <ul>{recommendation.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          <button type="button" className="button button-primary" onClick={applyRecommendedLayout}>Use recommended setup</button>
          <details><summary>Technical details and tradeoffs</summary><p><strong>{recommendation.technicalName}</strong></p><ul>{recommendation.tradeoffs.map((tradeoff) => <li key={tradeoff}>{tradeoff}</li>)}</ul></details>
        </Card>}
        <Card title="How should these drives be presented?" description={firstDrive?.connection.bus.toLowerCase() === "usb" ? "USB storage defaults to an independent drive. Array options are intentionally kept out of Guided setup." : "Choose how capacity should be exposed."}>
          <SelectedDriveSummary drives={selectedDrives} />
          <div className="choice-grid layout-choices">{layoutChoices.map((choice) => <ChoiceCard key={choice.id} name="storage-role" value={choice.id} checked={storageRole === choice.id} label={`${choice.label}${choice.recommended ? " — Recommended" : ""}`} description={choice.description} warning={choice.warning} onChange={() => {
            setStorageRole(choice.id);
            if (choice.id === "download-cache") setPurpose("downloads");
            if (choice.id === "block") setPurpose("block");
            if (choice.id === "mergerfs" && mergerFsInventory?.items.length === 0) setMergerFsTarget("create");
            if (choice.id === "zfs" && mode === "guided") {
              setZfsVdevType(selectedDriveIds.length === 2 ? "mirror" : "raidz1");
              setZfsVdevWidth(selectedDriveIds.length);
            }
            setUsbOverrideAck("");
            setPlan(null);
          }} />)}</div>
          {mode === "guided" && firstDrive?.connection.bus.toLowerCase() === "usb" && <Notice tone="info" title="USB arrays are kept in Advanced">ZFS vdev and SnapRAID membership can be forced in Advanced after a prominent hardware warning and typed acknowledgement.</Notice>}
          {override && <div className="advanced-panel"><Notice tone="danger" title="ARE YOU SURE?">USB bridges may hide drive identity and health, change enumeration, reset under load, or disconnect the member. This can fault or degrade the array. Type <strong>I AGREE</strong> exactly to override the guided policy.</Notice><Field label='Type “I AGREE”'><input value={usbOverrideAck} onChange={(event) => setUsbOverrideAck(event.target.value)} autoComplete="off" /></Field></div>}
        </Card>
        {mode === "guided" && storageRole === "zfs" && <Card title="How many drives may fail?" description="Hoardarr will use one protected ZFS group containing all selected drives. Exact ZFS geometry remains available in Advanced settings.">
          <div className="choice-grid layout-choices">
            <ChoiceCard name="guided-zfs-protection" value="one" checked={zfsVdevType === "mirror" || zfsVdevType === "raidz1"} label="One drive" description="Storage stays available after any one selected drive fails." onChange={() => { setZfsVdevType(selectedDriveIds.length === 2 ? "mirror" : "raidz1"); setZfsVdevWidth(selectedDriveIds.length); setPlan(null); }} />
            {selectedDriveIds.length >= 4 && <ChoiceCard name="guided-zfs-protection" value="two" checked={zfsVdevType === "raidz2"} label="Two drives" description="Storage stays available after any two selected drives fail." onChange={() => { setZfsVdevType("raidz2"); setZfsVdevWidth(selectedDriveIds.length); setPlan(null); }} />}
            {selectedDriveIds.length >= 5 && <ChoiceCard name="guided-zfs-protection" value="three" checked={zfsVdevType === "raidz3"} label="Three drives" description="Storage stays available after any three selected drives fail." onChange={() => { setZfsVdevType("raidz3"); setZfsVdevWidth(selectedDriveIds.length); setPlan(null); }} />}
          </div>
        </Card>}
        {mode === "guided" && storageRole === "snapraid" && <Card title="Media protection" description="Hoardarr combines the data drives into one media folder and keeps parity on the largest selected drive or drives."><Notice tone="info" title={recommendation.protection}>{recommendation.expansion}</Notice><details><summary>Technical details</summary><p>Uses mergerFS with {snapraidParityCount} SnapRAID parity drive{snapraidParityCount === 1 ? "" : "s"}. The first parity sync runs during setup.</p></details></Card>}
        {mode === "advanced" && (storageRole === "zfs" || storageRole === "raid" || storageRole === "snapraid" || storageRole === "mixed") && <Card title="Array settings" description="Choose the exact layout that will appear in the immutable review plan.">
          <div className="form-grid three-columns advanced-format-grid">
            <Field label="Storage name" source="Advanced selection"><input value={arrayName} onChange={(event) => { setArrayName(event.target.value.toLowerCase()); setPlan(null); }} /></Field>
            {storageRole === "zfs" && <>
              <Field label="Protection layout" source="Advanced selection"><select value={zfsVdevType} onChange={(event) => { setZfsVdevType(event.target.value as typeof zfsVdevType); setPlan(null); }}><option value="mirror">Mirror — 1 failure per vdev</option><option value="raidz1">RAIDZ1 — 1 failure per vdev</option><option value="raidz2">RAIDZ2 — 2 failures per vdev</option><option value="raidz3">RAIDZ3 — 3 failures per vdev</option></select></Field>
              <Field label="Drives per vdev" source="Advanced selection"><input type="number" min="2" max={selectedDriveIds.length || 2} value={zfsVdevWidth} onChange={(event) => { setZfsVdevWidth(Number(event.target.value)); setPlan(null); }} /></Field>
              <Field label="Sector alignment" source="Advanced selection"><select value={zfsAshift} onChange={(event) => { setZfsAshift(Number(event.target.value)); setPlan(null); }}><option value={9}>512 B</option><option value={12}>4 KiB</option><option value={13}>8 KiB</option><option value={14}>16 KiB</option></select></Field>
              <Field label="Record size" source="Advanced selection"><select value={zfsRecordsize} onChange={(event) => { setZfsRecordsize(event.target.value); setPlan(null); }}><option>128K</option><option>1M</option><option>2M</option><option>4M</option></select></Field>
              <Field label="Compression" source="Advanced selection"><select value={zfsCompression} onChange={(event) => { setZfsCompression(event.target.value); setPlan(null); }}><option value="off">Off</option><option value="lz4">LZ4</option><option value="zstd">Zstandard</option><option value="zstd-fast">Zstandard fast</option></select></Field>
            </>}
            {storageRole === "raid" && <>
              <Field label="RAID level" source="Advanced selection"><select value={mdLevel} onChange={(event) => { setMdLevel(event.target.value as typeof mdLevel); setPlan(null); }}><option value="raid1">RAID1</option><option value="raid5">RAID5</option><option value="raid6">RAID6</option><option value="raid10">RAID10</option></select></Field>
              <Field label="Chunk size" source="Advanced selection"><select value={mdChunkKib} onChange={(event) => { setMdChunkKib(Number(event.target.value)); setPlan(null); }}><option value={64}>64 KiB</option><option value={256}>256 KiB</option><option value={512}>512 KiB</option><option value={1024}>1 MiB</option></select></Field>
            </>}
            {storageRole === "snapraid" && <Field label="Parity drives" source="Advanced selection"><input type="number" min="1" max={Math.max(1, Math.min(6, selectedDriveIds.length - 1))} value={snapraidParityCount} onChange={(event) => { setSnapraidParityCount(Number(event.target.value)); setPlan(null); }} /></Field>}
            {storageRole === "mixed" && <>
              <Field label="Component pool type" source="Advanced selection"><select value={mixedComponentType} onChange={(event) => { setMixedComponentType(event.target.value as "zfs" | "raid"); setPlan(null); }}><option value="zfs">ZFS</option><option value="raid">Linux RAID</option></select></Field>
              <Field label="Drives per component" source="Advanced selection"><input type="number" min="2" max={Math.max(2, selectedDriveIds.length / 2)} value={mixedComponentWidth} onChange={(event) => { setMixedComponentWidth(Number(event.target.value)); setPlan(null); }} /></Field>
              {mixedComponentType === "zfs" ? <Field label="Component protection" source="Advanced selection"><select value={zfsVdevType} onChange={(event) => { setZfsVdevType(event.target.value as typeof zfsVdevType); setPlan(null); }}><option value="mirror">Mirror</option><option value="raidz1">RAIDZ1</option><option value="raidz2">RAIDZ2</option><option value="raidz3">RAIDZ3</option></select></Field> : <Field label="Component RAID level" source="Advanced selection"><select value={mdLevel} onChange={(event) => { setMdLevel(event.target.value as typeof mdLevel); setPlan(null); }}><option value="raid1">RAID1</option><option value="raid5">RAID5</option><option value="raid6">RAID6</option><option value="raid10">RAID10</option></select></Field>}
              <Field label="New-file placement" source="Advanced selection"><select value={mergerFsCreatePolicy} onChange={(event) => { setMergerFsCreatePolicy(event.target.value as "mfs" | "epmfs"); setPlan(null); }}><option value="mfs">Most free space</option><option value="epmfs">Existing path, then most free space</option></select></Field>
            </>}
          </div>
          {storageRole === "zfs" && <Notice tone="info" title="Vdev layout">Hoardarr will create {Math.floor(selectedDriveIds.length / Math.max(1, zfsVdevWidth))} {zfsVdevType.toUpperCase()} vdev{selectedDriveIds.length / Math.max(1, zfsVdevWidth) === 1 ? "" : "s"}. Each vdev tolerates {{ mirror: 1, raidz1: 1, raidz2: 2, raidz3: 3 }[zfsVdevType]} drive failure{zfsVdevType === "mirror" || zfsVdevType === "raidz1" ? "" : "s"}.</Notice>}
          {storageRole === "snapraid" && <Notice tone="warning" title="Parity starts out of date">The first sync runs as part of setup. Hoardarr will not report parity as current until that sync succeeds.</Notice>}
        </Card>}
        {storageRole === "mergerfs" && <Card title="Choose combined storage" description="Add these drives to an existing mergerFS path, or create a new combined path. The underlying drives remain independent filesystems.">
          {!mergerFsInventory ? <Notice tone="warning" title="Combined storage discovery is unavailable">Return to Storage and reopen this change after the server finishes loading.</Notice> : <>
            {mergerFsInventory.items.length ? <div className="choice-grid layout-choices">
              {mergerFsInventory.items.map((instance) => <ChoiceCard key={instance.id} name="mergerfs-target" value={instance.id} checked={mergerFsTarget === instance.id} label={instance.name} description={`${instance.mountpoint} · ${instance.branches.length} branch${instance.branches.length === 1 ? "" : "es"} · ${instance.active ? "Mounted" : "Configured, not mounted"}`} onChange={() => { setMergerFsTarget(instance.id); setPlan(null); }} />)}
              <ChoiceCard name="mergerfs-target" value="create" checked={mergerFsTarget === "create"} label="Create new combined storage" description="Set up a new mergerFS path using these drives as independent branches." onChange={() => { setMergerFsTarget("create"); setPlan(null); }} />
            </div> : <Notice tone="info" title="No combined storage exists yet">Hoardarr did not find a mounted or configured mergerFS instance. Creating the first one is selected.</Notice>}
            {!mergerFsInventory.available && <Notice tone="warning" title="mergerFS is not installed">The installation plan must install the mergerFS package before this combined storage can be created.</Notice>}
            {mergerFsTarget === "create" && <div className="advanced-panel mergerfs-create-panel">
              <h3>New combined storage</h3>
              <div className="form-grid two-columns">
                <Field label="Name" source="Hoardarr recommended"><input value={mergerFsName} onChange={(event) => { setMergerFsName(event.target.value.toLowerCase()); setPlan(null); }} /></Field>
                <Field label="Combined path" source="Hoardarr recommended"><input value={mergerFsMountpoint} onChange={(event) => { setMergerFsMountpoint(event.target.value); setPlan(null); }} /></Field>
              </div>
              {mode === "advanced" ? <div className="form-grid two-columns">
                <Field label="New-file placement" source="Advanced selection"><select value={mergerFsCreatePolicy} onChange={(event) => { setMergerFsCreatePolicy(event.target.value as "mfs" | "epmfs"); setPlan(null); }}><option value="mfs">Drive with most free space</option><option value="epmfs">Existing path, then most free space</option></select></Field>
                <Field label="File lookup" source="Advanced selection"><select value={mergerFsSearchPolicy} onChange={(event) => { setMergerFsSearchPolicy(event.target.value as "ff" | "all"); setPlan(null); }}><option value="ff">Use first matching copy</option><option value="all">Check all branches</option></select></Field>
              </div> : <dl className="settings-list"><div><dt aria-hidden="true">✓</dt><dd>Place new files on the drive with the most free space</dd></div><div><dt aria-hidden="true">✓</dt><dd>Use the first matching copy when reading files</dd></div><div><dt aria-hidden="true">✓</dt><dd>Keep every branch independently readable</dd></div></dl>}
            </div>}
          </>}
        </Card>}
        {preserveData ? <Card title="Preserve existing partitions and filesystems" description="Your preservation answer suppresses partition-table and filesystem-create actions. Hoardarr will request inspection/import rather than promise a format.">
          <Notice tone={geometryDependentWrite ? "danger" : "info"} title={geometryDependentWrite ? "This layout can still write storage metadata" : "Non-destructive preservation path"}>{geometryDependentWrite ? `Creating the ${storageRole} layout or encryption can overwrite metadata even though filesystems are being preserved. The immutable plan must mark those actions destructive.` : "The individual or combined-storage layout does not require a new partition table or filesystem in the current plan."}</Notice>
          {unsupportedGeometry.length > 0 && <Notice tone={geometryDependentWrite ? "danger" : "warning"} title="Sector format is not write-compatible">{unsupportedGeometry.map((drive) => <div key={drive.id}><code>{drive.path}</code>: {sectorGeometryAssessment(drive).message}</div>)}<div>{geometryDependentWrite ? "Continue is blocked because this selection requires a geometry-dependent write." : "Import may continue because the current selection is non-destructive. Any later formatting, cache/array creation, or encryption choice will be blocked."}</div></Notice>}
        </Card> : backendOwnsFilesystem ? <Card title="Backend-managed storage format" description="This backend owns its on-disk format; Hoardarr will not create a separate filesystem on each selected drive.">
          <Notice tone="info" title={storageRole === "zfs" ? "ZFS manages these drives directly" : "The selected component pools manage these drives directly"}>The immutable review will show the exact backend actions. Filesystem, partition-table, allocation-unit, and TRIM controls that do not apply to this layout are intentionally hidden.</Notice>
          {unsupportedGeometry.length > 0 && <Notice tone="danger" title="Storage creation cannot be planned">{unsupportedGeometry.map((drive) => <div key={drive.id}><code>{drive.path}</code>: {sectorGeometryAssessment(drive).message}</div>)}</Notice>}
        </Card> : <Card title="Proposed disk format" description="These settings will be placed in the exact review plan. No formatting runs until you approve that plan and select Build storage.">
          {unsupportedGeometry.length > 0 && <Notice tone="danger" title="Formatting cannot be planned">{unsupportedGeometry.map((drive) => <div key={drive.id}><code>{drive.path}</code>: {sectorGeometryAssessment(drive).message}</div>)}</Notice>}
          {mode === "guided" ? <>
            <div className="recommendation-head"><div><span>Filesystem</span><strong>{filesystem.filesystem}</strong></div><SourceBadge>{portability.includes("windows") ? "Windows portability answer" : portability.includes("macos") ? "macOS portability answer" : "Hoardarr storage policy"}</SourceBadge></div>
            <p>{filesystem.reason}</p>
            <dl className="settings-list">{filesystem.settings.map((setting) => <div key={setting}><dt aria-hidden="true">✓</dt><dd>{setting}</dd></div>)}</dl>
          </> : <>
            <Notice tone="info" title="Advanced disk format">These values are your selections. They replace Hoardarr's recommended format defaults in the immutable plan.</Notice>
            <div className="form-grid three-columns advanced-format-grid">
              <Field label="Filesystem" source="Advanced selection"><select aria-label="Filesystem" value={selectedFilesystem} onChange={(event) => { const value = event.target.value; setFormatFilesystem(value); setFormatAllocationUnitBytes(value === "exfat" ? 131_072 : 4096); setPlan(null); }}><option value="ext4">ext4</option><option value="xfs">XFS</option><option value="btrfs">Btrfs</option><option value="ntfs">NTFS</option><option value="exfat">exFAT</option></select></Field>
              <Field label="Partition table" source="Advanced selection"><select aria-label="Partition table" value={formatPartitionTable} onChange={(event) => { setFormatPartitionTable(event.target.value as "gpt" | "mbr"); setPlan(null); }}><option value="gpt">GPT (recommended)</option><option value="mbr">MBR</option></select></Field>
              <Field label="Partition alignment" source="Advanced selection"><select aria-label="Partition alignment" value={formatAlignmentBytes} onChange={(event) => { setFormatAlignmentBytes(Number(event.target.value)); setPlan(null); }}><option value={1_048_576}>1 MiB (recommended)</option><option value={4_194_304}>4 MiB</option></select></Field>
              <Field label="Allocation unit" source="Advanced selection"><select aria-label="Allocation unit" value={selectedAllocationUnitBytes} onChange={(event) => { setFormatAllocationUnitBytes(Number(event.target.value)); setPlan(null); }}><option value={4096}>4 KiB</option><option value={16_384}>16 KiB</option><option value={65_536}>64 KiB</option><option value={131_072}>128 KiB</option></select></Field>
              <Field label="TRIM / discard" source="Advanced selection"><select aria-label="TRIM or discard" value={formatTrimMode} onChange={(event) => { setFormatTrimMode(event.target.value as "conditional" | "periodic" | "continuous" | "disabled"); setPlan(null); }}><option value="conditional">Automatic when supported</option><option value="periodic">Scheduled fstrim</option><option value="continuous">Continuous discard</option><option value="disabled">Disabled</option></select></Field>
              <label className="advanced-format-toggle"><input type="checkbox" checked={formatNoatime} onChange={(event) => { setFormatNoatime(event.target.checked); setPlan(null); }} /><span><strong>Use noatime</strong><small>Do not write an access timestamp every time a file is read.</small></span></label>
            </div>
          </>}
          <Notice tone="warning" title="Formatting would destroy data">The immutable plan lists the exact device identities and operations. Hoardarr will not execute it until you type the required consent and separately select Build storage.</Notice>
        </Card>}
      </>
    );
  }

  function renderLibraries() {
    return (
      <>
        <Card title="Which media server do you use?" description="This keeps folder names and access guidance aligned with your media applications. You can select more than one."><div className="check-stack">{["Plex", "Jellyfin", "Emby"].map((server) => <label key={server}><input type="checkbox" checked={mediaServers.includes(server)} onChange={() => setMediaServers((values) => checkboxToggle(values, server))} /><span><strong>{server}</strong><small>Use the same stable <code>/data/media</code> library root.</small></span>{server === "Plex" && <SourceBadge>Common choice</SourceBadge>}</label>)}</div></Card>
        <Card title="Media libraries" description="Recommendations and app-detected values remain selected but visible. Uncheck anything you do not want.">
          <div className="library-list">{libraries.map((library) => <label className="library-row" key={library.id}><input type="checkbox" checked={library.selected} onChange={() => setLibraries((items) => items.map((item) => item.id === library.id ? { ...item, selected: !item.selected } : item))} /><span className="library-name"><strong>{library.label}</strong><code>/data/media/{library.id}</code></span><span>{library.app}</span><SourceBadge>{library.source === "detected" ? "Connected app" : library.source === "user" ? "You" : "Hoardarr recommended"}</SourceBadge></label>)}</div>
          <details className="add-library"><summary>Add another library</summary><div className="form-grid three-columns"><Field label="Library name"><input value={newLibraryName} onChange={(event) => setNewLibraryName(event.target.value)} placeholder="Anime" /></Field><Field label="Content type"><select value={newLibraryType} onChange={(event) => setNewLibraryType(event.target.value)}><option value="series">Series</option><option value="movies">Movies</option><option value="movies-and-series">Movies and series</option><option value="music">Music</option><option value="books">Books</option><option value="photos">Photos</option></select></Field><Field label="Application"><select value={newLibraryApp} onChange={(event) => setNewLibraryApp(event.target.value)}><option>Sonarr</option><option>Radarr</option><option>Sonarr + Radarr</option><option>Lidarr</option><option>Readarr</option><option>Immich</option><option>File share</option></select></Field></div><button type="button" className="button button-secondary" onClick={addLibrary}>Add library</button></details>
        </Card>
        <Card title="How do you download?" description="Downloader APIs can prefill these answers during app onboarding. They stay visible and can be unchecked.">
          <div className="download-layout"><label><input type="checkbox" checked={torrentDownloads} onChange={(event) => setTorrentDownloads(event.target.checked)} /><span><strong>Torrents</strong><code>/data/downloads/torrents/incomplete</code><code>/data/downloads/torrents/complete</code></span><SourceBadge>Hoardarr recommended</SourceBadge></label><label><input type="checkbox" checked={usenetDownloads} onChange={(event) => setUsenetDownloads(event.target.checked)} /><span><strong>Usenet</strong><code>/data/downloads/usenet/incomplete</code><code>/data/downloads/usenet/complete</code></span><SourceBadge>Hoardarr recommended</SourceBadge></label></div>
          {storageRole === "download-cache" ? <Notice tone="warning" title="Downloads stay fast while media moves safely">Completed torrents remain on the fast drive while seeding, with a copy imported to media. Usenet repair and unpack work stays on the fast drive before the completed file moves to media.</Notice> : <Notice tone="info" title="Completed files can move without another copy">When downloads and libraries share this filesystem, Hoardarr uses hardlinks where the application supports them. Cross-filesystem imports use a real copy or move.</Notice>}
        </Card>
      </>
    );
  }

  function renderSharing() {
    return (
      <>
        <Card title="Media application account" description="One service account is shared by the Docker containers on the application server. Per-application accounts and ACLs are available in Advanced.">
          <div className="form-grid two-columns">
            <Field label="Service username" source="Hoardarr recommended" hint="Lower-case letters, numbers, dashes, and underscores."><input value={serviceUsername} onChange={(event) => changeServiceUsername(event.target.value)} autoComplete="username" maxLength={32} /></Field>
            <Field label="Password" source="Your choice"><select value={serviceCredentialMode} onChange={(event) => changeServiceCredentialMode(event.target.value as "generate" | "provide")}><option value="generate">Generate a password for me</option><option value="provide">Set my own password</option></select></Field>
          </div>
          {serviceCredentialMode === "provide" && <div className="form-grid two-columns">
            <Field label="Password" hint="Any non-empty password is accepted; no complexity rules are imposed."><div className="password-field-with-eye"><input aria-label="Media application password" type={showServicePassword ? "text" : "password"} value={servicePassword} onChange={(event) => { setServicePassword(event.target.value); setProvisionedServiceUsername(null); }} autoComplete="new-password" /><button type="button" className="credential-eye-button" aria-label={showServicePassword ? "Hide media application password" : "Show media application password"} aria-pressed={showServicePassword} onClick={() => setShowServicePassword((shown) => !shown)}><EyeIcon crossed={showServicePassword} /></button></div></Field>
            <Field label="Confirm password"><div className="password-field-with-eye"><input aria-label="Confirm media application password" type={showServicePasswordConfirmation ? "text" : "password"} value={servicePasswordConfirmation} onChange={(event) => { setServicePasswordConfirmation(event.target.value); setProvisionedServiceUsername(null); }} autoComplete="new-password" /><button type="button" className="credential-eye-button" aria-label={showServicePasswordConfirmation ? "Hide confirmation password" : "Show confirmation password"} aria-pressed={showServicePasswordConfirmation} onClick={() => setShowServicePasswordConfirmation((shown) => !shown)}><EyeIcon crossed={showServicePasswordConfirmation} /></button></div></Field>
          </div>}
          <Notice tone="info" title="Created only when setup finishes">Hoardarr will create this account on the final wizard step. If Hoardarr generates the password, that final page is the only place it will be displayed.</Notice>
          <Notice tone="info" title="No shell or administrator access">This identity can modify media and download folders. It does not receive a login shell or Hoardarr administrator rights.</Notice>
        </Card>
        <Card title="Windows file access" description="SMB provides Windows-style user and group permissions for human access.">
          <div className="permissions-table" role="table" aria-label="Default SMB permissions"><div role="row" className="permissions-head"><span role="columnheader">Identity</span><span role="columnheader">Access</span></div><div role="row"><span role="cell">Administrators</span><strong role="cell">Full Control</strong></div><div role="row"><span role="cell">Media applications ({serviceUsername})</span><strong role="cell">Modify</strong></div><div role="row"><span role="cell">Media users</span><strong role="cell">Read &amp; Execute</strong></div><div role="row"><span role="cell">Anonymous</span><strong role="cell">No access</strong></div></div>
          <Notice tone="info" title="Linux permissions, familiar Windows access">Hoardarr uses Linux users, groups, and POSIX ACLs on Linux-native storage. SMB presents those permissions to Windows clients as familiar Full Control, Modify, and Read &amp; Execute access. NTFS is not required.</Notice>
          {importingNtfs && <Notice tone="warning" title="Imported NTFS filesystem detected">Hoardarr will preserve and serve the existing NTFS filesystem rather than reformat it. Existing Windows SID-based permissions require inspection and mapping before Hoardarr enables write access; no ACL is changed automatically.</Notice>}
          {mode === "advanced" && <div className="advanced-panel"><h3>Advanced file services</h3><p>Per-user ACLs, explicit deny entries, NFS, iSCSI, VM datastores, FCoE, and redundant controller paths belong in Advanced workflows.</p></div>}
        </Card>
      </>
    );
  }

  function renderConnectivity() {
    const combinedBackingPath = storageRole === "mergerfs"
      ? mergerFsTarget === "create"
        ? mergerFsMountpoint
        : mergerFsInventory?.items.find((item) => item.id === mergerFsTarget)?.mountpoint ?? "the selected combined-storage mount"
      : "the selected drive mount";
    return <>
      <Card title="How should other systems connect?" description="Hoardarr can publish the folders after storage is built, or you can skip this and configure them later from Storage Access.">
        <div className="choice-grid layout-choices">
          <ChoiceCard name="connectivity-timing" value="now" checked={!connectivitySkipped} label="Set up access now — Recommended" description="Include the selected connection methods in the immutable plan before approval." onChange={() => { setConnectivitySkipped(false); setPlan(null); }} />
          <ChoiceCard name="connectivity-timing" value="later" checked={connectivitySkipped} label="Set up storage access later" description="Build storage without publishing a share or target. Use Storage Access from the main menu later." onChange={() => { setConnectivitySkipped(true); setPlan(null); }} />
        </div>
      </Card>
      {!connectivitySkipped && <Card title="Connection methods" description="Media applications on one server normally use SMB. Block-storage and UNIX export choices remain under Advanced.">
        <div className="check-stack">
          <label><input type="checkbox" checked={smbEnabled} onChange={(event) => { setSmbEnabled(event.target.checked); setPlan(null); }} /><span><strong>Windows file sharing (SMB)</strong><small>Publish the selected folder to Windows, Linux, macOS, and containers using the media application account.</small></span><SourceBadge>Recommended</SourceBadge></label>
          {mode === "advanced" && <>
            <label><input type="checkbox" checked={nfsEnabled} onChange={(event) => { setNfsEnabled(event.target.checked); setPlan(null); }} /><span><strong>UNIX file sharing (NFS)</strong><small>Export a folder to explicitly allowed clients. This executor is not enabled yet and will block Apply.</small></span></label>
            <label><input type="checkbox" checked={iscsiEnabled} onChange={(event) => { setIscsiEnabled(event.target.checked); setPlan(null); }} /><span><strong>Block storage (iSCSI)</strong><small>Create a target and LUN for a remote host. Target size and initiator access must be completed before Apply.</small></span></label>
            <label><input type="checkbox" checked={fcoeEnabled} onChange={(event) => { setFcoeEnabled(event.target.checked); setPlan(null); }} /><span><strong>Fibre Channel over Ethernet (FCoE)</strong><small>Create an advanced block target only after interface, fabric, and access settings are complete.</small></span></label>
          </>}
        </div>
        {smbEnabled && <>
          <Card title="What should this SMB share contain?" description="Hoardarr publishes a stable application path, while the storage pool remains mounted at its exact backing path.">
            <div className="choice-grid layout-choices">
              <ChoiceCard name="smb-share-scope" value="application-root" checked={sharePath === "/data"} label="Media and downloads together — Recommended" description="Share /data so download clients and media applications see one filesystem tree. This supports atomic moves and hardlinks without copying completed downloads." onChange={() => { setSharePath("/data"); if (shareName === "media") setShareName("data"); setPlan(null); }} />
              <ChoiceCard name="smb-share-scope" value="media-only" checked={sharePath === "/data/media"} label="Media libraries only" description="Share /data/media for playback and library access. Download folders remain private and require separate connectivity if another system needs them." onChange={() => { setSharePath("/data/media"); if (shareName === "data") setShareName("media"); setPlan(null); }} />
            </div>
            <Notice tone="info" title="One storage pool, two useful paths">The storage is mounted at <code>{combinedBackingPath}</code> and presented to applications at <code>/data</code>. They refer to the same storage; Hoardarr is not creating another copy.</Notice>
          </Card>
          <div className="form-grid two-columns">
            <Field label="Share name" source="Hoardarr recommended"><input value={shareName} onChange={(event) => { setShareName(event.target.value); setPlan(null); }} /></Field>
            {mode === "advanced"
              ? <Field label="Exact folder to publish" source="Advanced"><input value={sharePath} onChange={(event) => { setSharePath(event.target.value); setPlan(null); }} /></Field>
              : <Field label="Folder to publish" source="Chosen above"><input value={sharePath} readOnly aria-readonly="true" /></Field>}
          </div>
        </>}
        {(nfsEnabled || iscsiEnabled || fcoeEnabled) && <Notice tone="warning" title="Advanced connectivity needs more settings">The immutable plan will name the missing privileged executor or target details and keep Apply disabled. Hoardarr will not silently omit a selected protocol.</Notice>}
      </Card>}
    </>;
  }

  function renderReview() {
    const reviewFilesystem = preserveData
      ? importedFilesystems.length ? `Preserve ${importedFilesystems.join(", ")}` : "Preserve detected filesystem"
      : storageRole === "zfs" ? "ZFS"
      : storageRole === "mixed" && mixedComponentType === "zfs" ? "ZFS component pools"
      : mode === "advanced" ? selectedFilesystem
      : filesystem.filesystem;
    return (
      <>
        {planNeedsApproval ? <Notice tone="danger" title="ARE YOU SURE?">{String(planRisk.message ?? "The plan contains destructive storage actions.")} Nothing has been changed yet.</Notice> : planDeclaredNonDestructive ? <Notice tone="success" title="No destructive approval is required">The backend explicitly marked this plan as non-destructive.</Notice> : <Notice tone="warning" title="Risk declaration is incomplete">The plan did not explicitly declare both destructive risk and approval status. Treat any undeclared action conservatively and do not apply it.</Notice>}
        <Card title="Exact drives in this plan" description="Verify device, model, serial or WWN, capacity, connection, and physical location—not just a friendly label."><SelectedDriveSummary drives={selectedDrives} detailed /></Card>
        <div className="review-grid">
          <Card title="Storage"><ReviewLine label="Setup" value={storageRoleLabel(storageRole)} />{mode === "guided" && <><ReviewLine label="Raw capacity" value={humanCapacity(recommendation.rawCapacityBytes)} /><ReviewLine label="Estimated usable" value={recommendation.usableCapacityBytes === null ? "Not calculated" : humanCapacity(recommendation.usableCapacityBytes)} /><ReviewLine label="Drive failure" value={recommendation.protection} /></>}{storageRole === "mergerfs" && <ReviewLine label="Combined storage" value={mergerFsTarget === "create" ? `${mergerFsName} (${mergerFsMountpoint})` : mergerFsInventory?.items.find((item) => item.id === mergerFsTarget)?.mountpoint ?? "Not selected"} />}<ReviewLine label="Filesystem" value={reviewFilesystem} /><ReviewLine label="Existing data" value={preserveData ? "Preserve/import" : "Replace only after final consent"} /><details><summary>Technical details</summary><ReviewLine label="Backend topology" value={storageRole} /><ReviewLine label="Partitioning" value={preserveData ? "Preserve existing" : storageRole === "zfs" ? "Whole-device ZFS vdevs; no partition creation planned" : "GPT, 1 MiB aligned"} /></details></Card>
          {storageRole !== "test" && <Card title="Libraries and downloads"><ReviewLine label="Media server" value={mediaServers.join(", ") || "None selected"} /><ReviewLine label="Libraries" value={libraries.filter((library) => library.selected).map((library) => library.label).join(", ")} /><ReviewLine label="Torrents" value={torrentDownloads ? "Configured" : "Not configured"} /><ReviewLine label="Usenet" value={usenetDownloads ? "Configured" : "Not configured"} /><ReviewLine label="Media path" value="/data/media" /></Card>}
          {storageRole !== "test" && <Card title="File access"><ReviewLine label="Protocol" value="SMB" /><ReviewLine label="Application identity" value={serviceUsername} /><ReviewLine label="Application access" value="Modify" /><ReviewLine label="Anonymous" value="No access" /></Card>}
          {storageRole !== "test" && <Card title="Storage Access"><ReviewLine label="When" value={connectivitySkipped ? "Set up later" : "Apply with storage"} /><ReviewLine label="Methods" value={connectivitySkipped ? "None" : [smbEnabled && "SMB", nfsEnabled && "NFS", iscsiEnabled && "iSCSI", fcoeEnabled && "FCoE"].filter(Boolean).join(", ")} /><ReviewLine label="Name" value={connectivitySkipped ? "—" : shareName} /><ReviewLine label="Path" value={connectivitySkipped ? "—" : sharePath} mono /></Card>}
          <Card title="Network"><ReviewLine label="Connection" value={networkMode} /><ReviewLine label="Selected ports" value={selectedInterfaceSummary} mono /><ReviewLine label="LLDP" value={lldp ? lldpMode === "rx_tx" ? "Receive + transmit" : "Receive only" : "Disabled"} /><ReviewLine label="CDP" value={cdpReceive ? cdpSmart ? "Listen + smart transmit" : "Listen only" : "Disabled"} /><ReviewLine label="Plan status" value={networkPlan ? networkPlan.plan.apply_available ? "Preview reports apply available" : "Review-only — apply blocked" : "Not previewed"} />{networkPlan && <ReviewLine label="Plan SHA-256" value={networkPlan.sha256} mono />}{networkPlan?.plan.blockers.map((blocker) => <Notice key={blocker.code} tone="warning" title={blocker.code}>{blocker.message}</Notice>)}</Card>
        </div>
        {plan ? <><BackendStoragePlan storage={plan.document.storage} /><Card title="Immutable backend plan"><ReviewLine label="Plan ID" value={plan.id} mono /><ReviewLine label="Revision" value={String(plan.revision)} /><ReviewLine label="SHA-256" value={plan.sha256} mono />{plan.document.blockers?.map((blocker) => <Notice key={blocker.code} tone="warning" title={blocker.code}>{blocker.message}</Notice>)}</Card></> : <Notice tone="warning" title="No current plan">Return to the previous step and create a review plan.</Notice>}
      </>
    );
  }

  function renderConsent() {
    const accountReady = provisionedServiceUsername === serviceUsername;
    return (
      <>
        {!storageOperation && <>
          {planNeedsApproval ? <>
            <Notice tone="danger" title="ARE YOU SURE?">Continuing authorizes the exact destructive actions in the immutable plan below. If the plan, selected devices, connectivity, or hardware identity changes, this consent must be collected again.</Notice>
            <Card title="Final identity check"><SelectedDriveSummary drives={selectedDrives} detailed /><div className="plan-hash"><span>Plan SHA-256</span><code>{plan?.sha256 ?? "No current plan"}</code></div></Card>
            <Card title="Explicit consent" description='Type the exact, case-sensitive words “I AGREE”, then select Apply settings. A checkbox or generic confirmed=true value is not accepted.'>
              <Field label='Type “I AGREE”'><input className="consent-input" value={consentPhrase} onChange={(event) => setConsentPhrase(event.target.value)} autoComplete="off" spellCheck={false} /></Field>
              <p className="consent-state" aria-live="polite">{exactConsentAccepted(consentPhrase) ? "Exact phrase accepted for this plan. Settings have not been applied yet." : "Consent has not been accepted. Nothing has been applied."}</p>
            </Card>
          </> : <Card title="Ready to apply"><Notice tone={planDeclaredNonDestructive ? "success" : "warning"} title={planDeclaredNonDestructive ? "No destructive approval is required" : "Risk declaration is incomplete"}>{planDeclaredNonDestructive ? "The backend explicitly marked this plan as non-destructive. Select Apply settings to start it." : "The plan cannot be trusted or applied until its risk declaration is complete."}</Notice></Card>}
          <Notice tone="warning" title="Storage has not been built">The wizard questions are complete, but no disk or connectivity work has started. <strong>Apply settings</strong> is the separate execution step.</Notice>
        </>}
        {storageOperation && <Card title="Storage activity" description={`Operation ${storageOperation.id}`}>
          <div className="storage-operation-heading">
            <StatusBadge status={storageOperation.status.replace("_", " ")} />
            <strong>{storageProgress?.percent ?? (storageOperation.status === "succeeded" ? 100 : 0)}%</strong>
          </div>
          <div className="operation-progress-track" role="progressbar" aria-label="Storage build progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={storageProgress?.percent ?? 0}>
            <span style={{ width: `${storageProgress?.percent ?? (storageOperation.status === "succeeded" ? 100 : 0)}%` }} />
          </div>
          <StorageProgressDetails progress={storageProgress} />
          {storageProgress && <StorageOperationNotices notices={storageProgress.notices} />}
          {storageOperation.status === "succeeded" && <Notice tone="success" title="Storage build completed">The executor completed the approved plan. Select Close to refresh inventory and leave the wizard.</Notice>}
          {["failed", "cancelled", "needs_attention"].includes(storageOperation.status) && <Notice tone="danger" title={storageOperation.error?.code ?? "Storage was not completed"}>{storageOperation.error?.detail ?? storageOperation.error?.message ?? "The operation stopped. No success is being claimed; review the latest event before retrying."}</Notice>}
          {storageOperation.status === "needs_attention" && <div className="button-row"><button type="button" className="button button-primary" disabled={busy} onClick={() => void resumeStorageBuild()}>Resume from safe checkpoint</button><button type="button" className="button button-secondary" onClick={minimizeStorageActivity}>Review in Activity</button></div>}
          {storageEvents.length > 0 && <details className="operation-events"><summary>Operation details</summary><ol>{storageEvents.slice(-12).map((event) => <li key={event.sequence}><time>{new Date(event.created_at).toLocaleTimeString()}</time><span>{event.message}</span></li>)}</ol></details>}
        </Card>}
        {storageRole !== "test" && storageOperation?.status === "succeeded" && accountReady && <Card title="File access account is ready" description={`The non-login Linux account and SMB credential are enabled for ${serviceUsername}.`}>
          {serviceCredentialMode === "provide" && <Notice tone="success" title="Your password is active">Hoardarr does not display or store the password you supplied.</Notice>}
          {generatedPasswordCopyConfirmed && <Notice tone="success" title="Password saved and removed">You explicitly confirmed that the generated password was saved. Hoardarr permanently removed it from this page and cannot display it again.</Notice>}
        </Card>}
        {storageRole !== "test" && storageOperation?.status === "succeeded" && generatedServicePassword && <Card title="Save your generated password" description="Storage completed first. This is the only time Hoardarr will display this credential, and it starts hidden.">
          <OneTimePassword password={generatedServicePassword} onSavedConfirmed={confirmGeneratedPasswordSaved} onCopyError={() => setError("The browser could not copy the password. Use the eye button, select the password, and copy it manually. Hoardarr will keep showing it until you explicitly confirm that it is saved.")} />
          <Notice tone="warning" title="Verify before confirming">Mobile browsers can report a successful copy incorrectly. Paste the password into your password manager and verify it, then select <strong>I saved this password</strong>. Only that explicit confirmation removes it.</Notice>
        </Card>}
        {storageRole !== "test" && storageOperation?.status === "succeeded" && !accountReady && <Notice tone="warning" title="Storage is ready; file access needs attention">Hoardarr is creating the file-access credential. If it does not complete, use Create access credential to retry without rebuilding storage.</Notice>}
      </>
    );
  }

  const pageCopy = [
    ["Name this server", "Choose the name shown on your network and confirm its clock settings."],
    ["Connect to the network", "Choose straightforward networking behavior. The backend prepares a reviewable, rollback-aware plan."],
    ["Find and identify storage", "Discovery comes before storage decisions so every answer is tied to actual hardware."],
    ["Check drive condition", "Plan intake tests and inspect read-only discovery evidence with its source, time, transport, and confidence."],
    ["Tell us how the drives will be used", "Simple answers let Hoardarr choose compatible storage defaults."],
    ["Choose a storage layout", "Guided choices remain easy to understand while unsafe USB array paths stay behind Advanced warnings."],
    ["Prepare media and download folders", "Create predictable paths and let connected applications prefill their assignments."],
    ["Set up file access", "Use one media application identity by default and Windows-style permissions for people."],
    ["Choose storage access", "Publish folders now or skip this step and configure SMB, NFS, iSCSI, or FCoE later from Storage Access."],
    ["Review the exact plan", "Nothing is applied here. Verify identities, actions, warnings, and the immutable plan hash."],
    ["Apply settings", "Approve the exact plan, monitor real backend activity, then save the file-access credential and close."],
  ] as const;

  const body = activeStep === 0 ? renderServer() : activeStep === 1 ? renderNetwork() : activeStep === 2 ? renderDiscovery() : activeStep === 3 ? renderTests() : activeStep === 4 ? renderPurpose() : activeStep === 5 ? renderLayout() : activeStep === 6 ? renderLibraries() : activeStep === 7 ? renderSharing() : activeStep === 8 ? renderConnectivity() : activeStep === 9 ? renderReview() : renderConsent();
  const finalAccountReady = storageRole === "test" || provisionedServiceUsername === serviceUsername;
  const finalGeneratedPasswordPending = finalAccountReady && serviceCredentialMode === "generate" && generatedServicePassword !== null;
  const storageTerminalWithAttention = storageOperation && ["failed", "cancelled", "needs_attention"].includes(storageOperation.status);
  const storageCanResume = storageOperation?.status === "needs_attention";
  const planExecutable = plan?.document.apply_available === true && (plan.document.blockers?.length ?? 0) === 0;
  const wizardNext = activeStep === 10
    ? !storageOperation
      ? submitConsent
      : storageOperation.status === "succeeded"
        ? !finalAccountReady
          ? provisionServiceAccountAtFinish
          : finalGeneratedPasswordPending
            ? undefined
            : closeCompletedStorageAction
        : storageCanResume
          ? resumeStorageBuild
          : storageTerminalWithAttention
            ? minimizeStorageActivity
          : undefined
    : advance;
  const wizardNextLabel = activeStep === 9
    ? planNeedsApproval ? "Continue to consent" : "Continue"
    : activeStep === 10
      ? !storageOperation
        ? "Apply settings"
        : storageCanResume
          ? "Resume from safe checkpoint"
          : storageTerminalWithAttention
            ? "Review in Activity"
          : storageOperation.status === "succeeded" && !finalAccountReady
            ? "Create access credential"
            : storageOperation.status === "succeeded" ? "Close" : "Working…"
      : activeStep === 1 && networkPlan?.plan.apply_available
        ? "Apply and continue"
        : "Continue";

  if (!authenticated) {
    return <AuthenticationPage setupStatus={setupStatus} busy={busy} error={error} demo={demoMode} onSubmit={authenticateAndLoad} />;
  }

  return (
    <AppShell activePage={activePage} onNavigate={setActivePage} demo={demoMode}>
      {activePage === "Overview" ? <OverviewDashboard onOpenStorage={(storageId) => { setFocusedStorageId(storageId); setActivePage("Storage"); }} /> : activePage === "Storage" ? <StoragePage
        snapshot={snapshot}
        drives={drives}
        busy={busy}
        status={storageAction ? null : status}
        error={storageAction ? null : error}
        onScan={() => void refreshHardware()}
        onAction={(action) => void openStorageAction(action)}
        onDriveAction={(action, driveId, selection) => void openDriveAction(action, driveId, selection)}
        savedDrafts={savedStorageDrafts}
        onResumeDraft={(draftId) => void resumeStorageDraft(draftId)}
        onDiscardDraft={(draftId) => void discardSavedStorageDraft(draftId)}
        reservedDriveIds={activeReservedDriveIds}
        storageInventory={storageInventory}
        activeOperation={storageOperation}
        operationProgress={storageProgress}
        focusedStorageId={focusedStorageId}
      /> : activePage === "Storage Access" ? <ConnectivityPage /> : activePage === "Networking" ? renderNetworkingPage() : activePage === "Applications" ? <ApplicationsPage onChanged={setIntegrations} onRecommendations={applyApplicationRecommendations} /> : activePage === "Activity" ? <ActivityPage /> : activePage === "Health" ? <HealthPage /> : activePage === "Analytics" ? <AnalyticsPage /> : <SettingsPage />}
      {storageAction && <StorageWizardDialog
        action={storageAction}
        mode={mode}
        busy={busy}
        onModeChange={(next) => void changeMode(next)}
        onCancelChanges={() => void handleCancel()}
        onSaveForLater={() => void saveStorageDraftForLater()}
        onClose={storageOperation?.status === "succeeded" ? () => void closeCompletedStorageAction() : storageOperation ? minimizeStorageActivity : () => void saveStorageDraftForLater()}
        closeSavesDraft={!storageOperation}
        firstRun={firstRunSetup}
      >
        {error && <Notice tone="danger" title="This step needs attention">{error}{error.includes("Storage discovery changed") && wizard && <div className="notice-actions"><button type="button" className="button button-primary" onClick={() => void refreshStaleStoragePlan()} disabled={busy}>Refresh and review</button></div>}</Notice>}
        {status && <Notice tone="info" title="Status">{status}</Notice>}
        <WizardFrame title={pageCopy[activeStep][0]} description={pageCopy[activeStep][1]} steps={firstRunSetup ? STEPS : STORAGE_CHANGE_STEPS} activeStep={firstRunSetup ? activeStep : activeStep - 2} onBack={activeStep > (firstRunSetup ? 0 : 2) && !consentRecorded && !storageOperation ? goBack : undefined} onNext={wizardNext} nextLabel={wizardNextLabel} busy={busy} nextDisabled={activeStep === 10 && !storageOperation && (!planExecutable || (planNeedsApproval && !consentRecorded && !exactConsentAccepted(consentPhrase)))}>
          {body}
        </WizardFrame>
      </StorageWizardDialog>}
    </AppShell>
  );
}

function CheckOption({ checked, onChange, title, detail, recommended = false }: { checked: boolean; onChange: (value: boolean) => void; title: string; detail: string; recommended?: boolean }) {
  return <label className="check-option"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span><strong>{title}</strong><small>{detail}</small></span>{recommended && <span className="recommended-badge">Recommended</span>}</label>;
}

function DriveEligibility({ drive, id }: { drive: Drive; id: string }) {
  return <small id={id} className={drive.selectable ? "cell-detail" : "hardware-warning"}>{drive.selectable ? "Eligible for planning" : drive.selectionBlockers.join(" ")}</small>;
}

function SectorGeometry({ drive }: { drive: Drive }) {
  const assessment = sectorGeometryAssessment(drive);
  if (!assessment.writeCompatible) return <span className="stacked"><strong>{assessment.kind === "unknown" ? "Not reported" : `${drive.sector.logical ?? "?"} B logical / ${drive.sector.physical ?? "?"} B physical`}</strong><small className="hardware-warning">{assessment.message} Geometry-dependent writes are blocked; a stable drive may still follow the non-destructive preservation/import path.</small></span>;
  return <span className="stacked"><span>{drive.sector.logical} B logical</span><span>{drive.sector.physical} B physical</span></span>;
}

function ExistingData({ drive }: { drive: Drive }) {
  const summary = existingDataSummary(drive);
  return <span className="stacked"><strong>{summary.headline}</strong>{summary.detail && <small className={summary.uncertain ? "hardware-warning" : "cell-detail"}>{summary.detail}</small>}<small className="cell-detail">Scan: {drive.signatureScan.status}{drive.signatureScan.source ? ` via ${drive.signatureScan.source}` : ""}</small></span>;
}

export function SelectedDriveSummary({ drives, detailed = false }: { drives: Drive[]; detailed?: boolean }) {
  if (!drives.length) return <Notice tone="warning" title="No drive selected">Return to discovery and select a drive.</Notice>;
  return <div className="selected-drives">{drives.map((drive, index) => <article key={`${drive.id}-${index}`}><div className="drive-icon" aria-hidden="true">▤</div><div><strong>{drive.vendor} {drive.model}</strong><span><code>{drive.path}</code> · {humanCapacity(drive.capacityBytes)} · {drive.connection.transport}</span>{detailed && <dl><div><dt>Stable identity</dt><dd>{drive.stableIdentity ? "Yes" : "No"}</dd></div><div><dt>Serial</dt><dd><code>{drive.serial}</code></dd></div><div><dt>WWN</dt><dd><code>{drive.wwn ?? "Not reported"}</code></dd></div><div><dt>Location</dt><dd>{drive.location}</dd></div><div><dt>Sectors</dt><dd><SectorGeometry drive={drive} /></dd></div><div><dt>Existing data</dt><dd><ExistingData drive={drive} /></dd></div></dl>}</div></article>)}</div>;
}

function ReviewLine({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="review-line"><span>{label}</span>{mono ? <code>{value}</code> : <strong>{value}</strong>}</div>;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function expansionSelectionValue(value: unknown): StorageExpansionSelection | null {
  const expansion = objectValue(value);
  const target = objectValue(expansion.target);
  const configuration = objectValue(expansion.configuration);
  const candidateId = stringValue(expansion.candidate_id, "");
  const kind = stringValue(expansion.kind, "");
  const snapshot = stringValue(expansion.hardware_snapshot_sha256, "");
  const diskIds = stringArray(expansion.disk_ids);
  const instanceId = stringValue(target.instance_id, "");
  const mountpoint = stringValue(target.mountpoint, "");
  if (
    !/^[a-f0-9]{24}$/.test(candidateId)
    || !kind
    || !/^[a-f0-9]{64}$/.test(snapshot)
    || !diskIds.length
  ) return null;
  const provider = target.provider === "mergerfs" || target.provider === "zfs" ? target.provider : null;
  const hasTarget = provider !== null
    && (provider === "mergerfs" ? /^mergerfs:[a-f0-9]{16}$/.test(instanceId) : /^zfs:[A-Za-z][A-Za-z0-9_.:-]{0,254}$/.test(instanceId))
    && mountpoint.startsWith("/");
  return {
    candidate_id: candidateId,
    kind,
    storage_group_id: typeof expansion.storage_group_id === "string" ? expansion.storage_group_id : null,
    hardware_snapshot_sha256: snapshot,
    disk_ids: diskIds,
    target: hasTarget && provider ? { provider, instance_id: instanceId, mountpoint } : null,
    configuration: {
      ...(typeof configuration.topology === "string" ? { topology: configuration.topology } : {}),
      ...(typeof configuration.vdev_type === "string" ? { vdev_type: configuration.vdev_type } : {}),
      ...(typeof configuration.vdev_width === "number" ? { vdev_width: configuration.vdev_width } : {}),
      ...(typeof configuration.snapraid_role === "string" && (configuration.snapraid_role === "data" || configuration.snapraid_role === "parity") ? { snapraid_role: configuration.snapraid_role } : {}),
      ...(typeof configuration.snapraid_instance_id === "string" ? { snapraid_instance_id: configuration.snapraid_instance_id } : {}),
      ...(typeof configuration.snapraid_config_sha256 === "string" ? { snapraid_config_sha256: configuration.snapraid_config_sha256 } : {}),
      ...(typeof configuration.zfs_pool_guid === "string" ? { zfs_pool_guid: configuration.zfs_pool_guid } : {}),
      ...(typeof configuration.zfs_config_sha256 === "string" ? { zfs_config_sha256: configuration.zfs_config_sha256 } : {}),
      ...(typeof configuration.zfs_vdev_count === "number" ? { zfs_vdev_count: configuration.zfs_vdev_count } : {}),
      ...(configuration.md_level === "raid1" || configuration.md_level === "raid5" || configuration.md_level === "raid6" || configuration.md_level === "raid10" ? { md_level: configuration.md_level } : {}),
      ...(typeof configuration.member_count === "number" ? { member_count: configuration.member_count } : {}),
    },
  };
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function integerInRange(value: unknown, minimum: number, maximum: number, fallback: number): number {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum && value <= maximum ? value : fallback;
}

function isStorageRole(value: string): value is StorageRole {
  return ["individual", "mergerfs", "download-cache", "block", "import", "test", "zfs", "raid", "snapraid", "mixed"].includes(value);
}

function isTrimMode(value: string): value is "conditional" | "periodic" | "continuous" | "disabled" {
  return ["conditional", "periodic", "continuous", "disabled"].includes(value);
}

function libraryChoices(value: unknown): LibraryChoice[] {
  if (!Array.isArray(value)) return LIBRARY_DEFAULTS.map((library) => ({ ...library }));
  const choices = value.flatMap((item): LibraryChoice[] => {
    const candidate = objectValue(item);
    if (typeof candidate.id !== "string" || typeof candidate.label !== "string" || typeof candidate.contentType !== "string" || typeof candidate.app !== "string") return [];
    const source = candidate.source === "detected" || candidate.source === "user" ? candidate.source : "recommended";
    return [{
      id: candidate.id,
      label: candidate.label,
      contentType: candidate.contentType,
      app: candidate.app,
      selected: booleanValue(candidate.selected, true),
      source,
    }];
  });
  return choices.length ? choices : LIBRARY_DEFAULTS.map((library) => ({ ...library }));
}

function wizardSelectedDeviceIds(wizard: WizardDocument): string[] {
  const draftIds = stringArray(objectValue(wizard.answers.draft_ui).selected_device_ids);
  return draftIds.length ? draftIds : stringArray(objectValue(wizard.answers.storage).selected_device_ids);
}

export function BackendStoragePlan({ storage }: { storage: Record<string, unknown> | undefined }) {
  if (!storage) return null;
  const format = objectValue(storage.format);
  const risk = objectValue(storage.risk);
  const actions = Array.isArray(storage.actions) ? storage.actions.map(objectValue) : [];
  const createsFilesystem = actions.some((action) => action.type === "filesystem.create");
  const folders = Array.isArray(storage.folders) ? storage.folders.map(String) : [];
  const warnings = Array.isArray(storage.warnings) ? storage.warnings.map(objectValue) : [];
  const intake = objectValue(storage.intake_tests);
  const expansion = objectValue(storage.expansion);
  const expansionTarget = objectValue(expansion.target);
  const expansionConfiguration = objectValue(expansion.configuration);
  const selectedChecks = Object.entries(intake)
    .filter(([, enabled]) => enabled === true)
    .map(([name]) => name.replaceAll("_", " "));
  return <Card title="Backend-derived storage actions" description="These are the exact actions and paths in the immutable plan, not a browser-side estimate.">
    {typeof expansion.candidate_id === "string" && <div className="advanced-panel" aria-label="Expansion plan binding">
      <h3>Reviewed expansion choice</h3>
      <div className="review-grid plan-storage-grid">
        <div><ReviewLine label="Candidate" value={expansion.candidate_id} mono /><ReviewLine label="Change" value={String(expansion.kind ?? "Not specified")} /><ReviewLine label="Storage Group" value={String(expansion.storage_group_id ?? "New Storage Group")} /></div>
        <div><ReviewLine label="Target" value={String(expansionTarget.mountpoint ?? "New storage")} mono /><ReviewLine label="Exact geometry" value={Object.entries(expansionConfiguration).map(([key, value]) => `${key}=${String(value)}`).join(" · ") || "Not specified"} /><ReviewLine label="Discovery SHA-256" value={String(expansion.hardware_snapshot_sha256 ?? "Not reported")} mono /></div>
      </div>
    </div>}
    <div className="review-grid plan-storage-grid"><div><h3>Layout</h3><ReviewLine label="Type" value={String(storage.topology ?? "Not specified")} /><ReviewLine label="Drive checks" value={selectedChecks.length ? selectedChecks.join(", ") : "None"} /><ReviewLine label="Snapshots" value={storage.snapshots === true ? "Enabled" : "Disabled"} /><ReviewLine label="Encryption" value={String(storage.encryption ?? "Not specified")} /></div><div><h3>Account</h3><ReviewLine label="Media identity" value={String(objectValue(storage.service_account).username ?? "Not specified")} /><ReviewLine label="Access model" value={String(objectValue(storage.file_access).acl_model ?? "Not specified")} /></div></div>
    <div className="review-grid plan-storage-grid"><div><h3>{createsFilesystem ? "Format" : "Filesystem handling"}</h3><ReviewLine label="Filesystem" value={createsFilesystem ? String(format.filesystem ?? "Not specified") : "Preserve existing"} /><ReviewLine label="Format method" value={createsFilesystem ? "Quick format" : "Not applicable"} /><ReviewLine label="Partition table" value={createsFilesystem ? String(format.partition_table ?? "None") : "No creation planned"} /><ReviewLine label="Alignment" value={createsFilesystem && format.alignment_bytes ? `${Number(format.alignment_bytes).toLocaleString()} bytes` : "Not applicable"} /><ReviewLine label="Allocation unit" value={createsFilesystem && format.allocation_unit_bytes ? `${Number(format.allocation_unit_bytes).toLocaleString()} bytes` : "Not applicable"} /></div><div><h3>Risk</h3><ReviewLine label="Destructive" value={risk.destructive === true ? "Yes" : risk.destructive === false ? "No" : "Not declared"} /><ReviewLine label="Approval required" value={risk.approval_required === true ? "Yes" : risk.approval_required === false ? "No" : "Not declared"} /><p>{String(risk.message ?? "No risk message was supplied.")}</p></div></div>
    <h3>Actions</h3><div className="table-scroll"><table className="data-table"><thead><tr><th>Action ID</th><th>Type</th><th>Device</th><th>Destructive</th></tr></thead><tbody>{actions.map((action, index) => <tr key={String(action.action_id ?? index)}><td><code>{String(action.action_id ?? "Unavailable")}</code></td><td>{String(action.type ?? "Unavailable")}</td><td><code>{String(action.device_id ?? "—")}</code></td><td>{actionDestructiveLabel(action, risk.destructive === true)}</td></tr>)}</tbody></table></div>
    <h3>Folders</h3><div className="folder-list">{folders.map((folder) => <code key={folder}>{folder}</code>)}</div>
    {warnings.map((warning, index) => <Notice key={String(warning.code ?? index)} tone="warning" title={String(warning.code ?? "Storage warning")}>{String(warning.message ?? "Review this warning.")}</Notice>)}
  </Card>;
}
