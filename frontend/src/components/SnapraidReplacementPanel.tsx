import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type {
  Drive,
  OperationDocument,
  SnapraidReplacementPlan,
  StorageInventory,
  StorageOperationProgress,
} from "../types";
import { Card, Notice, StatusBadge } from "./ui";

type ReplacementPreview = { plan: SnapraidReplacementPlan; plan_sha256: string };

export function SnapraidReplacementPanel({
  inventory,
  availableDrives,
}: {
  inventory: StorageInventory | null;
  availableDrives: Drive[];
}) {
  const pools = useMemo(
    () => (inventory?.pools.items ?? []).filter((item) => item.type.toLowerCase() === "snapraid"),
    [inventory],
  );
  const [poolName, setPoolName] = useState("");
  const [dataName, setDataName] = useState("");
  const [driveId, setDriveId] = useState("");
  const [filesystem, setFilesystem] = useState<"ext4" | "xfs" | "btrfs">("ext4");
  const [preview, setPreview] = useState<ReplacementPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [progress, setProgress] = useState<StorageOperationProgress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedPool = pools.find((item) => item.name === poolName) ?? pools[0] ?? null;
  const dataDisks = useMemo(
    () => selectedPool?.configuration?.quality === "available"
      ? selectedPool.configuration.data_disks ?? []
      : [],
    [selectedPool],
  );

  useEffect(() => {
    if (!selectedPool) return;
    if (poolName !== selectedPool.name) setPoolName(selectedPool.name);
    if (!dataDisks.some((item) => item.name === dataName)) setDataName(dataDisks[0]?.name ?? "");
  }, [dataDisks, dataName, poolName, selectedPool]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const [nextOperation, nextProgress] = await Promise.all([
          api.operation(operation.id),
          api.storageOperationProgress(operation.id),
        ]);
        if (!stopped) {
          setOperation(nextOperation);
          setProgress(nextProgress);
        }
      } catch (requestError) {
        if (!stopped) {
          setError(requestError instanceof Error ? requestError.message : "Replacement status could not be loaded.");
        }
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [operation?.id, operation?.status]);

  if (!pools.length) return null;

  const resetReview = () => {
    setPreview(null);
    setConfirmation("");
  };

  const review = async () => {
    if (!selectedPool || !dataName || !driveId) return;
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.previewSnapraidReplacement({
        pool_name: selectedPool.name,
        data_name: dataName,
        replacement_device_id: driveId,
        filesystem,
      }));
      setConfirmation("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The replacement could not be reviewed.");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview || confirmation !== "I AGREE") return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.applySnapraidReplacement(preview.plan, preview.plan_sha256));
      setProgress(null);
      setConfirmation("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The replacement could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return <Card
    title="Replace a SnapRAID data disk"
    description="Rebuild one missing or failed data member onto a verified replacement without changing your media namespace."
  >
    {error && <Notice tone="danger" title="Replacement needs attention">{error}</Notice>}
    {selectedPool?.configuration?.quality !== "available" ? <Notice tone="warning" title="SnapRAID configuration not available">Hoardarr cannot safely identify the configured data members. No replacement plan can be created.</Notice> : <>
      <div className="form-grid storage-group-form">
        <label>SnapRAID configuration<select aria-label="SnapRAID configuration" value={selectedPool?.name ?? ""} onChange={(event) => { setPoolName(event.target.value); setDataName(""); resetReview(); }}>{pools.map((pool) => <option key={pool.id} value={pool.name}>{pool.name}</option>)}</select></label>
        <label>Data disk to rebuild<select aria-label="SnapRAID data disk to rebuild" value={dataName} onChange={(event) => { setDataName(event.target.value); resetReview(); }}><option value="">Choose a configured data disk</option>{dataDisks.map((disk) => <option key={disk.name} value={disk.name}>{disk.name} · {disk.path}</option>)}</select></label>
        <label>Replacement drive<select aria-label="SnapRAID replacement drive" value={driveId} onChange={(event) => { setDriveId(event.target.value); resetReview(); }}><option value="">Choose an unassigned drive</option>{availableDrives.map((drive) => <option key={drive.id} value={drive.id}>{drive.vendor} {drive.model} · {drive.serial} · {humanCapacity(drive.capacityBytes)}</option>)}</select><small>Only detected, selectable, unassigned drives are offered. The selected replacement will be formatted.</small></label>
        <label>Filesystem<select aria-label="Replacement filesystem" value={filesystem} onChange={(event) => { setFilesystem(event.target.value as typeof filesystem); resetReview(); }}><option value="ext4">ext4 (recommended)</option><option value="xfs">XFS</option><option value="btrfs">Btrfs</option></select></label>
      </div>
      {!availableDrives.length && <Notice tone="info" title="No replacement drive available">Connect a suitable unassigned drive and run a hardware scan. Hoardarr will not repurpose a managed or protected system disk.</Notice>}
      <button type="button" className="button button-secondary" disabled={busy || !dataName || !driveId} onClick={() => void review()}>Review replacement</button>
    </>}
    {preview && !operation && <section className="storage-drain-preview" aria-live="polite" aria-labelledby="snapraid-replacement-review"><h3 id="snapraid-replacement-review">Review destructive replacement</h3><Notice tone="warning" title="The replacement drive will be erased">Hoardarr will clear signatures, partition, and format only the reviewed replacement drive. It will not format the old path or recreate your Storage Group.</Notice>{preview.plan.existing_data.detected && <Notice tone="danger" title="Existing data detected on the replacement">The latest scan found {preview.plan.existing_data.partition_count} partition{preview.plan.existing_data.partition_count === 1 ? "" : "s"}{preview.plan.existing_data.signature_types.length ? ` and these signatures: ${preview.plan.existing_data.signature_types.join(", ")}` : ""}. Approval will erase them.</Notice>}{preview.plan.existing_data.scan_status !== "complete" && <Notice tone="danger" title="Signature scan incomplete">Hoardarr could not completely inspect existing signatures. Treat this replacement drive as containing unknown data.</Notice>}<dl className="review-grid"><div><dt>Configuration</dt><dd>{preview.plan.pool_name}</dd></div><div><dt>Data member</dt><dd>{preview.plan.data_name}</dd></div><div><dt>Old path</dt><dd><code>{preview.plan.old_path}</code></dd></div><div><dt>Replacement identity</dt><dd><code>{preview.plan.device.id}</code></dd></div><div><dt>Current kernel path</dt><dd>Resolved and revalidated immediately before execution</dd></div><div><dt>Replacement capacity</dt><dd>{humanCapacity(preview.plan.device.capacity_bytes)}</dd></div><div><dt>Existing-data scan</dt><dd>{preview.plan.existing_data.scan_status.replace("_", " ")}</dd></div><div><dt>New managed mount</dt><dd><code>{preview.plan.replacement_mount}</code></dd></div><div><dt>Filesystem</dt><dd>{preview.plan.filesystem}</dd></div></dl><p>After formatting, Hoardarr updates exactly one data entry, asks SnapRAID to reconstruct that member, verifies parity status, and performs a synchronization. If the configuration or hardware snapshot changes, execution stops.</p><small>Immutable plan <code>{preview.plan_sha256}</code></small><label>Type I AGREE to erase the replacement drive<input aria-label="SnapRAID replacement confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></label><div className="button-row"><button type="button" className="button button-primary" disabled={busy || confirmation !== "I AGREE"} onClick={() => void apply()}>Start durable replacement</button><button type="button" className="button button-secondary" disabled={busy} onClick={resetReview}>Cancel</button></div></section>}
    {operation && <section className="storage-drain-operation" aria-live="polite"><div className="section-heading"><div><p className="eyebrow">Disk replacement</p><h3>SnapRAID rebuild</h3></div><StatusBadge status={progress?.state ?? operation.status} /></div><p>{progress?.phase ?? "Waiting for the durable worker"}</p><div className="operation-progress-track" role="progressbar" aria-label="SnapRAID replacement progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}><span style={{ width: `${progress?.percent ?? 0}%` }} /></div>{operation.error && <Notice tone="danger" title="Replacement stopped safely">{operation.error.detail || operation.error.message || "Review Activity for the durable failure evidence."}</Notice>}{operation.status === "succeeded" && <Notice tone="success" title="Replacement completed">SnapRAID reconstructed the selected data member and completed its final synchronization.</Notice>}{["succeeded", "failed", "cancelled", "needs_attention"].includes(operation.status) && <button type="button" className="button button-secondary" onClick={() => { setOperation(null); setProgress(null); resetReview(); }}>Close report</button>}</section>}
  </Card>;
}
