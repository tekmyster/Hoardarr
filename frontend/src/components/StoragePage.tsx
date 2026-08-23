import { useEffect, useState } from "react";
import { api } from "../api/client";
import { existingDataSummary, humanCapacity } from "../policy";
import type { DeviceMaintenancePlan, Drive, HardwareSnapshot, OperationDocument, StorageExpansionSelection, StorageInventory, StorageOperationProgress } from "../types";
import { Card, Notice, StatusBadge } from "./ui";
import { StorageProgressDetails } from "./StorageProgressDetails";
import { StorageTopologyPanels } from "./StorageTopologyPanels";
import { StoragePerformance } from "./StoragePerformance";
import { StorageRedundancyPanel } from "./StorageRedundancyPanel";
import { StorageGroupsPanel } from "./StorageGroupsPanel";
import { StorageExpansionPanel } from "./StorageExpansionPanel";
import { SnapraidReplacementPanel } from "./SnapraidReplacementPanel";

export type StorageAction = "add" | "move" | "change";
export type DriveAction = "configure" | "test" | "import" | "expand" | "cache" | "wipe" | "advanced";

export interface SavedStorageDraft {
  id: string;
  savedAt: string;
  mode: string;
  action: StorageAction;
  selectedDriveIds: string[];
  selectedDriveLabels: string[];
  available: boolean;
  unavailableReason: string | null;
}

const EMPTY_DRIVE_IDS = new Set<string>();

const DRIVE_ACTIONS: ReadonlyArray<{ id: DriveAction; label: string; detail: string }> = [
  { id: "configure", label: "Set up as storage", detail: "Choose its use, format, and sharing." },
  { id: "test", label: "Run drive checks", detail: "Review the recommended safe intake tests." },
  { id: "import", label: "Import existing data", detail: "Keep data while Hoardarr inspects the drive." },
  { id: "expand", label: "Expand combined storage", detail: "Add this drive to an existing mergerFS path." },
  { id: "cache", label: "Use for downloads/cache", detail: "Prepare fast working space for torrents or NZBs." },
  { id: "wipe", label: "Erase or decommission", detail: "Review a quick wipe or capability-verified secure erase." },
  { id: "advanced", label: "Advanced options", detail: "Show every supported storage choice and warning." },
];

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString();
}

function rawCapacity(drives: Drive[]): string {
  const reported = drives.filter((drive) => drive.capacityBytes > 0);
  if (!reported.length) return "Not reported";
  return humanCapacity(reported.reduce((total, drive) => total + drive.capacityBytes, 0));
}

function connectionSummary(drives: Drive[]): string {
  const connections = [...new Set(drives.map((drive) => drive.connection.bus).filter(Boolean))];
  return connections.length ? connections.join(", ") : "Not reported";
}

function healthSummary(drives: Drive[]): string {
  if (!drives.length) return "Not reported";
  const counts = { healthy: 0, warning: 0, critical: 0, unknown: 0 };
  for (const drive of drives) counts[drive.healthStatus] += 1;
  return `${counts.healthy} healthy · ${counts.warning} warning · ${counts.critical} critical · ${counts.unknown} unknown`;
}

export function StoragePage({
  snapshot,
  drives,
  busy,
  status,
  error,
  onScan,
  onAction,
  onDriveAction,
  savedDrafts = [],
  onResumeDraft,
  onDiscardDraft,
  assignedDriveIds = EMPTY_DRIVE_IDS,
  reservedDriveIds = EMPTY_DRIVE_IDS,
  storageInventory = null,
  activeOperation = null,
  operationProgress = null,
  focusedStorageId = null,
}: {
  snapshot: HardwareSnapshot | null;
  drives: Drive[];
  busy: boolean;
  status: string | null;
  error: string | null;
  onScan: () => void;
  onAction: (action: StorageAction) => void;
  onDriveAction: (action: DriveAction, driveId: string | string[], selection?: StorageExpansionSelection) => void;
  savedDrafts?: SavedStorageDraft[];
  onResumeDraft?: (draftId: string) => void;
  onDiscardDraft?: (draftId: string) => void;
  assignedDriveIds?: ReadonlySet<string>;
  reservedDriveIds?: ReadonlySet<string>;
  storageInventory?: StorageInventory | null;
  activeOperation?: OperationDocument | null;
  operationProgress?: StorageOperationProgress | null;
  focusedStorageId?: string | null;
}) {
  const [maintenanceDrive, setMaintenanceDrive] = useState<Drive | null>(null);
  const [maintenanceAction, setMaintenanceAction] = useState<"wipe" | "sector_conversion">("wipe");
  const [wipeMethod, setWipeMethod] = useState<"quick" | "hdd_overwrite" | "ata_secure_erase" | "nvme_sanitize">("quick");
  const [targetSector, setTargetSector] = useState<512 | 4096>(512);
  const [maintenancePlan, setMaintenancePlan] = useState<{ plan: DeviceMaintenancePlan; plan_sha256: string } | null>(null);
  const [maintenanceConsent, setMaintenanceConsent] = useState("");
  const [maintenanceOperation, setMaintenanceOperation] = useState<OperationDocument | null>(null);
  const [maintenanceProgress, setMaintenanceProgress] = useState<StorageOperationProgress | null>(null);
  const [maintenanceError, setMaintenanceError] = useState<string | null>(null);
  const [maintenanceBusy, setMaintenanceBusy] = useState(false);

  useEffect(() => {
    if (!maintenanceOperation || !["queued", "running"].includes(maintenanceOperation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const [operation, progress] = await Promise.all([
          api.operation(maintenanceOperation.id),
          api.storageOperationProgress(maintenanceOperation.id),
        ]);
        if (!stopped) {
          setMaintenanceOperation(operation);
          setMaintenanceProgress(progress);
        }
      } catch (requestError) {
        if (!stopped) setMaintenanceError(requestError instanceof Error ? requestError.message : "Status could not be refreshed.");
      }
    };
    const timer = window.setInterval(() => void refresh(), 1000);
    void refresh();
    return () => { stopped = true; window.clearInterval(timer); };
  }, [maintenanceOperation?.id, maintenanceOperation?.status]);

  const closeMaintenance = () => {
    setMaintenanceDrive(null);
    setMaintenancePlan(null);
    setMaintenanceConsent("");
    setMaintenanceOperation(null);
    setMaintenanceProgress(null);
    setMaintenanceError(null);
  };

  const previewMaintenance = async () => {
    if (!maintenanceDrive) return;
    setMaintenanceBusy(true);
    setMaintenanceError(null);
    try {
      setMaintenancePlan(await api.previewDeviceMaintenance({
        device_id: maintenanceDrive.id,
        action: maintenanceAction,
        ...(maintenanceAction === "wipe" ? { method: wipeMethod } : { target_logical_bytes: targetSector }),
      }));
      setMaintenanceConsent("");
    } catch (requestError) {
      setMaintenanceError(requestError instanceof Error ? requestError.message : "The maintenance plan could not be created.");
    } finally { setMaintenanceBusy(false); }
  };

  const applyMaintenance = async () => {
    if (!maintenancePlan || maintenanceConsent !== "I AGREE") return;
    setMaintenanceBusy(true);
    setMaintenanceError(null);
    try {
      setMaintenanceOperation(await api.applyDeviceMaintenance(maintenancePlan.plan, maintenancePlan.plan_sha256));
    } catch (requestError) {
      setMaintenanceError(requestError instanceof Error ? requestError.message : "The maintenance action could not be started.");
    } finally { setMaintenanceBusy(false); }
  };

  return <div className="storage-page">
    {error && <Notice tone="danger" title="Storage request failed">{error}</Notice>}
    {status && <Notice tone="info" title="Storage status">{status}</Notice>}
    {activeOperation && <Card title={`Storage operation: ${activeOperation.status.replace("_", " ")}`} description={`Operation ${activeOperation.id}`}>
      <div className="storage-operation-heading"><StatusBadge status={activeOperation.status.replace("_", " ")} /><strong>{operationProgress?.percent ?? 0}%</strong></div>
      <div className="operation-progress-track" role="progressbar" aria-label="Active storage operation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={operationProgress?.percent ?? 0}><span style={{ width: `${operationProgress?.percent ?? 0}%` }} /></div>
      <StorageProgressDetails progress={operationProgress} />
    </Card>}

    <div className="storage-page-actions" aria-label="Storage actions">
      <div>
        <h2>Storage</h2>
        <p>View current devices and capacity. Guided questions appear only when you make a change.</p>
      </div>
      <div>
        <button type="button" className="button button-secondary" onClick={onScan} disabled={busy}>{busy ? "Scanning…" : "Scan now"}</button>
        <button type="button" className="button button-secondary" onClick={() => onAction("move")}>Move data</button>
        <button type="button" className="button button-secondary" onClick={() => onAction("change")}>Change storage</button>
        <button type="button" className="button button-primary" onClick={() => onAction("add")}>Add storage</button>
      </div>
    </div>

    {savedDrafts.length > 0 && <Card title="Saved storage changes" description="Continue a dated draft only while every selected drive is still detected and available.">
      <div className="saved-draft-list">{savedDrafts.map((draft) => <article className="saved-draft-row" key={draft.id}>
        <div><strong>{draft.action === "add" ? "Add storage" : draft.action === "move" ? "Move data" : "Change storage"}</strong><span>Saved {formatDate(draft.savedAt)} · {draft.mode === "advanced" ? "Advanced" : "Guided"}</span><small>{draft.selectedDriveLabels.length ? draft.selectedDriveLabels.join(" · ") : "No drives selected yet"}</small>{!draft.available && <small className="hardware-warning">{draft.unavailableReason}</small>}</div>
        <div className="saved-draft-actions"><button type="button" className="button button-secondary" onClick={() => onDiscardDraft?.(draft.id)} disabled={busy}>Discard</button><button type="button" className="button button-primary" onClick={() => onResumeDraft?.(draft.id)} disabled={busy || !draft.available}>Continue</button></div>
      </article>)}</div>
    </Card>}

    <div className="storage-summary-grid">
      <article><span>Detected drives</span><strong>{snapshot ? drives.length : "—"}</strong><small>{snapshot ? "From the latest hardware scan" : "No scan available"}</small></article>
      <article><span>Known raw capacity</span><strong>{snapshot ? rawCapacity(drives) : "—"}</strong><small>Before filesystem or resiliency overhead</small></article>
      <article><span>Connections</span><strong>{snapshot ? connectionSummary(drives) : "—"}</strong><small>Reported by the operating system</small></article>
      <article><span>Drive health</span><strong>{snapshot ? healthSummary(drives) : "—"}</strong><small>Unknown means the controller or bridge did not report a trusted result</small></article>
      <article><span>Last scanned</span><strong>{snapshot ? formatDate(snapshot.captured_at) : "—"}</strong><small>{snapshot ? "Live hardware inventory" : "Run a scan to populate storage"}</small></article>
    </div>

    <StoragePerformance />

    <div id="storage-groups-panel"><StorageGroupsPanel /></div>

    <StorageExpansionPanel onPlan={onDriveAction} snapshotId={snapshot?.id ?? null} />

    <SnapraidReplacementPanel
      inventory={storageInventory}
      availableDrives={drives.filter((drive) => drive.selectable && !assignedDriveIds.has(drive.id) && !reservedDriveIds.has(drive.id))}
    />

    <StorageTopologyPanels
      topology={storageInventory?.topology}
      actionableDriveIds={new Set(drives.filter((drive) => drive.selectable && !assignedDriveIds.has(drive.id) && !reservedDriveIds.has(drive.id)).map((drive) => drive.id))}
      managedDriveIds={assignedDriveIds}
      onDriveAction={(action, driveId) => onDriveAction(action, driveId)}
      onManageLifecycle={() => document.getElementById("storage-groups-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
    />

    <StorageRedundancyPanel initialManagedId={focusedStorageId} />

    <Card title="Drives" description="Stable device paths and hardware identities are shown instead of friendly aliases.">
      {!snapshot ? <div className="empty-state storage-empty"><span aria-hidden="true">▤</span><h3>No storage inventory yet</h3><p>Run a read-only scan to identify controllers, enclosures, and drives.</p><button type="button" className="button button-primary" onClick={onScan} disabled={busy}>{busy ? "Scanning…" : "Scan storage"}</button></div> : !drives.length ? <div className="empty-state storage-empty"><span aria-hidden="true">▤</span><h3>No drives detected</h3><p>The latest hardware scan did not report any storage devices.</p></div> : <div className="table-scroll"><table className="data-table storage-inventory-table">
        <thead><tr><th>Device</th><th>Hardware identity</th><th>Model</th><th>Health</th><th>Connection</th><th>Capacity</th><th>Location</th><th>Existing data</th><th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{drives.map((drive, index) => {
          const existing = existingDataSummary(drive);
          const assigned = assignedDriveIds.has(drive.id);
          const reserved = reservedDriveIds.has(drive.id);
          return <tr key={`${drive.id}-${index}`}>
            <td><code>{drive.path}</code><small className="cell-detail">{drive.alternatePaths && drive.alternatePaths.length > 1 ? `${drive.alternatePaths.length} paths to one logical device` : drive.stableIdentity ? "Stable identity" : "Identity incomplete"}</small></td>
            <td><code>{drive.serial}</code><small className="cell-detail">WWN: {drive.wwn ?? "Not reported"}</small></td>
            <td>{drive.vendor} {drive.model}</td>
            <td><StatusBadge status={drive.healthStatus} />{drive.healthStatus === "unknown" && <small className="cell-detail">No trusted SMART/health result</small>}</td>
            <td>{drive.connection.bus}<small className="cell-detail">{drive.connection.transport}</small></td>
            <td>{drive.capacityBytes > 0 ? humanCapacity(drive.capacityBytes) : "Not reported"}</td>
            <td>{drive.location}</td>
            <td>{existing.headline}<small className={existing.uncertain ? "hardware-warning" : "cell-detail"}>{existing.detail || `Scan ${drive.signatureScan.status}`}</small></td>
            <td className="drive-action-cell">{assigned ? <span className="managed-drive-label">Managed</span> : reserved ? <span className="managed-drive-label">Active build</span> : <details className="drive-action-menu">
              <summary aria-label={`Actions for ${drive.path}`} title={`Actions for ${drive.path}`}><span aria-hidden="true">☰</span></summary>
              <div className="drive-action-dropdown" role="menu" aria-label={`Choose what to do with ${drive.path}`}>
                {DRIVE_ACTIONS.map((action) => <button
                  key={action.id}
                  type="button"
                  role="menuitem"
                  disabled={!drive.selectable}
                  title={!drive.selectable ? drive.selectionBlockers.join(" ") : undefined}
                  onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    if (action.id === "wipe") setMaintenanceDrive(drive);
                    else onDriveAction(action.id, drive.id);
                  }}
                ><strong>{action.label}</strong><small>{action.detail}</small></button>)}
                {!drive.selectable && <p className="drive-action-blocked">This drive needs attention before it can enter a storage plan.</p>}
              </div>
            </details>}</td>
          </tr>;
        })}</tbody>
      </table></div>}
    </Card>

    <Card title="Pools and shares" description="Only live, discovered storage is shown here. A saved or approved wizard plan is not counted as configured storage.">
      {storageInventory?.pools.items.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Name</th><th>Type</th><th>Path</th><th>Members</th><th>Status</th></tr></thead><tbody>{storageInventory.pools.items.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.type}</td><td><code>{item.mountpoint ?? "Not reported"}</code></td><td>{item.members ?? "Not reported"}</td><td><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <div className="empty-state compact-empty"><h3>No live ZFS pool, Linux MD array, or mergerFS mount was detected</h3><p>No pool or array is being claimed. Use Activity to see whether a build is queued, running, failed, or complete; use Scan now to refresh physical-drive health.</p></div>}
      {storageInventory?.shares.items.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Name</th><th>Protocol</th><th>Path or target</th></tr></thead><tbody>{storageInventory.shares.items.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.protocol}</td><td><code>{item.path ?? "Block target"}</code></td></tr>)}</tbody></table></div> : <Notice tone="info" title="No live shares or targets">No managed SMB, NFS, iSCSI, or FCoE configuration was detected.</Notice>}
    </Card>

    {maintenanceDrive && <div className="wizard-overlay" role="presentation"><section className="wizard-dialog" role="dialog" aria-modal="true" aria-labelledby="maintenance-title">
      <header className="wizard-header"><div><small>ADVANCED DRIVE MAINTENANCE</small><h2 id="maintenance-title">Erase or convert a drive</h2></div><button type="button" className="wizard-close" aria-label="Close drive maintenance" onClick={closeMaintenance}>×</button></header>
      <div className="wizard-scroll">
        {maintenanceError && <Notice tone="danger" title="This action needs attention">{maintenanceError}</Notice>}
        <Card title={`${maintenanceDrive.vendor} ${maintenanceDrive.model}`} description={`${maintenanceDrive.path} · ${maintenanceDrive.serial} · ${humanCapacity(maintenanceDrive.capacityBytes)}`}>
          {!maintenanceOperation && <div className="form-grid">
            <label>Action<select value={maintenanceAction} onChange={(event) => { setMaintenanceAction(event.target.value as "wipe" | "sector_conversion"); setMaintenancePlan(null); }}><option value="wipe">Erase drive</option>{[520, 528].includes(maintenanceDrive.sector.logical ?? 0) && <option value="sector_conversion">Convert sector format</option>}</select></label>
            {maintenanceAction === "wipe" ? <label>Method<select value={wipeMethod} onChange={(event) => { setWipeMethod(event.target.value as typeof wipeMethod); setMaintenancePlan(null); }}><option value="quick">Quick signature removal</option><option value="hdd_overwrite">HDD overwrite</option><option value="ata_secure_erase">ATA secure erase</option><option value="nvme_sanitize">NVMe block erase</option></select></label> : <label>Target sector size<select value={targetSector} onChange={(event) => { setTargetSector(Number(event.target.value) as 512 | 4096); setMaintenancePlan(null); }}><option value={512}>512 bytes</option><option value={4096}>4096 bytes</option></select></label>}
          </div>}
          {maintenancePlan && !maintenanceOperation && <><Notice tone="danger" title="ARE YOU SURE?">This permanently changes <code>{maintenanceDrive.serial}</code>. The drive identity and active-use state will be checked again before execution.</Notice><dl className="review-list"><div><dt>Plan SHA-256</dt><dd><code>{maintenancePlan.plan_sha256}</code></dd></div><div><dt>Action</dt><dd>{maintenancePlan.plan.action.replace("_", " ")}</dd></div><div><dt>Options</dt><dd><code>{JSON.stringify(maintenancePlan.plan.options)}</code></dd></div></dl><label>Type “I AGREE”<input value={maintenanceConsent} autoComplete="off" onChange={(event) => setMaintenanceConsent(event.target.value)} /></label></>}
          {maintenanceOperation && <><div className="storage-operation-heading"><StatusBadge status={maintenanceOperation.status.replace("_", " ")} /><strong>{maintenanceProgress?.percent ?? 0}%</strong></div><div className="operation-progress-track" role="progressbar" aria-label="Drive maintenance progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={maintenanceProgress?.percent ?? 0}><span style={{ width: `${maintenanceProgress?.percent ?? 0}%` }} /></div><StorageProgressDetails progress={maintenanceProgress} />{maintenanceOperation.error && <Notice tone="danger" title="Drive maintenance failed">{maintenanceOperation.error.message ?? maintenanceOperation.error.detail ?? "The operation did not finish."}</Notice>}</>}
        </Card>
      </div>
      <footer className="wizard-footer"><button type="button" className="button button-secondary" onClick={closeMaintenance} disabled={maintenanceBusy || maintenanceOperation?.status === "running"}>{maintenanceOperation && !["queued", "running"].includes(maintenanceOperation.status) ? "Close" : "Cancel"}</button>{!maintenanceOperation && (!maintenancePlan ? <button type="button" className="button button-primary" onClick={() => void previewMaintenance()} disabled={maintenanceBusy}>{maintenanceBusy ? "Checking…" : "Review plan"}</button> : <button type="button" className="button button-danger" onClick={() => void applyMaintenance()} disabled={maintenanceBusy || maintenanceConsent !== "I AGREE"}>{maintenanceBusy ? "Starting…" : "Apply"}</button>)}</footer>
    </section></div>}
  </div>;
}
