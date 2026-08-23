import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { IntegrationDocument, IntegrationProduct } from "../types";
import { humanCapacity } from "../policy";
import { Card, Field, Notice, Spinner, StatusBadge } from "./ui";

const PRODUCTS: Array<{ value: IntegrationProduct; label: string }> = [
  { value: "sonarr", label: "Sonarr" },
  { value: "radarr", label: "Radarr" },
  { value: "lidarr", label: "Lidarr" },
  { value: "readarr", label: "Readarr" },
  { value: "whisparr", label: "Whisparr" },
  { value: "prowlarr", label: "Prowlarr" },
  { value: "plex", label: "Plex" },
  { value: "jellyfin", label: "Jellyfin" },
  { value: "emby", label: "Emby" },
];

const DEFAULT_FOLDERS: Record<IntegrationProduct, string> = {
  sonarr: "/data/media/TV",
  radarr: "/data/media/Movies",
  lidarr: "/data/media/Music",
  readarr: "/data/media/Books",
  whisparr: "/data/media/Adult",
  prowlarr: "No media root required",
  plex: "Read-only library visibility",
  jellyfin: "Read-only library visibility",
  emby: "Read-only library visibility",
};

const MEDIA_PRODUCTS = new Set<IntegrationProduct>(["plex", "jellyfin", "emby"]);

type MediaLibrary = { id: string; name: string; media_type: string; paths: string[]; item_count: number | null; capacity_bytes: number | null; quality: string; storage_mapping?: { quality: string; confidence: string; source: string; storage_group_name: string | null; storage_group_namespace: string | null; storage_capacity_bytes: number | null; storage_free_bytes: number | null } };

function mediaLibraries(item: IntegrationDocument): MediaLibrary[] {
  if (!MEDIA_PRODUCTS.has(item.expected_product) || !Array.isArray(item.state.libraries)) return [];
  return item.state.libraries.filter((value): value is MediaLibrary => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const row = value as Record<string, unknown>;
    return typeof row.id === "string" && typeof row.name === "string" && Array.isArray(row.paths);
  });
}

function activitySummary(item: IntegrationDocument): { status: string; detail: string } {
  const activity = item.state.activity;
  if (!activity || typeof activity !== "object" || Array.isArray(activity)) {
    return { status: "Not reported", detail: "Write-sensitive activity has not been checked yet." };
  }
  const values = activity as Record<string, unknown>;
  if (values.quality === "unsupported") return { status: "Unsupported", detail: "This application does not expose media/download write activity." };
  if (values.quality !== "available") return { status: "Temporarily unavailable", detail: "Hoardarr will not assume storage is idle while activity is unavailable." };
  const active = typeof values.active_writes === "number" ? values.active_writes : null;
  if (active === null) return { status: "Not reported", detail: "The application returned incomplete activity data." };
  const downloading = typeof values.downloading === "number" ? values.downloading : 0;
  const importing = typeof values.importing === "number" ? values.importing : 0;
  const importingCommands = typeof values.importing_commands === "number" ? values.importing_commands : 0;
  const renaming = typeof values.renaming === "number" ? values.renaming : 0;
  const moving = typeof values.moving === "number" ? values.moving : 0;
  const pending = typeof values.pending === "number" ? values.pending : 0;
  const parts = [
    downloading ? `${downloading} downloading` : "",
    importing || importingCommands ? `${importing + importingCommands} importing` : "",
    renaming ? `${renaming} renaming` : "",
    moving ? `${moving} moving` : "",
    pending ? `${pending} pending` : "",
  ].filter(Boolean);
  return {
    status: active > 0 ? "Storage active" : "Idle",
    detail: active > 0
      ? parts.join(" · ")
      : pending ? `No active writes · ${pending} pending` : "No active downloads, imports, renames, or moves reported.",
  };
}

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
      if (!MEDIA_PRODUCTS.has(product)) onRecommendations?.({ product, media: useMediaRoot, torrents: useTorrentFolders, usenet: useUsenetFolders });
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
      {loading ? <Spinner label="Loading applications…" /> : items.length ? <div className="settings-list">{items.map((item) => { const activity = activitySummary(item); const libraries = mediaLibraries(item); const isMedia = MEDIA_PRODUCTS.has(item.expected_product); return <section key={item.id} className="settings-row"><div><strong>{item.name}</strong><small>{item.discovered_product ?? item.expected_product} {item.product_version ?? ""}</small><code>{item.base_url}</code>{isMedia ? <>{libraries.length ? <div className="media-library-grid">{libraries.map((library) => { const mapping = library.storage_mapping; return <article key={library.id}><strong>{library.name}</strong><small>{library.media_type}</small><span>{library.item_count === null ? "Item count not reported" : `${library.item_count.toLocaleString()} items`}</span>{mapping?.quality === "available" ? <><span>Storage Group: {mapping.storage_group_name}</span><span>{mapping.storage_capacity_bytes === null ? "Storage capacity not reported" : `${humanCapacity(mapping.storage_capacity_bytes)} storage capacity`}</span><small>Confirmed from the local namespace and filesystem identity.</small></> : <span>Storage Group not reported · library path could not be proven on this host</span>}{library.paths.map((path) => <code key={path}>{path}</code>)}</article>; })}</div> : <small>{item.status === "connected" ? "No libraries were reported." : "Library discovery has not completed."}</small>}<small>Read-only observability · Hoardarr does not modify media libraries.</small></> : <><small><strong>{activity.status}</strong> · {activity.detail}</small>{typeof item.state.activity_observed_at === "string" && <small>Activity checked {new Date(item.state.activity_observed_at).toLocaleString()}</small>}</>}</div><div><StatusBadge status={item.status} /><button type="button" className="button button-secondary" onClick={() => void api.refreshIntegration(item.id)} disabled={busy}>Refresh</button></div></section>; })}</div> : <div className="empty-state compact-empty"><h3>No applications connected</h3><p>Add an ARR or media application to discover its current folders and libraries.</p></div>}
    </Card>
    {adding && <Card title="Add application" description="Enter the address and API key shown in the application’s General settings.">
      <div className="form-grid two-columns">
        <Field label="Application"><select value={product} onChange={(event) => { const next = event.target.value as IntegrationProduct; setProduct(next); setName(PRODUCTS.find((item) => item.value === next)?.label ?? next); setUseMediaRoot(next !== "prowlarr" && !MEDIA_PRODUCTS.has(next)); }}>{PRODUCTS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>
        <Field label="Name"><input value={name} onChange={(event) => setName(event.target.value)} /></Field>
        <Field label="Address"><input type="url" placeholder="http://server:8989" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></Field>
        <Field label="API key"><input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></Field>
      </div>
      {!MEDIA_PRODUCTS.has(product) && <div className="check-stack">
        <label><input type="checkbox" checked={useMediaRoot} disabled={product === "prowlarr"} onChange={(event) => setUseMediaRoot(event.target.checked)} /><span><strong>Use the recommended media folder</strong><small>{DEFAULT_FOLDERS[product]}</small></span></label>
        <label><input type="checkbox" checked={useTorrentFolders} onChange={(event) => setUseTorrentFolders(event.target.checked)} /><span><strong>Prepare torrent folders</strong><small>/data/downloads/torrents/incomplete and /data/downloads/torrents/complete</small></span></label>
        <label><input type="checkbox" checked={useUsenetFolders} onChange={(event) => setUseUsenetFolders(event.target.checked)} /><span><strong>Prepare Usenet folders</strong><small>/data/downloads/usenet/incomplete and /data/downloads/usenet/complete</small></span></label>
      </div>}
      {MEDIA_PRODUCTS.has(product) && <Notice tone="info" title="Read-only media visibility">Hoardarr will read library names, paths, and item counts when the server reports them. It will not change Plex, Jellyfin, or Emby libraries.</Notice>}
      <div className="page-actions"><button type="button" className="button button-secondary" onClick={() => setAdding(false)} disabled={busy}>Cancel</button><button type="button" className="button button-primary" onClick={() => void add()} disabled={busy}>{busy ? "Adding…" : "Add application"}</button></div>
    </Card>}
  </div>;
}
