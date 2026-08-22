import type { DaylightSavingMode, NetworkMode, OnboardingDefaults, WizardMode } from "./types";

export interface UiOnboardingDefaults {
  experience: WizardMode;
  hostname: string;
  timezone: string;
  dstMode: DaylightSavingMode;
  networkMode: NetworkMode;
  selectedInterfaces: string[];
  addressing: "dhcp" | "static";
  address: string;
  gateway: string;
  dnsServers: string[];
  vlanId: string;
  mtu: string;
  bridge: boolean;
  ntpServers: string[];
  lldpEnabled: boolean;
  lldpMode: "rx_tx" | "receive_only";
  cdpReceive: boolean;
  cdpSmartTransmit: boolean;
}

export function uiDefaultsFromOnboarding(onboarding: OnboardingDefaults): UiOnboardingDefaults {
  const { defaults } = onboarding;
  const { network, discovery } = defaults;
  return {
    experience: defaults.experience,
    hostname: defaults.server.hostname,
    timezone: defaults.server.timezone,
    dstMode: defaults.server.dst_mode,
    networkMode: network.mode === "bridge" ? "single" : network.mode,
    selectedInterfaces: [...network.interface_ids],
    addressing: network.addressing,
    address: network.addresses[0] ?? "",
    gateway: network.gateway ?? "",
    dnsServers: [...network.dns_servers],
    vlanId: network.vlan_id === null ? "" : String(network.vlan_id),
    mtu: String(network.mtu),
    bridge: network.mode === "bridge" || network.bridge.enabled,
    ntpServers: [...defaults.ntp.servers],
    lldpEnabled: discovery.lldp.enabled,
    lldpMode: discovery.lldp.mode,
    cdpReceive: discovery.cdp.receive,
    cdpSmartTransmit: discovery.cdp.smart_transmit,
  };
}

export function gatewayForPayload(addressing: "dhcp" | "static", gateway: string): string | null {
  return addressing === "static" ? gateway.trim() || null : null;
}

export function serverSettingsError(hostname: string, timezone: string, ntpServers: string): string | null {
  const cleanHostname = hostname.trim();
  if (!cleanHostname) return "Enter a name for this server.";
  if (cleanHostname.length > 253 || !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i.test(cleanHostname) || cleanHostname.includes("..")) {
    return "Use letters, numbers, dots, or hyphens for the server name. It cannot begin or end with a dot or hyphen.";
  }
  if (!timezone.trim()) return "Choose the time zone where this server is located.";
  const servers = ntpServers.split(",").map((item) => item.trim()).filter(Boolean);
  if (!servers.length) return "At least one automatic time server is required.";
  if (servers.length > 8 || servers.some((server) => server.length > 253 || !/^[a-z0-9](?:[a-z0-9.:-]*[a-z0-9])?$/i.test(server))) {
    return "Enter valid time-server names or addresses, separated with commas.";
  }
  return null;
}

export function normalizeServerNameInput(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9.-]/g, "")
    .replace(/\.{2,}/g, ".")
    .replace(/^[.-]+/, "")
    .slice(0, 253);
}

export function supportedTimeZones(current: string): string[] {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: "timeZone") => string[] };
  const available = intl.supportedValuesOf?.("timeZone") ?? ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"];
  return [...new Set([current, "UTC", ...available].filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

export function timeZoneOffsetMinutes(timeZone: string, date: Date = new Date()): number {
  if (timeZone === "UTC") return 0;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const localTimeAsUtc = Date.UTC(
    Number(values.year),
    Number(values.month) - 1,
    Number(values.day),
    Number(values.hour),
    Number(values.minute),
    Number(values.second),
  );
  return Math.round((localTimeAsUtc - date.getTime()) / 60_000);
}

export function timeZoneOffsetLabel(timeZone: string, date: Date = new Date()): string {
  const offset = timeZoneOffsetMinutes(timeZone, date);
  const sign = offset >= 0 ? "+" : "-";
  const absolute = Math.abs(offset);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = String(absolute % 60).padStart(2, "0");
  return `UTC${sign}${hours}:${minutes}`;
}

export function timeZoneLabel(timeZone: string, date: Date = new Date()): string {
  return `${timeZone.replaceAll("_", " ")} (${timeZoneOffsetLabel(timeZone, date)})`;
}

export function timeZoneUsesDaylightSaving(timeZone: string, year: number = new Date().getUTCFullYear()): boolean {
  const offsets = new Set<number>();
  for (let month = 0; month < 12; month += 1) {
    offsets.add(timeZoneOffsetMinutes(timeZone, new Date(Date.UTC(year, month, 15, 12))));
  }
  return offsets.size > 1;
}
