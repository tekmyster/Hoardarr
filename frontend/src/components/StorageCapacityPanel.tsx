import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { humanCapacity } from "../policy";
import type {
  OperationDocument,
  StorageVolumeCapacityPlan,
  StorageVolumeDocument,
} from "../types";
import { Notice, StatusBadge } from "./ui";

const GIB = 1024 ** 3;

function numericLimit(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function currentLimits(volume: StorageVolumeDocument): Record<string, unknown> {
  const value = volume.config.capacity_limits;
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function StorageCapacityPanel({
  volume,
}: {
  volume: StorageVolumeDocument;
}) {
  const current = useMemo(() => currentLimits(volume), [volume]);
  const [quotaGiB, setQuotaGiB] = useState(
    numericLimit(current.quota_bytes) / GIB,
  );
  const [reservationGiB, setReservationGiB] = useState(
    numericLimit(current.reservation_bytes) / GIB,
  );
  const [thin, setThin] = useState(current.thin_provisioned !== false);
  const [plan, setPlan] = useState<StorageVolumeCapacityPlan | null>(null);
  const [operation, setOperation] = useState<OperationDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!operation || !["queued", "running"].includes(operation.status)) return;
    let stopped = false;
    const refresh = async () => {
      try {
        const next = await api.operation(operation.id);
        if (!stopped) setOperation(next);
      } catch (reason) {
        if (!stopped)
          setError(
            reason instanceof Error
              ? reason.message
              : "Capacity activity could not be loaded.",
          );
      }
    };
    const timer = window.setInterval(() => void refresh(), 1000);
    void refresh();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [operation?.id, operation?.status]);

  const preview = async () => {
    setBusy(true);
    setError(null);
    setOperation(null);
    try {
      setPlan(
        await api.previewStorageVolumeCapacity(
          volume.id,
          volume.resource_type === "dataset"
            ? {
                quota_bytes: Math.round(quotaGiB * GIB),
                reservation_bytes: Math.round(reservationGiB * GIB),
              }
            : { thin_provisioned: thin },
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Capacity limits could not be reviewed.",
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await api.applyStorageVolumeCapacity(volume.id, plan));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Capacity limits could not be applied.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className="storage-capacity-panel"
      aria-labelledby="capacity-limits-title"
    >
      <h3 id="capacity-limits-title">Capacity and allocation</h3>
      <p>
        Change provider-enforced limits without recreating this storage area.
        Every change is reviewed, journaled, and read back from ZFS.
      </p>
      {error && (
        <Notice tone="danger" title="Capacity settings need attention">
          {error}
        </Notice>
      )}
      {volume.resource_type === "dataset" ? (
        <div className="settings-grid">
          <label>
            Maximum size (GiB)
            <input
              aria-label="Dataset quota in GiB"
              type="number"
              min={0}
              max={1_073_741_824}
              value={quotaGiB}
              onChange={(event) => {
                setQuotaGiB(Number(event.target.value));
                setPlan(null);
              }}
            />
            <small>
              0 means no quota. Future writes fail when a nonzero quota is
              reached.
            </small>
          </label>
          <label>
            Reserved space (GiB)
            <input
              aria-label="Dataset reservation in GiB"
              type="number"
              min={0}
              max={1_073_741_824}
              value={reservationGiB}
              onChange={(event) => {
                setReservationGiB(Number(event.target.value));
                setPlan(null);
              }}
            />
            <small>
              0 means no reservation. Reserved space is unavailable to sibling
              datasets.
            </small>
          </label>
        </div>
      ) : (
        <label className="toggle-row">
          <input
            aria-label="Thin provision this block volume"
            type="checkbox"
            checked={thin}
            onChange={(event) => {
              setThin(event.target.checked);
              setPlan(null);
            }}
          />
          <span>
            <strong>Thin provision this block volume</strong>
            <small>
              Uses pool space as data is written. Disable to reserve the full
              volume size.
            </small>
          </span>
        </label>
      )}
      <dl className="review-grid">
        <div>
          <dt>Current allocated</dt>
          <dd>
            {volume.allocated_bytes === null
              ? "Not reported"
              : humanCapacity(volume.allocated_bytes)}
          </dd>
        </div>
        <div>
          <dt>Current provider limits</dt>
          <dd>
            {Object.keys(current).length
              ? volume.resource_type === "dataset"
                ? `${numericLimit(current.quota_bytes) ? humanCapacity(numericLimit(current.quota_bytes)) : "No quota"} · ${numericLimit(current.reservation_bytes) ? `${humanCapacity(numericLimit(current.reservation_bytes))} reserved` : "No reservation"}`
                : current.thin_provisioned === true
                  ? "Thin provisioned"
                  : current.thin_provisioned === false
                    ? "Fully reserved"
                    : "Not reported"
              : "Not reported"}
          </dd>
        </div>
      </dl>
      {plan && !operation && (
        <>
          <Notice tone="warning" title="Review provider capacity change">
            {plan.risk}
          </Notice>
          <dl className="review-list">
            <div>
              <dt>Provider resource</dt>
              <dd>
                <code>{plan.volume.provider_resource_id}</code>
              </dd>
            </div>
            <div>
              <dt>Will apply</dt>
              <dd>
                <code>
                  {Object.entries(plan.properties)
                    .map(([name, value]) => `${name}=${value}`)
                    .join(" ")}
                </code>
              </dd>
            </div>
            <div>
              <dt>Immutable plan</dt>
              <dd>
                <code>{plan.plan_sha256}</code>
              </dd>
            </div>
          </dl>
        </>
      )}
      {operation && (
        <Notice
          tone={
            operation.status === "failed" ||
            operation.status === "needs_attention"
              ? "danger"
              : "info"
          }
          title="Capacity operation"
        >
          <StatusBadge status={operation.status.replaceAll("_", " ")} />{" "}
          {operation.status === "succeeded"
            ? "Provider settings were applied and verified."
            : (operation.error?.message ??
              "Follow the durable operation in Activity.")}
        </Notice>
      )}
      <div className="button-row">
        {!plan || operation ? (
          <button
            className="button button-secondary"
            type="button"
            disabled={
              busy || (operation ? ["queued", "running"].includes(operation.status) : false)
            }
            onClick={() => {
              setPlan(null);
              setOperation(null);
              void preview();
            }}
          >
            {busy ? "Reviewing…" : "Review capacity change"}
          </button>
        ) : (
          <>
            <button
              className="button button-primary"
              type="button"
              disabled={busy}
              onClick={() => void apply()}
            >
              {busy ? "Starting…" : "Apply capacity limits"}
            </button>
            <button
              className="button button-secondary"
              type="button"
              disabled={busy}
              onClick={() => setPlan(null)}
            >
              Change values
            </button>
          </>
        )}
      </div>
    </section>
  );
}
