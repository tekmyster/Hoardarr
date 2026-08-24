import { useEffect, useMemo, useState } from "react";
import { api, ApiError, demoMode } from "../api/client";
import type { FleetPendingDocument, FleetTelemetrySettingsDocument } from "../types";
import { Card, Field, Notice, Spinner } from "./ui";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The telemetry request could not be completed.";
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function date(value: string | null): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

export function FleetTelemetryPanel() {
  const [settings, setSettings] = useState<FleetTelemetrySettingsDocument | null>(null);
  const [pending, setPending] = useState<FleetPendingDocument | null>(null);
  const [showPayload, setShowPayload] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [country, setCountry] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [resetText, setResetText] = useState("");

  async function refresh(): Promise<void> {
    const [nextSettings, nextPending] = await Promise.all([
      api.fleetTelemetrySettings(),
      api.fleetPendingPayloads(),
    ]);
    setSettings(nextSettings);
    setPending(nextPending);
    setCountry(nextSettings.country_code ?? "");
    setTimezone(nextSettings.timezone);
  }

  useEffect(() => {
    let current = true;
    Promise.all([api.fleetTelemetrySettings(), api.fleetPendingPayloads()])
      .then(([nextSettings, nextPending]) => {
        if (!current) return;
        setSettings(nextSettings);
        setPending(nextPending);
        setCountry(nextSettings.country_code ?? "");
        setTimezone(nextSettings.timezone);
      })
      .catch((caught) => { if (current) setError(errorMessage(caught)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, []);

  const exactPayload = useMemo(() => JSON.stringify(pending, null, 2), [pending]);

  async function act(action: () => Promise<FleetTelemetrySettingsDocument>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      setSettings(await action());
      setPending(await api.fleetPendingPayloads());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function save(): Promise<void> {
    if (!settings) return;
    await act(() => api.saveFleetTelemetrySettings({
      hardware_enabled: settings.hardware_enabled,
      enhanced_enabled: settings.enhanced_enabled,
      content_enabled: settings.content_enabled,
      country_code: country.trim() || null,
      timezone: timezone.trim(),
    }));
  }

  function exportPending(): void {
    const blob = new Blob([exactPayload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `hoardarr-pending-telemetry-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (busy && !settings) return <Card title="Telemetry & Privacy"><Spinner label="Loading telemetry privacy settings" /></Card>;
  if (!settings) return <Card title="Telemetry & Privacy"><Notice tone="danger" title="Telemetry settings unavailable">{error ?? "The settings could not be loaded."}</Notice><button className="button button-secondary" type="button" onClick={() => void refresh()}>Try again</button></Card>;

  return <Card title="Telemetry & Privacy" description="See and control exactly what this Hoardarr installation sends to hoardarr.com.">
    {error && <Notice tone="danger" title="Telemetry request failed">{error}</Notice>}
    <Notice tone="info" title="Anonymous installation heartbeat is required">
      It sends only this random installation ID, Hoardarr version/build, schema version, platform family, and heartbeat time. It never contains hardware, applications, paths, filenames, or drive identity.
    </Notice>
    <div className="settings-summary-list">
      <div><span>Installation ID</span><strong className="monospace-text">{settings.installation_id}</strong></div>
      <div><span>Endpoint</span><strong>{settings.endpoint}</strong></div>
      <div><span>Connection</span><strong>{settings.connection_status.replaceAll("_", " ")}</strong></div>
      <div><span>Schema</span><strong>v{settings.schema_version}</strong></div>
      <div><span>Queued</span><strong>{settings.queued_records} records · {bytes(settings.queued_bytes)}</strong></div>
      <div><span>Dead letter</span><strong>{settings.dead_letter_records}</strong></div>
      <div><span>Last upload</span><strong>{date(settings.last_successful_upload)}</strong></div>
      <div><span>Last attempt</span><strong>{date(settings.last_attempted_upload)}</strong></div>
    </div>
    <div className="telemetry-privacy-levels">
      <label><input type="checkbox" checked disabled /> <span><strong>Anonymous installation heartbeat</strong><small>Required minimal software-adoption signal.</small></span></label>
      <label><input type="checkbox" checked={settings.hardware_enabled} disabled={busy || demoMode} onChange={(event) => setSettings({ ...settings, hardware_enabled: event.target.checked, enhanced_enabled: event.target.checked && settings.enhanced_enabled, content_enabled: event.target.checked && settings.content_enabled })} /> <span><strong>Hardware & product telemetry</strong><small>On by default. Models, capacities, health summaries, storage layouts, and detected product names—never paths, URLs, usernames, or full serials.</small></span></label>
      <label><input type="checkbox" checked={settings.enhanced_enabled} disabled={busy || demoMode || !settings.hardware_enabled} onChange={(event) => setSettings({ ...settings, enhanced_enabled: event.target.checked, content_enabled: event.target.checked && settings.content_enabled })} /> <span><strong>Enhanced diagnostics</strong><small>Off by default. Adds selected detailed topology and aggregate file statistics.</small></span></label>
      <label><input type="checkbox" checked={settings.content_enabled} disabled={busy || demoMode || !settings.enhanced_enabled} onChange={(event) => setSettings({ ...settings, content_enabled: event.target.checked })} /> <span><strong>Content diagnostics</strong><small>Separate explicit opt-in. May include filenames and folder names, but never file contents.</small></span></label>
    </div>
    <div className="settings-grid compact-settings-grid">
      <Field label="Country / Region" hint="Two-letter code. Leave blank rather than accepting an uncertain inference."><input value={country} maxLength={2} onChange={(event) => setCountry(event.target.value.toUpperCase())} /></Field>
      <Field label="Timezone" hint={`Current source: ${settings.location_detection_method.replaceAll("_", " ")}`}><input value={timezone} onChange={(event) => setTimezone(event.target.value)} /></Field>
    </div>
    <p className="settings-help">{settings.limitations}</p>
    <div className="form-actions">
      <button className="button button-primary" type="button" disabled={busy || demoMode} onClick={() => void save()}>Save privacy settings</button>
      <button className="button button-secondary" type="button" disabled={busy || demoMode} onClick={() => void act(() => api.sendFleetTelemetryNow())}>Send now</button>
      <button className="button button-secondary" type="button" onClick={() => setShowPayload((shown) => !shown)}>{showPayload ? "Hide exact payload" : "View exactly what is sent"}</button>
      <button className="button button-secondary" type="button" disabled={!pending?.items.length} onClick={exportPending}>Export pending payload</button>
      <button className="button button-secondary" type="button" disabled={busy || demoMode} onClick={() => void act(() => api.clearOptionalFleetTelemetry())}>Clear unsent optional telemetry</button>
    </div>
    {showPayload && <div className="telemetry-payload-viewer">
      <h3>Exact pending payload</h3>
      {!pending?.items.length ? <p>No records are waiting to be sent.</p> : <pre tabIndex={0}>{exactPayload}</pre>}
    </div>}
    <details className="advanced-details"><summary>Advanced identity reset</summary>
      <Notice tone="warning" title="This starts a new installation identity">Pending telemetry and the current registration credential will be removed. Existing local storage and telemetry history are unaffected.</Notice>
      <Field label="Type RESET TELEMETRY IDENTITY"><input value={resetText} onChange={(event) => setResetText(event.target.value)} /></Field>
      <button className="button button-danger" type="button" disabled={busy || demoMode || resetText !== "RESET TELEMETRY IDENTITY"} onClick={() => void act(async () => { const result = await api.resetFleetTelemetryIdentity(); setResetText(""); return result; })}>Reset telemetry identity</button>
    </details>
  </Card>;
}
