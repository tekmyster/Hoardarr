import { describe, expect, it } from "vitest";
import { demoDrive } from "./demo/fixture";
import { actionDestructiveLabel, exactConsentAccepted, existingDataSummary, filesystemRecommendation, hasKnownSectorGeometry, isImportedNtfs, layoutChoicesForDrive, recommendStorage, sectorGeometryAssessment, selectPortableSystem, storageChoiceNeedsSectorGeometry, toggleNetworkInterfaceSelection } from "./policy";

describe("guided storage policy", () => {
  it("keeps USB array choices out of Guided setup", () => {
    const choices = layoutChoicesForDrive(demoDrive, "guided").map((choice) => choice.id);
    expect(choices).toEqual(["individual", "mergerfs", "download-cache", "block"]);
    expect(choices).not.toContain("zfs");
    expect(choices).not.toContain("snapraid");
    expect(choices).not.toContain("raid");
  });

  it("reveals USB array overrides only in Advanced", () => {
    const choices = layoutChoicesForDrive(demoDrive, "advanced");
    expect(choices.find((choice) => choice.id === "zfs")?.requiresAdvanced).toBe(true);
    expect(choices.find((choice) => choice.id === "raid")?.requiresAdvanced).toBe(true);
    expect(choices.find((choice) => choice.id === "snapraid")?.warning).toMatch(/USB identity/i);
  });

  it("offers ordinary-language protected storage for multiple direct-attached drives", () => {
    const directDrive = { ...demoDrive, connection: { bus: "SAS", transport: "sas/mpt3sas" } };
    const choices = layoutChoicesForDrive(directDrive, "guided", false, 4);
    const protectedStorage = choices.find((choice) => choice.id === "zfs");
    expect(protectedStorage?.label).toBe("Always-on protected storage");
    expect(protectedStorage?.requiresAdvanced).toBeUndefined();
    expect(choices.find((choice) => choice.id === "snapraid")?.label).toBe("Flexible protected media storage");
    expect(layoutChoicesForDrive(directDrive, "guided", false, 1).map((choice) => choice.id)).not.toContain("zfs");
  });

  it("recommends mergerFS plus SnapRAID for expandable four-drive media storage", () => {
    const drives = [0, 1, 2, 3].map((index) => ({
      ...demoDrive,
      id: `drive-${index}`,
      serial: `MEDIA-${index}`,
      capacityBytes: [12, 12, 8, 8][index] * 1_000_000_000_000,
      rotational: true,
      connection: { bus: "SAS", transport: "sas/mpt3sas" },
      signatureScan: { status: "complete" as const, source: "wipefs", reason: null },
    }));
    const recommendation = recommendStorage({
      drives, purpose: "media", preserveData: false, oneLargeLocation: true, protection: "one", easyExpansion: true,
    });
    expect(recommendation).toMatchObject({
      role: "snapraid",
      title: "Flexible protected media storage",
      technicalName: "mergerFS + SnapRAID (1 parity)",
      rawCapacityBytes: 40_000_000_000_000,
      usableCapacityBytes: 28_000_000_000_000,
      failureTolerance: 1,
      parityCount: 1,
    });
  });

  it("recommends a ZFS mirror for two direct-attached drives requesting protection", () => {
    const drives = [0, 1].map((index) => ({
      ...demoDrive, id: `ssd-${index}`, serial: `SSD-${index}`, capacityBytes: 4_000_000_000_000,
      rotational: false, connection: { bus: "NVME", transport: "nvme/pcie" },
      signatureScan: { status: "complete" as const, source: "wipefs", reason: null },
    }));
    expect(recommendStorage({
      drives, purpose: "media", preserveData: false, oneLargeLocation: true, protection: "one", easyExpansion: false,
    })).toMatchObject({ role: "zfs", zfsVdevType: "mirror", usableCapacityBytes: 4_000_000_000_000, failureTolerance: 1 });
  });

  it("defaults incomplete existing-data evidence to a non-formatting path", () => {
    expect(recommendStorage({
      drives: [demoDrive], purpose: "media", preserveData: true, oneLargeLocation: true, protection: "one", easyExpansion: true,
    })).toMatchObject({ role: "import", usableCapacityBytes: demoDrive.capacityBytes });
  });

  it("recommends a non-formatting import layout when existing data must be preserved", () => {
    const choices = layoutChoicesForDrive(demoDrive, "guided", true);
    expect(choices[0]).toMatchObject({ id: "import", recommended: true });
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "import", encryption: "none" })).toBe(false);
  });

  it("accepts only the exact destructive consent phrase", () => {
    expect(exactConsentAccepted("I AGREE")).toBe(true);
    expect(exactConsentAccepted("I Agree")).toBe(false);
    expect(exactConsentAccepted(" I AGREE ")).toBe(false);
    expect(exactConsentAccepted("true")).toBe(false);
  });

  it("derives NTFS from Windows portability", () => {
    const decision = filesystemRecommendation(["windows"], "media");
    expect(decision.filesystem).toBe("NTFS");
    expect(decision.partitionTable).toBe("gpt");
    expect(decision.alignmentBytes).toBe(1_048_576);
    expect(decision.allocationUnitBytes).toBe(4096);
    expect(decision.noatime).toBe(true);
    expect(decision.trimMode).toBe("conditional");
    expect(decision.settings).toContain("GPT partition table");
    expect(decision.settings).toContain("4 KiB allocation unit");
  });

  it("matches the backend exFAT decision for macOS without Windows", () => {
    const decision = filesystemRecommendation(["macos"], "media");
    expect(decision.filesystem).toBe("exFAT");
    expect(decision.settings).toContain("128 KiB allocation unit");
  });

  it("keeps Hoardarr-only Linux mutually exclusive with portable systems", () => {
    expect(selectPortableSystem(["linux"], "windows")).toEqual(["windows"]);
    expect(selectPortableSystem(["windows", "macos"], "linux")).toEqual(["linux"]);
    expect(selectPortableSystem(["windows"], "macos")).toEqual(["windows", "macos"]);
  });

  it("replaces rather than adds a port in single-interface mode", () => {
    expect(toggleNetworkInterfaceSelection(["port-1"], "port-2", true)).toEqual(["port-2"]);
    expect(toggleNetworkInterfaceSelection(["port-1"], "port-2", false)).toEqual(["port-1", "port-2"]);
  });

  it("never labels an omitted destructive flag as No", () => {
    expect(actionDestructiveLabel({ type: "filesystem.create" }, true)).toMatch(/^Yes/);
    expect(actionDestructiveLabel({ type: "folder.create" }, true)).toMatch(/treat as destructive/);
    expect(actionDestructiveLabel({ type: "folder.create" }, false)).toBe("Not declared");
    expect(actionDestructiveLabel({ type: "folder.create", destructive: false }, true)).toBe("No");
  });

  it("does not claim an empty drive when signature evidence is incomplete", () => {
    expect(existingDataSummary(demoDrive)).toMatchObject({
      headline: "No recognized signatures; scan is incomplete",
      uncertain: true,
    });
    expect(existingDataSummary({
      ...demoDrive,
      partitions: [{ kernelName: "sdb1", path: "/dev/sdb1", startBytes: 1_048_576, sizeBytes: 10_000, filesystem: "ntfs" }],
    }).headline).toMatch(/1 partition/);
  });

  it("shows NTFS guidance only while importing a detected NTFS filesystem", () => {
    const ntfsDrive = {
      ...demoDrive,
      partitions: [{ kernelName: "sdb1", path: "/dev/sdb1", startBytes: 1_048_576, sizeBytes: 10_000, filesystem: "ntfs" }],
    };
    expect(isImportedNtfs(true, [ntfsDrive])).toBe(true);
    expect(isImportedNtfs(false, [ntfsDrive])).toBe(false);
    expect(isImportedNtfs(true, [demoDrive])).toBe(false);
  });

  it("requires both reported sector sizes before formatting", () => {
    expect(hasKnownSectorGeometry(demoDrive)).toBe(true);
    expect(hasKnownSectorGeometry({ ...demoDrive, sector: { logical: null, physical: 4096 } })).toBe(false);
  });

  it("allows unknown-geometry drives through a preservation/import-only path", () => {
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "individual", encryption: "none" })).toBe(false);
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "mergerfs", encryption: "none" })).toBe(false);
  });

  it("blocks unknown geometry when the answers imply formatting or metadata writes", () => {
    expect(storageChoiceNeedsSectorGeometry({ preserveData: false, topology: "individual", encryption: "none" })).toBe(true);
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "zfs", encryption: "none" })).toBe(true);
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "download-cache", encryption: "none" })).toBe(true);
    expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "individual", encryption: "luks2" })).toBe(true);
  });

  it("treats 520-byte and 528-byte logical sectors as import-only until externally reformatted", () => {
    for (const logical of [520, 528]) {
      const drive = { ...demoDrive, sector: { logical, physical: logical } };
      expect(hasKnownSectorGeometry(drive)).toBe(false);
      expect(sectorGeometryAssessment(drive)).toMatchObject({
        writeCompatible: false,
        kind: "nonstandard",
        message: "Nonstandard sector format; dedicated low-level reformat required and not implemented.",
      });
      expect(storageChoiceNeedsSectorGeometry({ preserveData: true, topology: "individual", encryption: "none" })).toBe(false);
      expect(storageChoiceNeedsSectorGeometry({ preserveData: false, topology: "individual", encryption: "none" })).toBe(true);
    }
  });

  it("requires compatible physical-sector relationships for write planning", () => {
    expect(hasKnownSectorGeometry({ ...demoDrive, sector: { logical: 512, physical: 4096 } })).toBe(true);
    expect(hasKnownSectorGeometry({ ...demoDrive, sector: { logical: 4096, physical: 8192 } })).toBe(true);
    expect(sectorGeometryAssessment({ ...demoDrive, sector: { logical: 512, physical: 768 } }).kind).toBe("incompatible");
    expect(sectorGeometryAssessment({ ...demoDrive, sector: { logical: 4096, physical: 2048 } }).kind).toBe("incompatible");
  });
});
