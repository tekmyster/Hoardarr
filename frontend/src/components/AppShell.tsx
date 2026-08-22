import type { PropsWithChildren, ReactNode } from "react";
import packageMetadata from "../../package.json";

export type AppPage = "Overview" | "Storage" | "Storage Access" | "Networking" | "Applications" | "Activity" | "Health" | "Analytics" | "Settings";

const navItems: ReadonlyArray<readonly [AppPage, string]> = [
  ["Overview", "▦"],
  ["Storage", "▤"],
  ["Storage Access", "⇄"],
  ["Networking", "⌘"],
  ["Applications", "▣"],
  ["Activity", "◴"],
  ["Health", "♡"],
  ["Analytics", "⌁"],
  ["Settings", "⚙"],
] as const;

export function AppShell({
  activePage,
  onNavigate,
  demo,
  children,
}: PropsWithChildren<{
  activePage: AppPage;
  onNavigate: (page: AppPage) => void;
  demo: boolean;
}>) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div className="brand"><img className="brand-logo" src="/hoardarr-logo.png" alt="" aria-hidden="true" /><span>Hoardarr</span></div>
        <div className="topbar-title">{activePage}</div>
        <div className="topbar-actions" />
      </header>
      {demo && (
        <div className="demo-banner" role="status">
          Demonstration mode — clearly marked sample hardware is in use. No server changes can be made.
        </div>
      )}
      <div className="shell-body">
        <nav className="sidebar" aria-label="Primary navigation">
          <ul>
            {navItems.map(([label, icon]) => (
              <li key={label}><button className={label === activePage ? "active" : ""} type="button" aria-current={label === activePage ? "page" : undefined} onClick={() => onNavigate(label)}><span aria-hidden="true">{icon}</span>{label}</button></li>
            ))}
          </ul>
          <div className="sidebar-version">Hoardarr v{packageMetadata.version} · Beta 1</div>
        </nav>
        <main className="main" id="main-content">
          <div className="page-toolbar section-toolbar"><div><div className="eyebrow">Hoardarr</div><h1>{activePage}</h1></div></div>
          {children}
        </main>
      </div>
    </div>
  );
}

export function WizardFrame({
  title,
  description,
  steps,
  activeStep,
  onBack,
  onNext,
  nextLabel = "Continue",
  busy = false,
  nextDisabled = false,
  footerExtra,
  children,
}: PropsWithChildren<{
  title: string;
  description: string;
  steps: readonly string[];
  activeStep: number;
  onBack?: () => void;
  onNext?: () => void;
  nextLabel?: string;
  busy?: boolean;
  nextDisabled?: boolean;
  footerExtra?: ReactNode;
}>) {
  const percent = Math.round(((activeStep + 1) / steps.length) * 100);
  return (
    <div className="wizard-layout">
      <aside className="step-list" aria-label="Setup progress">
        <div className="progress-label"><span>Wizard questions</span><strong>{activeStep + 1} of {steps.length}</strong></div>
        <div className="progress-track" aria-hidden="true"><span style={{ width: `${percent}%` }} /></div>
        <ol>
          {steps.map((step, index) => (
            <li key={step} className={index === activeStep ? "current" : index < activeStep ? "complete" : ""} aria-current={index === activeStep ? "step" : undefined}>
              <span>{index < activeStep ? "✓" : index + 1}</span><div>{step}</div>
            </li>
          ))}
        </ol>
      </aside>
      <div className="wizard-main">
        <header className="wizard-heading">
          <p>Step {activeStep + 1} of {steps.length}</p>
          <h2>{title}</h2>
          <div>{description}</div>
        </header>
        <div className="wizard-content">{children}</div>
        {(onBack || onNext || footerExtra) && (
          <footer className="wizard-footer">
            <div>{onBack && <button className="button button-secondary" type="button" onClick={onBack} disabled={busy}>Back</button>}</div>
            <div className="wizard-footer-right">
              {footerExtra}
              {onNext && <button className="button button-primary" type="button" onClick={onNext} disabled={busy || nextDisabled}>{busy ? "Working…" : nextLabel}</button>}
            </div>
          </footer>
        )}
      </div>
    </div>
  );
}
