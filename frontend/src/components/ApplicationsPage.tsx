import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { IntegrationDocument, IntegrationProduct } from "../types";
import { Card, Field, Notice, Spinner, StatusBadge } from "./ui";

const PRODUCTS: Array<{ value: IntegrationProduct; label: string }> = [
  { value: "sonarr", label: "Sonarr" },
  { value: "radarr", label: "Radarr" },
  { value: "lidarr", label: "Lidarr" },
  { value: "readarr", label: "Readarr" },
  { value: "whisparr", label: "Whisparr" },
  { value: "prowlarr", label: "Prowlarr" },
];

const DEFAULT_FOLDERS: Record<IntegrationProduct, string> = {
  sonarr: "/data/media/TV",
  radarr: "/data/media/Movies",
  lidarr: "/data/media/Music",
  readarr: "/data/media/Books",
  whisparr: "/data/media/Adult",
  prowlarr: "No media root required",
};

export function ApplicationsPage({
  onChanged,
  onRecommendations,
}: {
  onChanged?: (items: IntegrationDocument[]) => void;
  onRecommendations?: (value: { product: IntegrationProduct; media: boolean; torrents: boolean; usenet: boolean }) => void;
}) {
  const [items, setItems] = useState<IntegrationDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [product, setProduct] = useState<IntegrationProduct>("sonarr");
  const [name, setName] = useState("Sonarr");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [useMediaRoot, setUseMediaRoot] = useState(true);
  const [useTorrentFolders, setUseTorrentFolders] = useState(true);
  const [useUsenetFolders, setUseUsenetFolders] = useState(true);

  async function load(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const result = await api.integrations();
      setItems(result);
      onChanged?.(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Applications could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function add(): Promise<void> {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (!name.trim() || !baseUrl.trim() || !apiKey) throw new Error("Name, address, and API key are required.");
      const result = await api.createIntegration({
        name: name.trim(),
        product,
        base_url: baseUrl.trim(),
        api_key: apiKey,
        verify_tls: true,
        allow_localhost: true,
      });
      setItems((current) => [...current, result.integration]);
      setApiKey("");
      setAdding(false);
      setStatus("Application added. Discovery is running in Activity.");
      onChanged?.([...items, result.integration]);
      onRecommendations?.({ product, media: useMediaRoot, torrents: useTorrentFolders, usenet: useUsenetFolders });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Application could not be added.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="applications-page">
    {error && <Notice tone="danger" title="Application request failed">{error}</Notice>}
    {status && <Notice tone="info" title="Applications">{status}</Notice>}
    <Card title="ARR applications" description="Connected applications supply their media and download paths to storage setup." actions={<button type="button" className="icon-add-button" aria-label="Add application" onClick={() => setAdding(true)}>+</button>}>
      {loading ? <Spinner label="Loading applications…" /> : items.length ? <div className="settings-list">{items.map((item) => <section key={item.id} className="settings-row"><div><strong>{item.name}</strong><small>{item.discovered_product ?? item.expected_product} {item.product_version ?? ""}</small><code>{item.base_url}</code></div><div><StatusBadge status={item.status} /><button type="button" className="button button-secondary" onClick={() => void api.refreshIntegration(item.id)} disabled={busy}>Refresh</button></div></section>)}</div> : <div className="empty-state compact-empty"><h3>No applications connected</h3><p>Add an ARR application to discover its current folders.</p></div>}
    </Card>
    {adding && <Card title="Add application" description="Enter the address and API key shown in the application’s General settings.">
      <div className="form-grid two-columns">
        <Field label="Application"><select value={product} onChange={(event) => { const next = event.target.value as IntegrationProduct; setProduct(next); setName(PRODUCTS.find((item) => item.value === next)?.label ?? next); setUseMediaRoot(next !== "prowlarr"); }}>{PRODUCTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>
        <Field label="Name"><input value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label="Address"><input type="url" placeholder="http://server:8989" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></Field>
        <Field label="API key"><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></Field>
      </div>
      <div className="check-stack">
        <label><input type="checkbox" checked={useMediaRoot} disabled={product === "prowlarr"} onChange={(event) => setUseMediaRoot(event.target.checked)} /><span><strong>Use the recommended media folder</strong><small>{DEFAULT_FOLDERS[product]}</small></span></label>
        <label><input type="checkbox" checked={useTorrentFolders} onChange={(event) => setUseTorrentFolders(event.target.checked)} /><span><strong>Prepare torrent folders</strong><small>/data/downloads/torrents/incomplete and /data/downloads/torrents/complete</small></span></label>
        <label><input type="checkbox" checked={useUsenetFolders} onChange={(event) => setUseUsenetFolders(event.target.checked)} /><span><strong>Prepare Usenet folders</strong><small>/data/downloads/usenet/incomplete and /data/downloads/usenet/complete</small></span></label>
      </div>
      <div className="page-actions"><button type="button" className="button button-secondary" onClick={() => setAdding(false)} disabled={busy}>Cancel</button><button type="button" className="button button-primary" onClick={() => void add()} disabled={busy}>{busy ? "Adding…" : "Add application"}</button></div>
    </Card>}
  </div>;
}
