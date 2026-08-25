import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { OperationDocument } from "../types";
import { ActivityPage } from "./ActivityPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ActivityPage recovery", () => {
  it("explains a missing storage account and resumes the durable checkpoint", async () => {
    const operation = {
      id: "11111111-1111-4111-8111-111111111111",
      kind: "storage.apply",
      status: "needs_attention",
      created_at: "2026-08-25T00:00:00Z",
      updated_at: "2026-08-25T00:01:00Z",
      result: null,
      error: {
        code: "storage_access_account_missing",
        detail: "The planned file-access account does not exist. No permissions were changed.",
      },
    } as unknown as OperationDocument;
    const resumed = { ...operation, status: "queued", error: null } as unknown as OperationDocument;
    vi.spyOn(api, "listOperations").mockResolvedValue([operation]);
    vi.spyOn(api, "operationEvents").mockResolvedValue([]);
    vi.spyOn(api, "storageOperationProgress").mockResolvedValue({
      operation_id: operation.id,
      state: "needs_attention",
      phase: "Creating media and download folders",
      completed_steps: 8,
      total_steps: 15,
      percent: 53,
      completed_actions: [],
      notices: [],
      current_action: null,
      estimate: null,
      updated_at: Date.parse("2026-08-25T00:01:00Z") / 1000,
    });
    const resume = vi.spyOn(api, "resumeOperation").mockResolvedValue(resumed);

    render(<ActivityPage />);

    expect(await screen.findByText("Create the reviewed media account first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Resume from safe checkpoint" }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith(operation.id));
    expect((await screen.findAllByTitle("Technical state: queued")).length).toBeGreaterThan(0);
  });
});
