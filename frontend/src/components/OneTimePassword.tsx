import { useState } from "react";
import { copyText } from "../clipboard";

export function OneTimePassword({
  password,
  onSavedConfirmed,
  onCopyError,
}: {
  password: string;
  onSavedConfirmed: () => void;
  onCopyError: () => void;
}) {
  const [visible, setVisible] = useState(false);
  const [browserReportedCopy, setBrowserReportedCopy] = useState(false);

  async function copyPassword(): Promise<void> {
    if (await copyText(password)) {
      setBrowserReportedCopy(true);
    } else {
      setBrowserReportedCopy(false);
      onCopyError();
    }
  }

  return (
    <div className="input-action generated-credential">
      <input
        aria-label="Generated media account password"
        readOnly
        type={visible ? "text" : "password"}
        value={password}
        onFocus={(event) => event.currentTarget.select()}
      />
      <button
        type="button"
        className="credential-eye-button"
        aria-label={visible ? "Hide generated password" : "Show generated password"}
        aria-pressed={visible}
        onClick={() => setVisible((shown) => !shown)}
      >
        <EyeIcon crossed={visible} />
      </button>
      <button type="button" className="button button-secondary" onClick={() => void copyPassword()}>
        Copy password
      </button>
      <p className="credential-copy-state" role="status">
        {browserReportedCopy
          ? "The browser reported a copy. Paste it into your password manager to verify it before confirming."
          : "Use Copy password or the eye button, then save and verify the password before confirming."}
      </p>
      <button type="button" className="button button-primary credential-saved-button" onClick={onSavedConfirmed}>
        I saved this password
      </button>
    </div>
  );
}

export function EyeIcon({ crossed }: { crossed: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M2.4 12s3.5-5.4 9.6-5.4 9.6 5.4 9.6 5.4-3.5 5.4-9.6 5.4S2.4 12 2.4 12Z" />
      <circle cx="12" cy="12" r="2.8" />
      {crossed && <path d="m4.2 4.2 15.6 15.6" />}
    </svg>
  );
}
