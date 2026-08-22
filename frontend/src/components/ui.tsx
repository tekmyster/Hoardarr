import { cloneElement, isValidElement, useId } from "react";
import type { PropsWithChildren, ReactNode } from "react";

export function Card({ title, description, actions, children, className = "" }: PropsWithChildren<{
  title?: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
}>) {
  return (
    <section className={`card ${className}`.trim()}>
      {(title || description || actions) && (
        <header className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Notice({ tone = "info", title, children }: PropsWithChildren<{
  tone?: "info" | "warning" | "danger" | "success";
  title: string;
}>) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <span className="notice-icon" aria-hidden="true">{tone === "danger" ? "!" : tone === "warning" ? "!" : tone === "success" ? "✓" : "i"}</span>
      <div><strong>{title}</strong><div>{children}</div></div>
    </div>
  );
}

export function FeatureReadiness({ status, title, children }: PropsWithChildren<{
  status: "partial" | "unavailable";
  title: string;
}>) {
  const label = status === "partial" ? "Partially working" : "Not completed";
  return (
    <div className={`feature-readiness feature-readiness-${status}`} role="status">
      <div className="feature-readiness-heading">
        <strong>{title}</strong>
        <span>{label}</span>
      </div>
      <div>{children}</div>
    </div>
  );
}

export function SourceBadge({ children }: PropsWithChildren) {
  return <span className="source-badge" title="Where this value came from">Source: {children}</span>;
}

export function statusPresentation(status: string): { label: string; tone: "good" | "bad" | "muted" | "info" } {
  const normalized = status.trim().toLowerCase().replaceAll("_", " ");
  if (normalized === "fully redundant") return { label: "Fully redundant", tone: "good" };
  if (normalized === "single path") return { label: "Single path", tone: "info" };
  if (normalized === "failed over") return { label: "Failed over", tone: "bad" };
  if (normalized === "reduced redundancy") return { label: "Reduced redundancy", tone: "bad" };
  if (normalized === "no path") return { label: "Offline", tone: "bad" };
  if (["passed", "up", "high", "healthy", "succeeded", "configured", "online"].includes(normalized)) return { label: "Healthy", tone: "good" };
  if (["failed", "warning", "critical", "degraded", "faulted", "needs attention"].includes(normalized)) return { label: "Needs attention", tone: "bad" };
  if (["unavailable", "unreliable", "down", "unknown", "not reported"].includes(normalized)) return { label: "Not reported", tone: "muted" };
  if (["running", "active"].includes(normalized)) return { label: "Active", tone: "good" };
  if (["queued", "pending", "preparing"].includes(normalized)) return { label: "Waiting", tone: "info" };
  return { label: status, tone: "info" };
}

export function StatusBadge({ status }: { status: string }) {
  const presentation = statusPresentation(status);
  return <span className={`status-badge status-${presentation.tone}`} title={`Technical state: ${status}`}>{presentation.label}</span>;
}

export function ChoiceCard({
  name,
  value,
  checked,
  label,
  description,
  warning,
  disabled,
  onChange,
}: {
  name: string;
  value: string;
  checked: boolean;
  label: string;
  description: string;
  warning?: string;
  disabled?: boolean;
  onChange: () => void;
}) {
  return (
    <label className={`choice-card ${checked ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`}>
      <input type="radio" name={name} value={value} checked={checked} disabled={disabled} onChange={onChange} />
      <span className="choice-copy">
        <strong>{label}</strong>
        <span>{description}</span>
        {warning && <span className="choice-warning">{warning}</span>}
      </span>
    </label>
  );
}

export function Field({ label, hint, source, error, children }: PropsWithChildren<{
  label: string;
  hint?: string;
  source?: string;
  error?: string;
}>) {
  const generatedId = useId();
  const isControl = isValidElement<{ id?: string }>(children)
    && typeof children.type === "string"
    && ["input", "select", "textarea"].includes(children.type);
  const controlId = isControl ? children.props.id ?? generatedId : undefined;
  const labelId = `${generatedId}-label`;
  return (
    <div className="field">
      <div className="field-label-row">
        <label id={labelId} htmlFor={controlId}>{label}</label>
        {source && <SourceBadge>{source}</SourceBadge>}
      </div>
      {isControl ? cloneElement(children, { id: controlId }) : <div role="group" aria-labelledby={labelId}>{children}</div>}
      {hint && <small className="field-hint">{hint}</small>}
      {error && <small className="field-error">{error}</small>}
    </div>
  );
}

export function Spinner({ label = "Working" }: { label?: string }) {
  return <span className="spinner-wrap" role="status"><span className="spinner" aria-hidden="true" />{label}</span>;
}
