import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, demoMode } from "../api/client";
import type {
  OperationDocument,
  RemoteBackupProvider,
  RemoteBackupRunDocument,
  RemoteBackupTargetDocument,
} from "../types";
import { Card, Field, Notice, Spinner } from "./ui";

const PROVIDERS: Array<{ value: RemoteBackupProvider; label: string }> = [
  { value: "minio", label: "MinIO" },
  { value: "aws_s3", label: "Amazon S3" },
  { value: "cloudflare_r2", label: "Cloudflare R2" },
  { value: "wasabi", label: "Wasabi" },
  { value: "backblaze_b2", label: "Backblaze B2 (S3 API)" },
  { value: "generic_s3", label: "Other S3-compatible service" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "The backup request could not be completed.";
}

function dateLabel(value: string | null): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function sizeLabel(value: number | null): string {
  if (value === null) return "Not reported";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 ** 2).toFixed(1)} MiB`;
}

function providerLabel(value: RemoteBackupProvider): string {
  return PROVIDERS.find((provider) => provider.value === value)?.label ?? value;
}

export function RemoteBackupsPanel() {
  const [targets, setTargets] = useState<RemoteBackupTargetDocument[]>([]);
  const [runs, setRuns] = useState<RemoteBackupRunDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [provider, setProvider] = useState<RemoteBackupProvider>("minio");
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [region, setRegion] = useState("us-east-1");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("hoardarr");
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [privateNetwork, setPrivateNetwork] = useState(false);
  const [insecureHttp, setInsecureHttp] = useState(false);
  const [rotatingTarget, setRotatingTarget] = useState<string | null>(null);
  const [replacementAccessKey, setReplacementAccessKey] = useState("");
  const [replacementSecretKey, setReplacementSecretKey] = useState("");

  const refresh = useCallback(async () => {
    const [nextTargets, nextRuns] = await Promise.all([api.backupTargets(), api.backupRuns()]);
    setTargets(nextTargets);
    setRuns(nextRuns);
  }, []);

  useEffect(() => {
    let active = true;
    refresh()
      .catch((caught) => { if (active) setError(errorMessage(caught)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    const operationId = operation.id;
    let stopped = false;
    let timer: number | undefined;
    async function poll(): Promise<void> {
      try {
        const next = await api.operation(operationId);
        if (stopped) return;
        setOperation(next);
        if (["queued", "running"].includes(next.status)) {
          timer = window.setTimeout(() => void poll(), 1_500);
        } else {
          setBusy(null);
          await refresh();
        }
      } catch (caught) {
        if (!stopped) {
          setBusy(null);
          setError(errorMessage(caught));
        }
      }
    }
    timer = window.setTimeout(() => void poll(), 500);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [operation, refresh]);

  async function createTarget(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setBusy("create");
    setError(null);
    try {
      await api.createBackupTarget({
        name: name.trim(),
        provider,
        endpoint_url: endpoint.trim() || undefined,
        region: region.trim(),
        bucket: bucket.trim(),
        prefix: prefix.trim(),
        access_key_id: accessKey,
        secret_access_key: secretKey,
        force_path_style: provider === "minio" || provider === "generic_s3",
        verify_tls: !insecureHttp,
        allow_private_network: privateNetwork,
        allow_insecure_http: insecureHttp,
      });
      setName("");
      setBucket("");
      setAccessKey("");
      setSecretKey("");
      setShowForm(false);
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function startAction(kind: "test" | "backup" | "validate", id: string): Promise<void> {
    setBusy(`${kind}:${id}`);
    setError(null);
    try {
      const next = kind === "test"
        ? await api.testBackupTarget(id)
        : kind === "backup"
          ? await api.startControlPlaneBackup(id)
          : await api.validateBackupRestore(id);
      setOperation(next);
      if (!["queued", "running"].includes(next.status)) {
        setBusy(null);
        await refresh();
      }
    } catch (caught) {
      setBusy(null);
      setError(errorMessage(caught));
    }
  }

  async function changeSchedule(target: RemoteBackupTargetDocument): Promise<void> {
    setBusy(`schedule:${target.id}`);
    setError(null);
    try {
      const updated = await api.updateBackupSchedule(target.id, {
        enabled: target.schedule.enabled !== true,
        interval_hours: typeof target.schedule.interval_hours === "number"
          ? target.schedule.interval_hours
          : 24,
      });
      setTargets((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function rotateCredentials(event: FormEvent<HTMLFormElement>, targetId: string): Promise<void> {
    event.preventDefault();
    setBusy(`credentials:${targetId}`);
    setError(null);
    try {
      const updated = await api.rotateBackupTargetCredentials(targetId, {
        access_key_id: replacementAccessKey,
        secret_access_key: replacementSecretKey,
      });
      setTargets((items) => items.map((item) => item.id === updated.id ? updated : item));
      setReplacementAccessKey("");
      setReplacementSecretKey("");
      setRotatingTarget(null);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Remote backups" description="Back up Hoardarr's database and non-secret configuration to an S3-compatible target.">
      <Notice tone="info" title="Control-plane backup">
        This protects Hoardarr settings and history—not the media files stored on your disks. Secrets and API keys are excluded.
      </Notice>
      {error && <Notice tone="danger" title="Backup request failed">{error}</Notice>}
      {operation && ["queued", "running"].includes(operation.status) && (
        <Notice tone="info" title="Backup activity in progress">
          {operation.kind.replaceAll(".", " ")} is {operation.status}. Full durable progress is available in Activity.
        </Notice>
      )}
      {operation && ["failed", "needs_attention"].includes(operation.status) && (
        <Notice tone="danger" title="Backup activity needs attention">
          {operation.error?.message ?? operation.error?.detail ?? "Review Activity for the recorded failure."}
        </Notice>
      )}
      <div className="form-actions">
        <button className="button button-secondary" type="button" disabled={demoMode || busy !== null} onClick={() => setShowForm((shown) => !shown)}>
          {showForm ? "Cancel new target" : "Add backup target"}
        </button>
      </div>
      {showForm && (
        <form className="api-key-create" onSubmit={(event) => void createTarget(event)}>
          <Field label="Name" hint="A recognizable name such as Home MinIO."><input required maxLength={128} value={name} onChange={(event) => setName(event.target.value)} /></Field>
          <Field label="Service"><select value={provider} onChange={(event) => setProvider(event.target.value as RemoteBackupProvider)}>{PROVIDERS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>
          <Field label="Endpoint" hint="Required for MinIO and other compatible services. Use HTTPS unless this is an explicitly trusted home-network target."><input type="url" placeholder="https://minio.example:9000" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} /></Field>
          <Field label="Region"><input required value={region} onChange={(event) => setRegion(event.target.value)} /></Field>
          <Field label="Bucket"><input required minLength={3} value={bucket} onChange={(event) => setBucket(event.target.value)} /></Field>
          <Field label="Folder prefix"><input required value={prefix} onChange={(event) => setPrefix(event.target.value)} /></Field>
          <Field label="Access key"><input required autoComplete="off" minLength={8} value={accessKey} onChange={(event) => setAccessKey(event.target.value)} /></Field>
          <Field label="Secret key"><input required autoComplete="new-password" type="password" minLength={8} value={secretKey} onChange={(event) => setSecretKey(event.target.value)} /></Field>
          <label className="check-row"><input type="checkbox" checked={privateNetwork} onChange={(event) => { setPrivateNetwork(event.target.checked); if (!event.target.checked) setInsecureHttp(false); }} />This endpoint is on a trusted private network</label>
          {privateNetwork && <label className="check-row"><input type="checkbox" checked={insecureHttp} onChange={(event) => setInsecureHttp(event.target.checked)} />Allow unencrypted HTTP for this private endpoint</label>}
          <button className="button button-primary" type="submit" disabled={busy !== null}>{busy === "create" ? "Saving…" : "Save target"}</button>
        </form>
      )}
      <div className="api-key-list" aria-live="polite">
        <h3>Targets</h3>
        {loading ? <Spinner label="Loading backup targets" /> : targets.length === 0 ? (
          <div className="empty-state compact-empty"><h3>No backup target</h3><p>Add an S3-compatible destination, then prove read/write access before running a backup.</p></div>
        ) : targets.map((target) => (
          <article className="api-key-row" key={target.id}>
            <div>
              <strong>{target.name}</strong>
              <span>{providerLabel(target.provider)} · {target.bucket}/{target.prefix}</span>
              <small>Status: {target.status.replaceAll("_", " ")} · Last verified: {dateLabel(target.last_tested_at)}</small>
              <small>Automatic: {target.schedule.enabled === true ? `Every ${String(target.schedule.interval_hours ?? 24)} hours` : "Off"}</small>
              {target.error?.message && <small className="danger-text">{target.error.message}</small>}
            </div>
            <div className="api-key-actions">
              <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void startAction("test", target.id)}>{busy === `test:${target.id}` ? "Testing…" : "Test connection"}</button>
              <button className="button button-secondary" type="button" disabled={busy !== null || !["available", "degraded"].includes(target.status)} onClick={() => void changeSchedule(target)}>{target.schedule.enabled === true ? "Turn off automatic backup" : "Back up every 24 hours"}</button>
              <button className="button button-primary" type="button" disabled={busy !== null || !["available", "degraded"].includes(target.status)} onClick={() => void startAction("backup", target.id)}>{busy === `backup:${target.id}` ? "Starting…" : "Back up now"}</button>
              <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => { setRotatingTarget((current) => current === target.id ? null : target.id); setReplacementAccessKey(""); setReplacementSecretKey(""); }}>
                {rotatingTarget === target.id ? "Cancel credential replacement" : "Replace credentials"}
              </button>
            </div>
            {rotatingTarget === target.id && (
              <form className="api-key-create" onSubmit={(event) => void rotateCredentials(event, target.id)}>
                <Notice tone="warning" title="Connection proof required again">Automatic backups will be turned off until the replacement credentials pass a new connection test.</Notice>
                <Field label="Replacement access key"><input required autoComplete="off" minLength={8} value={replacementAccessKey} onChange={(event) => setReplacementAccessKey(event.target.value)} /></Field>
                <Field label="Replacement secret key"><input required autoComplete="new-password" type="password" minLength={8} value={replacementSecretKey} onChange={(event) => setReplacementSecretKey(event.target.value)} /></Field>
                <button className="button button-primary" type="submit" disabled={busy !== null}>{busy === `credentials:${target.id}` ? "Replacing…" : "Replace and require retest"}</button>
              </form>
            )}
          </article>
        ))}
      </div>
      <div className="api-key-list" aria-live="polite">
        <h3>Backup history</h3>
        {!loading && runs.length === 0 ? <div className="empty-state compact-empty"><h3>No backups yet</h3><p>Successful and failed backup attempts will remain visible here and in Activity.</p></div> : runs.map((run) => (
          <article className="api-key-row" key={run.id}>
            <div>
              <strong>{run.status.replaceAll("_", " ")}</strong>
              <span>{dateLabel(run.created_at)} · {sizeLabel(run.artifact_size_bytes)}</span>
              <small>{run.object_key ?? run.phase}</small>
            </div>
            {run.status === "succeeded" && <button className="button button-secondary" type="button" disabled={busy !== null} onClick={() => void startAction("validate", run.id)}>{busy === `validate:${run.id}` ? "Starting…" : "Validate restore"}</button>}
          </article>
        ))}
      </div>
    </Card>
  );
}
