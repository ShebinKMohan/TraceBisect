"use client";

import { Braces, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";

type WorkspaceUnlockProps = {
  busy: boolean;
  error: string | null;
  managedSession: boolean;
  onUnlock: (apiKey: string) => void;
  sessionTtlSeconds: number;
};

export function WorkspaceConnecting() {
  return (
    <main className="unlock-page">
      <section className="unlock-card unlock-connecting" aria-live="polite">
        <div className="unlock-brand">
          <span aria-hidden><Braces size={22} /></span>
          <div><strong>TraceBisect</strong><small>Studio workspace</small></div>
        </div>
        <span className="unlock-spinner" aria-hidden />
        <div className="unlock-copy">
          <p className="unlock-eyebrow">Connecting</p>
          <h1>Checking your workspace</h1>
          <p>Studio is confirming the API, storage mode, and access requirements.</p>
        </div>
      </section>
    </main>
  );
}

export function WorkspaceUnlock({
  busy,
  error,
  managedSession,
  onUnlock,
  sessionTtlSeconds,
}: WorkspaceUnlockProps) {
  const [apiKey, setApiKey] = useState("");
  const sessionLifetime = formatSessionLifetime(sessionTtlSeconds);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiKey.trim() || busy) return;
    onUnlock(apiKey);
  }

  return (
    <main className="unlock-page">
      <section className="unlock-card" aria-labelledby="unlock-title">
        <div className="unlock-brand">
          <span aria-hidden><Braces size={22} /></span>
          <div><strong>TraceBisect</strong><small>Studio workspace</small></div>
        </div>
        <div className="unlock-icon" aria-hidden><LockKeyhole size={25} /></div>
        <div className="unlock-copy">
          <p className="unlock-eyebrow">Protected workspace</p>
          <h1 id="unlock-title">Open your workspace</h1>
          <p>Enter the access key from your workspace owner. The key chooses which traces and guardrails you can see.</p>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="workspace-api-key">Workspace access key</label>
          <div className="unlock-input-wrap">
            <KeyRound size={17} aria-hidden />
            <input
              autoComplete="off"
              id="workspace-api-key"
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Paste your workspace key"
              spellCheck={false}
              type="password"
              value={apiKey}
            />
          </div>
          {error ? <p className="unlock-error" role="alert">{error}</p> : null}
          <button disabled={!apiKey.trim() || busy} type="submit">
            {busy ? "Checking key…" : "Open workspace"}
          </button>
        </form>
        <div className="unlock-privacy-note">
          <ShieldCheck size={17} aria-hidden />
          <p>
            {managedSession ? (
              <><strong>Safer sign-in:</strong> your key is used once and is not saved in the page. Studio creates a protected session for up to {sessionLifetime}; <strong>Lock workspace</strong> ends it early.</>
            ) : (
              <><strong>Session-only:</strong> the key stays in this browser tab and is cleared when the tab closes.</>
            )}
          </p>
        </div>
      </section>
    </main>
  );
}

function formatSessionLifetime(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}
