export const DASHBOARD_PANEL_IDS = [
  "system",
  "performance",
  "storage-performance",
  "storage",
  "drive-health",
  "network",
  "neighbors",
  "alerts",
  "activity",
  "applications",
  "shares",
] as const;

export type DashboardPanelId = (typeof DASHBOARD_PANEL_IDS)[number];

export const DEFAULT_DASHBOARD_PANELS: DashboardPanelId[] = [
  "system",
  "performance",
  "storage-performance",
  "storage",
  "drive-health",
  "network",
  "neighbors",
  "alerts",
  "activity",
];

export const DASHBOARD_LAYOUT_KEY = "hoardarr.overview.layout.v1";

function isPanelId(value: unknown): value is DashboardPanelId {
  return typeof value === "string" && (DASHBOARD_PANEL_IDS as readonly string[]).includes(value);
}

export function loadDashboardPanels(raw: string | null): DashboardPanelId[] {
  if (raw === null) return [...DEFAULT_DASHBOARD_PANELS];
  try {
    const parsed = JSON.parse(raw) as { version?: unknown; panels?: unknown };
    if (!Array.isArray(parsed.panels) || ![1, 2, 3].includes(Number(parsed.version))) return [...DEFAULT_DASHBOARD_PANELS];
    const panels = parsed.panels.filter(isPanelId).filter((panel, index, items) => items.indexOf(panel) === index);
    if (parsed.version === 1 && panels.length && !panels.includes("neighbors")) {
      const networkIndex = panels.indexOf("network");
      panels.splice(networkIndex >= 0 ? networkIndex + 1 : panels.length, 0, "neighbors");
    }
    if (Number(parsed.version) < 3 && panels.length && !panels.includes("storage-performance")) {
      const performanceIndex = panels.indexOf("performance");
      panels.splice(performanceIndex >= 0 ? performanceIndex + 1 : panels.length, 0, "storage-performance");
    }
    return panels;
  } catch {
    return [...DEFAULT_DASHBOARD_PANELS];
  }
}

export function saveDashboardPanels(panels: DashboardPanelId[]): string {
  return JSON.stringify({ version: 3, panels });
}

export function moveDashboardPanel(
  panels: DashboardPanelId[],
  panel: DashboardPanelId,
  destination: DashboardPanelId,
): DashboardPanelId[] {
  const from = panels.indexOf(panel);
  const to = panels.indexOf(destination);
  if (from < 0 || to < 0 || from === to) return [...panels];
  const next = [...panels];
  next.splice(from, 1);
  next.splice(to, 0, panel);
  return next;
}

export function shiftDashboardPanel(
  panels: DashboardPanelId[],
  panel: DashboardPanelId,
  direction: -1 | 1,
): DashboardPanelId[] {
  const from = panels.indexOf(panel);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= panels.length) return [...panels];
  const next = [...panels];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}
