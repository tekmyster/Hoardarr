import { humanCapacity } from "../policy";
import type { StorageOperationProgress } from "../types";
import { Notice } from "./ui";

type StorageNotice = StorageOperationProgress["notices"][number];

function noticePresentation(notice: StorageNotice): {
  title: string;
  tone: "info" | "warning";
} {
  if (notice.code === "storage_build_resumed") {
    return { title: "Storage build resumed", tone: "info" };
  }
  if (notice.code.startsWith("smart_")) {
    return { title: "SMART self-test not run", tone: "warning" };
  }
  return { title: "Storage notice", tone: "warning" };
}

export function StorageOperationNotices({ notices }: { notices: StorageNotice[] }) {
  const seen = new Set<string>();
  const unique = notices.filter((notice) => {
    const key = `${notice.code}:${notice.action_id ?? ""}:${notice.device_id ?? ""}:${notice.message}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return <>{unique.map((notice) => {
    const presentation = noticePresentation(notice);
    return <Notice
      key={`${notice.code}:${notice.action_id ?? ""}:${notice.device_id ?? ""}`}
      tone={presentation.tone}
      title={presentation.title}
    >{notice.message}</Notice>;
  })}</>;
}

function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "Calculating…";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sec`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours} hr${remainder ? ` ${remainder} min` : ""}`;
}

function actionLabel(type: string | undefined): string {
  const labels: Record<string, string> = {
    "drive.identity.verify": "Verifying drive identity",
    "drive.surface.read": "Reading every block for errors",
    "drive.smart.short": "Running the short drive self-test",
    "drive.smart.extended": "Running the extended drive self-test",
    "drive.write_read.destructive": "Writing and verifying every block",
    "disk.partition_table.create": "Creating the partition table",
    "filesystem.create": "Creating the filesystem",
    "filesystem.mount": "Mounting the prepared drive",
    "storage.layout.apply": "Building the storage layout",
    "directory.ensure": "Creating media folders",
    "mount.configuration.save": "Saving automatic mounts",
    "smb.share.ensure": "Creating file shares",
  };
  return type ? labels[type] ?? type : "Preparing the next step";
}

export function StorageProgressDetails({ progress }: { progress: StorageOperationProgress | null }) {
  const action = progress?.current_action;
  const live = action?.progress;
  const estimate = progress?.estimate;
  return <dl className="operation-details">
    <div><dt>Current phase</dt><dd>{progress?.phase ?? "Waiting for status"}</dd></div>
    <div><dt>Overall progress</dt><dd>{progress ? `${progress.percent}% · ${progress.completed_steps} of ${progress.total_steps || "?"} steps` : "Preparing"}</dd></div>
    {action && <div><dt>Current step</dt><dd>{actionLabel(action.type)}{action.number && action.count ? ` (${action.number} of ${action.count})` : ""}</dd></div>}
    {live?.kind === "smart_self_test" && <>
      <div><dt>Drive</dt><dd><code>{live.device}</code></dd></div>
      <div><dt>Self-test</dt><dd>{live.test_kind === "extended" ? "Extended" : "Short"} · {live.state ?? "Running"}</dd></div>
      <div><dt>Drive-reported progress</dt><dd>{live.percent.toFixed(1)}%</dd></div>
      <div><dt>Elapsed</dt><dd>{duration(live.elapsed_seconds)}</dd></div>
      <div><dt>Expected finish</dt><dd>{live.expected_finish_at ? new Date(live.expected_finish_at * 1000).toLocaleString() : "Not reported by this drive"}</dd></div>
    </>}
    {live && live.kind !== "smart_self_test" && live.processed_bytes !== undefined && live.total_bytes !== undefined && <>
      <div><dt>Drive</dt><dd><code>{live.device}</code></dd></div>
      <div><dt>Current drive</dt><dd>{live.percent.toFixed(1)}% · {humanCapacity(live.processed_bytes)} of {humanCapacity(live.total_bytes)}</dd></div>
      <div><dt>Read speed</dt><dd>{live.bytes_per_second === undefined ? "Not reported" : `${humanCapacity(live.bytes_per_second)}/sec`}</dd></div>
      <div><dt>Elapsed</dt><dd>{duration(live.elapsed_seconds)}</dd></div>
      <div><dt>This drive remaining</dt><dd>{duration(live.estimated_seconds_remaining)}</dd></div>
    </>}
    {progress?.action_results?.map((result) => <div key={result.action_id}><dt>{result.test_kind === "extended" ? "Extended SMART result" : "Short SMART result"}</dt><dd>{result.outcome === "passed" ? "Passed" : result.outcome === "skipped" ? "Not supported by this connection" : "Failed"}{result.finished_at ? ` · ${new Date(result.finished_at * 1000).toLocaleString()}` : ""}</dd></div>)}
    {estimate && <>
      <div><dt>Time remaining</dt><dd>{duration(estimate.estimated_seconds_remaining)}</dd></div>
      <div><dt>Estimated completion</dt><dd>{new Date(estimate.estimated_completion_at * 1000).toLocaleString()}</dd></div>
    </>}
  </dl>;
}
