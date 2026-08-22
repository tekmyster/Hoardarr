import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type {
  LogicalStorageDocument,
  OperationDocument,
  StorageRedundancyPlan,
  StorageRedundancySettings,
} from "../types";
import { Card, Notice, StatusBadge } from "./ui";
import { ControllerRedundancyDetail } from "./ControllerRedundancyDetail";

type RedundancyAction = "add" | "remove" | "replace" | "configure";
type MultipathPolicy = "recommended" | "failover" | "multibus" | "group_by_prio";

function stateLabel(storage: LogicalStorageDocument): string {
  if (storage.topology_state === "fully_redundant") return "Fully redundant";
  if (storage.topology_state === "reduced_redundancy") return "Reduced redundancy";
  if (storage.topology_state === "failed_over") return "Failed over";
  if (storage.topology_state === "no_path") return "No path";
  return storage.paths.length > 1 ? "Fully redundant" : "Single path";
}

function stateDetail(storage: LogicalStorageDocument): string {
  if (storage.paths.length > 1) {
    return `${storage.paths.length} controller paths reach the same storage.`;
  }
  return "Storage is online through one controller connection.";
}

export function StorageRedundancyPanel({ initialManagedId }: { initialManagedId?: string | null } = {}) {
  const [items, setItems] = useState<LogicalStorageDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LogicalStorageDocument | null>(null);
  const [action, setAction] = useState<RedundancyAction>("add");
  const [candidatePath, setCandidatePath] = useState("");
  const [policy, setPolicy] = useState<MultipathPolicy>("recommended");
  const [plan, setPlan] = useState<StorageRedundancyPlan | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [managedId, setManagedId] = useState<string | null>(initialManagedId ?? null);
  const [settingsOverride, setSettingsOverride] = useState<StorageRedundancySettings | null>(null);

  const refresh = useCallback(async () => {
    try {
      const nextItems = await api.logicalStorage();
      setItems(nextItems);
      setSelected((current) => current
        ? nextItems.find((item) => item.id === current.id) ?? null
        : null);
      setError(null);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Controller connections could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (initialManagedId) setManagedId(initialManagedId);
  }, [initialManagedId]);

  useEffect(() => {
    if (!managedId) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [managedId, refresh]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const poll = async () => {
      try {
        const next = await api.operation(operation.id);
        if (stopped) return;
        setOperation(next);
        if (next.status === "succeeded") await refresh();
      } catch (requestError) {
        if (!stopped) {
          setError(requestError instanceof Error ? requestError.message : "Status could not be refreshed.");
        }
      }
    };
    const timer = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [operation?.id, operation?.status, refresh]);

  const close = () => {
    setSelected(null);
    setPlan(null);
    setOperation(null);
    setError(null);
    setCandidatePath("");
    setPolicy("recommended");
    setSettingsOverride(null);
  };

  const open = (storage: LogicalStorageDocument, nextAction: RedundancyAction, settings?: StorageRedundancySettings) => {
    setSelected(storage);
    setAction(nextAction);
    setCandidatePath(nextAction === "add" ? "" : storage.paths[0]?.stable_path_identity ?? "");
    setPlan(null);
    setOperation(null);
    setError(null);
    setSettingsOverride(settings ?? null);
  };

  const preview = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.previewStorageRedundancy({
        storage_entity_id: selected.id,
        action,
        ...(action === "remove" && candidatePath ? { path_identity: candidatePath } : {}),
        ...(action === "replace" && candidatePath
          ? { remove_path_identity: candidatePath }
          : {}),
        policy,
        ...(settingsOverride ? { settings: settingsOverride } : {}),
      });
      setPlan(result.plan);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The redundancy plan could not be created.");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.applyStorageRedundancy(plan, plan.plan_sha256));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "The redundancy change could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return <>
    <Card
      title="Controller connections"
      description="Advanced: add or replace a controller path without rebuilding storage or changing media paths."
    >
      {error && !selected && <Notice tone="danger" title="Controller details unavailable">{error}</Notice>}
      {loading ? <p>Loading controller connections…</p> : items.length === 0
        ? <div className="empty-state compact-empty">
            <h3>No managed logical storage yet</h3>
            <p>Controller redundancy becomes available after Hoardarr creates or imports eligible storage.</p>
          </div>
        : <div className="redundancy-storage-list">
            {items.map((storage) => <article className="redundancy-storage-row" key={storage.id}>
              <div>
                <strong>{storage.name}</strong>
                <span>{storage.mountpoint} · {humanCapacity(storage.capacity_bytes)}</span>
                <small>{stateDetail(storage)}</small>
                {(storage.available_paths?.length ?? 0) > 0 && <small className="redundancy-path-detected">Hoardarr found another verified connection to this storage.</small>}
              </div>
              <StatusBadge status={stateLabel(storage)} />
              <button type="button" className="button button-secondary button-compact" onClick={() => setManagedId(storage.id)}>Manage</button>
              <details className="drive-action-menu">
                <summary aria-label={`Controller settings for ${storage.name}`} title="Controller settings">
                  <span aria-hidden="true">⚙</span>
                </summary>
                <div className="drive-action-dropdown" role="menu">
                  <button type="button" role="menuitem" onClick={() => open(storage, "add")}>
                    <strong>Add redundant path</strong>
                    <small>Use another verified connection to this same storage.</small>
                  </button>
                  {storage.paths.length > 1 && <button type="button" role="menuitem" onClick={() => open(storage, "remove")}>
                    <strong>Remove redundant path</strong>
                    <small>Keep the storage, filesystem, mount, and shares unchanged.</small>
                  </button>}
                  {storage.paths.length > 1 && <button type="button" role="menuitem" onClick={() => open(storage, "replace")}>
                    <strong>Replace controller path</strong>
                    <small>Add the verified replacement before removing the old path.</small>
                  </button>}
                </div>
              </details>
            </article>)}
          </div>}
      </Card>

    {managedId && (() => {
      const managed = items.find((item) => item.id === managedId);
      return managed ? <ControllerRedundancyDetail storage={managed} onAction={(nextAction, settings) => open(managed, nextAction, settings)} /> : null;
    })()}

    {selected && <div className="wizard-overlay" role="presentation">
      <section className="wizard-dialog" role="dialog" aria-modal="true" aria-labelledby="redundancy-title">
        <header className="wizard-header">
          <div><small>ADVANCED STORAGE</small><h2 id="redundancy-title">{action === "add" ? "Add storage redundancy" : action === "replace" ? "Replace a controller path" : action === "configure" ? "Apply controller settings" : "Remove a storage path"}</h2></div>
          <button type="button" className="wizard-close" aria-label="Close controller settings" onClick={close}>×</button>
        </header>
        <div className="wizard-scroll">
          {error && <Notice tone="danger" title="This change needs attention">{error}</Notice>}
          <Card title={selected.name} description={`${selected.mountpoint} · ${selected.filesystem_uuid ?? "Filesystem UUID not reported"}`}>
            {!plan && !operation && <>
              <Notice tone={action === "remove" ? "warning" : "info"} title={action === "add" ? "Your existing storage is preserved" : action === "replace" ? "The replacement is added first" : action === "configure" ? "Storage remains online" : "Protection will be reduced"}>
                {action === "add"
                  ? "Hoardarr will verify that the new controller reaches this exact storage. It will not format, repartition, or copy your data."
                  : action === "replace"
                    ? "Hoardarr will verify the new path, add it to the existing logical storage, then remove the selected path. The mount stays online."
                    : action === "configure"
                      ? "Hoardarr will validate and reload the multipath configuration without recreating the filesystem or mount."
                      : "The filesystem, data, storage name, media paths, and shares remain unchanged."}
              </Notice>
              {action !== "add" && action !== "configure" && <label>{action === "replace" ? "Path being replaced" : "Path to remove"}<select value={candidatePath} onChange={(event) => setCandidatePath(event.target.value)}>{selected.paths.map((path) => <option key={path.id} value={path.stable_path_identity}>{path.controller?.model ?? path.controller?.stable_identity ?? "Controller"} · {path.kernel_path}</option>)}</select></label>}
              {action !== "configure" && <label>Redundancy settings<select value={policy} onChange={(event) => setPolicy(event.target.value as MultipathPolicy)}><option value="recommended">Use storage-recommended settings</option><option value="failover">Prefer failover</option><option value="group_by_prio">Honor optimized path groups</option><option value="multibus">Use all active paths</option></select></label>}
              {(action === "add" || action === "replace") && <div className="redundancy-detected-paths"><h3>Matching controller connection</h3>{selected.available_paths?.length ? selected.available_paths.map((path) => <article key={path.stable_path_identity}><div><strong>{path.controller_identity}</strong><span>{path.protocol} · <code>{path.kernel_path}</code></span></div><StatusBadge status="verified" /></article>) : <Notice tone="warning" title="No verified second path detected">Run storage discovery after connecting the additional controller path.</Notice>}</div>}
              <details><summary>Expert details</summary>
                <dl className="review-list"><div><dt>Logical storage ID</dt><dd><code>{selected.stable_identity}</code></dd></div><div><dt>Current device</dt><dd><code>{selected.presentation_device}</code></dd></div><div><dt>Current paths</dt><dd>{selected.paths.length}</dd></div></dl>
                <div className="table-scroll"><table className="data-table"><thead><tr><th>Controller</th><th>Protocol</th><th>Path</th><th>Access</th><th>State</th></tr></thead><tbody>{selected.paths.map((path) => <tr key={path.id}><td>{path.controller?.model ?? path.controller?.stable_identity ?? "Not reported"}</td><td>{path.protocol || "Not reported"}</td><td><code>{path.kernel_path}</code></td><td>{path.optimized === true ? "Optimized" : path.optimized === false ? "Standby / non-optimized" : "Not reported"}</td><td><StatusBadge status={path.active ? path.state || "active" : path.state || "unavailable"} /></td></tr>)}</tbody></table></div>
              </details>
            </>}
            {plan && !operation && <>
              <Notice tone="success" title="No storage contents will be rebuilt">The filesystem UUID, mount, Hoardarr storage ID, shares, ACLs, application paths, and telemetry history stay attached to the same storage object.</Notice>
              <div className="redundancy-change-summary"><section><h3>Will change</h3><ul><li>Storage access uses the reviewed redundant device.</li><li>Multipath/provider configuration is updated.</li><li>The reviewed controller-path count changes.</li></ul></section><section><h3>Will not change</h3><ul><li>Filesystem or stored data</li><li>Mount and media paths</li><li>SMB/NFS shares or ARR paths</li><li>Hoardarr storage identity and history</li></ul></section></div>
              <dl className="review-list">
                <div><dt>Paths</dt><dd>{plan.before.path_ids.length} → {plan.after.path_ids.length}</dd></div>
                <div><dt>Filesystem UUID</dt><dd><code>{plan.before.filesystem_uuid ?? "Not reported"}</code></dd></div>
                <div><dt>Mount path</dt><dd><code>{plan.before.mountpoint}</code></dd></div>
                <div><dt>Device access</dt><dd><code>{plan.before.presentation_device}</code> → <code>{plan.after.presentation_device}</code></dd></div>
                <div><dt>Availability</dt><dd>{plan.transition?.message ?? "The storage contents and application paths remain unchanged."}</dd></div>
                <div><dt>Managed access</dt><dd>{plan.managed_access_services?.length ? plan.managed_access_services.map((service) => `${service.protocol.toUpperCase()} ${service.name}`).join(", ") : "No Hoardarr-managed SMB/NFS services"}</dd></div>
                <div><dt>Plan SHA-256</dt><dd><code>{plan.plan_sha256}</code></dd></div>
              </dl>
            </>}
            {operation && <>
              <div className="storage-operation-heading"><StatusBadge status={operation.status.replace("_", " ")} /></div>
              {operation.status === "succeeded" && <Notice tone="success" title="Controller paths updated">Storage remains mounted at <code>{selected.mountpoint}</code>.</Notice>}
              {operation.error && <Notice tone="danger" title="Redundancy change failed">{operation.error.message ?? operation.error.detail ?? "The operation did not finish."}</Notice>}
            </>}
          </Card>
        </div>
        <footer className="wizard-footer">
          <button type="button" className="button button-secondary" onClick={close} disabled={busy || operation?.status === "running"}>{operation && !["queued", "running"].includes(operation.status) ? "Close" : "Cancel"}</button>
          {!operation && (!plan
            ? <button type="button" className="button button-primary" onClick={() => void preview()} disabled={busy}>{busy ? "Checking…" : "Review change"}</button>
            : <button type="button" className="button button-primary" onClick={() => void apply()} disabled={busy}>{busy ? "Starting…" : action === "add" ? "Add redundancy" : action === "replace" ? "Replace path" : action === "configure" ? "Apply settings" : "Remove path"}</button>)}
        </footer>
      </section>
    </div>}
  </>;
}
