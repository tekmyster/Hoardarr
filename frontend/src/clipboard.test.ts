import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
});

describe("copyText", () => {
  it("uses the modern clipboard API when it is available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const legacy = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    Object.defineProperty(document, "execCommand", { configurable: true, value: legacy });

    await expect(copyText("generated-secret")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("generated-secret");
    expect(legacy).not.toHaveBeenCalled();
  });

  it("falls back to a selected field when the modern API rejects HTTP origins", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("Not allowed", "NotAllowedError"));
    const legacy = vi.fn(() => {
      expect(document.activeElement).toBeInstanceOf(HTMLTextAreaElement);
      expect((document.activeElement as HTMLTextAreaElement).value).toBe("generated-secret");
      return true;
    });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    Object.defineProperty(document, "execCommand", { configurable: true, value: legacy });

    await expect(copyText("generated-secret")).resolves.toBe(true);
    expect(legacy).toHaveBeenCalledWith("copy");
    expect(document.querySelector('textarea[aria-hidden="true"]')).not.toBeInTheDocument();
  });

  it("reports failure only when neither copy method succeeds", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    });

    await expect(copyText("generated-secret")).resolves.toBe(false);
  });
});
