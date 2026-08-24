import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { OperationDocument, OperationEvent, StorageOperationProgress } from "../types";
import { StorageProgressDetails } from "./StorageProgressDetails";
import { Card, Notice, StatusBadge } from "./ui";

const REFRESH_MS = 2_000;
const STORAGE_PROGRESS_KINDS = new Set([
  "storage.apply",
  "storage.maintenance",
  "storage.foreign.inspect",
  "storage.snapraid.replace",
]);

function formatDate(value: string | undefined): string {
  if (!value) return "Not reported";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Not reported" : parsed.toLocaleString();
}

export function ActivityPage() {
  const [operations, setOperations] = useState<OperationDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [progress, setProgress] = useState<StorageOperationProgress | null>(null);
  const [events, setEvents] = useState<OperationEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => operations.find((operation) => operation.id === selectedId) ?? null,
    [operations, selectedId],
  );
  const smartResults = useMemo(() => {
    const value = selected?.result?.action_results;
    return Array.isArray(value)
      ? value.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item) && typeof (item as Record<string, unknown>).action_id === "string" && String((item as Record<string, unknown>).action_id).includes("smart"))
      : [];
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function refresh(): Promise<void> {
      try {
        const found = await api.listOperations();
        if (cancelled) return;
        setOperations(found);
        setSelectedId((current) => current ?? found.find((item) => item.kind === "storage.apply")?.id ?? found[0]?.id ?? null);
        setError(null);
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Activity could not be loaded.");
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void refresh(), REFRESH_MS);
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!selected) {
      setProgress(null);
      setEvents([]);
      return;
    }
    const operation = selected;
    let cancelled = false;
    let timer: number | undefined;
    async function refreshDetails(): Promise<void> {
      try {
        const [foundEvents, foundProgress] = await Promise.all([
          api.operationEvents(operation.id),
          STORAGE_PROGRESS_KINDS.has(operation.kind) ? api.storageOperationProgress(operation.id).catch(() => null) : Promise.resolve(null),
        ]);
        if (!cancelled) {
          setEvents(foundEvents);
          setProgress(foundProgress);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Operation details could not be loaded.");
      } finally {
        if (!cancelled && ["queued", "running"].includes(operation.status)) {
          timer = window.setTimeout(() => void refreshDetails(), REFRESH_MS);
        }
      }
    }
    void refreshDetails();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [selected?.id, selected?.status]);

  return <div className="activity-page">
    {error && <Notice tone="danger" title="Activity request failed">{error}</Notice>}
    <Card title="Backend activity" description="These are live operations recorded by the Hoardarr API. A completed wizard page is not treated as a completed operation.">
      {!operations.length ? <div className="empty-state compact-empty"><h3>No operations recorded</h3><p>There is no storage build, hardware scan, or application task currently recorded.</p></div> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Task</th><th>Status</th><th>Started</th><th>Updated</th><th>Result</th></tr></thead><tbody>{operations.map((operation) => <tr key={operation.id} className={operation.id === selectedId ? "selected-row" : ""} onClick={() => setSelectedId(operation.id)}>
        <td><button type="button" className="activity-select" onClick={() => setSelectedId(operation.id)}><strong>{operation.kind}</strong><code>{operation.id}</code></button></td>
        <td><StatusBadge status={operation.status.replace("_", " ")} /></td>
        <td>{formatDate(operation.created_at)}</td>
        <td>{formatDate(operation.updated_at)}</td>
        <td>{operation.error?.detail ?? operation.error?.message ?? (operation.status === "succeeded" ? "Completed" : "—")}</td>
      </tr>)}</tbody></table></div>}
    </Card>
    {selected && <Card title="Selected operation" description={selected.id}>
      <div className="storage-operation-heading"><StatusBadge status={selected.status.replace("_", " ")} /><strong>{progress?.percent ?? (selected.status === "succeeded" ? 100 : 0)}%</strong></div>
      {STORAGE_PROGRESS_KINDS.has(selected.kind) && <><div className="operation-progress-track" role="progressbar" aria-label="Selected storage operation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}><span style={{ width: `${progress?.percent ?? (selected.status === "succeeded" ? 100 : 0)}%` }} /></div><StorageProgressDetails progress={progress} /></>}
      {progress?.notices.map((notice) => <Notice key={`${notice.action_id ?? notice.code}:${notice.device_id ?? "drive"}`} tone="warning" title="SMART self-test skipped">{notice.message}</Notice>)}
      {smartResults.length > 0 && <div className="table-scroll" aria-label="SMART self-test history"><h3>SMART self-test result</h3><table className="data-table"><thead><tr><th>Drive</th><th>Test</th><th>Result</th><th>Detail</th></tr></thead><tbody>{smartResults.map((result) => <tr key={String(result.action_id)}><td><code>{String(result.device_id ?? "Not reported")}</code></td><td>{String(result.action_id).includes("extended") ? "Long / extended" : "Short"}</td><td><StatusBadge status={String(result.outcome ?? "not reported").replace("_", " ")} /></td><td>{String(result.message ?? result.code ?? "Not reported")}</td></tr>)}</tbody></table></div>}
      {selected.error && <Notice tone="danger" title={selected.error.code ?? "Operation failed"}>{selected.error.detail ?? selected.error.message ?? "The operation needs attention."}</Notice>}
      {events.length ? <ol className="activity-event-list">{events.map((event) => <li key={event.sequence}><time>{formatDate(event.created_at)}</time><strong>{event.type}</strong><span>{event.message}</span></li>)}</ol> : <p>No events have been recorded for this operation.</p>}
    </Card>}
  </div>;
}
