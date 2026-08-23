import type { CSSProperties } from "react";
import { humanCapacity } from "../policy";
import type {
  StorageEnclosure,
  StorageTopology,
  StorageTopologyLink,
  StorageTopologyNode,
  StorageTopologyProtocol,
} from "../types";
import { Card, StatusBadge } from "./ui";

const PROTOCOL_LABELS: StorageTopologyProtocol[] = ["SAS", "SATA", "FC", "FCoE", "NVMe", "USB", "SCSI", "Logical"];

function protocolClass(protocol: StorageTopologyProtocol | undefined): string {
  return (protocol ?? "SCSI").toLowerCase();
}

function speedLabel(negotiated?: number | null, capable?: number | null): string {
  if (negotiated != null && capable != null) return `${negotiated} Gb/s negotiated · ${capable} Gb/s capable`;
  if (negotiated != null) return `${negotiated} Gb/s negotiated`;
  if (capable != null) return `${capable} Gb/s capable`;
  return "Speed not reported";
}

function linkWidth(speed: number | null | undefined): number {
  if (speed == null || speed <= 0) return 2;
  if (speed <= 1.5) return 2;
  if (speed <= 3) return 3;
  if (speed <= 6) return 4;
  if (speed <= 12) return 6;
  if (speed <= 24) return 8;
  if (speed <= 40) return 10;
  if (speed <= 100) return 12;
  return 14;
}

function fillPercent(node: StorageTopologyNode): number | null {
  if (node.used_bytes == null || node.usable_bytes == null || node.usable_bytes <= 0) return null;
  return Math.min(100, Math.max(0, Math.round((node.used_bytes / node.usable_bytes) * 100)));
}

function health(node: StorageTopologyNode): "healthy" | "warning" | "critical" | "unknown" {
  if (node.temperature_c != null && node.temperature_c >= 65) return "critical";
  if (node.temperature_c != null && node.temperature_c >= 55 && node.health_status !== "critical") return "warning";
  return node.health_status ?? "unknown";
}

function mappingLabel(confidence: StorageTopologyNode["mapping_confidence"]): string {
  if (confidence === "high") return "Confirmed";
  if (confidence === "medium" || confidence === "low") return "Inferred";
  return "Not reported";
}

function DriveBay({ node, slot, status, locate, fault, mappingSource, mappingConfidence, mappingLastConfirmedAt }: { node: StorageTopologyNode | undefined; slot: string | null; status?: string | null; locate?: boolean | null; fault?: boolean | null; mappingSource?: string | null; mappingConfidence?: StorageTopologyNode["mapping_confidence"]; mappingLastConfirmedAt?: string | null }) {
  if (!node) return <article className="shelf-bay shelf-bay-empty">
    <header><span>Bay {slot ?? "—"} · {mappingLabel(mappingConfidence)}</span><small>{status ?? "Empty"}</small></header>
    <strong>Empty</strong>
    <small title={mappingLastConfirmedAt ?? undefined}>{mappingSource ?? "No trustworthy bay mapping source was reported"}</small>
  </article>;
  const driveHealth = health(node);
  const fill = fillPercent(node);
  return <article
    className={`shelf-bay health-${driveHealth}${fill != null && fill >= 90 ? " nearly-full" : ""}`}
    style={{ "--drive-fill": `${fill ?? 0}%` } as CSSProperties}
  >
    <span className="shelf-bay-fill" aria-hidden="true" />
    <header><span>Bay {slot ?? "—"} · {mappingLabel(mappingConfidence ?? node.mapping_confidence)}</span>{node.system_disk ? <span className="system-drive-badge">System</span> : <StatusBadge status={driveHealth} />}</header>
    <strong title={node.label}>{node.label}</strong>
    <code title={node.serial}>{node.serial ?? "Serial not reported"}</code>
    <div className="shelf-bay-meta"><span>{node.capacity_bytes ? humanCapacity(node.capacity_bytes) : "Not reported"}</span><span>{fill == null ? "Not reported" : `${fill}% used`}</span></div>
    <div className="shelf-bay-meta"><span>{node.temperature_c == null ? "Not reported" : `${node.temperature_c} °C`}</span><span>{node.negotiated_speed_gbps == null ? node.protocol : `${node.negotiated_speed_gbps} Gb/s`}</span></div>
    {(locate != null || fault != null) && <div className="shelf-bay-meta"><span>Locate {locate == null ? "Not reported" : locate ? "On" : "Off"}</span><span>Fault {fault == null ? "Not reported" : fault ? "On" : "Off"}</span></div>}
    <small title={mappingLastConfirmedAt ?? node.mapping_last_confirmed_at ?? undefined}>{mappingSource ?? node.mapping_source ?? "No trustworthy bay mapping source was reported"}</small>
  </article>;
}

function EnclosureMap({ enclosure, nodes }: { enclosure: StorageEnclosure; nodes: Map<string, StorageTopologyNode> }) {
  return <article className="storage-enclosure">
    <header>
      <div><strong>{enclosure.label}</strong><code>{enclosure.address}</code></div>
      <div className="enclosure-protocols">{enclosure.protocols.map((protocol) => <span className={`protocol-chip protocol-${protocolClass(protocol)}`} key={protocol}>{protocol}</span>)}</div>
    </header>
    <div className="shelf-bays">{enclosure.bays.map((bay, index) => <DriveBay key={`${bay.slot ?? "slot"}-${index}`} node={bay.drive_id ? nodes.get(bay.drive_id) : undefined} slot={bay.slot} status={bay.status} locate={bay.locate} fault={bay.fault} mappingSource={bay.mapping_source} mappingConfidence={bay.mapping_confidence} mappingLastConfirmedAt={bay.mapping_last_confirmed_at} />)}</div>
  </article>;
}

function LinkRail({ link }: { link: StorageTopologyLink }) {
  const speed = link.negotiated_speed_gbps ?? link.capable_speed_gbps;
  return <div
    className={`topology-link protocol-${protocolClass(link.protocol)}`}
    style={{ "--link-width": `${linkWidth(speed)}px` } as CSSProperties}
  ><span>{link.protocol}</span><small>{speedLabel(link.negotiated_speed_gbps, link.capable_speed_gbps)}</small></div>;
}

type TopologyDriveAction = "configure" | "test" | "import";

function DriveNode({ node, actionable, managed, onDriveAction, onManageLifecycle }: { node: StorageTopologyNode; actionable: boolean; managed: boolean; onDriveAction?: (action: TopologyDriveAction, driveId: string) => void; onManageLifecycle?: () => void }) {
  const driveHealth = health(node);
  const belowCapability = node.negotiated_speed_gbps != null && node.capable_speed_gbps != null && node.negotiated_speed_gbps < node.capable_speed_gbps;
  return <article className={`topology-drive health-${driveHealth}`}>
    <header><div><strong>{node.label}</strong><code>{node.serial}</code></div><StatusBadge status={driveHealth} /></header>
    <dl>
      <div><dt>Size</dt><dd>{node.capacity_bytes ? humanCapacity(node.capacity_bytes) : "Not reported"}</dd></div>
      <div><dt>Path</dt><dd><code>{node.path ?? "Not reported"}</code></dd></div>
      <div><dt>Protocol</dt><dd>{node.protocol ?? "Not reported"}</dd></div>
      <div><dt>Controller</dt><dd><code>{node.controller_id ?? "Not reported"}</code></dd></div>
      <div><dt>Enclosure</dt><dd><code>{node.enclosure_id ?? "Not reported"}</code></dd></div>
      <div><dt>Bay</dt><dd>{node.slot ?? "Not reported"}</dd></div>
      <div><dt>Bay mapping</dt><dd>{mappingLabel(node.mapping_confidence)}{node.mapping_source ? ` · ${node.mapping_source}` : ""}</dd></div>
      <div><dt>Last confirmed</dt><dd>{node.mapping_last_confirmed_at ? new Date(node.mapping_last_confirmed_at).toLocaleString() : "Not reported"}</dd></div>
      <div><dt>Capable</dt><dd>{node.capable_speed_gbps == null ? "Not reported" : `${node.capable_speed_gbps} Gb/s`}</dd></div>
      <div><dt>Negotiated</dt><dd>{node.negotiated_speed_gbps == null ? "Not reported" : `${node.negotiated_speed_gbps} Gb/s`}</dd></div>
      <div><dt>Temperature</dt><dd>{node.temperature_c == null ? "Not reported" : `${node.temperature_c} °C`}</dd></div>
      <div><dt>Use</dt><dd>{node.system_disk ? "System" : fillPercent(node) == null ? "Not reported" : `${fillPercent(node)}%`}</dd></div>
    </dl>
    {belowCapability && <p className="inline-notice warning">This link is operating at {node.negotiated_speed_gbps} Gb/s while the reported path capability is {node.capable_speed_gbps} Gb/s. A lower-rate drive or intermediate link can make this normal.</p>}
    {node.system_disk !== true && <div className="button-row topology-drive-actions" aria-label={`Actions for ${node.label}`}>
      {managed
        ? <button type="button" className="button button-secondary" onClick={onManageLifecycle}>Manage lifecycle</button>
        : actionable && node.stable_identity && <>
          <button type="button" className="button button-secondary" onClick={() => onDriveAction?.("test", node.stable_identity!)}>Run health tests</button>
          <button type="button" className="button button-secondary" onClick={() => onDriveAction?.("import", node.stable_identity!)}>Review existing data</button>
          <button type="button" className="button button-primary" onClick={() => onDriveAction?.("configure", node.stable_identity!)}>Set up this drive</button>
        </>}
    </div>}
  </article>;
}

function PhysicalNode({ node }: { node: StorageTopologyNode }) {
  return <article className={`topology-logical-node topology-${node.kind}`}>
    <header><div><span>{node.kind === "phy" ? "SAS PHY" : node.kind}</span><strong>{node.label}</strong>{node.address && <code>{node.address}</code>}</div><StatusBadge status={node.status ?? "detected"} /></header>
    {node.kind === "phy" && <dl>
      <div><dt>SAS address</dt><dd><code>{node.sas_address ?? "Not reported"}</code></dd></div>
      <div><dt>PHY identifier</dt><dd>{node.phy_identifier ?? "Not reported"}</dd></div>
      <div><dt>Link rates</dt><dd>{speedLabel(node.negotiated_speed_gbps, node.capable_speed_gbps)}{node.minimum_speed_gbps == null ? "" : ` · ${node.minimum_speed_gbps} Gb/s minimum`}</dd></div>
      <div><dt>Invalid DWORDs</dt><dd>{node.invalid_dwords ?? "Not reported"}</dd></div>
      <div><dt>Disparity errors</dt><dd>{node.disparity_errors ?? "Not reported"}</dd></div>
      <div><dt>Loss of sync</dt><dd>{node.loss_of_sync ?? "Not reported"}</dd></div>
      <div><dt>Reset problems</dt><dd>{node.reset_problems ?? "Not reported"}</dd></div>
    </dl>}
  </article>;
}

function LogicalNode({ node }: { node: StorageTopologyNode }) {
  return <article className={`topology-logical-node topology-${node.kind}`}><header><div><span>{node.kind}</span><strong>{node.label}</strong>{node.path && <code>{node.path}</code>}</div><StatusBadge status={node.status ?? node.health_status ?? "configured"} /></header>{node.pool_type && <small>{node.pool_type}</small>}</article>;
}

function TopologyBranch({ link, target, linksBySource, nodes, actionableDriveIds, managedDriveIds, onDriveAction, onManageLifecycle, visited = new Set<string>() }: { link: StorageTopologyLink; target: StorageTopologyNode; linksBySource: Map<string, StorageTopologyLink[]>; nodes: Map<string, StorageTopologyNode>; actionableDriveIds: ReadonlySet<string>; managedDriveIds: ReadonlySet<string>; onDriveAction?: (action: TopologyDriveAction, driveId: string) => void; onManageLifecycle?: () => void; visited?: Set<string> }) {
  if (visited.has(target.id)) return null;
  const nextVisited = new Set(visited).add(target.id);
  const childLinks = linksBySource.get(target.id) ?? [];
  return <div className="topology-branch">
    <LinkRail link={link} />
    {target.kind === "drive" ? <><DriveNode node={target} actionable={Boolean(target.stable_identity && actionableDriveIds.has(target.stable_identity))} managed={Boolean(target.stable_identity && managedDriveIds.has(target.stable_identity))} onDriveAction={onDriveAction} onManageLifecycle={onManageLifecycle} />{childLinks.map((child) => { const childNode = nodes.get(child.target); return childNode ? <TopologyBranch key={child.id} link={child} target={childNode} linksBySource={linksBySource} nodes={nodes} actionableDriveIds={actionableDriveIds} managedDriveIds={managedDriveIds} onDriveAction={onDriveAction} onManageLifecycle={onManageLifecycle} visited={nextVisited} /> : null; })}</> : target.kind !== "enclosure" ? <>{["port", "phy", "expander", "path"].includes(target.kind) ? <PhysicalNode node={target} /> : <LogicalNode node={target} />}{childLinks.map((child) => { const childNode = nodes.get(child.target); return childNode ? <TopologyBranch key={child.id} link={child} target={childNode} linksBySource={linksBySource} nodes={nodes} actionableDriveIds={actionableDriveIds} managedDriveIds={managedDriveIds} onDriveAction={onDriveAction} onManageLifecycle={onManageLifecycle} visited={nextVisited} /> : null; })}</> : <article className="topology-enclosure-node">
      <header><div><strong>{target.label}</strong><code>{target.address}</code></div><StatusBadge status={target.status ?? "detected"} /></header>
      <div className="topology-enclosure-drives">{childLinks.map((child) => {
        const childNode = nodes.get(child.target);
        return childNode ? <TopologyBranch key={child.id} link={child} target={childNode} linksBySource={linksBySource} nodes={nodes} actionableDriveIds={actionableDriveIds} managedDriveIds={managedDriveIds} onDriveAction={onDriveAction} onManageLifecycle={onManageLifecycle} visited={nextVisited} /> : null;
      })}</div>
    </article>}
  </div>;
}

export function StorageTopologyPanels({ topology, actionableDriveIds = new Set<string>(), managedDriveIds = new Set<string>(), onDriveAction, onManageLifecycle }: { topology: StorageTopology | null | undefined; actionableDriveIds?: ReadonlySet<string>; managedDriveIds?: ReadonlySet<string>; onDriveAction?: (action: TopologyDriveAction, driveId: string) => void; onManageLifecycle?: () => void }) {
  const nodes = new Map((topology?.nodes ?? []).map((node) => [node.id, node]));
  const links = topology?.links ?? [];
  const linksBySource = new Map<string, StorageTopologyLink[]>();
  links.forEach((link) => linksBySource.set(link.source, [...(linksBySource.get(link.source) ?? []), link]));
  const controllers = (topology?.nodes ?? []).filter((node) => node.kind === "controller");
  const directDrives = (topology?.direct_attached_drive_ids ?? []).map((id) => nodes.get(id)).filter((node): node is StorageTopologyNode => Boolean(node) && node?.system_disk !== true);
  const visibleProtocols = PROTOCOL_LABELS.filter((protocol) => links.some((link) => link.protocol === protocol));

  return <>
    <Card title="Attached storage" description="Physical shelves, bays, drive identity, capacity, temperature, and health.">
      {topology?.enclosures.length ? <div className="storage-enclosures">{topology.enclosures.map((enclosure) => <EnclosureMap key={enclosure.id} enclosure={enclosure} nodes={nodes} />)}</div> : <div className="empty-state compact-empty"><h3>No storage enclosure was reported</h3><p>Direct-attached drives remain visible below and in the topology.</p></div>}
      {directDrives.length > 0 && <article className="storage-enclosure direct-attached-enclosure"><header><div><strong>Direct-attached drives</strong><span>No enclosure or shelf was reported</span></div></header><div className="shelf-bays">{directDrives.map((node, index) => <DriveBay key={node.id} node={node} slot={node.slot ?? String(index + 1)} />)}</div></article>}
    </Card>

    <Card title="Storage topology" description="Controller, transport, enclosure, and disk paths with actual reported link rates.">
      {visibleProtocols.length > 0 && <div className="topology-legend" aria-label="Connection legend">{visibleProtocols.map((protocol) => <span className={`protocol-${protocolClass(protocol)}`} key={protocol}><i />{protocol}</span>)}<span className="topology-width-legend"><i className="thin" />Lower speed<i className="thick" />Higher speed</span></div>}
      {!controllers.length ? <div className="empty-state compact-empty"><h3>No storage topology is available</h3><p>Run Scan now after controllers and attached storage are visible to the operating system.</p></div> : <div className="storage-topology-tree">{controllers.map((controller) => {
        const controllerLinks = linksBySource.get(controller.id) ?? [];
        return <article className="topology-controller" key={controller.id}>
          <header><div><span>{controller.protocol ?? "Storage controller"}</span><strong>{controller.label}</strong><code>{controller.address}</code></div><StatusBadge status={controller.status ?? "detected"} /></header>
          <div className="controller-details"><span>{controller.driver ?? "Not reported"}</span><span>{speedLabel(controller.negotiated_speed_gbps, controller.capable_speed_gbps)}</span></div>
          <div className="topology-controller-branches">{controllerLinks.map((link) => {
            const target = nodes.get(link.target);
            return target ? <TopologyBranch key={link.id} link={link} target={target} linksBySource={linksBySource} nodes={nodes} actionableDriveIds={actionableDriveIds} managedDriveIds={managedDriveIds} onDriveAction={onDriveAction} onManageLifecycle={onManageLifecycle} /> : null;
          })}</div>
        </article>;
      })}</div>}
    </Card>
  </>;
}
