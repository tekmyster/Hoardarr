import { type FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { HAConfigurationInput, HAStatusDocument } from "../types";
import { Card, Notice, StatusBadge } from "./ui";

const initial: HAConfigurationInput = {
  local_node_id: "hoardarr-a",
  local_name: "Hoardarr-A",
  local_fqdn: "hoardarr-a.local",
  local_ip: "",
  local_role: "active",
  peer_node_id: "hoardarr-b",
  peer_name: "Hoardarr-B",
  peer_fqdn: "hoardarr-b.local",
  peer_ip: "",
  peer_role: "passive",
  service_ip: null,
};

function date(value: string | null | undefined): string {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "Not reported" : parsed.toLocaleString();
}

export function HASettingsPanel() {
  const [status, setStatus] = useState<HAStatusDocument | null>(null);
  const [form, setForm] = useState<HAConfigurationInput>(initial);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void api.haStatus(controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setStatus(value);
      if (value.configured && value.local && value.peer) {
        setForm({
          local_node_id: value.local.node_id, local_name: value.local.name, local_fqdn: value.local.fqdn,
          local_ip: value.local.ip, local_role: value.local.role, peer_node_id: value.peer.node_id,
          peer_name: value.peer.name, peer_fqdn: value.peer.fqdn, peer_ip: value.peer.ip,
          peer_role: value.peer.role, service_ip: value.service_ip ?? null,
        });
      }
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "HA status could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  const change = <K extends keyof HAConfigurationInput>(name: K, value: HAConfigurationInput[K]) => setForm((current) => ({ ...current, [name]: value }));
  const save = async (event: FormEvent) => {
    event.preventDefault(); setSaving(true); setError(null);
    try { setStatus(await api.saveHAConfiguration({ ...form, service_ip: form.service_ip || null })); setEditing(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "HA configuration could not be saved."); }
    finally { setSaving(false); }
  };

  return <Card title="High availability" description="Advanced two-node storage ownership and peer awareness. Controller multipath is configured on each storage object.">
    {loading ? <p role="status">Loading HA status…</p> : error && !status ? <Notice tone="danger" title="HA status unavailable">{error}</Notice> : null}
    {status && !status.configured && !editing && <><Notice tone="info" title="Two-node HA is not configured">Controlled handoff tests do not configure a persistent peer. Add both node identities before relying on this status.</Notice><button className="button button-primary" type="button" onClick={() => setEditing(true)}>Configure two nodes</button></>}
    {status?.configured && !editing && status.local && status.peer && <>
      <div className="settings-summary-list"><div><span>HA maturity</span><strong>{status.maturity_level} · Persistent peer awareness</strong></div><div><span>Mode</span><strong>Controlled single writer</strong></div><div><span>Current owner</span><strong>{status.current_owner_node_id ?? "Not reported"}</strong></div><div><span>Service IP</span><strong>{status.service_ip ?? "Not configured"}</strong></div><div><span>Synchronization</span><strong>{status.synchronization_state?.replaceAll("_", " ") ?? "Not reported"}</strong></div><div><span>Failover readiness</span><strong>{status.failover_readiness?.replaceAll("_", " ") ?? "Not reported"}</strong></div></div>
      <div className="ha-node-grid"><article><small>Local node</small><strong>{status.local.name}</strong><span>{status.local.fqdn} · {status.local.ip}</span><StatusBadge status={status.local.role} /></article><article><small>Peer node</small><strong>{status.peer.name}</strong><span>{status.peer.fqdn} · {status.peer.ip}</span><StatusBadge status={status.peer.state} /><small>Last heartbeat: {date(status.peer.last_seen_at)}</small></article></div>
      {!status.automatic_failover && <Notice tone="warning" title="Automatic failover is not configured">HA-3 records peer identity and health. It does not move storage ownership automatically, and fencing is not configured.</Notice>}
      <div className="form-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(true)}>Edit node settings</button></div>
      <h3>HA history</h3>{status.events.length ? <div className="activity-timeline">{status.events.map((item) => <article key={item.id}><strong>{item.event_type.replaceAll("_", " ")}</strong><span>{date(item.occurred_at)}</span><small>{item.cause ?? "Recorded persistent HA state change"}</small></article>)}</div> : <div className="empty-state compact-empty"><h3>No HA events</h3><p>Events appear after configuration and verified peer observations.</p></div>}
    </>}
    {editing && <form className="settings-grid" onSubmit={(event) => void save(event)}>
      <h3>Local node</h3><label>Node ID<input required value={form.local_node_id} onChange={(event) => change("local_node_id", event.target.value)} /></label><label>Name<input required value={form.local_name} onChange={(event) => change("local_name", event.target.value)} /></label><label>FQDN<input required value={form.local_fqdn} onChange={(event) => change("local_fqdn", event.target.value)} /></label><label>IP address<input required value={form.local_ip} onChange={(event) => change("local_ip", event.target.value)} /></label><label>Configured role<select value={form.local_role} onChange={(event) => { const role = event.target.value as "active" | "passive"; change("local_role", role); change("peer_role", role === "active" ? "passive" : "active"); }}><option value="active">Active</option><option value="passive">Passive</option></select></label>
      <h3>Peer node</h3><label>Peer node ID<input required value={form.peer_node_id} onChange={(event) => change("peer_node_id", event.target.value)} /></label><label>Peer name<input required value={form.peer_name} onChange={(event) => change("peer_name", event.target.value)} /></label><label>Peer FQDN<input required value={form.peer_fqdn} onChange={(event) => change("peer_fqdn", event.target.value)} /></label><label>Peer IP address<input required value={form.peer_ip} onChange={(event) => change("peer_ip", event.target.value)} /></label><label>Peer role<input readOnly value={form.peer_role} /></label><label>Floating/service IP (optional)<input value={form.service_ip ?? ""} onChange={(event) => change("service_ip", event.target.value || null)} /></label>
      <Notice tone="info" title="This configures HA-3 only">Saving records real identities and enables authenticated peer heartbeat status. Automatic ownership change and fencing remain unavailable until HA-6/HA-7 are implemented and validated.</Notice>{error && <Notice tone="danger" title="HA configuration rejected">{error}</Notice>}<div className="form-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(false)} disabled={saving}>Cancel</button><button className="button button-primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save node settings"}</button></div>
    </form>}
  </Card>;
}
