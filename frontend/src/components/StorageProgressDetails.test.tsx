import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StorageProgressDetails } from "./StorageProgressDetails";

describe("StorageProgressDetails", () => {
  it("explains measured drive progress and the estimate basis", () => {
    render(<StorageProgressDetails progress={{
      operation_id: "operation",
      state: "running",
      phase: "Checking and preparing drives",
      completed_steps: 1,
      total_steps: 5,
      percent: 30,
      completed_actions: ["identity"],
      notices: [],
      current_action: {
        id: "surface",
        type: "drive.surface.read",
        number: 2,
        count: 3,
        progress: {
          kind: "surface_read",
          device: "/dev/sdb",
          processed_bytes: 500_000_000,
          total_bytes: 1_000_000_000,
          percent: 50,
          elapsed_seconds: 10,
          bytes_per_second: 50_000_000,
          estimated_seconds_remaining: 10,
        },
      },
      estimate: {
        scope: "intake_tests",
        estimated_seconds_remaining: 30,
        estimated_completion_at: 2_000_000_000,
        remaining_bytes: 1_500_000_000,
      },
      updated_at: 1,
    }} />);

    expect(screen.getByText("30% · 1 of 5 steps")).toBeInTheDocument();
    expect(screen.getByText("/dev/sdb")).toBeInTheDocument();
    expect(screen.getByText(/50\.0%/)).toBeInTheDocument();
    expect(screen.getByText("Time remaining")).toBeInTheDocument();
    expect(screen.queryByText("Estimate basis")).not.toBeInTheDocument();
  });

  it("shows SMART state, drive-reported finish, and durable result", () => {
    render(<StorageProgressDetails progress={{
      operation_id: "smart-operation",
      state: "running",
      phase: "Checking and preparing drives",
      completed_steps: 0,
      total_steps: 1,
      percent: 35,
      completed_actions: [],
      notices: [],
      action_results: [{
        action_id: "smart-short",
        device_id: "wwn:test",
        outcome: "passed",
        code: "smart_self_test_passed",
        message: "Completed.",
        test_kind: "short",
        finished_at: 2_000_000_000,
      }],
      current_action: {
        id: "smart-short",
        type: "drive.smart.short",
        progress: {
          kind: "smart_self_test",
          device: "/dev/sdb",
          test_kind: "short",
          state: "running",
          percent: 35,
          elapsed_seconds: 60,
          estimated_seconds_remaining: 120,
          expected_finish_at: 2_000_000_000,
        },
      },
      estimate: null,
      updated_at: 1,
    }} />);

    expect(screen.getByText("Short · running")).toBeInTheDocument();
    expect(screen.getByText("35.0%")).toBeInTheDocument();
    expect(screen.getByText("Short SMART result")).toBeInTheDocument();
    expect(screen.getByText(/Passed/)).toBeInTheDocument();
  });
});
