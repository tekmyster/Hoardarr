import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { TopologyExpectationStatus } from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

function formatDate(value: string | null): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not reported" : date.toLocaleString();
}

export function TopologyExpectationPanel({ snapshotId }: { snapshotId: string | null }) {
  const [document, setDocument] = useState<TopologyExpectationStatus | null>(null);
  const [name, setName] = useState("Expected storage topology");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(Boolean(snapshotId));
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!snapshotId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setDocument(await api.topologyExpectation());
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Expected topology could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [snapshotId]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!snapshotId || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveTopologyExpectation(snapshotId, name.trim());
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Expected topology could not be saved.");
    } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!document?.expectation) return;
    setBusy(true);
    setError(null);
    try {
      await api.removeTopologyExpectation(document.expectation.id);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Expected topology could not be removed.");
    } finally { setBusy(false); }
  };

  const active = document?.active_drifts ?? [];
  return <Card title="Expected topology" description="Compare future scans with an operator-approved controller, shelf, path, bay, and link-rate baseline.">
    {error && <Notice tone="danger" title="Topology monitoring unavailable">{error}</Notice>}
    {!snapshotId ? <div className="empty-state compact-empty"><h3>No hardware scan is available</h3><p>Run a read-only scan before saving an expected topology.</p></div>
      : loading ? <Spinner label="Loading expected topology" />
      : !document?.expectation ? <div className="topology-expectation-empty">
        <Notice tone="info" title="No expected topology has been saved">Hoardarr is showing live discovery only. Save the current scan when cabling, shelves, bays, and link rates are in their intended state.</Notice>
        <label>Baseline name<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} /></label>
        <button type="button" className="button button-primary" disabled={busy || !name.trim()} onClick={() => void save()}>{busy ? "Saving…" : "Use current scan as expected"}</button>
      </div>
      : <div className="topology-expectation-content">
        <div className="topology-expectation-summary">
          <div><span>Baseline</span><strong>{document.expectation.name}</strong><small>Saved {formatDate(document.expectation.created_at)}</small></div>
          <div><span>Current state</span><StatusBadge status={active.length ? "warning" : "healthy"} /><small>{active.length ? `${active.length} difference${active.length === 1 ? " needs" : "s need"} review` : "Latest scan matches the baseline"}</small></div>
          <div><span>Tracked facts</span><strong>{document.expectation.expected.nodes.length}</strong><small>Controllers, shelves, paths, drives, bays, and reported rates</small></div>
        </div>
        {active.length > 0 ? <div className="topology-drift-list" aria-live="polite">{active.map((event) => <article key={event.id} className={`topology-drift-${event.severity}`}>
          <header><StatusBadge status={event.severity} /><strong>{event.kind.replaceAll("_", " ")}</strong><time dateTime={event.first_seen_at}>{formatDate(event.first_seen_at)}</time></header>
          <p>{event.message}</p><code>{event.entity_id}</code>
        </article>)}</div> : <Notice tone="success" title="Topology matches">No missing controller, shelf, path, or drive, moved bay, or degraded reported link rate was found in the latest scan.</Notice>}
        {document.recent_events.some((event) => event.state === "resolved") && <details><summary>Resolved topology changes</summary><div className="topology-drift-list">{document.recent_events.filter((event) => event.state === "resolved").map((event) => <article key={event.id}><header><StatusBadge status="healthy" /><strong>{event.kind.replaceAll("_", " ")}</strong><time dateTime={event.resolved_at ?? event.last_seen_at}>{formatDate(event.resolved_at)}</time></header><p>{event.message}</p></article>)}</div></details>}
        <div className="button-row"><button type="button" className="button button-secondary" disabled={busy} onClick={() => void save()}>Replace baseline with current scan</button><button type="button" className="button button-danger" disabled={busy} onClick={() => void remove()}>Stop topology monitoring</button></div>
      </div>}
  </Card>;
}
