import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";

afterEach(cleanup);

describe("AppShell", () => {
  it("wires every primary navigation page", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<AppShell activePage="Storage" onNavigate={onNavigate} demo={false}><div>Storage content</div></AppShell>);

    await user.click(screen.getByRole("button", { name: "Overview" }));
    await user.click(screen.getByRole("button", { name: "Storage Access" }));
    await user.click(screen.getByRole("button", { name: "Networking" }));
    await user.click(screen.getByRole("button", { name: "Activity" }));
    await user.click(screen.getByRole("button", { name: "Health" }));
    await user.click(screen.getByRole("button", { name: "Analytics" }));
    await user.click(screen.getByRole("button", { name: "Settings" }));

    expect(onNavigate.mock.calls.map(([page]) => page)).toEqual(["Overview", "Storage Access", "Networking", "Activity", "Health", "Analytics", "Settings"]);
  });

  it("shows Storage as a normal details page without setup controls", () => {
    render(<AppShell activePage="Storage" onNavigate={vi.fn()} demo={false}><div>Current storage details</div></AppShell>);

    expect(screen.getByRole("heading", { name: "Storage", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Current storage details")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Guided" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Advanced settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel setup/i })).not.toBeInTheDocument();
  });
});
