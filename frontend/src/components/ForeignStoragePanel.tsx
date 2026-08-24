import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { ForeignInspectionPlan, ForeignStackPreviewResult, ForeignStorageAssessment, OperationDocument, StorageOperationProgress } from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Foreign storage could not be assessed.";
}

function confidenceLabel(value: string): string {
  return value === "high" ? "Confirmed evidence" : value === "medium" ? "Partial evidence" : value === "low" ? "Limited evidence" : "Not reported";
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
  const [actionBusy, setActionBusy] = useState(false);

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

  const inventory = operation?.status === "succeeded" && operation.result && typeof operation.result.inventory === "object"
    ? operation.result.inventory as Record<string, unknown>
    : null;

  return <details className="advanced-panel foreign-storage-panel">
    <summary>Inspect storage from another system</summary>
    <Card title="Foreign storage" description="Hoardarr fingerprints persisted scan evidence first. This view does not mount, assemble, repair, or modify a source disk.">
      {busy && !assessment && <Spinner label="Reading persisted storage signatures…" />}
      {error && <Notice tone="danger" title="Foreign storage assessment unavailable"><p>{error}</p><button type="button" className="button button-secondary" onClick={refresh}>Try again</button></Notice>}
      {assessment && <>
        <Notice tone="info" title="Read-only is the default">Discovery did not mount, assemble, or modify anything. A reviewed inspection uses a private no-recovery read-only mount, records a bounded inventory in Activity, then detaches it without changing automatic mounts.</Notice>
        {!assessment.candidates.length ? <div className="empty-state compact-empty"><h3>No recognized foreign storage</h3><p>{assessment.unrecognized_device_count > 0 ? `${assessment.unrecognized_device_count} non-system device${assessment.unrecognized_device_count === 1 ? " has" : "s have"} insufficient signature evidence. Hoardarr does not call them empty.` : "The latest persisted scan did not report an unassigned supported filesystem or storage stack."}</p></div> : <div className="foreign-candidate-list">{assessment.candidates.map((candidate) => {
          const inspectionMode = candidate.modes.find((item) => item.id === "inspect_read_only");
          const stackMode = candidate.modes.find((item) => item.id === "preview_stack");
          const primaryMode = inspectionMode?.available ? inspectionMode : stackMode;
          return <article key={candidate.id} className="foreign-candidate">
          <header><div><strong>{candidate.profile_name}</strong><span>{candidate.filesystems.length ? candidate.filesystems.join(", ") : candidate.signature_types.join(", ")}</span></div><StatusBadge status={candidate.state === "ready" ? "ready for read-only review" : candidate.state === "degraded-review" ? "review required" : "blocked"} /></header>
          <dl className="settings-list">
            <div><dt>Source system</dt><dd>{candidate.origin.name}<small>{candidate.origin.reason}</small></dd></div>
            <div><dt>Evidence</dt><dd>{confidenceLabel(candidate.confidence)}</dd></div>
            <div><dt>Members</dt><dd>{candidate.members.length}</dd></div>
            <div><dt>Raw member capacity</dt><dd>{candidate.capacity_bytes === null ? "Not reported" : humanCapacity(candidate.capacity_bytes)}</dd></div>
          </dl>
          {candidate.warnings.map((warning) => <Notice key={warning} tone="warning" title="Review required">{warning}</Notice>)}
          {candidate.blockers.map((blocker) => <Notice key={blocker} tone="danger" title="Automatic inspection blocked">{blocker}</Notice>)}
          <details><summary>Member and signature evidence</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Device</th><th>Model</th><th>Signatures</th><th>Scan</th><th>Mounted</th></tr></thead><tbody>{candidate.members.map((member) => <tr key={member.device_id}><td><code>{member.kernel_path ?? member.device_id}</code></td><td>{member.model}</td><td>{member.signatures.map((item) => item.type).join(", ") || "Not reported"}</td><td>{member.signature_scan.status ?? "Not reported"}<small className="cell-detail">{member.signature_scan.source ?? "Source not reported"}</small></td><td>{member.mounted ? member.mountpoints.join(", ") : "No"}</td></tr>)}</tbody></table></div></details>
          <footer><span>{primaryMode?.reason ?? "No safe inspection mode is available."}</span>{inspectionMode?.available ? <button type="button" className="button button-secondary" disabled={actionBusy} title={inspectionMode.reason} onClick={() => void preview(candidate.id)}>{actionBusy ? "Checking…" : "Review read-only inspection"}</button> : stackMode?.available ? <button type="button" className="button button-secondary" disabled={actionBusy} title={stackMode.reason} onClick={() => void previewStack(candidate.id)}>{actionBusy ? "Reading metadata…" : "Review stack metadata"}</button> : <button type="button" className="button button-secondary" disabled title={primaryMode?.reason}>Inspection unavailable</button>}</footer>
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
        <footer className="panel-actions"><small>Snapshot {new Date(assessment.snapshot.captured_at).toLocaleString()}</small><button type="button" className="button button-secondary" onClick={refresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh assessment"}</button></footer>
      </>}
    </Card>
  </details>;
}
