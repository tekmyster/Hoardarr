import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Drive, TopologyPlanDocument, TopologyPlanTemplate } from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

function newChangeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function capacityLabel(value: number | null): string {
  if (value === null) return "Capacity not specified";
  return `${(value / 1_000_000_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })} TB planned`;
}

export function TopologyPlanningPanel({ drives }: { drives: Drive[] }) {
  const [templates, setTemplates] = useState<TopologyPlanTemplate[]>([]);
  const [plans, setPlans] = useState<TopologyPlanDocument[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [name, setName] = useState("Future media storage");
  const [templateId, setTemplateId] = useState<TopologyPlanTemplate["id"]>("generic-12-bay");
  const [changeKind, setChangeKind] = useState<"disk_addition" | "disk_retirement">("disk_addition");
  const [changeLabel, setChangeLabel] = useState("Add media drive");
  const [enclosureId, setEnclosureId] = useState("");
  const [slot, setSlot] = useState("1");
  const [capacityTb, setCapacityTb] = useState("");
  const [retirementId, setRetirementId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextTemplates, nextPlans] = await Promise.all([api.topologyPlanTemplates(), api.topologyPlans()]);
      setTemplates(nextTemplates);
      setPlans(nextPlans);
      setActiveId((current) => current && nextPlans.some((item) => item.id === current) ? current : nextPlans[0]?.id ?? null);
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Topology plans could not be loaded.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const active = plans.find((item) => item.id === activeId) ?? null;
  useEffect(() => {
    const first = active?.plan.enclosures[0];
    if (first && !active.plan.enclosures.some((item) => item.id === enclosureId)) setEnclosureId(first.id);
  }, [active, enclosureId]);

  const occupied = useMemo(() => new Set(active?.plan.changes.filter((item) => item.kind === "disk_addition").map((item) => `${item.enclosure_id}:${item.slot}`) ?? []), [active]);
  const availableRetirements = drives.filter((drive) => drive.stableIdentity && !active?.plan.changes.some((item) => item.kind === "disk_retirement" && item.stable_device_id === drive.id));

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true); setError(null);
    try {
      const created = await api.createTopologyPlan(name.trim(), templateId);
      setPlans((current) => [created, ...current]);
      setActiveId(created.id);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Topology plan could not be created."); }
    finally { setBusy(false); }
  };

  const save = async (next: TopologyPlanDocument) => {
    setBusy(true); setError(null);
    try {
      const saved = await api.updateTopologyPlan(next);
      setPlans((current) => current.map((item) => item.id === saved.id ? saved : item));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Topology plan could not be saved."); }
    finally { setBusy(false); }
  };

  const addChange = async () => {
    if (!active || !changeLabel.trim()) return;
    const enclosure = active.plan.enclosures.find((item) => item.id === enclosureId);
    const slotNumber = Number(slot);
    const capacity = capacityTb.trim() ? Number(capacityTb) * 1_000_000_000_000 : null;
    if (changeKind === "disk_addition" && (!enclosure || !Number.isInteger(slotNumber) || slotNumber < 1 || slotNumber > enclosure.bay_count)) {
      setError("Choose an available bay within the planned enclosure."); return;
    }
    if (changeKind === "disk_addition" && occupied.has(`${enclosureId}:${slotNumber}`)) {
      setError("That bay already has a planned disk addition."); return;
    }
    if (capacity !== null && (!Number.isFinite(capacity) || capacity <= 0)) {
      setError("Planned capacity must be a positive number of TB."); return;
    }
    if (changeKind === "disk_retirement" && !retirementId) {
      setError("Choose a currently discovered drive to retire."); return;
    }
    await save({ ...active, plan: { ...active.plan, changes: [...active.plan.changes, {
      id: newChangeId(changeKind === "disk_addition" ? "add" : "retire"),
      kind: changeKind,
      label: changeLabel.trim(),
      enclosure_id: changeKind === "disk_addition" ? enclosureId : null,
      slot: changeKind === "disk_addition" ? slotNumber : null,
      capacity_bytes: changeKind === "disk_addition" ? capacity : null,
      stable_device_id: changeKind === "disk_retirement" ? retirementId : null,
    }] } });
  };

  const removeChange = async (changeId: string) => {
    if (active) await save({ ...active, plan: { ...active.plan, changes: active.plan.changes.filter((item) => item.id !== changeId) } });
  };

  const removePlan = async () => {
    if (!active) return;
    setBusy(true); setError(null);
    try { await api.removeTopologyPlan(active.id); await load(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Topology plan could not be removed."); }
    finally { setBusy(false); }
  };

  return <Card title="Planning topology" description="Model future chassis, shelves, controllers, drive additions, and retirements without changing live storage.">
    <Notice tone="info" title="Planning mode is separate from live discovery">Everything here is an operator-declared future design. It never creates hardware, changes placement, or appears as current telemetry.</Notice>
    {error && <Notice tone="danger" title="Planning change not saved">{error}</Notice>}
    {loading ? <Spinner label="Loading topology plans" /> : plans.length === 0 ? <div className="topology-plan-empty">
      <div className="empty-state compact-empty"><h3>No future layout has been planned</h3><p>Start with a generic layout, then add exact expansion and retirement intentions.</p></div>
      <div className="topology-plan-create"><label>Plan name<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} /></label><label>Starting layout<select value={templateId} onChange={(event) => setTemplateId(event.target.value as TopologyPlanTemplate["id"])}>{templates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><p>{templates.find((item) => item.id === templateId)?.description}</p><button type="button" className="button button-primary" disabled={busy || !name.trim()} onClick={() => void create()}>{busy ? "Creating…" : "Create planning layout"}</button></div>
    </div> : active && <div className="topology-plan-workspace">
      <div className="topology-plan-toolbar"><label>Plan<select value={active.id} onChange={(event) => setActiveId(event.target.value)}>{plans.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><StatusBadge status="planned" /><small>Revision {active.revision} · not live</small></div>
      <div className="topology-plan-map" aria-label={`${active.name} planned topology`}>
        <article className="topology-plan-chassis"><span>Planned chassis</span><strong>{active.plan.chassis.label}</strong></article>
        <div className="topology-plan-controllers">{active.plan.controllers.map((controller) => <article key={controller.id}><span>Controller</span><strong>{controller.label}</strong><small>{controller.state}</small></article>)}</div>
        <div className="topology-plan-enclosures">{active.plan.enclosures.map((enclosure) => <article key={enclosure.id}><header><div><span>Planned enclosure</span><strong>{enclosure.label}</strong></div><small>{enclosure.bay_count} bays · {enclosure.controller_ids.length} controller path{enclosure.controller_ids.length === 1 ? "" : "s"}</small></header><div className="topology-plan-bays">{Array.from({ length: enclosure.bay_count }, (_, index) => { const bay = index + 1; const addition = active.plan.changes.find((item) => item.kind === "disk_addition" && item.enclosure_id === enclosure.id && item.slot === bay); return <div key={bay} className={addition ? "planned-filled" : "planned-empty"}><span>Bay {String(bay).padStart(2, "0")}</span><strong>{addition?.label ?? "Open"}</strong>{addition && <small>{capacityLabel(addition.capacity_bytes)}</small>}</div>; })}</div></article>)}</div>
      </div>
      <section className="topology-plan-changes"><h3>Planned changes</h3>{active.plan.changes.length === 0 ? <p>No disk additions or retirements have been declared.</p> : active.plan.changes.map((change) => <article key={change.id}><div><StatusBadge status={change.kind === "disk_addition" ? "planned" : "warning"} /><strong>{change.label}</strong><small>{change.kind === "disk_addition" ? `${change.enclosure_id} · bay ${change.slot} · ${capacityLabel(change.capacity_bytes)}` : `Retire ${change.stable_device_id}`}</small></div><button type="button" className="button button-secondary" disabled={busy} onClick={() => void removeChange(change.id)}>Remove from plan</button></article>)}</section>
      <div className="topology-plan-editor"><label>Change type<select value={changeKind} onChange={(event) => setChangeKind(event.target.value as typeof changeKind)}><option value="disk_addition">Add a drive later</option><option value="disk_retirement">Retire an existing drive</option></select></label><label>Description<input value={changeLabel} maxLength={128} onChange={(event) => setChangeLabel(event.target.value)} /></label>{changeKind === "disk_addition" ? <><label>Enclosure<select value={enclosureId} onChange={(event) => setEnclosureId(event.target.value)}>{active.plan.enclosures.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><label>Bay<input type="number" min="1" max={active.plan.enclosures.find((item) => item.id === enclosureId)?.bay_count ?? 1} value={slot} onChange={(event) => setSlot(event.target.value)} /></label><label>Capacity (TB, optional)<input inputMode="decimal" value={capacityTb} onChange={(event) => setCapacityTb(event.target.value)} /></label></> : <label>Discovered drive<select value={retirementId} onChange={(event) => setRetirementId(event.target.value)}><option value="">Choose drive</option>{availableRetirements.map((drive) => <option key={drive.id} value={drive.id}>{drive.model} · {drive.serial}</option>)}</select></label>}<button type="button" className="button button-primary" disabled={busy || !changeLabel.trim()} onClick={() => void addChange()}>{busy ? "Saving…" : "Add to plan"}</button></div>
      <label>Planning notes<textarea value={active.plan.notes} maxLength={4096} onChange={(event) => setPlans((current) => current.map((item) => item.id === active.id ? { ...item, plan: { ...item.plan, notes: event.target.value } } : item))} /></label><div className="button-row"><button type="button" className="button button-secondary" disabled={busy} onClick={() => void save(active)}>Save notes</button><button type="button" className="button button-danger" disabled={busy} onClick={() => void removePlan()}>Delete planning layout</button></div>
    </div>}
  </Card>;
}
