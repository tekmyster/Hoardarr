import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OneTimePassword } from "./OneTimePassword";

afterEach(cleanup);

describe("OneTimePassword", () => {
  it("starts masked and supports the eye control", async () => {
    const user = userEvent.setup();
    render(<OneTimePassword password="generated-secret" onSavedConfirmed={vi.fn()} onCopyError={vi.fn()} />);
    const password = screen.getByLabelText("Generated media account password");

    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show generated password" }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide generated password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("keeps the secret after browser copy and discards it only after explicit saved confirmation", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    function OneTimeHarness() {
      const [password, setPassword] = useState<string | null>("generated-secret");
      return password
        ? <OneTimePassword password={password} onSavedConfirmed={() => setPassword(null)} onCopyError={vi.fn()} />
        : <p>Password saved and removed</p>;
    }
    render(<OneTimeHarness />);

    await user.click(screen.getByRole("button", { name: "Copy password" }));

    expect(writeText).toHaveBeenCalledWith("generated-secret");
    expect(screen.getByLabelText("Generated media account password")).toBeInTheDocument();
    expect(screen.getByText(/browser reported a copy/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "I saved this password" }));
    expect(screen.queryByLabelText("Generated media account password")).not.toBeInTheDocument();
    expect(screen.getByText("Password saved and removed")).toBeInTheDocument();
  });
});
