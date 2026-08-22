import { afterEach, describe, expect, it, vi } from "vitest";
import { demoPlan, demoWizard } from "./fixture";
import { sha256Hex } from "../sha256";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("dynamic demonstration plan", () => {
  it("reflects the current preservation, topology, portability, library, and download answers", async () => {
    const wizard = {
      ...demoWizard("guided"),
      revision: 3,
      answers: {
        storage: {
          selected_device_ids: ["usb-STP26501RAW"],
          topology: "mergerfs",
          preserve_data: true,
          portable_systems: ["linux"],
          libraries: ["Movies"],
          custom_libraries: [{ name: "Anime", content_type: "both", applications: ["sonarr", "radarr"] }],
          downloads: { torrents: false, usenet: true },
          intake_tests: { identity: true, full_surface_read: false, smart_short: false, smart_extended: false, destructive_write_read: false },
        },
        layout: { media_path: "/data/media", downloads_path: "/data/downloads" },
      },
    };

    const plan = await demoPlan(wizard);
    const storage = plan.document.storage as Record<string, unknown>;
    const risk = storage.risk as Record<string, unknown>;
    const actions = storage.actions as Array<Record<string, unknown>>;
    const folders = storage.folders as string[];

    expect(storage).toMatchObject({ topology: "mergerfs", preserve_data: true, format: { filesystem: "ext4" } });
    expect(risk).toMatchObject({ destructive: false, approval_required: false });
    expect(folders).toEqual(["/data/media/Movies", "/data/media/Anime", "/data/downloads/usenet/incomplete", "/data/downloads/usenet/complete"]);
    expect(actions.every((action) => typeof action.destructive === "boolean")).toBe(true);
    expect(actions.some((action) => action.type === "filesystem.create")).toBe(false);
    expect(plan.sha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("creates the Step 8 review plan when SubtleCrypto is unavailable", async () => {
    vi.stubGlobal("crypto", {});
    const plan = await demoPlan(demoWizard("guided"));
    expect(plan.sha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("produces a standards-compliant SHA-256 digest", () => {
    expect(sha256Hex(new TextEncoder().encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });
});
