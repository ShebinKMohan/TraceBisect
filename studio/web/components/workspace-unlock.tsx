"use client";

import {
  Braces,
  Check,
  Clipboard,
  CircleAlert,
  KeyRound,
  LifeBuoy,
  LockKeyhole,
  Mail,
  RefreshCw,
  ShieldCheck,
  UserPlus,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  acceptWorkspaceInvitation,
  previewWorkspaceInvitation,
  recoverStudioAccount,
} from "@/lib/api";
import type {
  IdentityLoginResult,
  IdentityWorkspaceChoice,
  WorkspaceInvitation,
} from "@/lib/types";

type UnlockMode = "account" | "key" | "recovery" | "invitation" | "recovery-codes";

type WorkspaceUnlockProps = {
  busy: boolean;
  error: string | null;
  humanAccounts: boolean;
  managedSession: boolean;
  onAccountLogin: (
    email: string,
    password: string,
    workspaceId?: string,
  ) => Promise<IdentityLoginResult>;
  onUnlock: (apiKey: string) => void;
  sessionTtlSeconds: number;
};

export function WorkspaceConnecting() {
  return (
    <main className="unlock-page">
      <section className="unlock-card unlock-connecting" aria-live="polite">
        <Brand />
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

export function WorkspaceConnectionError({
  error,
  onRetry,
}: {
  error: string | null;
  onRetry: () => void;
}) {
  return (
    <main className="unlock-page">
      <section className="unlock-card unlock-connection-error" aria-labelledby="connection-error-title">
        <Brand />
        <div className="unlock-icon unlock-icon-error" aria-hidden>
          <CircleAlert size={25} />
        </div>
        <div className="unlock-copy">
          <p className="unlock-eyebrow">Connection problem</p>
          <h1 id="connection-error-title">Studio could not open this workspace</h1>
          <p>The service behind this workspace did not respond. Your data was not changed.</p>
        </div>
        <button className="unlock-continue-button" onClick={onRetry} type="button">
          <RefreshCw size={16} aria-hidden />
          Try again
        </button>
        {error ? (
          <details className="unlock-technical-details">
            <summary>Technical details</summary>
            <code>{error}</code>
          </details>
        ) : null}
      </section>
    </main>
  );
}

export function WorkspaceUnlock({
  busy,
  error,
  humanAccounts,
  managedSession,
  onAccountLogin,
  onUnlock,
  sessionTtlSeconds,
}: WorkspaceUnlockProps) {
  const [mode, setMode] = useState<UnlockMode>(humanAccounts ? "account" : "key");
  const [apiKey, setApiKey] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [invitationToken, setInvitationToken] = useState("");
  const [invitation, setInvitation] = useState<WorkspaceInvitation | null>(null);
  const [workspaces, setWorkspaces] = useState<IdentityWorkspaceChoice[]>([]);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [resultMessage, setResultMessage] = useState("");
  const [localBusy, setLocalBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const sessionLifetime = formatSessionLifetime(sessionTtlSeconds);
  const isBusy = busy || localBusy;

  useEffect(() => {
    if (!humanAccounts) return;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("invite");
    if (!token) return;
    setInvitationToken(token);
    setMode("invitation");
    setLocalBusy(true);
    void previewWorkspaceInvitation(token)
      .then((record) => {
        setInvitation(record);
        setEmail(record.email);
      })
      .catch((previewError: unknown) => {
        setLocalError(messageFor(previewError, "This invitation could not be opened."));
      })
      .finally(() => setLocalBusy(false));
  }, [humanAccounts]);

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password || isBusy) return;
    setLocalError(null);
    try {
      const result = await onAccountLogin(email.trim(), password);
      if (result.kind === "workspace_choice") setWorkspaces(result.workspaces);
    } catch (loginError) {
      setLocalError(messageFor(loginError, "Studio could not sign you in."));
    }
  }

  async function chooseWorkspace(workspaceId: string) {
    if (isBusy) return;
    setLocalError(null);
    try {
      await onAccountLogin(email.trim(), password, workspaceId);
    } catch (loginError) {
      setLocalError(messageFor(loginError, "Studio could not open that workspace."));
    }
  }

  function submitKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!apiKey.trim() || isBusy) return;
    onUnlock(apiKey);
  }

  async function acceptInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !invitation ||
      !displayName.trim() ||
      !password ||
      password !== confirmPassword ||
      isBusy
    ) return;
    setLocalBusy(true);
    setLocalError(null);
    try {
      const result = await acceptWorkspaceInvitation({
        invitation_token: invitationToken,
        display_name: displayName.trim(),
        password,
      });
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      setEmail(result.email);
      setRecoveryCodes(result.recovery_codes);
      setResultMessage(
        result.recovery_codes.length
          ? "Your account is ready. Save these one-time recovery codes before signing in."
          : "This workspace was added to your existing account. Sign in with your current password.",
      );
      setMode("recovery-codes");
    } catch (acceptError) {
      setLocalError(messageFor(acceptError, "Studio could not accept this invitation."));
    } finally {
      setLocalBusy(false);
    }
  }

  async function recover(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !email.trim() ||
      !recoveryCode.trim() ||
      !newPassword ||
      newPassword !== confirmPassword ||
      isBusy
    ) return;
    setLocalBusy(true);
    setLocalError(null);
    try {
      const result = await recoverStudioAccount({
        email: email.trim(),
        recovery_code: recoveryCode.trim(),
        new_password: newPassword,
      });
      if (result.accepted) {
        setPassword("");
        setRecoveryCodes(result.recovery_codes);
        setResultMessage(result.message);
        setMode("recovery-codes");
      } else {
        setLocalError(result.message);
      }
    } catch (recoveryError) {
      setLocalError(messageFor(recoveryError, "Studio could not process account recovery."));
    } finally {
      setLocalBusy(false);
    }
  }

  async function copyRecoveryCodes() {
    await copyText(recoveryCodes.join("\n"));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  function showAccount() {
    setMode("account");
    setWorkspaces([]);
    setRecoveryCodes([]);
    setConfirmPassword("");
    setLocalError(null);
  }

  return (
    <main className="unlock-page">
      <section className="unlock-card" aria-labelledby="unlock-title">
        <Brand />
        <div className="unlock-icon" aria-hidden>
          {mode === "invitation" ? <UserPlus size={25} /> : mode === "recovery" ? <LifeBuoy size={25} /> : <LockKeyhole size={25} />}
        </div>

        {mode === "account" ? (
          <>
            <UnlockCopy eyebrow="Protected workspace" title="Sign in to your workspace">
              Use the email and password you created from your invitation. Your role decides what you can review or change.
            </UnlockCopy>
            {workspaces.length ? (
              <div className="unlock-workspace-choices" aria-live="polite">
                <strong>Choose a workspace</strong>
                <p>Your account belongs to more than one workspace.</p>
                {workspaces.map((workspace) => (
                  <button key={workspace.workspace_id} onClick={() => void chooseWorkspace(workspace.workspace_id)} type="button">
                    <span>{workspace.workspace_id}</span>
                    <small>{roleName(workspace.role)} access</small>
                  </button>
                ))}
              </div>
            ) : (
              <form onSubmit={submitAccount}>
                <label htmlFor="account-email">Email</label>
                <div className="unlock-input-wrap">
                  <Mail size={17} aria-hidden />
                  <input autoComplete="email" id="account-email" onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" type="email" value={email} />
                </div>
                <label htmlFor="account-password">Password</label>
                <div className="unlock-input-wrap">
                  <KeyRound size={17} aria-hidden />
                  <input autoComplete="current-password" id="account-password" maxLength={128} minLength={15} onChange={(event) => setPassword(event.target.value)} placeholder="Your password" type="password" value={password} />
                </div>
                <ErrorMessage error={localError ?? error} />
                <button disabled={!email.trim() || !password || isBusy} type="submit">
                  {isBusy ? "Signing in…" : "Sign in"}
                </button>
              </form>
            )}
            <div className="unlock-link-row">
              <button onClick={() => { setMode("recovery"); setLocalError(null); }} type="button">Use a recovery code</button>
              <button onClick={() => { setMode("key"); setLocalError(null); }} type="button">Use an access key</button>
            </div>
          </>
        ) : null}

        {mode === "key" ? (
          <>
            <UnlockCopy eyebrow="Access key" title="Open a workspace with a key">
              Use this for automation, emergency access, or before the first person has accepted an invitation.
            </UnlockCopy>
            <form onSubmit={submitKey}>
              <label htmlFor="workspace-api-key">Workspace access key</label>
              <div className="unlock-input-wrap">
                <KeyRound size={17} aria-hidden />
                <input autoComplete="off" id="workspace-api-key" onChange={(event) => setApiKey(event.target.value)} placeholder="Paste your workspace key" spellCheck={false} type="password" value={apiKey} />
              </div>
              <ErrorMessage error={localError ?? error} />
              <button disabled={!apiKey.trim() || isBusy} type="submit">
                {isBusy ? "Checking key…" : "Open workspace"}
              </button>
            </form>
            {humanAccounts ? <div className="unlock-link-row"><button onClick={showAccount} type="button">Sign in with your account</button></div> : null}
          </>
        ) : null}

        {mode === "invitation" ? (
          <>
            <UnlockCopy eyebrow="Workspace invitation" title={invitation ? `Join ${invitation.workspace_id}` : "Opening your invitation"}>
              {invitation ? `${invitation.email} was invited with ${roleName(invitation.role).toLowerCase()} access.` : "Studio is checking that this invitation is current."}
            </UnlockCopy>
            {localBusy ? <p className="unlock-status">Checking invitation…</p> : null}
            {invitation ? (
              <form onSubmit={acceptInvitation}>
                <label htmlFor="invite-name">Your name</label>
                <div className="unlock-input-wrap"><UserPlus size={17} aria-hidden /><input autoComplete="name" id="invite-name" maxLength={80} onChange={(event) => setDisplayName(event.target.value)} placeholder="How teammates will see you" value={displayName} /></div>
                <label htmlFor="invite-password">Account password</label>
                <div className="unlock-input-wrap"><KeyRound size={17} aria-hidden /><input autoComplete="new-password" id="invite-password" maxLength={128} minLength={15} onChange={(event) => setPassword(event.target.value)} placeholder="At least 15 characters" type="password" value={password} /></div>
                <label htmlFor="invite-password-confirm">Confirm password</label>
                <div className="unlock-input-wrap"><KeyRound size={17} aria-hidden /><input autoComplete="new-password" id="invite-password-confirm" maxLength={128} minLength={15} onChange={(event) => setConfirmPassword(event.target.value)} placeholder="Enter it again" type="password" value={confirmPassword} /></div>
                <p className="unlock-field-help">Use 15–128 characters. Spaces are allowed. If you already have an account, enter its current password in both fields.</p>
                {confirmPassword && password !== confirmPassword ? <p className="unlock-error" role="alert">The passwords do not match.</p> : null}
                <ErrorMessage error={localError} />
                <button disabled={!displayName.trim() || password.length < 15 || password !== confirmPassword || isBusy} type="submit">{isBusy ? "Creating account…" : "Accept invitation"}</button>
              </form>
            ) : <ErrorMessage error={localError} />}
          </>
        ) : null}

        {mode === "recovery" ? (
          <>
            <UnlockCopy eyebrow="Account recovery" title="Reset with a saved code">
              Enter one recovery code you saved when the account was created. A successful reset signs out every device.
            </UnlockCopy>
            <form onSubmit={recover}>
              <label htmlFor="recovery-email">Email</label>
              <div className="unlock-input-wrap"><Mail size={17} aria-hidden /><input autoComplete="email" id="recovery-email" onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" type="email" value={email} /></div>
              <label htmlFor="recovery-code">Recovery code</label>
              <div className="unlock-input-wrap"><LifeBuoy size={17} aria-hidden /><input autoComplete="off" id="recovery-code" onChange={(event) => setRecoveryCode(event.target.value)} placeholder="tbrc_…" spellCheck={false} value={recoveryCode} /></div>
              <label htmlFor="recovery-password">New password</label>
              <div className="unlock-input-wrap"><KeyRound size={17} aria-hidden /><input autoComplete="new-password" id="recovery-password" maxLength={128} minLength={15} onChange={(event) => setNewPassword(event.target.value)} placeholder="At least 15 characters" type="password" value={newPassword} /></div>
              <label htmlFor="recovery-password-confirm">Confirm new password</label>
              <div className="unlock-input-wrap"><KeyRound size={17} aria-hidden /><input autoComplete="new-password" id="recovery-password-confirm" maxLength={128} minLength={15} onChange={(event) => setConfirmPassword(event.target.value)} placeholder="Enter it again" type="password" value={confirmPassword} /></div>
              {confirmPassword && newPassword !== confirmPassword ? <p className="unlock-error" role="alert">The passwords do not match.</p> : null}
              <ErrorMessage error={localError} />
              <button disabled={!email.trim() || !recoveryCode.trim() || newPassword.length < 15 || newPassword !== confirmPassword || isBusy} type="submit">{isBusy ? "Checking code…" : "Reset password"}</button>
            </form>
            <div className="unlock-link-row"><button onClick={showAccount} type="button">Back to sign in</button></div>
          </>
        ) : null}

        {mode === "recovery-codes" ? (
          <>
            <UnlockCopy eyebrow="Account ready" title={recoveryCodes.length ? "Save your recovery codes" : "Invitation accepted"}>
              {resultMessage}
            </UnlockCopy>
            {recoveryCodes.length ? (
              <div className="unlock-recovery-codes" aria-live="polite">
                <code>{recoveryCodes.join("\n")}</code>
                <button onClick={() => void copyRecoveryCodes()} type="button">{copied ? <Check size={16} aria-hidden /> : <Clipboard size={16} aria-hidden />}{copied ? "Copied" : "Copy all codes"}</button>
                <p>Each code works once. Store them in a password manager, not in this browser.</p>
              </div>
            ) : null}
            <button className="unlock-continue-button" onClick={showAccount} type="button">Continue to sign in</button>
          </>
        ) : null}

        {mode !== "invitation" && mode !== "recovery-codes" ? (
          <div className="unlock-privacy-note">
            <ShieldCheck size={17} aria-hidden />
            <p>
              <strong>Protected session:</strong> Studio uses a private browser cookie for up to {sessionLifetime}. <strong>Lock workspace</strong> ends it early. {managedSession ? "Access keys are exchanged once and are not saved in the page." : "Access keys stay only in this browser tab."}
            </p>
          </div>
        ) : null}
      </section>
    </main>
  );
}

function Brand() {
  return <div className="unlock-brand"><span aria-hidden><Braces size={22} /></span><div><strong>TraceBisect</strong><small>Studio workspace</small></div></div>;
}

function UnlockCopy({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return <div className="unlock-copy"><p className="unlock-eyebrow">{eyebrow}</p><h1 id="unlock-title">{title}</h1><p>{children}</p></div>;
}

function ErrorMessage({ error }: { error: string | null }) {
  return error ? <p className="unlock-error" role="alert">{error}</p> : null;
}

function roleName(role: string): string {
  return `${role[0]?.toUpperCase() ?? ""}${role.slice(1)}`;
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
}

function formatSessionLifetime(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}
