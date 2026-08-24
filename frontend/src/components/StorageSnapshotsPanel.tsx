import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  OperationDocument,
  StorageVolumeDocument,
  StorageVolumeSnapshotDocument,
  StorageVolumeSnapshotInventory,
  StorageVolumeSnapshotPlan,
} from "../types";
import { Notice, StatusBadge } from "./ui";

type SnapshotAction = StorageVolumeSnapshotPlan["action"];

export function StorageSnapshotsPanel({ volume }: { volume: StorageVolumeDocument }) {
  const [inventory, setInventory] = useState<StorageVolumeSnapshotInventory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("manual");
  const [cloneName, setCloneName] = useState("");
  const [selected, setSelected] = useState<StorageVolumeSnapshotDocument | null>(null);
  const [action, setAction] = useState<SnapshotAction>("create");
  const [plan, setPlan] = useState<StorageVolumeSnapshotPlan | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const request = useRef<AbortController | null>(null);

  const load = async (signal?: AbortSignal) => {
    const current = await api.storageVolumeSnapshots(volume.id, signal);
    setInventory(current);
  };

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    void load(controller.signal)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Snapshots could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
        if (request.current === controller) request.current = null;
      });
    return () => controller.abort();
  }, [volume.id]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const current = await api.operation(operation.id);
        if (stopped) return;
        setOperation(current);
        if (current.status === "succeeded") {
          await load();
          setPlan(null);
          setSelected(null);
        }
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : "Snapshot status could not be loaded.");
      }
    };
    const timer = window.setInterval(() => void refresh(), 1000);
    void refresh();
    return () => { stopped = true; window.clearInterval(timer); };
  }, [operation?.id, operation?.status]);

  const review = async (nextAction: SnapshotAction, snapshot?: StorageVolumeSnapshotDocument) => {
    setBusy(true); setError(null); setAction(nextAction); setSelected(snapshot ?? null); setOperation(null);
    try {
      setPlan(await api.previewStorageVolumeSnapshot(volume.id, {
        action: nextAction,
        ...(nextAction === "create" ? { snapshot_name: name } : { snapshot_id: snapshot?.id }),
        ...(nextAction === "clone" ? { clone_name: cloneName } : {}),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The snapshot plan could not be reviewed.");
    } finally { setBusy(false); }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy(true); setError(null);
    try { setOperation(await api.applyStorageVolumeSnapshot(volume.id, plan)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The snapshot operation could not start."); }
    finally { setBusy(false); }
  };

  const saveSchedule = async () => {
    if (!inventory) return;
    setScheduleSaving(true); setError(null);
    try {
      const schedule = await api.saveStorageVolumeSnapshotSchedule(volume.id, {
        enabled: inventory.schedule.enabled,
        interval_hours: inventory.schedule.interval_hours,
        retention_count: inventory.schedule.retention_count,
        prefix: inventory.schedule.prefix,
      });
      setInventory({ ...inventory, schedule });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The snapshot schedule could not be saved."); }
    finally { setScheduleSaving(false); }
  };

  if (loading) return <p role="status">Loading snapshots and schedule…</p>;
  if (!inventory) return <Notice tone="danger" title="Snapshots unavailable">{error ?? "The provider did not return snapshot state."}</Notice>;
  const available = inventory.items.filter((item) => item.state === "available");
  const updateSchedule = (patch: Partial<StorageVolumeSnapshotInventory["schedule"]>) => setInventory({ ...inventory, schedule: { ...inventory.schedule, ...patch } });

  return <section aria-labelledby="storage-snapshots-title">
    <h3 id="storage-snapshots-title">Snapshots and clones</h3>
    <p>Provider snapshots are real ZFS recovery points. Hoardarr records only provider-confirmed results; a clone becomes another managed storage area.</p>
    {error && <Notice tone="danger" title="Snapshot action needs attention">{error}</Notice>}
    <div className="settings-grid">
      <label>New snapshot name<input value={name} maxLength={96} onChange={(event) => setName(event.target.value.toLowerCase())} /></label>
      <div><span className="field-label">Manual recovery point</span><button className="button button-secondary" type="button" disabled={busy} onClick={() => void review("create")}>Review snapshot</button></div>
    </div>
    {available.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Snapshot</th><th>Created</th><th>Provider identity</th><th>Last restored</th><th>Actions</th></tr></thead><tbody>{available.map((item) => <tr key={item.id}><td><strong>{item.snapshot_name}</strong><small className="cell-detail">{item.provider_snapshot_id}</small></td><td>{item.created_at}</td><td><code>{item.provider_guid}</code></td><td>{item.restored_at ?? "Never"}</td><td><div className="button-row"><button className="button button-secondary" type="button" onClick={() => void review("restore", item)}>Restore</button><button className="button button-secondary" type="button" onClick={() => { setSelected(item); setAction("clone"); setPlan(null); }}>Clone</button><button className="button button-danger" type="button" onClick={() => void review("delete", item)}>Delete</button></div></td></tr>)}</tbody></table></div> : <div className="empty-state compact-empty"><h3>No provider snapshots</h3><p>Create a recovery point before a risky application or configuration change.</p></div>}
    {selected && action === "clone" && !plan && <div className="settings-grid"><label>Clone name<input value={cloneName} maxLength={63} placeholder="media-test" onChange={(event) => setCloneName(event.target.value.toLowerCase())} /></label><div><span className="field-label">Clone {selected.snapshot_name}</span><button className="button button-secondary" type="button" disabled={busy || !cloneName} onClick={() => void review("clone", selected)}>Review clone</button></div></div>}
    {plan && <div className="operation-review"><Notice tone={plan.action === "restore" || plan.action === "delete" ? "warning" : "info"} title={`Review ${plan.action}`}>{plan.risk}</Notice><dl className="review-list"><div><dt>Provider snapshot</dt><dd><code>{plan.snapshot.provider_snapshot_id}</code></dd></div>{plan.target_resource_id && <div><dt>New provider resource</dt><dd><code>{plan.target_resource_id}</code></dd></div>}{plan.target_mountpoint && <div><dt>Clone mount path</dt><dd><code>{plan.target_mountpoint}</code></dd></div>}<div><dt>Exact confirmation</dt><dd><code>{plan.confirmation}</code></dd></div><div><dt>Immutable plan</dt><dd><code>{plan.plan_sha256}</code></dd></div></dl><div className="button-row"><button className="button button-secondary" type="button" onClick={() => setPlan(null)}>Back</button><button className={plan.action === "restore" || plan.action === "delete" ? "button button-danger" : "button button-primary"} type="button" disabled={busy} onClick={() => void apply()}>{busy ? "Starting…" : plan.confirmation}</button></div></div>}
    {operation && <Notice tone={operation.status === "failed" ? "danger" : operation.status === "succeeded" ? "success" : "info"} title={`Snapshot operation ${operation.status.replaceAll("_", " ")}`}><StatusBadge status={operation.status} /> {operation.error?.message ?? "The durable provider operation is recorded in Activity."}</Notice>}
    <h3>Automatic snapshots</h3>
    <div className="settings-grid"><label className="toggle-row"><input type="checkbox" checked={inventory.schedule.enabled} onChange={(event) => updateSchedule({ enabled: event.target.checked })} /><span><strong>Keep automatic recovery points</strong><small>Executed by the durable worker even when no browser is connected.</small></span></label><label>Every (hours)<input type="number" min={1} max={8760} value={inventory.schedule.interval_hours} onChange={(event) => updateSchedule({ interval_hours: Number(event.target.value) })} /></label><label>Keep latest<input type="number" min={1} max={1024} value={inventory.schedule.retention_count} onChange={(event) => updateSchedule({ retention_count: Number(event.target.value) })} /></label><label>Name prefix<input value={inventory.schedule.prefix} maxLength={32} onChange={(event) => updateSchedule({ prefix: event.target.value.toLowerCase() })} /></label></div>
    <dl className="review-list"><div><dt>Next run</dt><dd>{inventory.schedule.next_run_at ?? "Disabled"}</dd></div><div><dt>Last run</dt><dd>{inventory.schedule.last_run_at ?? "Never"}</dd></div><div><dt>History source</dt><dd>{inventory.source.replaceAll("_", " ")}</dd></div></dl>
    <button className="button button-secondary" type="button" disabled={scheduleSaving} onClick={() => void saveSchedule()}>{scheduleSaving ? "Saving…" : "Save snapshot schedule"}</button>
  </section>;
}
