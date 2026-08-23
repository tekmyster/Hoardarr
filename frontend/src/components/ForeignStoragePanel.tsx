import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { ForeignStorageAssessment } from "../types";
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

  return <details className="advanced-panel foreign-storage-panel">
    <summary>Inspect storage from another system</summary>
    <Card title="Foreign storage" description="Hoardarr fingerprints persisted scan evidence first. This view does not mount, assemble, repair, or modify a source disk.">
      {busy && !assessment && <Spinner label="Reading persisted storage signatures…" />}
      {error && <Notice tone="danger" title="Foreign storage assessment unavailable"><p>{error}</p><button type="button" className="button button-secondary" onClick={refresh}>Try again</button></Notice>}
      {assessment && <>
        <Notice tone="info" title="Read-only is the default">No filesystem, array, volume group, or pool was activated. A later inspection plan must revalidate every stable identity and use provider-specific no-recovery behavior.</Notice>
        {!assessment.candidates.length ? <div className="empty-state compact-empty"><h3>No recognized foreign storage</h3><p>{assessment.unrecognized_device_count > 0 ? `${assessment.unrecognized_device_count} non-system device${assessment.unrecognized_device_count === 1 ? " has" : "s have"} insufficient signature evidence. Hoardarr does not call them empty.` : "The latest persisted scan did not report an unassigned supported filesystem or storage stack."}</p></div> : <div className="foreign-candidate-list">{assessment.candidates.map((candidate) => <article key={candidate.id} className="foreign-candidate">
          <header><div><strong>{candidate.profile_name}</strong><span>{candidate.filesystems.length ? candidate.filesystems.join(", ") : candidate.signature_types.join(", ")}</span></div><StatusBadge status={candidate.state === "degraded-review" ? "review required" : "blocked"} /></header>
          <dl className="settings-list">
            <div><dt>Source system</dt><dd>{candidate.origin.name}<small>{candidate.origin.reason}</small></dd></div>
            <div><dt>Evidence</dt><dd>{confidenceLabel(candidate.confidence)}</dd></div>
            <div><dt>Members</dt><dd>{candidate.members.length}</dd></div>
            <div><dt>Raw member capacity</dt><dd>{candidate.capacity_bytes === null ? "Not reported" : humanCapacity(candidate.capacity_bytes)}</dd></div>
          </dl>
          {candidate.warnings.map((warning) => <Notice key={warning} tone="warning" title="Review required">{warning}</Notice>)}
          {candidate.blockers.map((blocker) => <Notice key={blocker} tone="danger" title="Automatic inspection blocked">{blocker}</Notice>)}
          <details><summary>Member and signature evidence</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Device</th><th>Model</th><th>Signatures</th><th>Scan</th><th>Mounted</th></tr></thead><tbody>{candidate.members.map((member) => <tr key={member.device_id}><td><code>{member.kernel_path ?? member.device_id}</code></td><td>{member.model}</td><td>{member.signatures.map((item) => item.type).join(", ") || "Not reported"}</td><td>{member.signature_scan.status ?? "Not reported"}<small className="cell-detail">{member.signature_scan.source ?? "Source not reported"}</small></td><td>{member.mounted ? member.mountpoints.join(", ") : "No"}</td></tr>)}</tbody></table></div></details>
          <footer><span>No changes made</span><button type="button" className="button button-secondary" disabled title={candidate.modes[0].reason}>Read-only inspection plan</button></footer>
        </article>)}</div>}
        <footer className="panel-actions"><small>Snapshot {new Date(assessment.snapshot.captured_at).toLocaleString()}</small><button type="button" className="button button-secondary" onClick={refresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh assessment"}</button></footer>
      </>}
    </Card>
  </details>;
}
