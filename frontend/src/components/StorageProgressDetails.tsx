import { humanCapacity } from "../policy";
import type { StorageOperationProgress } from "../types";

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
    {live && <>
      <div><dt>Drive</dt><dd><code>{live.device}</code></dd></div>
      <div><dt>Current drive</dt><dd>{live.percent.toFixed(1)}% · {humanCapacity(live.processed_bytes)} of {humanCapacity(live.total_bytes)}</dd></div>
      <div><dt>Read speed</dt><dd>{humanCapacity(live.bytes_per_second)}/sec</dd></div>
      <div><dt>Elapsed</dt><dd>{duration(live.elapsed_seconds)}</dd></div>
      <div><dt>This drive remaining</dt><dd>{duration(live.estimated_seconds_remaining)}</dd></div>
    </>}
    {estimate && <>
      <div><dt>Time remaining</dt><dd>{duration(estimate.estimated_seconds_remaining)}</dd></div>
      <div><dt>Estimated completion</dt><dd>{new Date(estimate.estimated_completion_at * 1000).toLocaleString()}</dd></div>
    </>}
  </dl>;
}
