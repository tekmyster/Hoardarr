import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { copyText } from "../clipboard";
import type {
  ConnectivityCapabilities,
  ConnectivityProtocol,
  ConnectivityServiceDocument,
  ConnectivityServiceInput,
} from "../types";
import { EyeIcon } from "./OneTimePassword";
import { Card, Field, Notice, Spinner, StatusBadge } from "./ui";

const PROTOCOLS: Array<{ id: ConnectivityProtocol; label: string }> = [
  { id: "smb", label: "SMB" },
  { id: "nfs", label: "NFS" },
  { id: "iscsi", label: "iSCSI" },
  { id: "fcoe", label: "FCoE" },
];

function csv(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function stringValue(config: Record<string, unknown>, key: string, fallback = ""): string {
  return typeof config[key] === "string" ? String(config[key]) : fallback;
}

function listValue(config: Record<string, unknown>, key: string): string {
  return Array.isArray(config[key]) ? (config[key] as unknown[]).map(String).join(", ") : "";
}

function targetValue(config: Record<string, unknown>): string {
  return stringValue(config, "path")
    || stringValue(config, "target_iqn")
    || listValue(config, "target_wwpns")
    || listValue(config, "interfaces");
}

function boolValue(config: Record<string, unknown>, key: string, fallback: boolean): boolean {
  return typeof config[key] === "boolean" ? Boolean(config[key]) : fallback;
}

type AclRole = "administrator" | "media_application" | "media_user";

function aclNames(
  config: Record<string, unknown>,
  kind: "user" | "group",
  role: AclRole,
): string {
  const acl = config.acl;
  if (!acl || typeof acl !== "object" || !Array.isArray((acl as { entries?: unknown }).entries)) return "";
  return ((acl as { entries: unknown[] }).entries)
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .filter((entry) => entry.kind === kind && entry.role === role && typeof entry.name === "string")
    .map((entry) => String(entry.name))
    .join(", ");
}

function PlusIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M12 5v14M5 12h14" /></svg>;
}

function GearIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 8.94 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.57 15 1.7 1.7 0 0 0 3 14H3v-4h.09A1.7 1.7 0 0 0 4.6 8.94a1.7 1.7 0 0 0-.34-1.88L4.2 7l2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.57 1.7 1.7 0 0 0 10 3V3h4v.09A1.7 1.7 0 0 0 15.06 4.6a1.7 1.7 0 0 0 1.88-.34L17 4.2 19.83 7l-.06.06A1.7 1.7 0 0 0 19.43 9 1.7 1.7 0 0 0 21 10h.09v4H21a1.7 1.7 0 0 0-1.6 1Z" /></svg>;
}

function TrashIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false"><path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6" /></svg>;
}

export function ConnectivityPage() {
  const [services, setServices] = useState<ConnectivityServiceDocument[]>([]);
  const [capabilities, setCapabilities] = useState<ConnectivityCapabilities | null>(null);
  const [editing, setEditing] = useState<ConnectivityServiceDocument | "new" | null>(null);
  const [protocol, setProtocol] = useState<ConnectivityProtocol>("smb");
  const [name, setName] = useState("media");
  const [path, setPath] = useState("/data/media");
  const [readOnly, setReadOnly] = useState(false);
  const [browseable, setBrowseable] = useState(true);
  const [writeUsers, setWriteUsers] = useState("media");
  const [readUsers, setReadUsers] = useState("");
  const [administratorUsers, setAdministratorUsers] = useState("");
  const [administratorGroups, setAdministratorGroups] = useState("");
  const [applicationGroups, setApplicationGroups] = useState("");
  const [mediaGroups, setMediaGroups] = useState("");
  const [inheritAcl, setInheritAcl] = useState(true);
  const [clients, setClients] = useState("192.168.0.0/16");
  const [backingPath, setBackingPath] = useState("/data/targets/media.img");
  const [sizeGiB, setSizeGiB] = useState("100");
  const [targetIqn, setTargetIqn] = useState("iqn.2026-08.local.hoardarr:media");
  const [portalIps, setPortalIps] = useState("0.0.0.0");
  const [initiatorIqns, setInitiatorIqns] = useState("");
  const [chapEnabled, setChapEnabled] = useState(true);
  const [chapUsername, setChapUsername] = useState("hoardarr");
  const [chapMode, setChapMode] = useState<"generate" | "provide" | "keep">("generate");
  const [chapPassword, setChapPassword] = useState("");
  const [fcoeInterfaces, setFcoeInterfaces] = useState<string[]>([]);
  const [fcoeMode, setFcoeMode] = useState<"fabric" | "vn2vn">("fabric");
  const [dcbMode, setDcbMode] = useState<"auto" | "host" | "firmware" | "none">("auto");
  const [autoVlan, setAutoVlan] = useState(true);
  const [fipResponder, setFipResponder] = useState(false);
  const [initiatorWwpns, setInitiatorWwpns] = useState("");
  const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);
  const [showGeneratedPassword, setShowGeneratedPassword] = useState(false);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [deleting, setDeleting] = useState<ConnectivityServiceDocument | null>(null);
  const [deleteBacking, setDeleteBacking] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const changing = useMemo(
    () => services.some((service) => ["pending", "removing"].includes(service.status)),
    [services],
  );

  async function refresh(): Promise<void> {
    const [foundServices, foundCapabilities] = await Promise.all([
      api.connectivityServices(),
      api.connectivityCapabilities(),
    ]);
    setServices(foundServices);
    setCapabilities(foundCapabilities);
  }

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function poll(): Promise<void> {
      try {
        await refresh();
        if (!stopped) setError(null);
      } catch (caught) {
        if (!stopped) setError(caught instanceof Error ? caught.message : "Storage access could not be loaded.");
      } finally {
        if (!stopped) timer = window.setTimeout(() => void poll(), changing ? 2_000 : 10_000);
      }
    }
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [changing]);

  function resetForm(nextProtocol: ConnectivityProtocol = "smb"): void {
    setProtocol(nextProtocol);
    setName("media");
    setPath("/data/media");
    setReadOnly(false);
    setBrowseable(true);
    setWriteUsers("media");
    setReadUsers("");
    setAdministratorUsers("");
    setAdministratorGroups("");
    setApplicationGroups("");
    setMediaGroups("");
    setInheritAcl(true);
    setClients("192.168.0.0/16");
    setBackingPath("/data/targets/media.img");
    setSizeGiB("100");
    setTargetIqn("iqn.2026-08.local.hoardarr:media");
    setPortalIps("0.0.0.0");
    setInitiatorIqns("");
    setChapEnabled(true);
    setChapUsername("hoardarr");
    setChapMode("generate");
    setChapPassword("");
    setFcoeInterfaces([]);
    setFcoeMode("fabric");
    setDcbMode("auto");
    setAutoVlan(true);
    setFipResponder(false);
    setInitiatorWwpns("");
    setGeneratedPassword(null);
    setPasswordSaved(false);
  }

  function startCreate(nextProtocol: ConnectivityProtocol = "smb"): void {
    resetForm(nextProtocol);
    setEditing("new");
    setError(null);
  }

  function startEdit(service: ConnectivityServiceDocument): void {
    const config = service.config;
    setEditing(service);
    setProtocol(service.protocol);
    setName(service.name);
    setPath(stringValue(config, "path", "/data/media"));
    setReadOnly(boolValue(config, "read_only", false));
    setBrowseable(boolValue(config, "browseable", true));
    const legacyUsers = listValue(config, "valid_users");
    setWriteUsers(listValue(config, "write_users") || (boolValue(config, "read_only", false) ? "" : legacyUsers));
    setReadUsers(listValue(config, "read_users") || (boolValue(config, "read_only", false) ? legacyUsers : ""));
    setAdministratorUsers(aclNames(config, "user", "administrator"));
    setAdministratorGroups(aclNames(config, "group", "administrator"));
    setApplicationGroups(aclNames(config, "group", "media_application"));
    setMediaGroups(aclNames(config, "group", "media_user"));
    const acl = config.acl;
    setInheritAcl(
      acl && typeof acl === "object" && typeof (acl as { inherit?: unknown }).inherit === "boolean"
        ? Boolean((acl as { inherit: boolean }).inherit)
        : true,
    );
    setClients(listValue(config, "clients"));
    setBackingPath(stringValue(config, "backing_path", "/data/targets/media.img"));
    const bytes = typeof config.size_bytes === "number" ? config.size_bytes : 100 * 1024 ** 3;
    setSizeGiB(String(Math.round(bytes / 1024 ** 3)));
    setTargetIqn(stringValue(config, "target_iqn"));
    setPortalIps(listValue(config, "portal_ips"));
    setInitiatorIqns(listValue(config, "initiator_iqns"));
    setChapEnabled(boolValue(config, "chap_enabled", true));
    setChapUsername(stringValue(config, "chap_username", "hoardarr"));
    setChapMode("keep");
    setChapPassword("");
    setFcoeInterfaces(Array.isArray(config.interfaces) ? config.interfaces.map(String) : []);
    setFcoeMode(stringValue(config, "fcoe_mode", "fabric") as "fabric" | "vn2vn");
    setDcbMode(stringValue(config, "dcb_mode", "auto") as typeof dcbMode);
    setAutoVlan(boolValue(config, "auto_vlan", true));
    setFipResponder(boolValue(config, "fip_responder", false));
    setInitiatorWwpns(listValue(config, "initiator_wwpns"));
    setGeneratedPassword(null);
    setPasswordSaved(false);
    setError(null);
  }

  function payload(): ConnectivityServiceInput {
    if (protocol === "smb") {
      const writers = csv(writeUsers);
      const readers = csv(readUsers).filter((user) => !writers.includes(user));
      const allowed = [...new Set([...writers, ...readers])];
      const aclEntries: NonNullable<ConnectivityServiceInput["acl_entries"]> = [
        ...csv(administratorUsers).map((entry) => ({ kind: "user" as const, name: entry, role: "administrator" as const })),
        ...csv(administratorGroups).map((entry) => ({ kind: "group" as const, name: entry, role: "administrator" as const })),
        ...writers.map((entry) => ({ kind: "user" as const, name: entry, role: "media_application" as const })),
        ...csv(applicationGroups).map((entry) => ({ kind: "group" as const, name: entry, role: "media_application" as const })),
        ...readers.map((entry) => ({ kind: "user" as const, name: entry, role: "media_user" as const })),
        ...csv(mediaGroups).map((entry) => ({ kind: "group" as const, name: entry, role: "media_user" as const })),
      ];
      if (!aclEntries.length) throw new Error("Add at least one SMB user or group.");
      const identities = new Set<string>();
      for (const entry of aclEntries) {
        const identity = `${entry.kind}:${entry.name}`;
        if (identities.has(identity)) throw new Error(`${entry.name} has more than one SMB access level.`);
        identities.add(identity);
      }
      return {
        protocol,
        name,
        path,
        read_only: !writers.length && !csv(applicationGroups).length && !csv(administratorUsers).length && !csv(administratorGroups).length,
        browseable,
        valid_users: allowed,
        write_users: writers,
        read_users: readers,
        acl_entries: aclEntries,
        inherit_acl: inheritAcl,
      };
    }
    if (protocol === "nfs") {
      return { protocol, name, path, read_only: readOnly, clients: csv(clients) };
    }
    const parsedSize = Number(sizeGiB);
    if (!Number.isFinite(parsedSize) || parsedSize < 1) throw new Error("Enter a valid size.");
    if (protocol === "iscsi") {
      return {
        protocol,
        name,
        backing_path: backingPath,
        size_bytes: Math.round(parsedSize * 1024 ** 3),
        target_iqn: targetIqn,
        portal_ips: csv(portalIps),
        initiator_iqns: csv(initiatorIqns),
        chap_enabled: chapEnabled,
        chap_username: chapEnabled ? chapUsername : undefined,
        chap_password: chapEnabled && chapMode === "provide" ? chapPassword : undefined,
        generate_chap_password: chapEnabled && chapMode === "generate",
      };
    }
    return {
      protocol,
      name,
      backing_path: backingPath,
      size_bytes: Math.round(parsedSize * 1024 ** 3),
      interfaces: fcoeInterfaces,
      fcoe_mode: fcoeMode,
      dcb_mode: dcbMode,
      auto_vlan: autoVlan,
      fip_responder: fipResponder,
      initiator_wwpns: csv(initiatorWwpns),
    };
  }

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const input = payload();
      const result = editing === "new"
        ? await api.createConnectivityService(input)
        : await api.updateConnectivityService(editing!.id, input);
      await refresh();
      if (result.generated_password) {
        setGeneratedPassword(result.generated_password);
        setPasswordSaved(false);
      } else {
        setEditing(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Storage access could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    if (!deleting || deleteConfirmation !== "I AGREE") return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteConnectivityService(deleting.id, deleteBacking);
      setDeleting(null);
      setDeleteConfirmation("");
      setDeleteBacking(false);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Storage access could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="connectivity-page">
    {error && <Notice tone="danger" title="Storage access request failed">{error}</Notice>}
    {!capabilities ? <Card><Spinner label="Loading…" /></Card> : <div className="storage-access-panels">{PROTOCOLS.map((item) => {
      const protocolServices = services.filter((service) => service.protocol === item.id);
      const available = capabilities.protocols[item.id].available;
      return <Card
        key={item.id}
        title={item.label}
        className="storage-access-panel"
        actions={<button type="button" className="icon-button icon-button-primary" aria-label={`Add ${item.label} storage access`} title={`Add ${item.label}`} disabled={!available} onClick={() => startCreate(item.id)}><PlusIcon /></button>}
      >
        <div className={`storage-access-availability ${available ? "available" : "unavailable"}`}>{available ? "Ready" : "Unavailable"}</div>
        {!protocolServices.length ? <div className="empty-state compact-empty"><h3>No {item.label} entries</h3></div> : <div className="connectivity-services">{protocolServices.map((service) => <article className="connectivity-service-card" key={service.id}>
          <header><strong>{service.name}</strong><StatusBadge status={service.status} /></header>
          <code>{targetValue(service.config)}</code>
          {service.error?.message && <small className="field-error">{service.error.message}</small>}
          <footer className="connectivity-row-actions"><button type="button" className="icon-button" aria-label={`Edit ${item.label} ${service.name}`} title="Settings" disabled={["pending", "removing"].includes(service.status)} onClick={() => startEdit(service)}><GearIcon /></button><button type="button" className="icon-button icon-button-danger" aria-label={`Remove ${item.label} ${service.name}`} title="Remove" disabled={["pending", "removing"].includes(service.status)} onClick={() => setDeleting(service)}><TrashIcon /></button></footer>
        </article>)}</div>}
      </Card>;
    })}</div>}

    {editing && <div className="modal-backdrop" role="presentation"><section className="connectivity-editor" role="dialog" aria-modal="true" aria-labelledby="connectivity-editor-title">
      <header><h2 id="connectivity-editor-title">{editing === "new" ? "Add storage access" : "Edit storage access"}</h2><button type="button" className="dialog-close" aria-label="Close" disabled={busy || Boolean(generatedPassword && !passwordSaved)} onClick={() => setEditing(null)}>×</button></header>
      <div className="connectivity-editor-body">
        {generatedPassword ? <div className="generated-connectivity-password">
          <Field label="CHAP password"><div className="input-action"><input readOnly type={showGeneratedPassword ? "text" : "password"} value={generatedPassword} onFocus={(event) => event.currentTarget.select()} /><button type="button" className="credential-eye-button" aria-label={showGeneratedPassword ? "Hide password" : "Show password"} onClick={() => setShowGeneratedPassword((value) => !value)}><EyeIcon crossed={showGeneratedPassword} /></button><button type="button" className="button button-secondary" onClick={() => void copyText(generatedPassword)}>Copy</button></div></Field>
          <button type="button" className="button button-primary" onClick={() => { setPasswordSaved(true); setGeneratedPassword(null); setEditing(null); }}>I saved it</button>
        </div> : <>
          <div className="form-grid two-columns">
            <Field label="Type"><select value={protocol} disabled={editing !== "new"} onChange={(event) => resetForm(event.target.value as ConnectivityProtocol)}>{PROTOCOLS.map((item) => <option key={item.id} value={item.id} disabled={!capabilities?.protocols[item.id].available}>{item.label}</option>)}</select></Field>
            <Field label="Name"><input value={name} pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,62}" onChange={(event) => setName(event.target.value)} /></Field>
          </div>
          {(protocol === "smb" || protocol === "nfs") && <div className="form-grid two-columns">
            <Field label="Folder"><input value={path} onChange={(event) => setPath(event.target.value)} /></Field>
            {protocol === "smb" ? <>
              <Field label="Administrators" hint="Users with full control, comma separated"><input value={administratorUsers} onChange={(event) => setAdministratorUsers(event.target.value)} /></Field>
              <Field label="Administrator groups" hint="Groups with full control, comma separated"><input value={administratorGroups} onChange={(event) => setAdministratorGroups(event.target.value)} /></Field>
              <Field label="Media applications" hint="Users that can read and change files"><input value={writeUsers} onChange={(event) => setWriteUsers(event.target.value)} /></Field>
              <Field label="Media application groups" hint="Groups that can read and change files"><input value={applicationGroups} onChange={(event) => setApplicationGroups(event.target.value)} /></Field>
              <Field label="Media users" hint="Users with read-only access"><input value={readUsers} onChange={(event) => setReadUsers(event.target.value)} /></Field>
              <Field label="Media user groups" hint="Groups with read-only access"><input value={mediaGroups} onChange={(event) => setMediaGroups(event.target.value)} /></Field>
              <label className="check-row"><input type="checkbox" checked={inheritAcl} onChange={(event) => setInheritAcl(event.target.checked)} /> Apply these permissions to new files and folders</label>
              <p className="field-hint">Anonymous access is denied.</p>
            </> : <><Field label="Allowed networks" hint="Comma separated"><input value={clients} onChange={(event) => setClients(event.target.value)} /></Field><label className="check-row"><input type="checkbox" checked={readOnly} onChange={(event) => setReadOnly(event.target.checked)} /> Read only</label></>}
            {protocol === "smb" && <label className="check-row"><input type="checkbox" checked={browseable} onChange={(event) => setBrowseable(event.target.checked)} /> Show in network browsing</label>}
          </div>}
          {(protocol === "iscsi" || protocol === "fcoe") && <div className="form-grid two-columns">
            <Field label="Backing file"><input value={backingPath} onChange={(event) => setBackingPath(event.target.value)} /></Field>
            <Field label="Size (GiB)"><input type="number" min="1" value={sizeGiB} onChange={(event) => setSizeGiB(event.target.value)} /></Field>
            {protocol === "iscsi" ? <>
              <Field label="Target IQN"><input value={targetIqn} onChange={(event) => setTargetIqn(event.target.value)} /></Field>
              <Field label="Portal IPs" hint="Comma separated"><input value={portalIps} onChange={(event) => setPortalIps(event.target.value)} /></Field>
              <Field label="Initiator IQNs" hint="Comma separated"><textarea value={initiatorIqns} onChange={(event) => setInitiatorIqns(event.target.value)} /></Field>
              <label className="check-row"><input type="checkbox" checked={chapEnabled} onChange={(event) => setChapEnabled(event.target.checked)} /> CHAP authentication</label>
              {chapEnabled && <><Field label="CHAP username"><input value={chapUsername} onChange={(event) => setChapUsername(event.target.value)} /></Field><Field label="CHAP password"><select value={chapMode} onChange={(event) => setChapMode(event.target.value as typeof chapMode)}>{editing !== "new" && <option value="keep">Keep current</option>}<option value="generate">Generate</option><option value="provide">Set password</option></select></Field>{chapMode === "provide" && <Field label="Password"><input type="password" value={chapPassword} onChange={(event) => setChapPassword(event.target.value)} /></Field>}</>}
            </> : <>
              <Field label="Network ports">
                <div className="fcoe-interface-list">
                  {capabilities?.fcoe_interfaces?.length ? capabilities.fcoe_interfaces.map((item) => <label className="fcoe-interface" key={item.name}>
                    <input
                      type="checkbox"
                      checked={fcoeInterfaces.includes(item.name)}
                      onChange={(event) => setFcoeInterfaces((current) => event.target.checked
                        ? [...current, item.name]
                        : current.filter((name) => name !== item.name))}
                    />
                    <span><strong>{item.name}</strong><small>{item.driver}{item.speed_mbps ? ` · ${item.speed_mbps / 1000} Gb/s` : ""} · {item.online ? "Online" : "Offline"}</small></span>
                    <code>{item.target_wwpn}</code>
                  </label>) : <Notice tone="warning" title="No FCoE network ports">Install a supported network adapter to enable FCoE.</Notice>}
                </div>
              </Field>
              <Field label="Connection mode"><select value={fcoeMode} onChange={(event) => {
                const mode = event.target.value as typeof fcoeMode;
                setFcoeMode(mode);
                setAutoVlan(mode === "fabric");
                setFipResponder(mode === "vn2vn");
              }}><option value="fabric">Cisco Nexus fabric</option><option value="vn2vn">Direct connection</option></select></Field>
              <Field label="DCB control"><select value={dcbMode} onChange={(event) => setDcbMode(event.target.value as typeof dcbMode)}><option value="auto">Automatic</option><option value="host">Managed by Hoardarr</option><option value="firmware">Managed by adapter</option><option value="none">Disabled</option></select></Field>
              {fcoeMode === "fabric" && <label className="check-row"><input type="checkbox" checked={autoVlan} onChange={(event) => setAutoVlan(event.target.checked)} /> Discover FCoE VLAN automatically</label>}
              {fcoeMode === "vn2vn" && <label className="check-row"><input type="checkbox" checked={fipResponder} onChange={(event) => setFipResponder(event.target.checked)} /> Respond to FIP discovery</label>}
              <Field label="Allowed initiator WWPNs" hint="Comma separated"><textarea value={initiatorWwpns} onChange={(event) => setInitiatorWwpns(event.target.value)} /></Field>
            </>}
          </div>}
        </>}
      </div>
      {!generatedPassword && <footer><button type="button" className="button button-secondary" disabled={busy} onClick={() => setEditing(null)}>Cancel</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Apply"}</button></footer>}
    </section></div>}

    {deleting && <div className="modal-backdrop" role="presentation"><section className="connectivity-editor delete-connectivity" role="dialog" aria-modal="true" aria-labelledby="delete-connectivity-title">
      <header><h2 id="delete-connectivity-title">Remove {deleting.name}</h2><button type="button" className="dialog-close" aria-label="Close" onClick={() => setDeleting(null)}>×</button></header>
      <div className="connectivity-editor-body"><Notice tone="danger" title="ARE YOU SURE?">This disconnects the service.</Notice>{["iscsi", "fcoe"].includes(deleting.protocol) && <label className="check-row danger-option"><input type="checkbox" checked={deleteBacking} onChange={(event) => setDeleteBacking(event.target.checked)} /> Delete backing file</label>}<Field label="Type I AGREE"><input className="consent-input" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} /></Field></div>
      <footer><button type="button" className="button button-secondary" onClick={() => setDeleting(null)}>Cancel</button><button type="button" className="button button-danger" disabled={busy || deleteConfirmation !== "I AGREE"} onClick={() => void remove()}>{busy ? "Removing…" : "Remove"}</button></footer>
    </section></div>}
  </div>;
}
