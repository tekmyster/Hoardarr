import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type { OperationDocument, StorageGroupDocument, TierTransferPlan, TierTransferSummary } from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

type BackendChoice = {
  id: string;
  label: string;
  path: string;
  role: string;
};

function backendChoices(groups: StorageGroupDocument[], roles: ReadonlySet<string>): BackendChoice[] {
  return groups.flatMap((group) => group.backends
    .filter((backend) => roles.has(backend.role) && backend.namespace_path && !["retired", "reuse_ready"].includes(backend.lifecycle_state))
    .map((backend) => ({
      id: backend.id,
      label: `${group.name} · ${backend.role}`,
      path: backend.namespace_path!,
      role: backend.role,
    })));
}

export function DownloadTierPanel() {
  const [groups, setGroups] = useState<StorageGroupDocument[]>([]);
  const [summary, setSummary] = useState<TierTransferSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workload, setWorkload] = useState<"torrent" | "usenet">("torrent");
  const [source, setSource] = useState("");
  const [destination, setDestination] = useState("");
  const [method, setMethod] = useState<"auto" | "copy" | "move" | "hardlink">("auto");
  const [retainUntil, setRetainUntil] = useState<"seeding_complete" | "manual" | "never">("seeding_complete");
  const [preview, setPreview] = useState<{ plan: TierTransferPlan; plan_sha256: string } | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);

  const landing = useMemo(() => backendChoices(groups, new Set(["cache", "landing"])), [groups]);
  const media = useMemo(() => backendChoices(groups, new Set(["data"])), [groups]);

  useEffect(() => {
    let active = true;
    void Promise.all([api.storageGroups(), api.tierTransferSummary()]).then(([items, nextSummary]) => {
      if (!active) return;
      setGroups(items);
      setSummary(nextSummary);
      const landingPath = backendChoices(items, new Set(["cache", "landing"]))[0]?.path;
      const mediaPath = backendChoices(items, new Set(["data"]))[0]?.path;
      if (landingPath) setSource(`${landingPath}/completed/example.mkv`);
      if (mediaPath) setDestination(`${mediaPath}/Movies/example.mkv`);
    }).catch((caught: unknown) => {
      if (active) setError(caught instanceof Error ? caught.message : "Download tiers could not be loaded.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!operation || ["queued", "running"].includes(operation.status)) return;
    let active = true;
    void api.tierTransferSummary().then((value) => { if (active) setSummary(value); }).catch(() => undefined);
    return () => { active = false; };
  }, [operation?.id, operation?.status]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api.operation(operation.id).then((next) => { if (active) setOperation(next); }).catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "Transfer progress is unavailable.");
      });
    }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [operation?.id, operation?.status]);

  function resetReview(): void {
    setPreview(null);
    setOperation(null);
    setError(null);
  }

  async function review(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.previewTierTransfer({
        workload,
        source,
        destination,
        method,
        retain_until: workload === "torrent" ? retainUntil : "import_complete",
        cleanup: true,
        completed_steps: workload === "torrent" ? ["download_complete"] : ["download", "repair", "unpack", "verify"],
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The transfer could not be reviewed.");
    } finally {
      setBusy(false);
    }
  }

  async function apply(): Promise<void> {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.applyTierTransfer(preview.plan, preview.plan_sha256));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The transfer could not be started.");
    } finally {
      setBusy(false);
    }
  }

  async function cleanup(): Promise<void> {
    if (!operation) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.cleanupTierTransfer(operation.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Retained download cleanup could not be started.");
    } finally {
      setBusy(false);
    }
  }

  const retained = operation?.status === "succeeded" && operation.result?.state === "retained";
  return <Card title="Download & landing tier" description="Move completed downloads from fast working storage into the media library without pretending cross-filesystem copies are hardlinks.">
    {loading ? <Spinner label="Loading configured download storage…" /> : !landing.length ? <div className="empty-state compact-empty"><h3>No download SSD or NVMe is configured</h3><p>Select an unassigned fast drive and choose <strong>Use for downloads/cache</strong>. Hoardarr will keep this panel empty until a real landing backend exists.</p></div> : !media.length ? <Notice tone="warning" title="No media destination is configured">Create or import a data backend before moving completed downloads.</Notice> : <>
      {error && <Notice tone="danger" title="Download transfer needs attention">{error}</Notice>}
      {summary && <section aria-label="Download tier status">
        <dl className="review-grid">
          <div><dt>Queued migrations</dt><dd>{summary.queue.queued_count} · {humanCapacity(summary.queue.queued_bytes)}</dd></div>
          <div><dt>Running</dt><dd>{summary.queue.running_count}{summary.queue.running_count ? ` · up to ${humanCapacity(summary.queue.running_planned_bytes)}` : ""}</dd></div>
          <div><dt>Queued drain estimate</dt><dd>{summary.queue.estimated_queued_seconds === null ? "Not reported" : summary.queue.estimated_queued_seconds === 0 ? "Nothing queued" : `About ${Math.max(1, Math.ceil(summary.queue.estimated_queued_seconds / 60))} min`}</dd></div>
          <div><dt>Retained for seeding</dt><dd>{summary.queue.retained_for_seeding_count} · {humanCapacity(summary.queue.retained_for_seeding_bytes)}</dd></div>
          <div><dt>Transfer failures</dt><dd>{summary.queue.failed_count}</dd></div>
        </dl>
        <p className="muted">{summary.queue.estimate_methodology}{summary.queue.observed_bytes_per_second !== null ? ` Observed rate: ${humanCapacity(summary.queue.observed_bytes_per_second)}/s from ${summary.queue.rate_sample_count} completed transfers.` : ""}</p>
        <div className="tier-capacity-list">{summary.tiers.map((tier) => <article key={tier.backend_id} className="compact-panel"><strong>{tier.storage_group_name} · {tier.role}</strong>{tier.quality === "available" && tier.total_bytes !== null && tier.used_bytes !== null && tier.free_bytes !== null ? <p>{humanCapacity(tier.used_bytes)} used · {humanCapacity(tier.free_bytes)} free · {humanCapacity(tier.total_bytes)} total</p> : <p>Capacity {tier.quality === "not_reported" ? "Not reported" : "temporarily unavailable"}</p>}<small>{tier.path ?? "Path Not reported"}</small></article>)}</div>
      </section>}
      {!preview && !operation && <div className="form-grid two-columns">
        <label>Workload<select value={workload} onChange={(event) => { setWorkload(event.target.value as "torrent" | "usenet"); resetReview(); }}><option value="torrent">Torrent — keep source while seeding</option><option value="usenet">Usenet — move after repair and unpack</option></select></label>
        <label>Download storage<select value={landing.find((item) => source.startsWith(item.path))?.id ?? ""} onChange={(event) => { const selected = landing.find((item) => item.id === event.target.value); if (selected) setSource(`${selected.path}/completed/example.mkv`); resetReview(); }}>{landing.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.path}</option>)}</select></label>
        <label>Completed file<input value={source} onChange={(event) => { setSource(event.target.value); resetReview(); }} /></label>
        <label>Media destination<select value={media.find((item) => destination.startsWith(item.path))?.id ?? ""} onChange={(event) => { const selected = media.find((item) => item.id === event.target.value); if (selected) setDestination(`${selected.path}/Movies/example.mkv`); resetReview(); }}>{media.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.path}</option>)}</select></label>
        <label>Library file<input value={destination} onChange={(event) => { setDestination(event.target.value); resetReview(); }} /></label>
        {workload === "torrent" && <label>Keep download until<select value={retainUntil} onChange={(event) => { setRetainUntil(event.target.value as typeof retainUntil); resetReview(); }}><option value="seeding_complete">Seeding completes — Recommended</option><option value="manual">I clean it up manually</option><option value="never">Do not retain after verified import</option></select></label>}
        <details className="advanced-panel"><summary>Advanced transfer method</summary><label>Method<select value={method} onChange={(event) => { setMethod(event.target.value as typeof method); resetReview(); }}><option value="auto">Automatic — Recommended</option><option value="copy">Copy</option><option value="move">Move</option><option value="hardlink">Hardlink only</option></select></label></details>
        <div className="page-actions"><button type="button" className="button button-primary" disabled={busy || !source || !destination} onClick={() => void review()}>{busy ? "Checking…" : "Review transfer"}</button></div>
      </div>}
      {preview && !operation && <section aria-live="polite"><Notice tone="info" title="Exact transfer plan">Hoardarr will <strong>{preview.plan.method}</strong> this {preview.plan.workload} file. {preview.plan.same_filesystem ? "The paths share one filesystem." : "The paths use different filesystems; a hardlink is not possible."}</Notice><dl className="review-grid"><div><dt>Source</dt><dd><code>{preview.plan.source}</code></dd></div><div><dt>Destination</dt><dd><code>{preview.plan.destination}</code></dd></div><div><dt>Size</dt><dd>{humanCapacity(preview.plan.required_bytes)}</dd></div><div><dt>Retention</dt><dd>{preview.plan.retain_until.replaceAll("_", " ")}</dd></div></dl><div className="button-row"><button type="button" className="button button-primary" disabled={busy} onClick={() => void apply()}>{busy ? "Starting…" : "Start durable transfer"}</button><button type="button" className="button button-secondary" onClick={resetReview}>Change</button></div></section>}
      {operation && <section aria-live="polite"><div className="storage-operation-heading"><StatusBadge status={operation.status.replaceAll("_", " ")} /><strong>{operation.kind === "storage.transfer.cleanup" ? "Post-seeding cleanup" : "Media transfer"}</strong></div>{["queued", "running"].includes(operation.status) && <p>Real progress and completion are recorded in Activity. This panel does not invent a percentage.</p>}{operation.error && <Notice tone="danger" title="Transfer failed">{operation.error.message ?? operation.error.detail ?? "The worker could not complete the transfer."}</Notice>}{retained && <><Notice tone="success" title="Imported and retained for seeding">The verified media copy is available. Remove the retained download only after the download client reports seeding complete.</Notice><button type="button" className="button button-primary" disabled={busy} onClick={() => void cleanup()}>{busy ? "Starting cleanup…" : "Seeding complete — clean up source"}</button></>}{operation.status === "succeeded" && !retained && <Notice tone="success" title="Transfer completed">The worker verified the destination and applied the reviewed source-retention policy.</Notice>}<button type="button" className="button button-secondary" disabled={["queued", "running"].includes(operation.status)} onClick={resetReview}>Start another</button></section>}
    </>}
  </Card>;
}
