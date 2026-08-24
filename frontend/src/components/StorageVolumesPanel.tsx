import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { OperationDocument, StorageInventory, StorageVolumeDetailDocument, StorageVolumeDocument, StorageVolumePlan } from "../types";
import { Card, Notice, StatusBadge } from "./ui";
import { StorageSnapshotsPanel } from "./StorageSnapshotsPanel";

const PURPOSES: Array<{ id: StorageVolumePlan["purpose"]; label: string; detail: string }> = [
  { id: "media", label: "Movies, TV, and media", detail: "Large-file defaults for Plex, Jellyfin, Emby, and ARR libraries." },
  { id: "downloads", label: "Downloads and temporary work", detail: "General-purpose records for torrent and Usenet working data." },
  { id: "archive", label: "Archive", detail: "Large-file defaults for long-lived content." },
  { id: "backup", label: "Backups", detail: "A separate area for backup content." },
  { id: "general", label: "Files and folders", detail: "Balanced defaults for mixed files." },
  { id: "vm", label: "Virtual machine storage", detail: "Dedicated block storage; requires an explicit size." },
];

export function StorageVolumesPanel({ pools }: { pools: StorageInventory["pools"]["items"] }) {
  const [items, setItems] = useState<StorageVolumeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("media");
  const [purpose, setPurpose] = useState<StorageVolumePlan["purpose"]>("media");
  const zfsPools = useMemo(() => pools.filter((item) => item.type === "ZFS"), [pools]);
  const [poolId, setPoolId] = useState("");
  const [sizeGiB, setSizeGiB] = useState(20);
  const [customize, setCustomize] = useState(false);
  const [resourceType, setResourceType] = useState<"dataset" | "zvol">("dataset");
  const [compression, setCompression] = useState<"off" | "lz4" | "zstd" | "zstd-1" | "zstd-3" | "zstd-6">("zstd");
  const [recordsize, setRecordsize] = useState<"16K" | "32K" | "64K" | "128K" | "256K" | "512K" | "1M">("128K");
  const [atime, setAtime] = useState<"on" | "off">("off");
  const [mountpoint, setMountpoint] = useState("");
  const [volblocksize, setVolblocksize] = useState<"4K" | "8K" | "16K" | "32K" | "64K" | "128K">("16K");
  const [sparse, setSparse] = useState(true);
  const [plan, setPlan] = useState<StorageVolumePlan | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<StorageVolumeDetailDocument | null>(null);
  const [detailTarget, setDetailTarget] = useState<StorageVolumeDocument | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailRequest = useRef<AbortController | null>(null);

  const load = async (signal?: AbortSignal) => setItems(await api.storageVolumes(signal));
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Logical storage could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const current = await api.operation(operation.id);
        if (stopped) return;
        setOperation(current);
        if (current.status === "succeeded") await load();
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : "Creation status could not be loaded.");
      }
    };
    const timer = window.setInterval(() => void refresh(), 1000);
    void refresh();
    return () => { stopped = true; window.clearInterval(timer); };
  }, [operation?.id, operation?.status]);

  useEffect(() => () => detailRequest.current?.abort(), []);

  const preview = async () => {
    setBusy(true); setError(null);
    try {
      setPlan(await api.previewStorageVolume({
        name,
        purpose,
        ...(poolId ? { pool_id: poolId } : {}),
        ...((customize ? resourceType === "zvol" : purpose === "vm") ? { size_bytes: Math.round(sizeGiB * 1024 ** 3) } : {}),
        ...(customize ? {
          advanced: true,
          resource_type: resourceType,
          compression,
          ...(resourceType === "dataset" ? { recordsize, atime, ...(mountpoint ? { mountpoint } : {}) } : { volblocksize, sparse }),
        } : {}),
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The storage plan could not be created."); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!plan?.ready) return;
    setBusy(true); setError(null);
    try { setOperation(await api.createStorageVolume(plan)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Logical storage could not be created."); }
    finally { setBusy(false); }
  };
  const close = () => { setAdding(false); setPlan(null); setOperation(null); setError(null); };
  const closeDetail = () => {
    detailRequest.current?.abort();
    detailRequest.current = null;
    setDetail(null); setDetailTarget(null); setDetailError(null); setDetailLoading(false);
  };
  const openDetail = async (item: StorageVolumeDocument) => {
    detailRequest.current?.abort();
    const controller = new AbortController();
    detailRequest.current = controller;
    setDetail(null); setDetailTarget(item); setDetailError(null); setDetailLoading(true);
    try { setDetail(await api.storageVolume(item.id, controller.signal)); }
    catch (reason) {
      if (!controller.signal.aborted) setDetailError(reason instanceof Error ? reason.message : "Storage area detail could not be loaded.");
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
      if (detailRequest.current === controller) detailRequest.current = null;
    }
  };

  return <Card title="Storage areas" description="Create a media, download, backup, or VM area on a provider that reports the required capability." actions={<button className="icon-add-button" type="button" aria-label="Add storage area" onClick={() => setAdding(true)}>+</button>}>
    {loading ? <p role="status">Loading storage areas…</p> : error && !adding ? <Notice tone="danger" title="Storage areas unavailable">{error}</Notice> : items.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Name</th><th>Provider</th><th>Type</th><th>Path or device</th><th>Size</th><th>State</th><th aria-label="Actions" /></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.name}</strong><small className="cell-detail">{item.stable_identity}</small></td><td>{item.provider}</td><td>{item.resource_type.replaceAll("_", " ")}</td><td><code>{item.mountpoint ?? item.device_path ?? "Not reported"}</code></td><td>{item.size_bytes === null ? "Not reported" : humanCapacity(item.size_bytes)}</td><td><StatusBadge status={item.lifecycle_state.replaceAll("_", " ")} /></td><td><button className="button button-secondary" type="button" onClick={() => void openDetail(item)}>Manage</button></td></tr>)}</tbody></table></div> : <div className="empty-state compact-empty"><h3>No provider-backed storage areas registered</h3><p>Create one only after a compatible live pool is detected. Hoardarr does not claim planned storage as active.</p></div>}
    {detailTarget && <div className="wizard-overlay" role="presentation"><section className="wizard-dialog" role="dialog" aria-modal="true" aria-labelledby="volume-detail-title"><header className="wizard-header"><div><small>PROVIDER-BACKED STORAGE</small><h2 id="volume-detail-title">{detail?.item.name ?? detailTarget.name}</h2></div><button className="wizard-close" type="button" aria-label="Close storage area details" onClick={closeDetail}>×</button></header><div className="wizard-scroll">{detailLoading && <p role="status">Loading provider capabilities and history…</p>}{detailError && <Notice tone="danger" title="Storage area detail unavailable">{detailError}</Notice>}{detail && <><dl className="review-list"><div><dt>Stable identity</dt><dd><code>{detail.item.stable_identity}</code></dd></div><div><dt>Provider resource</dt><dd><code>{detail.item.provider_resource_id}</code></dd></div><div><dt>Presentation</dt><dd>{detail.item.presentation}</dd></div><div><dt>Path or device</dt><dd><code>{detail.item.mountpoint ?? detail.item.device_path ?? "Not reported"}</code></dd></div><div><dt>Provider observation</dt><dd>{detail.item.capabilities_detected_at ?? "Not reported"}</dd></div></dl><h3>Provider capabilities</h3><div className="table-scroll"><table className="data-table"><thead><tr><th>Capability</th><th>Support</th><th>Current availability</th><th>Source</th></tr></thead><tbody>{Object.entries(detail.item.capabilities).map(([name, capability]) => <tr key={name}><td>{name.replaceAll("_", " ")}</td><td>{capability.support.replaceAll("_", " ")}</td><td>{capability.availability.replaceAll("_", " ")}</td><td>{capability.source.replaceAll("_", " ")}</td></tr>)}</tbody></table></div>{detail.item.capabilities.snapshot?.support === "supported" && detail.item.capabilities.snapshot.availability === "available" ? <StorageSnapshotsPanel volume={detail.item} /> : <Notice tone="warning" title="Provider snapshots unavailable">{detail.item.capabilities.snapshot?.support === "unsupported" ? "This provider does not support snapshots." : "The provider did not report a currently usable snapshot capability."}</Notice>}<h3>Activity history</h3>{detail.operations.length ? <div className="activity-timeline">{detail.operations.map((item) => <article key={item.id}><strong>{item.kind.replaceAll(".", " ")}</strong><StatusBadge status={item.status} /><small>{item.created_at ?? "Time Not reported"}</small></article>)}</div> : <div className="empty-state compact-empty"><h3>No storage-area operations</h3><p>No durable provider operation has been recorded for this storage area.</p></div>}</>}</div><footer className="wizard-footer"><button className="button button-secondary" type="button" onClick={closeDetail}>Close</button></footer></section></div>}
    {adding && <div className="wizard-overlay" role="presentation"><section className="wizard-dialog" role="dialog" aria-modal="true" aria-labelledby="volume-title"><header className="wizard-header"><div><small>GUIDED STORAGE</small><h2 id="volume-title">Add a storage area</h2></div><button className="wizard-close" type="button" aria-label="Close storage area wizard" onClick={close}>×</button></header><div className="wizard-scroll">
      {error && <Notice tone="danger" title="Storage plan needs attention">{error}</Notice>}
      {!plan && !operation && <><Card title="What will you store?"><div className="choice-grid">{PURPOSES.map((item) => <label className={`choice-card ${purpose === item.id ? "selected" : ""}`} key={item.id}><input type="radio" name="volume-purpose" checked={purpose === item.id} onChange={() => { setPurpose(item.id); setResourceType(item.id === "vm" ? "zvol" : "dataset"); }} /><strong>{item.label}</strong><small>{item.detail}</small></label>)}</div></Card><Card title="Name and location"><label>Name<input value={name} maxLength={63} onChange={(event) => setName(event.target.value)} /></label><label>Storage pool<select value={poolId} onChange={(event) => setPoolId(event.target.value)}><option value="">Use the recommended healthy pool</option>{zfsPools.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.free_bytes === null ? "free space Not reported" : `${humanCapacity(item.free_bytes)} free`}</option>)}</select></label>{((customize && resourceType === "zvol") || (!customize && purpose === "vm")) && <label>Size (GiB)<input type="number" min={1} max={1_000_000} value={sizeGiB} onChange={(event) => setSizeGiB(Number(event.target.value))} /></label>}{zfsPools.length === 0 && <Notice tone="warning" title="No compatible pool detected">Guided storage areas currently require an online ZFS pool with a stable pool identity. Create or import storage first.</Notice>}</Card><Card title="Provider settings" description="Recommended defaults are used unless you deliberately customize ZFS geometry."><label className="toggle-row"><input type="checkbox" checked={customize} onChange={(event) => setCustomize(event.target.checked)} /><span><strong>Customize ZFS settings</strong><small>Advanced · changes the actual provider command</small></span></label>{customize && <div className="settings-grid"><label>Resource type<select value={resourceType} onChange={(event) => setResourceType(event.target.value as "dataset" | "zvol")}><option value="dataset">Dataset · file storage</option><option value="zvol">Zvol · block storage</option></select></label><label>Compression<select value={compression} onChange={(event) => setCompression(event.target.value as typeof compression)}>{["off", "lz4", "zstd", "zstd-1", "zstd-3", "zstd-6"].map((value) => <option key={value}>{value}</option>)}</select></label>{resourceType === "dataset" ? <><label>Record size<select value={recordsize} onChange={(event) => setRecordsize(event.target.value as typeof recordsize)}>{["16K", "32K", "64K", "128K", "256K", "512K", "1M"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Access-time updates<select value={atime} onChange={(event) => setAtime(event.target.value as "on" | "off")}><option value="off">Off</option><option value="on">On</option></select></label><label>Mount path (optional)<input value={mountpoint} placeholder={`/srv/hoardarr/volumes/${name}`} onChange={(event) => setMountpoint(event.target.value)} /></label></> : <><label>Volume block size<select value={volblocksize} onChange={(event) => setVolblocksize(event.target.value as typeof volblocksize)}>{["4K", "8K", "16K", "32K", "64K", "128K"].map((value) => <option key={value}>{value}</option>)}</select></label><label className="toggle-row"><input type="checkbox" checked={sparse} onChange={(event) => setSparse(event.target.checked)} /><span><strong>Thin/sparse allocation</strong><small>Disable to reserve the full zvol size now.</small></span></label></>}</div>}</Card></>}
      {plan && !operation && <><Notice tone={plan.ready ? "info" : "warning"} title={plan.ready ? "Recommended plan" : "This plan is blocked"}>{plan.explanation}</Notice><dl className="review-list"><div><dt>Creates</dt><dd>{plan.resource_type === "zvol" ? "Dedicated block storage" : "A separate file storage area"}</dd></div><div><dt>Provider</dt><dd>ZFS pool {plan.parent.pool_name}</dd></div><div><dt>Result</dt><dd><code>{String(plan.properties.mountpoint ?? `/dev/zvol/${plan.provider_resource_id}`)}</code></dd></div><div><dt>Parent pool identity</dt><dd><code>{plan.parent.pool_guid}</code></dd></div><div><dt>Plan SHA-256</dt><dd><code>{plan.plan_sha256}</code></dd></div></dl>{plan.blockers.map((blocker) => <Notice key={blocker.code} tone="warning" title={blocker.code}>{blocker.message}</Notice>)}<details><summary>Advanced provider settings</summary><pre>{JSON.stringify(plan.properties, null, 2)}</pre></details></>}
      {operation && <Card title="Creating storage"><StatusBadge status={operation.status.replaceAll("_", " ")} />{["queued", "running"].includes(operation.status) ? <p role="status">Hoardarr is creating and verifying the provider resource. You can also follow this in Activity.</p> : operation.status === "succeeded" ? <Notice tone="info" title="Storage area ready">The provider resource was created, verified, and registered.</Notice> : <Notice tone="danger" title="Creation did not finish">{operation.error?.message ?? "Review Activity for the safe provider error."}</Notice>}</Card>}
    </div><footer className="wizard-footer"><button className="button button-secondary" type="button" onClick={close} disabled={busy || operation?.status === "running"}>{operation && !["queued", "running"].includes(operation.status) ? "Close" : "Cancel"}</button>{!plan && !operation && <button className="button button-primary" type="button" onClick={() => void preview()} disabled={busy || zfsPools.length === 0}>{busy ? "Reviewing…" : "Review plan"}</button>}{plan && !operation && <button className="button button-primary" type="button" onClick={() => void apply()} disabled={busy || !plan.ready}>{busy ? "Starting…" : "Create storage"}</button>}</footer></section></div>}
  </Card>;
}
