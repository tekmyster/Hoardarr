import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, demoMode } from "../api/client";
import { copyText } from "../clipboard";
import type { AddonDocument, ApiKeyDocument, StorageOperationProgress, UpdateCheckDocument, UpdateStatusDocument } from "../types";
import { OneTimePassword } from "./OneTimePassword";
import { RemoteBackupsPanel } from "./RemoteBackupsPanel";
import { WebhooksPanel } from "./WebhooksPanel";
import { Card, Field, Notice, Spinner } from "./ui";

type AccessLevel = "read" | "operate" | "admin";

function messageFromError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The request could not be completed.";
}

function scopesFor(level: AccessLevel): ApiKeyDocument["scopes"] {
  if (level === "admin") return ["read", "operate", "admin"];
  if (level === "operate") return ["read", "operate"];
  return ["read"];
}

function accessLabel(scopes: ApiKeyDocument["scopes"]): string {
  if (scopes.includes("admin")) return "Administrator";
  if (scopes.includes("operate")) return "Monitor and operate";
  return "Monitor only";
}

function dateLabel(value: string | null): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

export function SettingsPage() {
  const [keys, setKeys] = useState<ApiKeyDocument[]>([]);
  const [name, setName] = useState("");
  const [access, setAccess] = useState<AccessLevel>("read");
  const [secret, setSecret] = useState<string | null>(null);
  const [showSecret, setShowSecret] = useState(false);
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusDocument | null>(null);
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckDocument | null>(null);
  const [addons, setAddons] = useState<AddonDocument[]>([]);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [updateProgress, setUpdateProgress] = useState<StorageOperationProgress | null>(null);
  const [mediaUsername, setMediaUsername] = useState("media");
  const [mediaPasswordMode, setMediaPasswordMode] = useState<"generate" | "provide">("generate");
  const [mediaPassword, setMediaPassword] = useState("");
  const [generatedMediaPassword, setGeneratedMediaPassword] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    Promise.all([api.apiKeys(), api.updateStatus(), api.addons()])
      .then(([items, status, installed]) => {
        if (current) {
          setKeys(items);
          setUpdateStatus(status);
          setAddons(installed);
        }
      })
      .catch((caught) => { if (current) setError(messageFromError(caught)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, []);

  useEffect(() => {
    const operation = updateStatus?.operation;
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    const operationId = operation.id;
    let stopped = false;
    let timer: number | undefined;
    async function poll(): Promise<void> {
      try {
        const [nextOperation, progress] = await Promise.all([
          api.operation(operationId),
          api.storageOperationProgress(operationId),
        ]);
        if (stopped) return;
        setUpdateStatus((current) => current ? { ...current, operation: nextOperation } : current);
        setUpdateProgress(progress);
        if (["queued", "running"].includes(nextOperation.status)) {
          timer = window.setTimeout(() => void poll(), 2_000);
        } else {
          setUpdateStatus(await api.updateStatus());
        }
      } catch (caught) {
        if (!stopped) setError(messageFromError(caught));
      }
    }
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [updateStatus?.operation?.id, updateStatus?.operation?.status]);

  async function createKey(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) {
      setError("Name the app or device that will use this API key.");
      return;
    }
    setBusy(true);
    setError(null);
    setCopyStatus(null);
    try {
      const created = await api.createApiKey({ name: cleanName, scopes: scopesFor(access) });
      setKeys((items) => [...items, created.key]);
      setSecret(created.secret);
      setShowSecret(false);
      setName("");
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function copySecret(): Promise<void> {
    if (!secret) return;
    if (await copyText(secret)) {
      setCopyStatus("Copied to clipboard.");
    } else {
      setCopyStatus("Copy is unavailable in this browser. Select the key and copy it manually.");
    }
  }

  async function removeKey(id: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.deleteApiKey(id);
      setKeys((items) => items.filter((item) => item.id !== id));
      setPendingRemoval(null);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function checkForUpdates(): Promise<void> {
    setUpdateBusy(true);
    setError(null);
    try {
      const checked = await api.checkUpdates();
      setUpdateCheck(checked);
      setUpdateStatus(await api.updateStatus());
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setUpdateBusy(false);
    }
  }

  async function startUpdate(): Promise<void> {
    if (!updateCheck?.compatible || !updateCheck.metadata_sha256) return;
    setUpdateBusy(true);
    setError(null);
    try {
      const operation = await api.applyUpdate(updateCheck.metadata_sha256);
      setUpdateStatus((current) => current ? { ...current, operation } : current);
      setUpdateProgress(null);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setUpdateBusy(false);
    }
  }

  async function changeAddon(addon: AddonDocument, action: "enable" | "disable" | "remove"): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.changeAddon(addon.id, action);
      setAddons(await api.addons());
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function saveMediaAccount(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.provisionMediaAccount({
        username: mediaUsername.trim(),
        credential_mode: mediaPasswordMode,
        password: mediaPasswordMode === "provide" ? mediaPassword : undefined,
      });
      setMediaPassword("");
      setGeneratedMediaPassword(result.credential.password);
    } catch (caught) {
      setError(messageFromError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-page">
      <Notice tone="info" title="Setup and app access use different credentials">
        The one-time server pairing code can only create the first account. Apps use separately generated API keys beginning with <code>hak_</code>.
      </Notice>
      <Card title="Media account" description="The shared Linux and SMB account used by ARR applications and containers.">
        {generatedMediaPassword ? <>
          <Notice tone="success" title="Save this password now">It cannot be displayed again after you confirm it.</Notice>
          <OneTimePassword
            password={generatedMediaPassword}
            onCopyError={() => setError("The browser could not copy the password. Reveal it and copy it manually.")}
            onSavedConfirmed={() => setGeneratedMediaPassword(null)}
          />
        </> : <form className="api-key-create" onSubmit={(event) => void saveMediaAccount(event)}>
          <Field label="Username"><input required pattern="[a-z_][a-z0-9_-]{0,31}" value={mediaUsername} onChange={(event) => setMediaUsername(event.target.value)} /></Field>
          <Field label="Password"><select value={mediaPasswordMode} onChange={(event) => setMediaPasswordMode(event.target.value as "generate" | "provide")}><option value="generate">Generate a password</option><option value="provide">Set a password</option></select></Field>
          {mediaPasswordMode === "provide" && <Field label="New password"><input required type="password" value={mediaPassword} onChange={(event) => setMediaPassword(event.target.value)} /></Field>}
          <button className="button button-primary" type="submit" disabled={busy || demoMode}>{busy ? "Saving…" : "Create or reset account"}</button>
        </form>}
      </Card>
      <Card title="API Keys" description="Create a key for an app, script, or monitoring system that needs to connect to Hoardarr.">
        <p className="settings-help">A Monitor only key can read the bounded Home Assistant summary at <code>/api/v1/integrations/home-assistant/summary</code>. It cannot start storage work.</p>
        {error && <Notice tone="danger" title="Settings request failed">{error}</Notice>}
        {secret && (
          <Notice tone="success" title="Save this API key now">
            <p>It is shown only once. Hoardarr stores only a hash and cannot display it again.</p>
            <div className="api-key-secret-row">
              <input aria-label="New API Key" readOnly type={showSecret ? "text" : "password"} value={secret} onFocus={(event) => event.currentTarget.select()} />
              <button className="button button-secondary" type="button" onClick={() => setShowSecret((shown) => !shown)}>{showSecret ? "Hide" : "Show"}</button>
              <button className="button button-secondary" type="button" onClick={() => void copySecret()}>Copy</button>
            </div>
            {copyStatus && <small className="field-hint" role="status">{copyStatus}</small>}
          </Notice>
        )}
        <form className="api-key-create" onSubmit={(event) => void createKey(event)}>
          <Field label="Name" hint="Use the app or device name so you can identify it later."><input value={name} maxLength={128} disabled={busy || demoMode} placeholder="Example: Home Assistant" onChange={(event) => setName(event.target.value)} /></Field>
          <Field label="Access">
            <select value={access} disabled={busy || demoMode} onChange={(event) => setAccess(event.target.value as AccessLevel)}>
              <option value="read">Monitor only</option>
              <option value="operate">Monitor and operate</option>
              <option value="admin">Administrator</option>
            </select>
          </Field>
          <button className="button button-primary" type="submit" disabled={busy || demoMode}>{busy ? "Working…" : "Generate API Key"}</button>
        </form>
        <div className="api-key-list" aria-live="polite">
          <h3>Active keys</h3>
          {busy && !keys.length ? <Spinner label="Loading API keys" /> : !keys.length ? <div className="empty-state compact-empty"><h3>No API keys</h3><p>Generate one when an app needs access.</p></div> : keys.map((key) => (
            <article className="api-key-row" key={key.id}>
              <div><strong>{key.name}</strong><span>{accessLabel(key.scopes)} · Created {dateLabel(key.created_at)}</span><small>Last used: {dateLabel(key.last_used_at)}</small></div>
              {pendingRemoval === key.id ? <div className="api-key-actions"><button className="button button-secondary" type="button" disabled={busy} onClick={() => setPendingRemoval(null)}>Cancel</button><button className="button button-danger" type="button" disabled={busy} onClick={() => void removeKey(key.id)}>Confirm removal</button></div> : <button className="button button-secondary" type="button" disabled={busy} onClick={() => setPendingRemoval(key.id)}>Remove</button>}
            </article>
          ))}
        </div>
      </Card>
      <RemoteBackupsPanel />
      <WebhooksPanel />
      <Card title="Updates" description="Signed direct-to-latest Hoardarr releases.">
        {!updateStatus ? <Spinner label="Loading update status" /> : (
          <div className="settings-summary-list">
            <div><span>Installed</span><strong>{updateStatus.current_version}</strong></div>
            <div><span>Channel</span><strong>{updateStatus.channel}</strong></div>
            <div><span>Available</span><strong>{updateCheck?.latest_version ?? updateStatus.latest_version ?? "Not checked"}</strong></div>
            {updateStatus.operation && <div><span>Update status</span><strong>{updateStatus.operation.status.replaceAll("_", " ")}</strong></div>}
          </div>
        )}
        {updateCheck?.blockers.map((blocker) => <Notice key={blocker.code} tone="warning" title="Update needs attention">{blocker.message}</Notice>)}
        {updateStatus?.operation && ["queued", "running"].includes(updateStatus.operation.status) && <div className="storage-progress-card" aria-live="polite">
          <div className="progress-heading"><span>{updateProgress?.phase ?? "Waiting for update worker"}</span><strong>{updateProgress?.percent ?? 0}%</strong></div>
          <progress aria-label="Update progress" value={updateProgress?.percent ?? 0} max={100} />
        </div>}
        {updateStatus?.operation?.status === "failed" || updateStatus?.operation?.status === "needs_attention" ? <Notice tone="danger" title="Update failed">{updateStatus.operation.error?.message ?? updateStatus.operation.error?.detail ?? "Review Activity for details."}</Notice> : null}
        <div className="form-actions">
          <button className="button button-secondary" type="button" disabled={updateBusy || demoMode} onClick={() => void checkForUpdates()}>{updateBusy ? "Working…" : "Check for updates"}</button>
          {updateCheck?.compatible && updateCheck.latest_version !== updateCheck.current_version && <button className="button button-primary" type="button" disabled={updateBusy || demoMode} onClick={() => void startUpdate()}>Install update</button>}
        </div>
      </Card>
      <Card title="Add-ons" description="Trusted local modules installed by an administrator.">
        {!addons.length ? <div className="empty-state compact-empty"><h3>No add-ons</h3><p>Place a signed package in the server add-on inbox, then install it through the API or CLI.</p></div> : addons.map((addon) => (
          <article className="api-key-row" key={addon.id}>
            <div><strong>{addon.name}</strong><span>Version {addon.version} · {addon.state}</span><small>{addon.privileges.length ? addon.privileges.join(", ") : "No host privileges"}</small></div>
            <div className="api-key-actions">
              {addon.state === "enabled" ? <button className="button button-secondary" type="button" disabled={busy} onClick={() => void changeAddon(addon, "disable")}>Disable</button> : <button className="button button-secondary" type="button" disabled={busy} onClick={() => void changeAddon(addon, "enable")}>Enable</button>}
              <button className="button button-danger" type="button" disabled={busy || addon.state === "enabled"} onClick={() => void changeAddon(addon, "remove")}>Remove</button>
            </div>
          </article>
        ))}
      </Card>
      <Card title="Logging" description="Service logs are written to the system journal.">
        <p>Use Activity for durable operation history. Advanced service logs remain available through <code>journalctl</code>.</p>
      </Card>
    </div>
  );
}
