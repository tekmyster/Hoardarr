import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { StorageExpansionAssessment } from "../types";
import type { DriveAction } from "./StoragePage";
import { Card, Notice, StatusBadge } from "./ui";

function setupAction(mode: StorageExpansionAssessment["candidates"][number]["setup_mode"]): DriveAction {
  return mode;
}

function capacity(value: number | null): string {
  return value === null ? "Not calculated" : humanCapacity(value);
}

export function StorageExpansionPanel({
  onPlan,
  snapshotId,
}: {
  onPlan: (action: DriveAction, diskIds: string[]) => void;
  snapshotId: string | null;
}) {
  const [assessment, setAssessment] = useState<StorageExpansionAssessment | null>(null);
  const [loading, setLoading] = useState(true);
  const [reservationBusy, setReservationBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reservedDisks = assessment?.reserved_disks ?? [];

  const load = async (signal?: AbortSignal) => {
    setError(null);
    setAssessment(await api.storageExpansion(signal));
  };

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Expansion choices could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [snapshotId]);

  const diskNames = useMemo(() => new Map(
    [...(assessment?.available_disks ?? []), ...reservedDisks].map((disk) => [disk.id, `${disk.vendor ?? ""} ${disk.model ?? "Disk"}`.trim()]),
  ), [assessment]);

  const changeReservation = async (diskId: string, action: "reserve" | "release") => {
    setReservationBusy(diskId);
    setError(null);
    try {
      await api.setDiskReservation(diskId, action);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The disk reservation could not be changed.");
    } finally {
      setReservationBusy(null);
    }
  };

  return <Card title="Plan storage expansion" description="See safe choices for newly detected disks before changing storage.">
    <div className="section-heading"><div><p className="eyebrow">Read-only analysis</p><h3>Expansion choices</h3></div><button className="button button-secondary" type="button" onClick={() => { setLoading(true); void load().catch((reason) => setError(reason instanceof Error ? reason.message : "Expansion choices could not be loaded.")).finally(() => setLoading(false)); }} disabled={loading}>{loading ? "Analyzing…" : "Refresh choices"}</button></div>
    {error && <Notice tone="danger" title="Expansion analysis unavailable">{error}</Notice>}
    {loading && !assessment ? <p role="status">Analyzing current Storage Groups and unassigned disks…</p> : assessment && assessment.available_disks.length === 0 && reservedDisks.length === 0 ? <div className="empty-state compact-empty"><h3>No unassigned disks detected</h3><p>Add or release a disk, then run a storage scan. Existing managed storage will not be changed.</p></div> : assessment && <>
      <p>{assessment.methodology}</p>
      {reservedDisks.length > 0 && <section aria-label="Disks reserved for later"><h4>Reserved for later</h4><p>These disks stay unmodified and are excluded from new storage plans until released.</p><div className="expansion-disk-list">{reservedDisks.map((disk) => <article key={disk.id}><div><strong>{disk.vendor} {disk.model}</strong><code>{disk.kernel_path ?? disk.stable_identity}</code></div><span>{capacity(disk.capacity_bytes)}</span><StatusBadge status="reserved" /><button className="button button-secondary" type="button" disabled={reservationBusy === disk.id} onClick={() => void changeReservation(disk.id, "release")}>{reservationBusy === disk.id ? "Releasing…" : "Release disk"}</button></article>)}</div></section>}
      {assessment.storage_groups.length > 0 && <div className="expansion-disk-list" aria-label="Current Storage Group state">{assessment.storage_groups.map((group) => <article key={group.id}><div><strong>{group.name}</strong><code>{group.namespace_path}</code></div><span>{group.capacity.quality === "available" ? `${capacity(group.capacity.free_bytes)} free of ${capacity(group.capacity.total_bytes)}` : "Capacity not reported"}</span><span>{group.protection.summary}</span><span>{group.distribution.spread_percentage_points === null ? "Member balance not reported" : `${group.distribution.spread_percentage_points.toFixed(1)} point member-usage spread`}</span></article>)}</div>}
      <div className="expansion-capabilities" aria-label="Detected expansion capabilities"><span>mergerFS: <strong>{assessment.detected_capabilities.mergerfs ? "Detected" : "Not configured"}</strong></span><span>SnapRAID: <strong>{assessment.detected_capabilities.snapraid ? "Detected" : "Not configured"}</strong></span><span>ZFS: <strong>{assessment.detected_capabilities.zfs ? "Detected" : "Not configured"}</strong></span></div>
      <div className="expansion-disk-list">{assessment.available_disks.map((disk) => <article key={disk.id}><div><strong>{disk.vendor} {disk.model}</strong><code>{disk.kernel_path ?? disk.stable_identity}</code></div><span>{capacity(disk.capacity_bytes)}</span><StatusBadge status={disk.health} /><span>{disk.existing_data.state === "none_detected" ? "No existing data detected" : disk.existing_data.state === "detected" ? "Existing storage detected" : "Existing data unknown"}</span>{disk.blockers.length > 0 && <small>{disk.blockers.join(" ")}</small>}</article>)}</div>
      {assessment.candidates.length === 0 ? <Notice tone="warning" title="No safe expansion plan yet">The detected disks need health, identity, or existing-data review before Hoardarr can recommend a change.</Notice> : <div className="expansion-candidate-list">{assessment.candidates.map((candidate) => <article key={candidate.id} className={candidate.recommended ? "recommended" : ""} aria-label={candidate.title}>
        <header><div>{candidate.recommended && <span className="recommended-badge">Recommended</span>}<h4>{candidate.title}</h4><p>{candidate.summary}</p></div><StatusBadge status={candidate.setup_mode === "import" ? "read only" : "plan"} /></header>
        <div className="review-grid"><dl><dt>Drives</dt><dd>{candidate.disk_ids.map((id) => diskNames.get(id) ?? id).join(" + ")}</dd></dl><dl><dt>Raw capacity added</dt><dd>{capacity(candidate.capacity.raw_delta_bytes)}</dd></dl><dl><dt>Estimated usable added</dt><dd>{capacity(candidate.capacity.estimated_usable_delta_bytes)}</dd></dl><dl><dt>Protection</dt><dd>{candidate.protection_impact}</dd></dl><dl><dt>Adding drives later</dt><dd>{candidate.future_expansion}</dd></dl><dl><dt>Work required</dt><dd>{candidate.migration_work}</dd></dl></div>
        {candidate.restrictions.length > 0 && <details><summary>Restrictions and calculation details</summary><ul>{candidate.restrictions.map((item) => <li key={item}>{item}</li>)}</ul><p>{candidate.capacity.methodology}</p></details>}
        <div className="button-row"><button className="button button-primary" type="button" onClick={() => onPlan(setupAction(candidate.setup_mode), candidate.disk_ids)}>{candidate.setup_mode === "import" ? "Review existing storage" : "Customize this plan"}</button>{candidate.disk_ids.length === 1 && <button className="button button-secondary" type="button" disabled={reservationBusy === candidate.disk_ids[0]} onClick={() => void changeReservation(candidate.disk_ids[0], "reserve")}>{reservationBusy === candidate.disk_ids[0] ? "Reserving…" : "Reserve for later"}</button>}</div>
      </article>)}</div>}
      <small>Based on hardware scan {new Date(assessment.captured_at).toLocaleString()} · <code>{assessment.hardware_snapshot_sha256.slice(0, 12)}</code></small>
    </>}
  </Card>;
}
