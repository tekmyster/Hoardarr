import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, demoMode } from "../api/client";
import type { WebhookDeliveryDocument, WebhookEndpointDocument } from "../types";
import { Card, Field, Notice, Spinner } from "./ui";

const DEFAULT_EVENTS = ["alert.opened", "alert.cleared", "test.delivery"];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The webhook request could not be completed.";
}

function eventLabel(value: string): string {
  return value.replaceAll(".", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function dateLabel(value: string | null): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

export function WebhooksPanel() {
  const [endpoints, setEndpoints] = useState<WebhookEndpointDocument[]>([]);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [deliveries, setDeliveries] = useState<Record<string, WebhookDeliveryDocument[]>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>(DEFAULT_EVENTS);
  const [allowLocalhost, setAllowLocalhost] = useState(false);
  const [verifyTls, setVerifyTls] = useState(true);
  const [rotatingEndpoint, setRotatingEndpoint] = useState<string | null>(null);
  const [replacementSecret, setReplacementSecret] = useState("");
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [nextEndpoints, nextTypes] = await Promise.all([
      api.webhookEndpoints(),
      api.webhookEventTypes(),
    ]);
    setEndpoints(nextEndpoints);
    setEventTypes(nextTypes);
    const nextDeliveries = await Promise.all(
      nextEndpoints.map(async (endpoint) => [endpoint.id, await api.webhookDeliveries(endpoint.id)] as const),
    );
    setDeliveries(Object.fromEntries(nextDeliveries));
  }, []);

  useEffect(() => {
    let active = true;
    refresh()
      .catch((caught) => { if (active) setError(errorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    const pending = Object.values(deliveries).flat().some((item) => ["queued", "delivering", "retrying"].includes(item.status));
    if (!pending) return;
    const timer = window.setTimeout(() => void refresh().catch((caught) => setError(errorMessage(caught))), 1_500);
    return () => window.clearTimeout(timer);
  }, [deliveries, refresh]);

  function toggleEvent(eventType: string): void {
    setSelectedEvents((current) => current.includes(eventType)
      ? current.filter((item) => item !== eventType)
      : [...current, eventType]);
  }

  async function createEndpoint(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy("create");
    setError(null);
    try {
      await api.createWebhookEndpoint({
        name: name.trim(),
        url: url.trim(),
        secret,
        event_types: selectedEvents,
        allow_localhost: allowLocalhost,
        verify_tls: verifyTls,
      });
      setName("");
      setUrl("");
      setSecret("");
      setSelectedEvents(DEFAULT_EVENTS);
      setShowForm(false);
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function toggleEndpoint(endpoint: WebhookEndpointDocument): Promise<void> {
    setBusy(`toggle:${endpoint.id}`);
    setError(null);
    try {
      const updated = await api.updateWebhookEndpoint(endpoint.id, {
        enabled: !endpoint.enabled,
        event_types: endpoint.event_types,
        verify_tls: endpoint.verify_tls,
      });
      setEndpoints((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function testEndpoint(endpointId: string): Promise<void> {
    setBusy(`test:${endpointId}`);
    setError(null);
    try {
      const delivery = await api.testWebhookEndpoint(endpointId);
      setDeliveries((current) => ({
        ...current,
        [endpointId]: [delivery, ...(current[endpointId] ?? []).filter((item) => item.id !== delivery.id)],
      }));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function rotateSecret(event: FormEvent<HTMLFormElement>, endpointId: string): Promise<void> {
    event.preventDefault();
    setBusy(`secret:${endpointId}`);
    setError(null);
    try {
      const updated = await api.rotateWebhookSecret(endpointId, replacementSecret);
      setEndpoints((items) => items.map((item) => item.id === updated.id ? updated : item));
      setReplacementSecret("");
      setRotatingEndpoint(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function removeEndpoint(endpointId: string): Promise<void> {
    setBusy(`remove:${endpointId}`);
    setError(null);
    try {
      await api.deleteWebhookEndpoint(endpointId);
      setEndpoints((items) => items.filter((item) => item.id !== endpointId));
      setDeliveries((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== endpointId)));
      setPendingRemoval(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Webhooks" description="Send signed alert events to Home Assistant, an automation service, or another trusted home-network endpoint.">
      <Notice tone="info" title="Durable and independent">
        Hoardarr queues a bounded, redacted event in its database. The worker signs and retries delivery without blocking storage jobs or requiring this page to stay open.
      </Notice>
      {error && <Notice tone="danger" title="Webhook request failed">{error}</Notice>}
      <div className="form-actions">
        <button className="button button-secondary" type="button" disabled={demoMode || busy !== null} onClick={() => setShowForm((shown) => !shown)}>
          {showForm ? "Cancel new webhook" : "Add webhook"}
        </button>
      </div>
      {showForm && <form className="api-key-create" onSubmit={(event) => void createEndpoint(event)}>
        <Field label="Name" hint="A recognizable destination such as Home Assistant."><input required maxLength={128} value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label="Webhook URL" hint="Use a trusted local HTTP service or HTTPS endpoint on an approved network."><input required type="url" value={url} placeholder="http://homeassistant.local:8123/api/webhook/..." onChange={(event) => setUrl(event.target.value)} /></Field>
        <Field label="Signing secret" hint="At least 32 characters. Hoardarr encrypts it and never displays it again."><input required minLength={32} maxLength={512} type="password" autoComplete="new-password" value={secret} onChange={(event) => setSecret(event.target.value)} /></Field>
        <fieldset><legend>Events</legend>{eventTypes.map((eventType) => <label className="check-row" key={eventType}><input type="checkbox" checked={selectedEvents.includes(eventType)} onChange={() => toggleEvent(eventType)} />{eventLabel(eventType)}</label>)}</fieldset>
        <label className="check-row"><input type="checkbox" checked={allowLocalhost} onChange={(event) => setAllowLocalhost(event.target.checked)} />This endpoint runs on the Hoardarr host</label>
        <label className="check-row"><input type="checkbox" checked={verifyTls} onChange={(event) => setVerifyTls(event.target.checked)} />Verify HTTPS certificate</label>
        <button className="button button-primary" type="submit" disabled={busy !== null || selectedEvents.length === 0}>{busy === "create" ? "Saving…" : "Save webhook"}</button>
      </form>}
      <div className="api-key-list" aria-live="polite">
        <h3>Destinations</h3>
        {loading ? <Spinner label="Loading webhooks" /> : endpoints.length === 0 ? <div className="empty-state compact-empty"><h3>No webhook destination</h3><p>Add one to route real alert lifecycle events. Nothing is sent until configured.</p></div> : endpoints.map((endpoint) => {
          const latest = deliveries[endpoint.id]?.[0];
          return <article className="api-key-row" key={endpoint.id}>
            <div><strong>{endpoint.name}</strong><span>{endpoint.url}</span><small>{endpoint.enabled ? "Enabled" : "Disabled"} · Status: {endpoint.status.replaceAll("_", " ")} · Last success: {dateLabel(endpoint.last_success_at)}</small><small>{endpoint.event_types.map(eventLabel).join(", ")}</small>{latest && <small>Latest delivery: {latest.status.replaceAll("_", " ")} · {latest.attempt_count} attempt{latest.attempt_count === 1 ? "" : "s"}{latest.response_status ? ` · HTTP ${latest.response_status}` : ""}</small>}{endpoint.last_error?.message && <small className="danger-text">{endpoint.last_error.message}</small>}</div>
            <div className="api-key-actions"><button className="button button-secondary" type="button" disabled={busy !== null || !endpoint.enabled || !endpoint.event_types.includes("test.delivery")} onClick={() => void testEndpoint(endpoint.id)}>{busy === `test:${endpoint.id}` ? "Queueing…" : "Send test"}</button><button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void toggleEndpoint(endpoint)}>{endpoint.enabled ? "Disable" : "Enable"}</button><button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => { setRotatingEndpoint((current) => current === endpoint.id ? null : endpoint.id); setReplacementSecret(""); }}>{rotatingEndpoint === endpoint.id ? "Cancel secret replacement" : "Replace signing secret"}</button>{pendingRemoval === endpoint.id ? <><button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => setPendingRemoval(null)}>Cancel removal</button><button className="button button-danger" type="button" disabled={busy !== null || endpoint.enabled} onClick={() => void removeEndpoint(endpoint.id)}>Confirm removal</button></> : <button className="button button-danger" type="button" disabled={busy !== null || endpoint.enabled} onClick={() => setPendingRemoval(endpoint.id)}>Remove</button>}</div>
            {rotatingEndpoint === endpoint.id && <form className="api-key-create" onSubmit={(event) => void rotateSecret(event, endpoint.id)}><Notice tone="warning" title="Retest after replacement">Existing queued deliveries will use the replacement secret. Send a test and update the receiver at the same time.</Notice><Field label="Replacement signing secret"><input required minLength={32} maxLength={512} type="password" autoComplete="new-password" value={replacementSecret} onChange={(event) => setReplacementSecret(event.target.value)} /></Field><button className="button button-primary" type="submit" disabled={busy !== null}>{busy === `secret:${endpoint.id}` ? "Replacing…" : "Replace signing secret"}</button></form>}
          </article>;
        })}
      </div>
    </Card>
  );
}
