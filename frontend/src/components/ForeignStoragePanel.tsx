import { useCallback, useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { ForeignInspectionPlan, ForeignMigrationPlan, ForeignStackPreviewResult, ForeignStorageAssessment, OperationDocument, StorageOperationProgress, UnraidEvidenceInput } from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Foreign storage could not be assessed.";
}

function confidenceLabel(value: string): string {
  return value === "high" ? "Confirmed evidence" : value === "medium" ? "Partial evidence" : value === "low" ? "Limited evidence" : "Not reported";
}

function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The assignment export could not be read."));
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.readAsText(file);
  });
}

function filterValues(value: string): string[] {
  return [...new Set(value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean))];
}

export function ForeignStoragePanel() {
  const [assessment, setAssessment] = useState<ForeignStorageAssessment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [reload, setReload] = useState(0);
  const [plan, setPlan] = useState<ForeignInspectionPlan | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [progress, setProgress] = useState<StorageOperationProgress | null>(null);
  const [stackPreview, setStackPreview] = useState<ForeignStackPreviewResult | null>(null);
  const [migrationCandidateId, setMigrationCandidateId] = useState<string | null>(null);
  const [migrationDestinationId, setMigrationDestinationId] = useState("");
  const [migrationVerification, setMigrationVerification] = useState<"fast" | "accurate">("accurate");
  const [migrationCollisionPolicy, setMigrationCollisionPolicy] = useState<"stop" | "reuse_identical">("stop");
  const [migrationSelectionMode, setMigrationSelectionMode] = useState<"full" | "selected_folders" | "filtered">("full");
  const [migrationSelectedPaths, setMigrationSelectedPaths] = useState<string[]>([]);
  const [migrationExtensions, setMigrationExtensions] = useState("");
  const [migrationIncludeGlobs, setMigrationIncludeGlobs] = useState("");
  const [migrationExcludeGlobs, setMigrationExcludeGlobs] = useState("");
  const [migrationPlan, setMigrationPlan] = useState<ForeignMigrationPlan | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [evidenceBusy, setEvidenceBusy] = useState(false);

  const refresh = useCallback(() => setReload((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError(null);
    void api.foreignStorage(controller.signal).then((document) => {
      if (!controller.signal.aborted) setAssessment(document);
    }).catch((requestError) => {
      if (!controller.signal.aborted) setError(errorText(requestError));
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let active = true;
    const poll = async () => {
      try {
        const [next, nextProgress] = await Promise.all([
          api.operation(operation.id),
          api.storageOperationProgress(operation.id).catch(() => null),
        ]);
        if (active) { setOperation(next); setProgress(nextProgress); }
      } catch (requestError) {
        if (active) setError(errorText(requestError));
      }
    };
    const timer = window.setInterval(() => void poll(), 1_000);
    void poll();
    return () => { active = false; window.clearInterval(timer); };
  }, [operation?.id, operation?.status]);

  const preview = async (candidateId: string) => {
    setActionBusy(true);
    setError(null);
    try {
      setPlan(await api.previewForeignInspection(candidateId));
      setOperation(null);
      setProgress(null);
      setStackPreview(null);
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const previewStack = async (candidateId: string) => {
    setActionBusy(true);
    setError(null);
    setPlan(null);
    setOperation(null);
    setProgress(null);
    try {
      setStackPreview(await api.previewForeignStack(candidateId));
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const start = async () => {
    if (!plan) return;
    setActionBusy(true);
    setError(null);
    try {
      setOperation(await api.startForeignInspection(plan));
      setProgress(null);
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const selectMigration = (candidateId: string) => {
    setMigrationCandidateId(candidateId);
    setMigrationDestinationId(assessment?.migration_destinations[0]?.id ?? "");
    setMigrationPlan(null);
    setOperation(null);
    setProgress(null);
    setPlan(null);
    setStackPreview(null);
    setMigrationSelectionMode("full");
    setMigrationSelectedPaths([]);
    setMigrationExtensions("");
    setMigrationIncludeGlobs("");
    setMigrationExcludeGlobs("");
  };

  const previewMigration = async () => {
    if (!migrationCandidateId || !migrationDestinationId) return;
    setActionBusy(true);
    setError(null);
    try {
      setMigrationPlan(await api.previewForeignMigration({
        candidate_id: migrationCandidateId,
        destination_backend_id: migrationDestinationId,
        verification_mode: migrationVerification,
        collision_policy: migrationCollisionPolicy,
        reserve_bytes: 1_073_741_824,
        selection: {
          mode: migrationSelectionMode,
          include_paths: migrationSelectionMode === "selected_folders" ? migrationSelectedPaths : [],
          include_extensions: migrationSelectionMode === "filtered" ? filterValues(migrationExtensions) : [],
          include_globs: migrationSelectionMode === "filtered" ? filterValues(migrationIncludeGlobs) : [],
          exclude_globs: migrationSelectionMode === "filtered" ? filterValues(migrationExcludeGlobs) : [],
        },
      }));
      setOperation(null);
      setProgress(null);
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const startMigration = async () => {
    if (!migrationPlan) return;
    setActionBusy(true);
    setError(null);
    try {
      setOperation(await api.startForeignMigration(migrationPlan));
      setProgress(null);
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const toggleMigrationPause = async () => {
    if (!operation) return;
    setActionBusy(true);
    setError(null);
    try {
      setOperation(operation.status === "paused"
        ? await api.resumeOperation(operation.id)
        : await api.pauseOperation(operation.id));
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setActionBusy(false);
    }
  };

  const importUnraidEvidence = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(null);
    if (file.size > 262_144) {
      setError("The Unraid assignment export exceeds the 256 KiB safety limit.");
      return;
    }
    setEvidenceBusy(true);
    try {
      const parsed = JSON.parse(await readFileText(file)) as UnraidEvidenceInput;
      await api.saveUnraidEvidence(parsed);
      refresh();
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setEvidenceBusy(false);
    }
  };

  const removeUnraidEvidence = async () => {
    setEvidenceBusy(true);
    setError(null);
    try {
      await api.removeUnraidEvidence();
      refresh();
    } catch (requestError) {
      setError(errorText(requestError));
    } finally {
      setEvidenceBusy(false);
    }
  };

  const inventory = operation?.status === "succeeded" && operation.result && typeof operation.result.inventory === "object"
    ? operation.result.inventory as Record<string, unknown>
    : null;
  const migrationCandidate = assessment?.candidates.find((item) => item.id === migrationCandidateId);
  const selectableEntries = migrationCandidate?.latest_inventory?.inventory.top_level_entries ?? [];
  const migrationSelectionReady = migrationSelectionMode === "full"
    || (migrationSelectionMode === "selected_folders" && migrationSelectedPaths.length > 0)
    || (migrationSelectionMode === "filtered" && filterValues(`${migrationExtensions},${migrationIncludeGlobs},${migrationExcludeGlobs}`).length > 0);

  return <details className="advanced-panel foreign-storage-panel">
    <summary>Inspect storage from another system</summary>
    <Card title="Foreign storage" description="Hoardarr fingerprints persisted scan evidence first. This view does not mount, assemble, repair, or modify a source disk.">
      {busy && !assessment && <Spinner label="Reading persisted storage signatures…" />}
      {error && <Notice tone="danger" title="Foreign storage assessment unavailable"><p>{error}</p><button type="button" className="button button-secondary" onClick={refresh}>Try again</button></Notice>}
      {assessment && <>
        <Notice tone="info" title="Read-only is the default">Discovery did not mount, assemble, or modify anything. A reviewed inspection uses a private no-recovery read-only mount, records a bounded inventory in Activity, then detaches it without changing automatic mounts.</Notice>
        <section className="foreign-evidence-panel" aria-labelledby="unraid-evidence-heading">
          <div className="section-heading"><div><p className="eyebrow">OPTIONAL SOURCE EVIDENCE</p><h3 id="unraid-evidence-heading">Unraid disk assignments</h3></div>{assessment.unraid_evidence && <StatusBadge status="assignment evidence loaded" />}</div>
          <p>Load the bounded JSON assignment export from the old Unraid server to identify data and parity roles by stable serial/WWN. Without it, Hoardarr labels compatible disks only as possible or suspected.</p>
          {assessment.unraid_evidence ? <>
            <dl className="review-list"><div><dt>Assignments matched</dt><dd>{assessment.unraid_evidence.matched_assignment_count} of {assessment.unraid_evidence.assignment_count}</dd></div><div><dt>Captured</dt><dd>{new Date(assessment.unraid_evidence.captured_at).toLocaleString()}</dd></div><div><dt>Unraid version</dt><dd>{assessment.unraid_evidence.unraid_version ?? "Not reported"}</dd></div><div><dt>Evidence SHA-256</dt><dd><code>{assessment.unraid_evidence.document_sha256}</code></dd></div></dl>
            {assessment.unraid_evidence.unmatched_slots.length > 0 && <Notice tone="warning" title="Some assigned disks are not attached">Unmatched slots: {assessment.unraid_evidence.unmatched_slots.join(", ")}. Hoardarr will not substitute a same-size disk.</Notice>}
            {assessment.unraid_evidence.ambiguous_slots.length > 0 && <Notice tone="danger" title="Ambiguous stable identity">These slots matched more than one current device and were not classified: {assessment.unraid_evidence.ambiguous_slots.join(", ")}.</Notice>}
          </> : <Notice tone="info" title="No Unraid assignment export loaded">A readable XFS/Btrfs/ext4 disk may be compatible with Unraid, but the filesystem alone cannot prove where it came from. A disk without a filesystem is never treated as confirmed parity.</Notice>}
          <div className="panel-actions"><label className="button button-secondary">{evidenceBusy ? "Loading…" : assessment.unraid_evidence ? "Replace assignment export" : "Load assignment export"}<input className="visually-hidden" type="file" accept="application/json,.json" disabled={evidenceBusy} aria-label="Load Unraid assignment export" onChange={(event) => void importUnraidEvidence(event)} /></label>{assessment.unraid_evidence && <button type="button" className="button button-secondary" disabled={evidenceBusy} onClick={() => void removeUnraidEvidence()}>Forget assignment evidence</button>}</div>
        </section>
        {!assessment.candidates.length ? <div className="empty-state compact-empty"><h3>No recognized foreign storage</h3><p>{assessment.unrecognized_device_count > 0 ? `${assessment.unrecognized_device_count} non-system device${assessment.unrecognized_device_count === 1 ? " has" : "s have"} insufficient signature evidence. Hoardarr does not call them empty.` : "The latest persisted scan did not report an unassigned supported filesystem or storage stack."}</p></div> : <div className="foreign-candidate-list">{assessment.candidates.map((candidate) => {
          const inspectionMode = candidate.modes.find((item) => item.id === "inspect_read_only");
          const stackMode = candidate.modes.find((item) => item.id === "preview_stack");
          const primaryMode = inspectionMode?.available ? inspectionMode : stackMode;
          const latestInventory = candidate.latest_inventory;
          return <article key={candidate.id} className="foreign-candidate">
          <header><div><strong>{candidate.profile_name}</strong><span>{candidate.filesystems.length ? candidate.filesystems.join(", ") : candidate.signature_types.join(", ")}</span></div><StatusBadge status={candidate.archive_intake?.state === "discovered_external" ? "discovered external" : candidate.state === "ready" ? "ready for read-only review" : candidate.state === "degraded-review" ? "review required" : "blocked"} /></header>
          {candidate.archive_intake?.state === "discovered_external" && <Notice tone="info" title="Archive intake source detected">{candidate.archive_intake.reason} Formatting and automatic mounting remain disabled.</Notice>}
          <dl className="settings-list">
            <div><dt>Source system</dt><dd>{candidate.origin.name}<small>{candidate.origin.reason}</small></dd></div>
            <div><dt>Evidence</dt><dd>{confidenceLabel(candidate.confidence)}</dd></div>
            {candidate.unraid && <div><dt>Unraid role</dt><dd>{candidate.unraid.classification === "identified" ? "Identified" : candidate.unraid.classification === "suspected" ? "Suspected only" : "Unknown"}: {candidate.unraid.role}<small>{candidate.unraid.slot ? `Original slot: ${candidate.unraid.slot}. ` : ""}{candidate.unraid.reason}</small></dd></div>}
            <div><dt>Members</dt><dd>{candidate.members.length}</dd></div>
            <div><dt>Raw member capacity</dt><dd>{candidate.capacity_bytes === null ? "Not reported" : humanCapacity(candidate.capacity_bytes)}</dd></div>
            <div><dt>Current health</dt><dd>{candidate.health.state ?? "Not reported"}<small>{candidate.health.reason}</small></dd></div>
          </dl>
          {latestInventory && <section className="foreign-inventory-summary" aria-label={`${candidate.profile_name} latest read-only inventory`}>
            <div className="section-heading"><div><p className="eyebrow">READ-ONLY INVENTORY</p><h4>{latestInventory.current_snapshot_match ? "Current inspection report" : "Earlier inspection report"}</h4></div><StatusBadge status={latestInventory.current_snapshot_match ? "current" : "refresh required"} /></div>
            {!latestInventory.current_snapshot_match && <Notice tone="warning" title="Discovery changed after this inventory">The report remains available for audit, but it is not treated as current. Inspect this disk again before migration planning.</Notice>}
            <dl className="review-list"><div><dt>Files</dt><dd>{latestInventory.inventory.file_count.toLocaleString()}</dd></div><div><dt>File bytes</dt><dd>{humanCapacity(latestInventory.inventory.total_bytes)}</dd></div><div><dt>Largest file</dt><dd>{latestInventory.inventory.largest_file ? `${latestInventory.inventory.largest_file.path} (${humanCapacity(latestInventory.inventory.largest_file.bytes)})` : "Not reported"}</dd></div><div><dt>Oldest / newest</dt><dd>{latestInventory.inventory.oldest_mtime_unix === null ? "Not reported" : new Date(latestInventory.inventory.oldest_mtime_unix * 1000).toLocaleDateString()} · {latestInventory.inventory.newest_mtime_unix === null ? "Not reported" : new Date(latestInventory.inventory.newest_mtime_unix * 1000).toLocaleDateString()}</dd></div><div><dt>Read/stat errors</dt><dd>{latestInventory.inventory.read_errors.length}</dd></div><div><dt>Permission anomalies</dt><dd>{Object.values(latestInventory.inventory.permission_anomalies ?? {}).reduce((total, value) => total + value, 0)}</dd></div><div><dt>Name collisions</dt><dd>{latestInventory.inventory.case_collision_count} case · {latestInventory.inventory.unicode_collision_count} Unicode</dd></div><div><dt>Completed</dt><dd>{new Date(latestInventory.completed_at).toLocaleString()}</dd></div></dl>
            {latestInventory.inventory.truncated && <Notice tone="warning" title="Inventory reached its safety limit">The bounded report stopped at its configured entry/error limit. Increase nothing until the source and report are reviewed.</Notice>}
            {latestInventory.inventory.extension_distribution.length > 0 && <details><summary>File extension distribution</summary><ul className="compact-list">{latestInventory.inventory.extension_distribution.slice(0, 12).map((item) => <li key={item.extension}><code>{item.extension}</code> — {item.files.toLocaleString()} files</li>)}</ul></details>}
            {(latestInventory.inventory.top_level_entries?.length ?? 0) > 0 && <details><summary>Top-level folders and files</summary><ul className="compact-list">{latestInventory.inventory.top_level_entries?.slice(0, 32).map((item) => <li key={`${item.type}:${item.name}`}><strong>{item.name}</strong> — {item.type}{item.bytes === undefined ? "" : ` · ${humanCapacity(item.bytes)}`}</li>)}</ul></details>}
          </section>}
          {candidate.warnings.map((warning) => <Notice key={warning} tone="warning" title="Review required">{warning}</Notice>)}
          {candidate.blockers.map((blocker) => <Notice key={blocker} tone="danger" title="Automatic inspection blocked">{blocker}</Notice>)}
          <details><summary>Member and signature evidence</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Device</th><th>Model</th><th>Signatures</th><th>Scan</th><th>Mounted</th></tr></thead><tbody>{candidate.members.map((member) => <tr key={member.device_id}><td><code>{member.kernel_path ?? member.device_id}</code></td><td>{member.model}</td><td>{member.signatures.map((item) => item.type).join(", ") || "Not reported"}</td><td>{member.signature_scan.status ?? "Not reported"}<small className="cell-detail">{member.signature_scan.source ?? "Source not reported"}</small></td><td>{member.mounted ? member.mountpoints.join(", ") : "No"}</td></tr>)}</tbody></table></div></details>
          <footer><span>{primaryMode?.reason ?? "No safe inspection mode is available."}</span><div className="panel-actions">{latestInventory?.current_snapshot_match && !latestInventory.inventory.truncated && latestInventory.inventory.read_errors.length === 0 && candidate.unraid?.role !== "parity" && <button type="button" className="button button-primary" disabled={actionBusy || assessment.migration_destinations.length === 0} title={assessment.migration_destinations.length ? "Copy this reviewed source into managed storage." : "Create and activate a managed Storage Group backend first."} onClick={() => selectMigration(candidate.id)}>Plan verified copy</button>}{inspectionMode?.available ? <button type="button" className="button button-secondary" disabled={actionBusy} title={inspectionMode.reason} onClick={() => void preview(candidate.id)}>{actionBusy ? "Checking…" : latestInventory ? "Refresh read-only inventory" : "Review read-only inspection"}</button> : stackMode?.available ? <button type="button" className="button button-secondary" disabled={actionBusy} title={stackMode.reason} onClick={() => void previewStack(candidate.id)}>{actionBusy ? "Reading metadata…" : "Review stack metadata"}</button> : <button type="button" className="button button-secondary" disabled title={primaryMode?.reason}>Inspection unavailable</button>}</div></footer>
        </article>})}</div>}
        {stackPreview && <section className="foreign-inspection-review" aria-live="polite">
          <div className="section-heading"><div><p className="eyebrow">NO-ACTIVATION PREVIEW</p><h3>{stackPreview.name}</h3></div><StatusBadge status={stackPreview.completeness.state} /></div>
          <Notice tone="success" title="Storage stack was not activated">Hoardarr revalidated every stable member and read only provider metadata. It did not assemble an MD array, activate an LVM volume group, import a ZFS pool, mount a filesystem, or change storage configuration.</Notice>
          <dl className="review-list"><div><dt>Provider</dt><dd>{stackPreview.provider === "linux_md" ? "Linux MD" : stackPreview.provider === "lvm" ? "Linux LVM" : "ZFS"}</dd></div><div><dt>Stable stack identity</dt><dd><code>{stackPreview.identity}</code></dd></div><div><dt>Layout</dt><dd>{stackPreview.layout}</dd></div><div><dt>Members observed</dt><dd>{stackPreview.members.length}{stackPreview.completeness.expected_members === null ? " (expected total Not reported)" : ` of ${stackPreview.completeness.expected_members}`}</dd></div><div><dt>Read-only inspection readiness</dt><dd>{stackPreview.mountability.state}<small>{stackPreview.mountability.reason}</small></dd></div><div><dt>Current health</dt><dd>{stackPreview.health.state ?? "Not reported"}<small>{stackPreview.health.reason}</small></dd></div></dl>
          <Notice tone="info" title="What this proves">Matching provider labels identify these members as one storage stack. Completeness and mountability are shown only when the provider metadata supports that conclusion; current inactive-stack health remains Not reported when it cannot be proven.</Notice>
          <button type="button" className="button button-secondary" onClick={() => setStackPreview(null)}>Close preview</button>
        </section>}
        {plan && <section className="foreign-inspection-review" aria-live="polite">
          <div className="section-heading"><div><p className="eyebrow">READ-ONLY INSPECTION</p><h3>{plan.source.filesystem_label || plan.source.filesystem_type.toUpperCase()}</h3></div><StatusBadge status={operation?.status ?? "ready"} /></div>
          <Notice tone="success" title="No storage configuration will change">Hoardarr will revalidate the stable disk identity and filesystem signature, mount privately with <code>{plan.source.read_only_options.join(",")}</code>, inventory metadata within fixed limits, and always detach the source. It will not write fstab or adopt the disk.</Notice>
          <dl className="review-list"><div><dt>Device</dt><dd><code>{String(plan.device.id)}</code></dd></div><div><dt>Source at review</dt><dd><code>{plan.source.kernel_path_at_preview}</code></dd></div><div><dt>Filesystem UUID</dt><dd><code>{plan.source.filesystem_uuid ?? "Not reported"}</code></dd></div><div><dt>Inventory limit</dt><dd>{plan.limits.maximum_entries.toLocaleString()} entries</dd></div><div><dt>Plan SHA-256</dt><dd><code>{plan.plan_sha256}</code></dd></div></dl>
          {!operation && <div className="panel-actions"><button type="button" className="button button-secondary" onClick={() => setPlan(null)} disabled={actionBusy}>Cancel</button><button type="button" className="button button-primary" onClick={() => void start()} disabled={actionBusy}>{actionBusy ? "Starting…" : "INSPECT READ ONLY"}</button></div>}
          {operation && <>
            {["queued", "running"].includes(operation.status) && <><p>{progress?.phase ?? "Waiting for the durable worker"}</p><div className="operation-progress-track" role="progressbar" aria-label="Read-only inspection progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}><span style={{ width: `${progress?.percent ?? 0}%` }} /></div><p>Identity verification, the private mount, inventory, and cleanup are recorded in Activity. Progress comes from the executor journal.</p></>}
            {operation.error && <Notice tone="danger" title="Inspection stopped safely">{operation.error.detail ?? operation.error.message ?? "The source was not imported or changed."}</Notice>}
            {inventory && <Notice tone="success" title="Read-only inventory completed"><p>{Number(inventory.file_count ?? 0).toLocaleString()} files · {humanCapacity(Number(inventory.total_bytes ?? 0))} · {Number(inventory.read_errors instanceof Array ? inventory.read_errors.length : 0)} reported read/stat errors.</p><p>The private source mount was detached. The complete bounded report remains in Activity.</p></Notice>}
            {!["queued", "running"].includes(operation.status) && <button type="button" className="button button-secondary" onClick={() => { setPlan(null); setOperation(null); setProgress(null); refresh(); }}>Close report</button>}
          </>}
        </section>}
        {migrationCandidateId && !migrationPlan && <section className="foreign-inspection-review" aria-live="polite">
          <div className="section-heading"><div><p className="eyebrow">COPY INTO MANAGED STORAGE</p><h3>Plan a verified file migration</h3></div><StatusBadge status="source remains read only" /></div>
          <Notice tone="info" title="This copies files; it does not adopt or erase the source">Hoardarr will privately mount the reviewed filesystem read-only, preserve every relative path, checkpoint the copy, verify the destination, and detach the source. Original files and Unraid parity are not changed or reused.</Notice>
          {assessment.migration_destinations.length === 0 ? <Notice tone="warning" title="No managed destination is ready">Create a Storage Group and activate at least one backend before copying foreign files.</Notice> : <div className="settings-grid">
            <label>Destination<select value={migrationDestinationId} onChange={(event) => setMigrationDestinationId(event.target.value)}>{assessment.migration_destinations.map((destination) => <option key={destination.id} value={destination.id}>{destination.name} — {destination.path} — {humanCapacity(destination.free_bytes)} free</option>)}</select></label>
            <label>Verification<select value={migrationVerification} onChange={(event) => setMigrationVerification(event.target.value as "fast" | "accurate")}><option value="accurate">Accurate — BLAKE3 read verification</option><option value="fast">Fast — size and modified time</option></select></label>
            <label>Existing files<select value={migrationCollisionPolicy} onChange={(event) => setMigrationCollisionPolicy(event.target.value as "stop" | "reuse_identical")}><option value="stop">Stop before replacing anything</option><option value="reuse_identical">Reuse only byte-identical files</option></select></label>
            <label>What to copy<select value={migrationSelectionMode} onChange={(event) => setMigrationSelectionMode(event.target.value as "full" | "selected_folders" | "filtered")}><option value="full">Everything in this source</option><option value="selected_folders">Selected top-level folders/files</option><option value="filtered">Custom include/exclude filters</option></select></label>
          </div>}
          {migrationSelectionMode === "selected_folders" && <fieldset className="settings-fieldset"><legend>Select source folders or files</legend>{selectableEntries.length === 0 ? <Notice tone="warning" title="Top-level selection unavailable">Refresh the read-only inventory with this Hoardarr version before selecting folders. Copy everything remains available.</Notice> : <div className="check-list">{selectableEntries.map((item) => <label key={`${item.type}:${item.name}`}><input type="checkbox" checked={migrationSelectedPaths.includes(item.name)} onChange={(event) => setMigrationSelectedPaths((current) => event.target.checked ? [...current, item.name] : current.filter((value) => value !== item.name))} /> <span><strong>{item.name}</strong><small>{item.type}{item.bytes === undefined ? "" : ` · ${humanCapacity(item.bytes)}`}</small></span></label>)}</div>}</fieldset>}
          {migrationSelectionMode === "filtered" && <div className="settings-grid"><label>Include extensions<input value={migrationExtensions} onChange={(event) => setMigrationExtensions(event.target.value)} placeholder=".mkv, .mp4, .flac" /><small>Optional, comma-separated. Extension matching is case-insensitive.</small></label><label>Include path patterns<input value={migrationIncludeGlobs} onChange={(event) => setMigrationIncludeGlobs(event.target.value)} placeholder="Movies/*, Photos/2025/*" /><small>Paths are relative to the read-only source.</small></label><label>Exclude path patterns<input value={migrationExcludeGlobs} onChange={(event) => setMigrationExcludeGlobs(event.target.value)} placeholder="@eaDir/*, .Trash-*" /><small>Excluded files are never added to the migration manifest.</small></label></div>}
          {migrationSelectionMode !== "full" && <Notice tone="info" title="Exact selected size is calculated before copying">The persisted inventory provides a safe full-source upper bound. The worker rebuilds the selected manifest, verifies destination capacity, and writes nothing until the selected files fit with the configured reserve.</Notice>}
          <div className="panel-actions"><button type="button" className="button button-secondary" onClick={() => setMigrationCandidateId(null)} disabled={actionBusy}>Cancel</button><button type="button" className="button button-primary" onClick={() => void previewMigration()} disabled={actionBusy || !migrationDestinationId || !migrationSelectionReady}>{actionBusy ? "Checking…" : "Review copy plan"}</button></div>
        </section>}
        {migrationPlan && <section className="foreign-inspection-review" aria-live="polite">
          <div className="section-heading"><div><p className="eyebrow">VERIFIED FILE MIGRATION</p><h3>{migrationPlan.selection?.mode === "full" || !migrationPlan.selection ? `${migrationPlan.inventory.file_count.toLocaleString()} files` : "Selected archive files"} to {migrationPlan.destination.name}</h3></div><StatusBadge status={operation?.status ?? "ready"} /></div>
          <Notice tone="success" title="Source data stays untouched">The source remains read-only and attached only to a private temporary mount. Hoardarr {migrationPlan.selection?.mode === "full" || !migrationPlan.selection ? `copies ${humanCapacity(migrationPlan.inventory.total_bytes)}` : `builds the exact selected manifest before copying (full-source upper bound ${humanCapacity(migrationPlan.inventory.total_bytes)})`}, preserves relative paths, and verifies the destination using {migrationPlan.verification.algorithm === "blake3" ? "BLAKE3" : "size and modified time"}.</Notice>
          <dl className="review-list"><div><dt>Destination</dt><dd><code>{migrationPlan.destination.path}</code></dd></div><div><dt>Selection</dt><dd>{migrationPlan.selection?.mode === "selected_folders" ? `Selected: ${migrationPlan.selection.include_paths.join(", ")}` : migrationPlan.selection?.mode === "filtered" ? "Custom include/exclude filters" : "Everything"}</dd></div><div><dt>Free at review</dt><dd>{humanCapacity(migrationPlan.destination.free_bytes_at_preview)}</dd></div><div><dt>Collision behavior</dt><dd>{migrationPlan.collision_policy === "stop" ? "Stop before replacing any existing file" : "Reuse only a byte-identical existing file"}</dd></div><div><dt>Source after completion</dt><dd>Retained unchanged</dd></div><div><dt>Parity reuse</dt><dd>Not supported or claimed</dd></div><div><dt>Plan SHA-256</dt><dd><code>{migrationPlan.plan_sha256}</code></dd></div></dl>
          {!operation && <div className="panel-actions"><button type="button" className="button button-secondary" onClick={() => setMigrationPlan(null)} disabled={actionBusy}>Change options</button><button type="button" className="button button-primary" onClick={() => void startMigration()} disabled={actionBusy}>{actionBusy ? "Starting…" : "COPY AND VERIFY"}</button></div>}
          {operation && <>
            {["queued", "running", "paused"].includes(operation.status) && <><p>{progress?.phase ?? (operation.status === "paused" ? "Paused at a safe checkpoint" : "Waiting for the durable worker")}</p><div className="operation-progress-track" role="progressbar" aria-label="Foreign migration progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}><span style={{ width: `${progress?.percent ?? 0}%` }} /></div><p>{progress?.files ? `${progress.files.verified.toLocaleString()} of ${progress.files.total.toLocaleString()} files verified` : "Preparing durable checkpoints…"}</p><button type="button" className="button button-secondary" onClick={() => void toggleMigrationPause()} disabled={actionBusy || operation.status === "queued"}>{operation.status === "paused" ? "Resume copy" : "Pause safely"}</button></>}
            {operation.error && <Notice tone="danger" title="Copy stopped safely">{operation.error.detail ?? operation.error.message ?? "The source was retained and no destination file was overwritten."}</Notice>}
            {operation.status === "succeeded" && <Notice tone="success" title="Copy and verification completed"><p>{Number(operation.result?.files_verified ?? 0).toLocaleString()} files verified at <code>{String(operation.result?.destination_path ?? migrationPlan.destination.path)}</code>.</p><p>The source stayed read-only and remains unchanged. The full report and checkpoints remain in Activity.</p></Notice>}
            {!['queued', 'running', 'paused'].includes(operation.status) && <button type="button" className="button button-secondary" onClick={() => { setMigrationCandidateId(null); setMigrationPlan(null); setOperation(null); setProgress(null); refresh(); }}>Close report</button>}
          </>}
        </section>}
        <footer className="panel-actions"><small>Snapshot {new Date(assessment.snapshot.captured_at).toLocaleString()}</small><button type="button" className="button button-secondary" onClick={refresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh assessment"}</button></footer>
      </>}
    </Card>
  </details>;
}
