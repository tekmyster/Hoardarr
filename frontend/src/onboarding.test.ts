import { describe, expect, it } from "vitest";
import { demoOnboarding } from "./demo/fixture";
import { gatewayForPayload, normalizeServerNameInput, serverSettingsError, supportedTimeZones, timeZoneLabel, timeZoneOffsetLabel, timeZoneUsesDaylightSaving, uiDefaultsFromOnboarding } from "./onboarding";
import type { OnboardingDefaults } from "./types";

describe("onboarding contract", () => {
  it("consumes the backend's nested server, network, time, and discovery defaults", () => {
    const response: OnboardingDefaults = {
      ...demoOnboarding,
      defaults: {
        experience: "advanced",
        server: { hostname: "storage-01", timezone: "America/New_York", dst_mode: "standard_time" },
        network: {
          mode: "active_passive",
          interface_ids: ["port-a", "port-b"],
          addressing: "static",
          addresses: ["192.0.2.10/24"],
          gateway: "192.0.2.1",
          dns_servers: ["192.0.2.53", "192.0.2.54"],
          vlan_id: 30,
          mtu: 9000,
          bridge: { enabled: false, stp: true, prefer_rstp: true },
        },
        ntp: { servers: ["time.example.test"] },
        discovery: {
          lldp: { enabled: true, mode: "receive_only" },
          cdp: { receive: true, smart_transmit: false },
        },
      },
    };

    expect(uiDefaultsFromOnboarding(response)).toEqual({
      experience: "advanced",
      hostname: "storage-01",
      timezone: "America/New_York",
      dstMode: "standard_time",
      networkMode: "active_passive",
      selectedInterfaces: ["port-a", "port-b"],
      addressing: "static",
      address: "192.0.2.10/24",
      gateway: "192.0.2.1",
      dnsServers: ["192.0.2.53", "192.0.2.54"],
      vlanId: "30",
      mtu: "9000",
      bridge: false,
      ntpServers: ["time.example.test"],
      lldpEnabled: true,
      lldpMode: "receive_only",
      cdpReceive: true,
      cdpSmartTransmit: false,
    });
  });

  it("maps backend bridge mode to the UI bridge flag and a single physical selector", () => {
    const response: OnboardingDefaults = {
      ...demoOnboarding,
      defaults: {
        ...demoOnboarding.defaults,
        experience: "advanced",
        network: {
          ...demoOnboarding.defaults.network,
          mode: "bridge",
          interface_ids: ["port-a"],
          bridge: { enabled: true, stp: true, prefer_rstp: true },
        },
      },
    };

    expect(uiDefaultsFromOnboarding(response)).toMatchObject({
      experience: "advanced",
      networkMode: "single",
      selectedInterfaces: ["port-a"],
      bridge: true,
    });
  });

  it("keeps a static gateway optional and emits null when omitted", () => {
    expect(gatewayForPayload("static", "")).toBeNull();
    expect(gatewayForPayload("static", " 192.0.2.1 ")).toBe("192.0.2.1");
    expect(gatewayForPayload("dhcp", "192.0.2.1")).toBeNull();
  });

  it("accepts simple server settings and explains invalid names in plain language", () => {
    expect(serverSettingsError("media-storage", "America/New_York", "time.cloudflare.com, time.google.com")).toBeNull();
    expect(serverSettingsError("-storage", "America/New_York", "time.cloudflare.com")).toContain("letters, numbers, dots, or hyphens");
    expect(serverSettingsError("storage", "", "time.cloudflare.com")).toBe("Choose the time zone where this server is located.");
    expect(serverSettingsError("storage", "UTC", "")).toBe("At least one automatic time server is required.");
  });

  it("prevents unsupported server-name characters while typing", () => {
    expect(normalizeServerNameInput(" Media_Storage!01 ")).toBe("mediastorage01");
    expect(normalizeServerNameInput("..Media..Storage")).toBe("media.storage");
  });

  it("always offers the browser time zone and UTC", () => {
    const choices = supportedTimeZones("America/New_York");
    expect(choices).toContain("America/New_York");
    expect(choices).toContain("UTC");
  });

  it("shows the selected zone's offset, including half-hour offsets", () => {
    const summer = new Date("2026-07-01T12:00:00Z");
    expect(timeZoneOffsetLabel("America/New_York", summer)).toBe("UTC-04:00");
    expect(timeZoneOffsetLabel("Asia/Kolkata", summer)).toBe("UTC+05:30");
    expect(timeZoneLabel("America/New_York", summer)).toBe("America/New York (UTC-04:00)");
  });

  it("derives the regional daylight-saving default from the zone rules", () => {
    expect(timeZoneUsesDaylightSaving("America/New_York", 2026)).toBe(true);
    expect(timeZoneUsesDaylightSaving("America/Phoenix", 2026)).toBe(false);
    expect(timeZoneUsesDaylightSaving("UTC", 2026)).toBe(false);
  });
});
