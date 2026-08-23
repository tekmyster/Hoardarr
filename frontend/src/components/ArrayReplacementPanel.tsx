import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { ArrayReplacementPlan, Drive, OperationDocument, StorageInventory, StorageOperationProgress } from "../types";
import { Card, Notice, StatusBadge } from "./ui";

type Preview = { plan: ArrayReplacementPlan; plan_sha256: string };

export function ArrayReplacementPanel({ inventory, availableDrives }: { inventory: StorageInventory | null; availableDrives: Drive[] }) {
  const pools = useMemo(
    () => (inventory?.pools.items ?? []).filter((item) => item.id.startsWith("zfs:") || item.id.startsWith("md:")),
    [inventory],
  );
  const [targetId, setTargetId] = useState("");
  const [memberPath, setMemberPath] = useState("");
  const [driveId, setDriveId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [progress, setProgress] = useState<StorageOperationProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = pools.find((item) => item.id === targetId) ?? pools[0] ?? null;
  const members = selected?.configuration?.member_paths ?? [];
  const isMd = selected?.id.startsWith("md:") ?? false;

  useEffect(() => {
    if (!selected) return;
    if (targetId !== selected.id) setTargetId(selected.id);
    if (!members.includes(memberPath) && !(isMd && selected.degraded && memberPath === "__missing__")) {
      setMemberPath(isMd && selected.degraded ? "__missing__" : members[0] ?? "");
    }
  }, [isMd, memberPath, members, selected, targetId]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const [next, nextProgress] = await Promise.all([api.operation(operation.id), api.storageOperationProgress(operation.id)]);
        if (!stopped) { setOperation(next); setProgress(nextProgress); }
      } catch (requestError) {
        if (!stopped) setError(requestError instanceof Error ? requestError.message : "Replacement status could not be loaded.");
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [operation?.id, operation?.status]);

  if (!pools.length) return null;
  const reset = () => { setPreview(null); setConfirmation(""); };
  const review = async () => {
    if (!selected || !memberPath || !driveId) return;
    setBusy(true); setError(null);
    try {
      setPreview(await api.previewArrayReplacement({
        target_id: selected.id,
        old_member_path: memberPath === "__missing__" ? null : memberPath,
        replacement_device_id: driveId,
      }));
      setConfirmation("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The replacement could not be reviewed.");
    } finally { setBusy(false); }
  };
  const apply = async () => {
    if (!preview || confirmation !== "I AGREE") return;
    setBusy(true); setError(null);
    try { setOperation(await api.applyArrayReplacement(preview.plan, preview.plan_sha256)); setProgress(null); setConfirmation(""); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "The replacement could not be started."); }
    finally { setBusy(false); }
  };

  return <Card title="Replace a ZFS or Linux MD disk" description="Rebuild a failed or aging array member without changing the storage name, mount, shares, or media paths.">
    {error && <Notice tone="danger" title="Replacement needs attention">{error}</Notice>}
    {selected?.configuration?.quality && selected.configuration.quality !== "available" && <Notice tone="warning" title="Provider details unavailable">Hoardarr cannot create a safe replacement plan until the provider reports authoritative membership.</Notice>}
    <div className="form-grid storage-group-form">
      <label>Storage array<select aria-label="Storage array to repair" value={selected?.id ?? ""} onChange={(event) => { setTargetId(event.target.value); setMemberPath(""); reset(); }}>{pools.map((pool) => <option key={pool.id} value={pool.id}>{pool.name} · {pool.type} · {pool.status}</option>)}</select></label>
      <label>Member to replace<select aria-label="Array member to replace" value={memberPath} onChange={(event) => { setMemberPath(event.target.value); reset(); }}>{isMd && selected?.degraded && <option value="__missing__">Missing member / empty array slot</option>}{members.map((path) => <option key={path} value={path}>{path}</option>)}</select><small>{memberPath === "__missing__" ? "Linux MD will rebuild the missing slot onto the reviewed drive." : "The provider will replace this exact current member."}</small></label>
      <label>Replacement drive<select aria-label="Array replacement drive" value={driveId} onChange={(event) => { setDriveId(event.target.value); reset(); }}><option value="">Choose an unassigned drive</option>{availableDrives.map((drive) => <option key={drive.id} value={drive.id}>{drive.vendor} {drive.model} · {drive.serial} · {humanCapacity(drive.capacityBytes)}</option>)}</select><small>Only unassigned, selectable, non-system drives are offered.</small></label>
    </div>
    {!availableDrives.length && <Notice tone="info" title="No replacement drive available">Connect a suitable unassigned drive and scan again. Hoardarr will not repurpose managed or protected system storage.</Notice>}
    <button type="button" className="button button-secondary" disabled={busy || !memberPath || !driveId || Boolean(selected?.configuration?.quality && selected.configuration.quality !== "available")} onClick={() => void review()}>Review array replacement</button>
    {preview && !operation && <section className="storage-drain-preview" aria-live="polite" aria-labelledby="array-replacement-review"><h3 id="array-replacement-review">Review destructive replacement</h3><Notice tone="warning" title="Only the replacement drive will be erased">The existing array, filesystem, mount, shares, and application paths will not be recreated. {preview.plan.provider === "zfs" ? "ZFS will resilver onto the replacement." : "Linux MD will recover onto the replacement."}</Notice>{preview.plan.existing_data.detected && <Notice tone="danger" title="Existing data detected on the replacement">Discovery found {preview.plan.existing_data.partition_count} partition{preview.plan.existing_data.partition_count === 1 ? "" : "s"}{preview.plan.existing_data.signature_types.length ? ` and ${preview.plan.existing_data.signature_types.join(", ")} signatures` : ""}. Approval erases them.</Notice>}{preview.plan.existing_data.scan_status !== "complete" && <Notice tone="danger" title="Signature scan incomplete">Treat this replacement drive as containing unknown data.</Notice>}<dl className="review-grid"><div><dt>Provider</dt><dd>{preview.plan.provider === "zfs" ? "ZFS" : "Linux MD"}</dd></div><div><dt>Storage</dt><dd>{preview.plan.target_name}</dd></div><div><dt>Stable storage identity</dt><dd><code>{preview.plan.target_identity}</code></dd></div><div><dt>Member</dt><dd><code>{preview.plan.old_member_path ?? "Missing array slot"}</code></dd></div><div><dt>Replacement identity</dt><dd><code>{preview.plan.device.id}</code></dd></div><div><dt>Replacement capacity</dt><dd>{humanCapacity(preview.plan.device.capacity_bytes)}</dd></div><div><dt>Layout</dt><dd>{preview.plan.level}</dd></div><div><dt>Existing-data scan</dt><dd>{preview.plan.existing_data.scan_status.replace("_", " ")}</dd></div></dl><p>The executor will re-check hardware identity and provider membership immediately before erasing the reviewed replacement, wait for the real rebuild, and verify the same pool/array identity afterward.</p><small>Immutable plan <code>{preview.plan_sha256}</code></small><label>Type I AGREE to erase the replacement drive<input aria-label="Array replacement confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></label><div className="button-row"><button type="button" className="button button-primary" disabled={busy || confirmation !== "I AGREE"} onClick={() => void apply()}>Start durable array replacement</button><button type="button" className="button button-secondary" disabled={busy} onClick={reset}>Cancel</button></div></section>}
    {operation && <section className="storage-drain-operation" aria-live="polite"><div className="section-heading"><div><p className="eyebrow">Disk replacement</p><h3>{preview?.plan.provider === "zfs" ? "ZFS resilver" : "Linux MD recovery"}</h3></div><StatusBadge status={progress?.state ?? operation.status} /></div><p>{progress?.phase ?? "Waiting for the durable worker"}</p><div className="operation-progress-track" role="progressbar" aria-label="Array replacement progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}><span style={{ width: `${progress?.percent ?? 0}%` }} /></div>{operation.error && <Notice tone="danger" title="Replacement stopped safely">{operation.error.detail || operation.error.message || "Review Activity before taking another storage action."}</Notice>}{operation.status === "succeeded" && <Notice tone="success" title="Array replacement completed">The provider reports the replacement member present and the original storage identity healthy.</Notice>}{["succeeded", "failed", "cancelled", "needs_attention"].includes(operation.status) && <button type="button" className="button button-secondary" onClick={() => { setOperation(null); setProgress(null); reset(); }}>Close report</button>}</section>}
  </Card>;
}
