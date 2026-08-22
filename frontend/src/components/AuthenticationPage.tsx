import { type FormEvent, useEffect, useState } from "react";
import type { SetupStatus } from "../types";

export interface AuthenticationInput {
  username: string;
  password: string;
  setupCode?: string;
  rememberMe?: boolean;
}

export function AuthenticationPage({
  setupStatus,
  busy,
  error,
  demo,
  onSubmit,
}: {
  setupStatus: SetupStatus | null;
  busy: boolean;
  error: string | null;
  demo: boolean;
  onSubmit: (input: AuthenticationInput) => Promise<void>;
}) {
  const firstRun = setupStatus?.configured === false;
  const setupUnavailable = firstRun && !setupStatus?.claim_available;
  const [username, setUsername] = useState(demo ? "admin" : "");
  const [password, setPassword] = useState(demo ? "correct horse battery staple" : "");
  const [passwordConfirmation, setPasswordConfirmation] = useState(demo ? "correct horse battery staple" : "");
  const [showPassword, setShowPassword] = useState(false);
  const [showPasswordConfirmation, setShowPasswordConfirmation] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);
  const [setupCode] = useState(demo ? "DEMO-CLAIM-TOKEN-0001" : setupCodeFromLocation());
  const [validationError, setValidationError] = useState<string | null>(null);
  const browserPaired = !demo && setupCode.startsWith("hsetup_");
  const pairingRequired = firstRun && !browserPaired;

  useEffect(() => {
    if (browserPaired && window.location.hash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    }
  }, [browserPaired]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setValidationError(null);
    const cleanUsername = username.trim();
    if (!cleanUsername) {
      setValidationError("Enter your username.");
      return;
    }
    if (!password) {
      setValidationError("Enter your password.");
      return;
    }
    if (firstRun && password !== passwordConfirmation) {
      setValidationError("The passwords do not match.");
      return;
    }
    if (firstRun && !browserPaired) {
      setValidationError("Open the one-time setup link provided by the server.");
      return;
    }
    await onSubmit({
      username: cleanUsername,
      password,
      ...(firstRun ? { setupCode: setupCode.trim() } : {}),
      ...(!firstRun ? { rememberMe } : {}),
    });
  }

  const visibleError = validationError ?? error;

  return (
    <main className={`auth-page ${firstRun ? "auth-page-setup" : "auth-page-signin"}`}>
      {demo && <div className="auth-demo" role="status">Demonstration mode — sample credentials are filled in.</div>}
      <div className="auth-center">
        <div className="auth-content">
          <section className="auth-panel" aria-labelledby={setupStatus ? "auth-title" : undefined} aria-label={setupStatus ? undefined : "Hoardarr authentication"}>
            <header className="auth-panel-header">
              <img className={`auth-logo ${firstRun ? "auth-logo-setup" : "auth-logo-signin"}`} src={firstRun ? "/hoardarr-wordmark.jpg" : "/hoardarr-logo.png"} alt="Hoardarr" />
            </header>
            <div className="auth-panel-body">
              {!setupStatus && error ? (
                <div className="auth-startup-error">
                  <div className="auth-error" role="alert">{error}</div>
                  <p>Check that the server is running, then reload this page.</p>
                </div>
              ) : !setupStatus ? (
                <div className="auth-loading" role="status">
                  <span className="spinner" aria-hidden="true" />
                  <span>Checking server…</span>
                </div>
              ) : (
                <>
                  <div className="auth-heading" id="auth-title">
                    {firstRun ? "CREATE ACCOUNT TO CONTINUE" : "SIGN IN TO CONTINUE"}
                  </div>
                  {firstRun && <p className="auth-intro">Create the account you will use to manage this server.</p>}
                  {pairingRequired ? (
                    <div className="auth-pairing-required" role="status">
                      <strong>Pair this browser from the server</strong>
                      <p>Run <code>hoardarr setup</code>, then open the one-time link it provides. The server code is applied automatically and is never shown here.</p>
                    </div>
                  ) : <form noValidate onSubmit={(event) => void submit(event)}>
                    {firstRun && (
                      <div className="auth-form-group">
                        <div className="auth-help" role="status">This browser is paired with your Hoardarr server.</div>
                      </div>
                    )}
                    <div className="auth-form-group">
                      <label className="sr-only" htmlFor="username">Username</label>
                      <input
                        id="username"
                        className="auth-input"
                        type="text"
                        placeholder="Username"
                        autoComplete="username"
                        autoCapitalize="none"
                        autoFocus
                        value={username}
                        disabled={busy || setupUnavailable}
                        onChange={(event) => setUsername(event.target.value)}
                      />
                    </div>
                    <div className="auth-form-group">
                      <label className="sr-only" htmlFor="password">Password</label>
                      <div className="auth-password-control">
                        <input
                          id="password"
                          className="auth-input"
                          type={showPassword ? "text" : "password"}
                          placeholder="Password"
                          autoComplete={firstRun ? "new-password" : "current-password"}
                          value={password}
                          disabled={busy || setupUnavailable}
                          onChange={(event) => setPassword(event.target.value)}
                        />
                        <button type="button" className="auth-password-toggle" aria-label={showPassword ? "Hide Password" : "Show Password"} aria-pressed={showPassword} disabled={busy || setupUnavailable} onClick={() => setShowPassword((visible) => !visible)}><EyeIcon crossed={showPassword} /></button>
                      </div>
                      {firstRun && <small className="auth-help">Choose any password you want.</small>}
                    </div>
                    {firstRun && (
                      <div className="auth-form-group">
                        <label className="sr-only" htmlFor="password-confirmation">Confirm Password</label>
                        <div className="auth-password-control">
                          <input
                            id="password-confirmation"
                            className="auth-input"
                            type={showPasswordConfirmation ? "text" : "password"}
                            placeholder="Confirm Password"
                            autoComplete="new-password"
                            value={passwordConfirmation}
                            disabled={busy || setupUnavailable}
                            onChange={(event) => setPasswordConfirmation(event.target.value)}
                          />
                          <button type="button" className="auth-password-toggle" aria-label={showPasswordConfirmation ? "Hide Confirm Password" : "Show Confirm Password"} aria-pressed={showPasswordConfirmation} disabled={busy || setupUnavailable} onClick={() => setShowPasswordConfirmation((visible) => !visible)}><EyeIcon crossed={showPasswordConfirmation} /></button>
                        </div>
                      </div>
                    )}
                    {!firstRun && (
                      <div className="auth-login-options">
                        <label><input type="checkbox" checked={rememberMe} disabled={busy} onChange={(event) => setRememberMe(event.target.checked)} /> <span>Remember Me</span></label>
                      </div>
                    )}
                    {setupUnavailable && (
                      <div className="auth-error" role="alert">
                        This setup link has expired. Run <code>hoardarr setup</code> again and open the new link.
                      </div>
                    )}
                    {visibleError && !setupUnavailable && <div className="auth-error" role="alert">{visibleError}</div>}
                    <button className="auth-button" type="submit" disabled={busy || setupUnavailable}>
                      {busy ? "Please wait…" : firstRun ? "Create Account" : "Login"}
                    </button>
                  </form>}
                </>
              )}
            </div>
          </section>
          <footer className="auth-copy">© {new Date().getFullYear()} - Hoardarr</footer>
        </div>
      </div>
    </main>
  );
}

function EyeIcon({ crossed }: { crossed: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M2.4 12s3.5-5.4 9.6-5.4 9.6 5.4 9.6 5.4-3.5 5.4-9.6 5.4S2.4 12 2.4 12Z" />
      <circle cx="12" cy="12" r="2.8" />
      {crossed && <path d="m4.2 4.2 15.6 15.6" />}
    </svg>
  );
}

function setupCodeFromLocation(): string {
  if (typeof window === "undefined" || !window.location.hash) return "";
  const parameters = new URLSearchParams(window.location.hash.slice(1));
  return parameters.get("pair")?.trim() ?? parameters.get("setup")?.trim() ?? "";
}
