import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { PhysicalDiskDocument, StorageGroupDocument } from "../types";
import { Card, Notice, StatusBadge } from "./ui";

type Purpose = "media" | "downloads" | "archive" | "backup" | "general";

export function StorageGroupsPanel() {
  const [groups, setGroups] = useState<StorageGroupDocument[]>([]);
  const [disks, setDisks] = useState<PhysicalDiskDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [namespacePath, setNamespacePath] = useState("/srv/hoardarr/media");
  const [purpose, setPurpose] = useState<Purpose>("media");
  const [selectedDisks, setSelectedDisks] = useState<Record<string, string>>(Object.create(null));

  const load = async (signal?: AbortSignal) => {
    const [nextGroups, nextDisks] = await Promise.all([
      api.storageGroups(signal),
      api.registeredDisks(signal),
    ]);
    setGroups(nextGroups);
    setDisks(nextDisks);
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

  const assignedDiskIds = useMemo(
    () => new Set(groups.flatMap((group) => group.backends.map((backend) => backend.physical_disk_id).filter(Boolean))),
    [groups],
  );
  const availableDisks = disks.filter((disk) => !assignedDiskIds.has(disk.id));

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.createStorageGroup({ name, namespace_path: namespacePath, purpose });
      setName("");
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
      await api.assignStorageGroupDisk(groupId, diskId);
      setSelectedDisks((current) => ({ ...current, [groupId]: "" }));
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The disk could not be assigned.");
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

  return <Card
    title="Storage Groups"
    description="Keep one stable media path while disks are added, preferred for new files, drained, or retired."
  >
    {error && <Notice tone="danger" title="Storage Group needs attention">{error}</Notice>}
    <details className="storage-group-create">
      <summary className="button button-secondary">Create Storage Group</summary>
      <div className="form-grid storage-group-form">
        <label>Name<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} placeholder="Media" /></label>
        <label>Stable media path<input value={namespacePath} maxLength={4096} onChange={(event) => setNamespacePath(event.target.value)} /></label>
        <label>Used for<select value={purpose} onChange={(event) => setPurpose(event.target.value as Purpose)}><option value="media">Movies, TV, and music</option><option value="downloads">Downloads and temporary work</option><option value="archive">Archive</option><option value="backup">Backup</option><option value="general">General files</option></select></label>
        <button type="button" className="button button-primary" disabled={busy || !name.trim()} onClick={() => void create()}>Create group</button>
      </div>
    </details>
    {loading ? <p role="status">Loading Storage Groups…</p> : groups.length === 0 ? <div className="empty-state compact-empty"><h3>No Storage Groups yet</h3><p>Create a stable media location, then assign a registered disk or logical storage backend.</p></div> : <div className="storage-group-list">
      {groups.map((group) => <section className="storage-group" key={group.id} aria-labelledby={`storage-group-${group.id}`}>
        <header><div><h3 id={`storage-group-${group.id}`}>{group.name}</h3><code>{group.namespace_path}</code></div><StatusBadge status={group.state} /></header>
        <p>{group.purpose === "media" ? "Media libraries" : group.purpose} · New-write placement follows the preferred healthy backend.</p>
        {group.backends.length === 0 ? <p className="muted">No backends assigned.</p> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Backend identity</th><th>Role</th><th>Lifecycle</th><th>Placement</th></tr></thead><tbody>{group.backends.map((backend) => <tr key={backend.id}><td><code>{backend.stable_identity}</code></td><td>{backend.role}</td><td><StatusBadge status={backend.lifecycle_state.replace("_", " ")} /></td><td>{backend.lifecycle_state === "assigned" ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void transition(group.id, backend.id, "active")}>Activate</button> : backend.lifecycle_state === "active" ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void transition(group.id, backend.id, "preferred_write")}>Prefer new files here</button> : backend.lifecycle_state === "preferred_write" ? "Preferred for new files" : "Managed by lifecycle operation"}</td></tr>)}</tbody></table></div>}
        <div className="storage-group-assign"><label>Add a registered disk<select aria-label={`Disk to add to ${group.name}`} value={selectedDisks[group.id] ?? ""} onChange={(event) => setSelectedDisks((current) => ({ ...current, [group.id]: event.target.value }))}><option value="">Choose a disk</option>{availableDisks.map((disk) => <option key={disk.id} value={disk.id}>{disk.model || "Disk"} · {disk.serial || disk.wwn || disk.stable_identity}</option>)}</select></label><button type="button" className="button button-secondary" disabled={busy || !selectedDisks[group.id]} onClick={() => void assign(group.id)}>Assign</button></div>
        {group.events.length > 0 && <details><summary>Recent lifecycle activity</summary><ol className="event-list">{group.events.slice(0, 8).map((event) => <li key={event.id}><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time> {event.event_type.replaceAll("_", " ")}</li>)}</ol></details>}
      </section>)}
    </div>}
    <Notice tone="info" title="Drain and retire safely">Those actions become available only through a durable move-and-verify operation; Hoardarr does not allow skipping directly to a retired state.</Notice>
  </Card>;
}
