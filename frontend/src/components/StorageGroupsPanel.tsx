import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  OperationDocument,
  LogicalStorageDocument,
  PhysicalDiskDocument,
  StorageBackendActivationPlan,
  StorageDrainPlan,
  StorageGroupDocument,
  StorageOperationProgress,
} from "../types";
import { Card, Notice, StatusBadge } from "./ui";

type Purpose = "media" | "downloads" | "archive" | "backup" | "general";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB", "PiB"];
  let next = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && next >= 1024; index += 1) {
    next /= 1024;
    unit = units[index];
  }
  return `${next.toFixed(next >= 10 ? 1 : 2)} ${unit}`;
}

export function StorageGroupsPanel() {
  const [groups, setGroups] = useState<StorageGroupDocument[]>([]);
  const [disks, setDisks] = useState<PhysicalDiskDocument[]>([]);
  const [logicalStorage, setLogicalStorage] = useState<LogicalStorageDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Creating the first stable namespace is the primary empty-state action. Keep
  // the form visible by default so it remains usable immediately after a
  // completed storage wizard or browser focus transition is dismissed.
  const [createOpen, setCreateOpen] = useState(true);
  const [name, setName] = useState("");
  const [namespacePath, setNamespacePath] = useState("/srv/hoardarr/media");
  const [namespaceTouched, setNamespaceTouched] = useState(false);
  const [purpose, setPurpose] = useState<Purpose>("media");
  const [selectedDisks, setSelectedDisks] = useState<Record<string, string>>(Object.create(null));
  const [selectedStorage, setSelectedStorage] = useState<Record<string, string>>(Object.create(null));
  const [backendPaths, setBackendPaths] = useState<Record<string, string>>(Object.create(null));
  const [drainPlan, setDrainPlan] = useState<StorageDrainPlan | null>(null);
  const [drainVerification, setDrainVerification] = useState<"fast" | "accurate" | "paranoid">("accurate");
  const [drainReadOnly, setDrainReadOnly] = useState(false);
  const [drainBandwidth, setDrainBandwidth] = useState(0);
  const [drainIoPriority, setDrainIoPriority] = useState<"normal" | "background" | "idle">("normal");
  const [drainStart, setDrainStart] = useState("");
  const [drainWindowMinutes, setDrainWindowMinutes] = useState(0);
  const [drainConfirmation, setDrainConfirmation] = useState("");
  const [drainOperation, setDrainOperation] = useState<OperationDocument | null>(null);
  const [drainProgress, setDrainProgress] = useState<StorageOperationProgress | null>(null);
  const [releaseBackendId, setReleaseBackendId] = useState<string | null>(null);
  const [releaseConfirmation, setReleaseConfirmation] = useState("");
  const [activationPlan, setActivationPlan] = useState<StorageBackendActivationPlan | null>(null);

  const load = async (signal?: AbortSignal) => {
    const [nextGroups, nextDisks, nextLogicalStorage] = await Promise.all([
      api.storageGroups(signal),
      api.registeredDisks(signal),
      api.logicalStorage(signal),
    ]);
    setGroups(nextGroups);
    setDisks(nextDisks);
    setLogicalStorage(nextLogicalStorage);
  };

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .catch((requestError) => {
        if (!controller.signal.aborted) {
          setError(requestError instanceof Error ? requestError.message : "Storage Groups could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (
      !namespaceTouched
      && groups.length === 0
      && logicalStorage.length === 1
      && logicalStorage[0].mountpoint
    ) {
      setNamespacePath(logicalStorage[0].mountpoint);
    }
  }, [groups.length, logicalStorage, namespaceTouched]);

  useEffect(() => {
    if (!drainOperation || ["succeeded", "failed", "cancelled", "needs_attention"].includes(drainOperation.status)) return;
    const controller = new AbortController();
    let stopped = false;
    const refresh = async () => {
      try {
        const [operation, progress] = await Promise.all([
          api.operation(drainOperation.id),
          api.storageOperationProgress(drainOperation.id),
        ]);
        if (stopped) return;
        const completedSource = operation.status === "succeeded" || progress.state === "succeeded"
          ? progress.report?.source_backend_id
          : null;
        if (typeof completedSource === "string") {
          setGroups((current) => current.map((group) => ({
            ...group,
            backends: group.backends.map((backend) => backend.id === completedSource
              ? { ...backend, lifecycle_state: "retired" }
              : backend),
          })));
        }
        setDrainOperation(operation);
        setDrainProgress(progress);
      } catch (requestError) {
        if (!stopped && !controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "Drain progress could not be loaded.");
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => {
      stopped = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, [drainOperation?.id, drainOperation?.status]);

  useEffect(() => {
    if (!drainOperation || !["succeeded", "failed", "cancelled", "needs_attention"].includes(drainOperation.status)) return;
    const controller = new AbortController();
    void load(controller.signal).catch((requestError) => {
      if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "Storage Group state could not be refreshed.");
    });
    return () => controller.abort();
  }, [drainOperation?.id, drainOperation?.status]);

  const assignedDiskIds = useMemo(
    () => new Set(groups.flatMap((group) => group.backends.map((backend) => backend.physical_disk_id).filter(Boolean))),
    [groups],
  );
  const assignedStorageIds = useMemo(
    () => new Set(groups.flatMap((group) => group.backends.map((backend) => backend.storage_entity_id).filter(Boolean))),
    [groups],
  );
  const availableDisks = disks.filter(
    (disk) => !assignedDiskIds.has(disk.id) && disk.assignable === true && disk.system_device === false,
  );
  const availableStorage = logicalStorage.filter((storage) => !assignedStorageIds.has(storage.id));

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.createStorageGroup({ name, namespace_path: namespacePath, purpose });
      setName("");
      setCreateOpen(false);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The Storage Group could not be created.");
    } finally {
      setBusy(false);
    }
  };

  const assign = async (groupId: string) => {
    const diskId = selectedDisks[groupId];
    if (!diskId) return;
    setBusy(true);
    setError(null);
    try {
      await api.assignStorageGroupDisk(groupId, diskId, backendPaths[groupId]);
      setSelectedDisks((current) => ({ ...current, [groupId]: "" }));
      setBackendPaths((current) => ({ ...current, [groupId]: "" }));
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The disk could not be assigned.");
    } finally {
      setBusy(false);
    }
  };

  const assignLogicalStorage = async (groupId: string) => {
    const storageId = selectedStorage[groupId];
    const storage = logicalStorage.find((item) => item.id === storageId);
    if (!storage) return;
    setBusy(true);
    setError(null);
    try {
      await api.assignStorageGroupEntity(groupId, storage.id, storage.mountpoint);
      setSelectedStorage((current) => ({ ...current, [groupId]: "" }));
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The managed storage could not be assigned.");
    } finally {
      setBusy(false);
    }
  };

  const previewDrain = async (group: StorageGroupDocument, sourceBackendId: string) => {
    const destinations = group.backends
      .filter((backend) => backend.id !== sourceBackendId && ["active", "preferred_write"].includes(backend.lifecycle_state) && ["data", "archive"].includes(backend.role))
      .map((backend) => backend.id);
    setBusy(true);
    setError(null);
    setDrainPlan(null);
    try {
      setDrainPlan(await api.previewStorageGroupDrain(group.id, {
        source_backend_id: sourceBackendId,
        destination_backend_ids: destinations,
        verification_mode: drainVerification,
        reserve_bytes: 1_073_741_824,
        enforce_source_read_only: drainReadOnly,
        bandwidth_limit_mib_per_second: drainBandwidth > 0 ? drainBandwidth : null,
        io_priority: drainIoPriority,
        start_at: drainStart ? new Date(drainStart).toISOString() : null,
        maintenance_window_minutes: drainStart && drainWindowMinutes > 0 ? drainWindowMinutes : null,
      }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Drain preflight could not be completed.");
    } finally {
      setBusy(false);
    }
  };

  const startDrain = async () => {
    if (!drainPlan || drainConfirmation !== "I AGREE") return;
    setBusy(true);
    setError(null);
    try {
      const operation = await api.startStorageGroupDrain(drainPlan);
      setDrainOperation(operation);
      setDrainProgress(null);
      setDrainConfirmation("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The drain could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const pauseOrResumeDrain = async () => {
    if (!drainOperation) return;
    setBusy(true);
    setError(null);
    try {
      const operation = ["paused", "failed", "needs_attention"].includes(drainOperation.status)
        ? await api.resumeOperation(drainOperation.id)
        : await api.pauseOperation(drainOperation.id);
      setDrainOperation(operation);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The drain state could not be changed.");
    } finally {
      setBusy(false);
    }
  };

  const transition = async (groupId: string, backendId: string, target: "active" | "preferred_write") => {
    setBusy(true);
    setError(null);
    try {
      await api.transitionStorageBackend(groupId, backendId, target);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The backend state could not be changed.");
    } finally {
      setBusy(false);
    }
  };

  const previewActivation = async (groupId: string, backendId: string) => {
    setBusy(true);
    setError(null);
    setActivationPlan(null);
    try {
      setActivationPlan(await api.previewStorageBackendActivation(groupId, backendId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The mounted storage could not be verified.");
    } finally {
      setBusy(false);
    }
  };

  const applyActivation = async () => {
    if (!activationPlan?.ready) return;
    setBusy(true);
    setError(null);
    try {
      await api.activateStorageBackend(activationPlan);
      setActivationPlan(null);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The backend could not be activated.");
    } finally {
      setBusy(false);
    }
  };

  const releaseForReuse = async (groupId: string, backendId: string) => {
    if (releaseConfirmation !== "RELEASE") return;
    setBusy(true);
    setError(null);
    try {
      await api.releaseRetiredStorageBackend(
        groupId,
        backendId,
        "Verified drain complete; operator released the retired disk for reuse.",
      );
      setReleaseBackendId(null);
      setReleaseConfirmation("");
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The retired disk could not be released.");
    } finally {
      setBusy(false);
    }
  };

  const reconcileNamespace = async (groupId: string, backendId: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.reconcileStorageGroupNamespace(groupId, backendId);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The stable media path could not be reconciled.");
    } finally {
      setBusy(false);
    }
  };

  return <Card
    title="Storage Groups"
    description="Keep one stable media path while disks are added, preferred for new files, drained, or retired."
  >
    {error && <Notice tone="danger" title="Storage Group needs attention">{error}</Notice>}
    <section className="storage-group-create" aria-labelledby="storage-group-create-heading">
      <button
        id="storage-group-create-heading"
        type="button"
        className="button button-secondary"
        aria-expanded={createOpen}
        aria-controls="storage-group-create-form"
        onClick={() => setCreateOpen((current) => !current)}
      >
        {createOpen ? "Close Storage Group form" : "Create Storage Group"}
      </button>
      {createOpen && <div id="storage-group-create-form" className="form-grid storage-group-form">
        <label>Name<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} placeholder="Media" /></label>
        <label>Stable media path<input value={namespacePath} maxLength={4096} onChange={(event) => { setNamespaceTouched(true); setNamespacePath(event.target.value); }} /><small>{logicalStorage.length === 1 && logicalStorage[0].mountpoint === namespacePath ? "Uses the mounted path of the detected managed storage." : "Applications use this path; choose an existing managed mount unless new storage will be created here."}</small></label>
        <label>Used for<select value={purpose} onChange={(event) => setPurpose(event.target.value as Purpose)}><option value="media">Movies, TV, and music</option><option value="downloads">Downloads and temporary work</option><option value="archive">Archive</option><option value="backup">Backup</option><option value="general">General files</option></select></label>
        <button type="button" className="button button-primary" disabled={busy || !name.trim()} onClick={() => void create()}>Create group</button>
      </div>}
    </section>
    <details className="storage-group-create">
      <summary className="button button-secondary">Drain scheduling and limits</summary>
      <div className="form-grid storage-group-form">
        <label>Copy speed limit (MiB/s)<input aria-label="Copy speed limit (MiB/s)" type="number" min="0" max="10240" value={drainBandwidth} onChange={(event) => { setDrainBandwidth(Number(event.target.value)); setDrainPlan(null); }} /><small>Use 0 for no Hoardarr limit. A limit reduces interference with playback and downloads.</small></label>
        <label>I/O priority<select aria-label="Drain I/O priority" value={drainIoPriority} onChange={(event) => { setDrainIoPriority(event.target.value as typeof drainIoPriority); setDrainPlan(null); }}><option value="normal">Normal</option><option value="background">Background</option><option value="idle">Only when storage is otherwise idle</option></select><small>Linux applies this to the durable mover and restores the worker default afterward.</small></label>
        <label>Scheduled start<input aria-label="Scheduled start" type="datetime-local" value={drainStart} onChange={(event) => { setDrainStart(event.target.value); setDrainPlan(null); }} /><small>Leave empty to start after approval. The selected local time is stored with its timezone.</small></label>
        <label>Maintenance window (minutes)<input aria-label="Maintenance window (minutes)" type="number" min="0" max="10080" value={drainWindowMinutes} disabled={!drainStart} onChange={(event) => { setDrainWindowMinutes(Number(event.target.value)); setDrainPlan(null); }} /><small>Use 0 for no end time. If the window ends, the drain pauses at a safe checkpoint.</small></label>
        <label className="check-option"><input aria-label="Temporarily enforce a read-only source mount" type="checkbox" checked={drainReadOnly} onChange={(event) => { setDrainReadOnly(event.target.checked); setDrainPlan(null); }} /><span><strong>Temporarily enforce a read-only source mount</strong><small>Available only when the backend path is an exact Linux mount. Hoardarr returns it to writable only for verified source cleanup, then leaves the retired mount read-only.</small></span></label>
      </div>
    </details>
    {loading ? <p role="status">Loading Storage Groups…</p> : groups.length === 0 ? <div className="empty-state compact-empty"><h3>No Storage Groups yet</h3><p>Create a stable media location, then assign a registered disk or logical storage backend.</p></div> : <div className="storage-group-list">
      {groups.map((group) => <section className="storage-group" key={group.id} aria-labelledby={`storage-group-${group.id}`}>
        <header><div><h3 id={`storage-group-${group.id}`}>{group.name}</h3><code>{group.namespace_path}</code></div><StatusBadge status={group.state} /></header>
        <p>{group.purpose === "media" ? "Media libraries" : group.purpose} · New-write placement follows the preferred healthy backend.</p>
        {group.namespace?.available === false && (() => {
          const candidates = group.backends.filter((backend) => ["active", "preferred_write"].includes(backend.lifecycle_state) && Boolean(backend.namespace_path));
          return <Notice tone="warning" title="Stable media path is not available"><p><code>{group.namespace_path}</code> is not a managed mount. Hoardarr will not send Plex or ARR applications to an empty path on the system disk.</p>{candidates.length === 1 && <button className="button button-secondary" type="button" disabled={busy} onClick={() => void reconcileNamespace(group.id, candidates[0].id)}>Use verified storage path {candidates[0].namespace_path}</button>}</Notice>;
        })()}
        {group.backends.length === 0 ? <p className="muted">No backends assigned.</p> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Backend identity</th><th>Role</th><th>Lifecycle</th><th>Placement and lifecycle</th></tr></thead><tbody>{group.backends.map((backend) => {
          const drainDestinations = group.backends.filter((item) => item.id !== backend.id && ["active", "preferred_write"].includes(item.lifecycle_state) && ["data", "archive"].includes(item.role));
          return <tr key={backend.id}><td><code>{backend.stable_identity}</code><small>{backend.namespace_path || "Mount path not configured"}</small></td><td>{backend.role}</td><td><StatusBadge status={backend.lifecycle_state.replace("_", " ")} /></td><td>{backend.lifecycle_state === "assigned" ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void previewActivation(group.id, backend.id)}>Review activation</button> : backend.lifecycle_state === "active" ? <div className="button-row"><button className="button button-secondary" type="button" disabled={busy} onClick={() => void transition(group.id, backend.id, "preferred_write")}>Prefer new files here</button><button className="button button-secondary" type="button" disabled={busy || drainDestinations.length === 0 || !backend.namespace_path} onClick={() => void previewDrain(group, backend.id)}>Preview drain</button></div> : backend.lifecycle_state === "preferred_write" ? <div><span>Preferred for new files</span><button className="button button-secondary" type="button" disabled={busy || drainDestinations.length === 0 || !backend.namespace_path} onClick={() => void previewDrain(group, backend.id)}>Preview drain</button></div> : backend.lifecycle_state === "retired" ? <div>{releaseBackendId === backend.id ? <div className="form-grid compact-form"><p>The verified source is retired. Releasing removes only its Hoardarr assignment; it does not erase, format, mount, or wipe the disk.</p><label>Type RELEASE to make this disk available<input aria-label="Release retired disk confirmation" value={releaseConfirmation} onChange={(event) => setReleaseConfirmation(event.target.value)} autoComplete="off" /></label><div className="button-row"><button className="button button-primary" type="button" disabled={busy || releaseConfirmation !== "RELEASE"} onClick={() => void releaseForReuse(group.id, backend.id)}>Release for reuse</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => { setReleaseBackendId(null); setReleaseConfirmation(""); }}>Cancel</button></div></div> : <button className="button button-secondary" type="button" disabled={busy} onClick={() => { setReleaseBackendId(backend.id); setReleaseConfirmation(""); }}>Release retired disk</button>}</div> : "Managed by lifecycle operation"}</td></tr>;
        })}</tbody></table></div>}
        <div className="storage-group-assign"><label>Add a registered disk<select aria-label={`Disk to add to ${group.name}`} value={selectedDisks[group.id] ?? ""} onChange={(event) => {
          const diskId = event.target.value;
          setSelectedDisks((current) => ({ ...current, [group.id]: diskId }));
          setBackendPaths((current) => ({ ...current, [group.id]: diskId ? `/srv/hoardarr/backends/${diskId}` : "" }));
        }}><option value="">Choose a disk</option>{availableDisks.map((disk) => <option key={disk.id} value={disk.id}>{disk.model || "Disk"} · {disk.serial || disk.wwn || disk.stable_identity}</option>)}</select></label><label>Backend mount path<input aria-label={`Backend mount path for ${group.name}`} value={backendPaths[group.id] ?? ""} onChange={(event) => setBackendPaths((current) => ({ ...current, [group.id]: event.target.value }))} placeholder="/srv/hoardarr/backends/disk-id" /></label><button type="button" className="button button-secondary" disabled={busy || !selectedDisks[group.id] || !backendPaths[group.id]} onClick={() => void assign(group.id)}>Assign</button></div>
        {availableStorage.length > 0 && <div className="storage-group-assign"><label>Add existing managed storage<select aria-label={`Managed storage to add to ${group.name}`} value={selectedStorage[group.id] ?? ""} onChange={(event) => setSelectedStorage((current) => ({ ...current, [group.id]: event.target.value }))}><option value="">Choose managed storage</option>{availableStorage.map((storage) => <option key={storage.id} value={storage.id}>{storage.name} · {storage.provider || storage.storage_kind || "managed"} · {storage.mountpoint}</option>)}</select></label><p className="muted">Attach the existing pool as one logical backend. Its member disks remain protected from separate reuse.</p><button type="button" className="button button-secondary" disabled={busy || !selectedStorage[group.id]} onClick={() => void assignLogicalStorage(group.id)}>Attach managed storage</button></div>}
        {group.events.length > 0 && <details><summary>Recent lifecycle activity</summary><ol className="event-list">{group.events.slice(0, 8).map((event) => <li key={event.id}><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time> {event.event_type.replaceAll("_", " ")}</li>)}</ol></details>}
      </section>)}
    </div>}
    {activationPlan && <section className="storage-drain-preview" aria-live="polite" aria-labelledby="activation-review-title"><h3 id="activation-review-title">Review mounted storage</h3><StatusBadge status={activationPlan.ready ? "ready" : "blocked"} /><p>Hoardarr inspected the exact mount before allowing this disk to receive files. This review does not format, mount, or write to the disk.</p><dl className="review-grid"><div><dt>Stable identity</dt><dd><code>{activationPlan.stable_identity}</code></dd></div><div><dt>Backend path</dt><dd><code>{activationPlan.evidence.path}</code></dd></div><div><dt>Mounted source</dt><dd><code>{activationPlan.evidence.mount_source}</code></dd></div><div><dt>Identity proof</dt><dd>{activationPlan.evidence.identity_match ? "Matches assigned storage" : "Could not verify"}</dd></div><div><dt>Capacity</dt><dd>{formatBytes(activationPlan.evidence.total_bytes)}</dd></div><div><dt>Free</dt><dd>{formatBytes(activationPlan.evidence.free_bytes)}</dd></div></dl>{activationPlan.blockers.length > 0 && <Notice tone="danger" title="Activation blocked"><ul>{activationPlan.blockers.map((item) => <li key={item.code}>{item.message}</li>)}</ul></Notice>}<small>Evidence: {activationPlan.evidence.identity_basis}. Immutable review <code>{activationPlan.plan_sha256}</code></small><div className="button-row"><button className="button button-primary" type="button" disabled={busy || !activationPlan.ready} onClick={() => void applyActivation()}>Activate verified storage</button><button className="button button-secondary" type="button" disabled={busy} onClick={() => setActivationPlan(null)}>Cancel</button></div></section>}
    {drainPlan && <section className="storage-drain-preview" aria-live="polite"><h3>Drain preflight</h3><StatusBadge status={drainPlan.ready ? "ready" : "blocked"} /><p>Move and verify {formatBytes(drainPlan.capacity.required_bytes)} from <code>{drainPlan.source.path}</code>. {formatBytes(drainPlan.capacity.destination_free_bytes)} is currently available across {drainPlan.destinations.length} destination{drainPlan.destinations.length === 1 ? "" : "s"}; {formatBytes(drainPlan.capacity.reserve_bytes)} remains reserved.</p><p>Verification: {drainPlan.verification.mode === "fast" ? "size and modified time" : drainPlan.verification.mode === "paranoid" ? "two full checksum read passes (high CPU and I/O)" : "full file checksums"}. Source files are removed only after their destination copy is verified. This preview does not move or delete files.</p><dl className="review-grid"><div><dt>Copy speed</dt><dd>{drainPlan.controls.bandwidth_limit_mib_per_second ? `${drainPlan.controls.bandwidth_limit_mib_per_second} MiB/s maximum` : "No Hoardarr limit"}</dd></div><div><dt>Start</dt><dd>{drainPlan.controls.start_at ? new Date(drainPlan.controls.start_at).toLocaleString() : "After approval"}</dd></div><div><dt>Work window</dt><dd>{drainPlan.controls.maintenance_window_minutes ? `${drainPlan.controls.maintenance_window_minutes} minutes per run or resume` : "No scheduled pause"}</dd></div><div><dt>Source write protection</dt><dd>{drainPlan.controls.enforce_source_read_only ? "Read-only while copying and verifying" : "Lifecycle exclusion only"}</dd></div></dl>{drainPlan.blockers.length > 0 && <ul>{drainPlan.blockers.map((item) => <li key={item.code}>{item.message}</li>)}</ul>}{drainPlan.warnings.length > 0 && <details><summary>Preflight warnings</summary><ul>{drainPlan.warnings.map((item) => <li key={item.code}>{item.message}</li>)}</ul></details>}<small>Immutable plan <code>{drainPlan.plan_sha256}</code></small>{drainPlan.ready && !drainOperation && <div className="form-grid"><label>Verification strength<select value={drainVerification} onChange={(event) => { setDrainVerification(event.target.value as typeof drainVerification); setDrainPlan(null); }}><option value="fast">Fast — size and time</option><option value="accurate">Accurate — full checksums</option><option value="paranoid">Make My CPU Bleed — two reads</option></select><small>Changing this requires a fresh preflight.</small></label><label>Type I AGREE to start<input aria-label="Drain destructive confirmation" value={drainConfirmation} onChange={(event) => setDrainConfirmation(event.target.value)} autoComplete="off" /></label><div className="button-row"><button type="button" className="button button-primary" disabled={busy || drainConfirmation !== "I AGREE"} onClick={() => void startDrain()}>Start durable drain</button><button type="button" className="button button-secondary" onClick={() => { setDrainPlan(null); setDrainConfirmation(""); }}>Cancel</button></div></div>}</section>}
    {drainOperation && <section className="storage-drain-operation" aria-live="polite" aria-labelledby="drain-operation-title"><div className="section-heading"><div><p className="eyebrow">Storage lifecycle</p><h3 id="drain-operation-title">Drain and retire source</h3></div><StatusBadge status={drainProgress?.state ?? drainOperation.status} /></div><p>{drainProgress?.phase ? drainProgress.phase.replaceAll("_", " ") : "Waiting for the durable worker"}</p><div className="operation-progress-track" role="progressbar" aria-label="Storage drain progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={drainProgress?.percent ?? 0}><span style={{ width: `${drainProgress?.percent ?? 0}%` }} /></div><p><strong>{drainProgress?.percent ?? 0}%</strong>{drainProgress?.files ? ` · ${drainProgress.files.copied}/${drainProgress.files.total} copied · ${drainProgress.files.verified}/${drainProgress.files.total} verified` : ""}</p>{drainProgress?.current_action?.id && <p>Current file: <code>{drainProgress.current_action.id}</code></p>}{drainOperation.error && <Notice tone="danger" title="Drain needs attention">{drainOperation.error.detail || drainOperation.error.message || "The operation stopped safely."}</Notice>}{drainProgress?.report && <Notice tone="success" title="Drain completed">Moved and verified {String(drainProgress.report.files_moved ?? 0)} files while preserving <code>{String(drainProgress.report.namespace_path ?? "the Storage Group namespace")}</code>.</Notice>}<div className="button-row">{["queued", "running", "paused"].includes(drainOperation.status) && <button className="button button-secondary" type="button" disabled={busy} onClick={() => void pauseOrResumeDrain()}>{drainOperation.status === "paused" ? "Resume drain" : "Pause drain"}</button>}{["succeeded", "failed", "cancelled", "needs_attention"].includes(drainOperation.status) && <button className="button button-secondary" type="button" onClick={() => { setDrainOperation(null); setDrainProgress(null); setDrainPlan(null); }}>Close report</button>}{["failed", "needs_attention"].includes(drainOperation.status) && <button className="button button-primary" type="button" disabled={busy} onClick={() => void pauseOrResumeDrain()}>Resume from checkpoint</button>}</div></section>}
    {drainProgress?.report && <details className="storage-drain-report"><summary>Final drain report</summary><dl className="review-grid"><div><dt>Files moved</dt><dd>{String(drainProgress.report.files_moved ?? "Not reported")}</dd></div><div><dt>Bytes moved</dt><dd>{typeof drainProgress.report.bytes_moved === "number" ? formatBytes(drainProgress.report.bytes_moved) : "Not reported"}</dd></div><div><dt>Verification</dt><dd>{String(drainProgress.report.verification_mode ?? "Not reported")} · {String(drainProgress.report.verification_algorithm ?? "Not reported").toUpperCase()}</dd></div><div><dt>I/O priority</dt><dd>{String(drainProgress.report.io_priority ?? "normal")}</dd></div><div><dt>Elapsed</dt><dd>{typeof drainProgress.report.elapsed_seconds === "number" ? `${drainProgress.report.elapsed_seconds.toFixed(1)} seconds` : "Not reported"}</dd></div><div><dt>Measured average</dt><dd>{typeof drainProgress.report.average_mib_per_second === "number" ? `${drainProgress.report.average_mib_per_second.toFixed(2)} MiB/s` : "Not reported"}</dd></div><div><dt>Stable media path</dt><dd><code>{String(drainProgress.report.namespace_path ?? "Not reported")}</code></dd></div><div><dt>Source cleanup</dt><dd>{drainProgress.report.source_files_removed_after_verification === true ? "Removed only after verification" : "Not reported"}</dd></div></dl></details>}
    <Notice tone="info" title="Drain and retire safely">Those actions become available only through a durable move-and-verify operation; Hoardarr does not allow skipping directly to a retired state.</Notice>
  </Card>;
}
