import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthenticationPage } from "./AuthenticationPage";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

describe("AuthenticationPage", () => {
  it("uses the ARR forms terminology for an existing server", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: true, claim_available: false }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    expect(screen.getByText("SIGN IN TO CONTINUE")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Hoardarr" })).toHaveAttribute("src", "/hoardarr-logo.png");
    expect(screen.getByRole("checkbox", { name: "Remember Me" })).toBeChecked();
    expect(screen.queryByPlaceholderText("Setup Code")).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("Username"), "owner");
    await user.type(screen.getByPlaceholderText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(onSubmit).toHaveBeenCalledWith({ username: "owner", password: "secret", rememberMe: true });
  });

  it("allows a sign-in to be limited to the current browser session", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: true, claim_available: false }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    await user.click(screen.getByRole("checkbox", { name: "Remember Me" }));
    await user.type(screen.getByPlaceholderText("Username"), "owner");
    await user.type(screen.getByPlaceholderText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Login" }));

    expect(onSubmit).toHaveBeenCalledWith({ username: "owner", password: "secret", rememberMe: false });
  });

  it("reveals and hides the entered password with an accessible eye control", async () => {
    const user = userEvent.setup();
    render(<AuthenticationPage setupStatus={{ configured: true, claim_available: false }} busy={false} error={null} demo={false} onSubmit={vi.fn()} />);
    const password = screen.getByPlaceholderText("Password");

    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show Password" }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide Password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("requires the one-time CLI link without exposing a setup-code field", () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: false, claim_available: true }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    expect(screen.getByText("CREATE ACCOUNT TO CONTINUE")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Hoardarr" })).toHaveAttribute("src", "/hoardarr-wordmark.jpg");
    expect(screen.getByText("Pair this browser from the server")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Setup Code")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Username")).not.toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("automatically pairs a browser opened from the CLI setup link", async () => {
    window.history.replaceState(null, "", "/#pair=hsetup_automatically-paired-code");
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: false, claim_available: true }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    expect(screen.queryByPlaceholderText("Setup Code")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("This browser is paired");
    expect(window.location.hash).toBe("");
    await user.type(screen.getByPlaceholderText("Username"), "owner");
    await user.type(screen.getByPlaceholderText("Password"), "x");
    await user.type(screen.getByPlaceholderText("Confirm Password"), "x");
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    expect(onSubmit).toHaveBeenCalledWith({
      username: "owner",
      password: "x",
      setupCode: "hsetup_automatically-paired-code",
    });
  });

  it("allows a one-character password during first use", async () => {
    window.history.replaceState(null, "", "/#pair=hsetup_one-character-password-code");
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: false, claim_available: true }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText("Username"), "owner");
    await user.type(screen.getByPlaceholderText("Password"), "x");
    await user.type(screen.getByPlaceholderText("Confirm Password"), "x");
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    expect(onSubmit).toHaveBeenCalledWith({ username: "owner", password: "x", setupCode: "hsetup_one-character-password-code" });
  });

  it("stops mismatched passwords before sending account details", async () => {
    window.history.replaceState(null, "", "/#pair=hsetup_mismatched-password-code");
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<AuthenticationPage setupStatus={{ configured: false, claim_available: true }} busy={false} error={null} demo={false} onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText("Username"), "owner");
    await user.type(screen.getByPlaceholderText("Password"), "a secure password");
    await user.type(screen.getByPlaceholderText("Confirm Password"), "something different");
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    expect(screen.getByRole("alert")).toHaveTextContent("The passwords do not match.");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("blocks account creation when a fresh setup code is required", () => {
    window.history.replaceState(null, "", "/#pair=hsetup_expired-pairing-code");
    render(<AuthenticationPage setupStatus={{ configured: false, claim_available: false }} busy={false} error={null} demo={false} onSubmit={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("setup link has expired");
    expect(screen.getByRole("button", { name: "Create Account" })).toBeDisabled();
    expect(screen.queryByPlaceholderText("Setup Code")).not.toBeInTheDocument();
  });
});
