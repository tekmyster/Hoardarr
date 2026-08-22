import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StatusBadge, statusPresentation } from "./ui";

afterEach(cleanup);

describe("plain-language status presentation", () => {
  it.each([
    ["healthy", "Healthy"],
    ["degraded", "Needs attention"],
    ["unknown", "Not reported"],
    ["running", "Active"],
    ["queued", "Waiting"],
  ])("maps %s to %s while retaining the technical state", (technical, label) => {
    expect(statusPresentation(technical).label).toBe(label);
    render(<StatusBadge status={technical} />);
    expect(screen.getByText(label)).toHaveAttribute("title", `Technical state: ${technical}`);
  });

  it("keeps an unfamiliar provider state visible instead of guessing", () => {
    expect(statusPresentation("vendor-state-17")).toEqual({ label: "vendor-state-17", tone: "info" });
  });
});
