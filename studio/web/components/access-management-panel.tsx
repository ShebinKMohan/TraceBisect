"use client";

import { Check, Clipboard, KeyRound, RefreshCw, ShieldCheck, UserPlus, UsersRound, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  createWorkspaceAccessKey,
  fetchWorkspaceAccessKeys,
  revokeWorkspaceAccessKey,
} from "@/lib/api";
import type {
  IssuedWorkspaceAccessKey,
  WorkspaceAccessKey,
  WorkspaceRole,
} from "@/lib/types";

const roleHelp: Record<WorkspaceRole, string> = {
  viewer: "Can review traces, comparisons, and generated tests. Cannot change workspace data.",
  editor: "Can upload traces, compare runs, save guardrails, and rerun checks.",
  admin: "Can do everything an editor can and manage workspace access.",
};

export function AccessManagementPanel() {
  const [keys, setKeys] = useState<WorkspaceAccessKey[]>([]);
  const [currentKeyId, setCurrentKeyId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("viewer");
  const [expiresInDays, setExpiresInDays] = useState(30);
  const [issued, setIssued] = useState<IssuedWorkspaceAccessKey | null>(null);
  const [confirmRevokeId, setConfirmRevokeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const activeCount = useMemo(
    () => keys.filter((key) => key.status === "active").length,
    [keys],
  );

  useEffect(() => {
    void loadKeys();
  }, []);

  async function loadKeys() {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchWorkspaceAccessKeys();
      setKeys(payload.keys);
      setCurrentKeyId(payload.current_key_id);
    } catch (loadError) {
      setError(messageFor(loadError, "Studio could not load workspace access."));
    } finally {
      setLoading(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedLabel = label.trim();
    if (!normalizedLabel || busy || issued) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createWorkspaceAccessKey({
        label: normalizedLabel,
        role,
        expires_in_days: expiresInDays,
      });
      setIssued(result);
      setKeys((items) => [result.record, ...items]);
      setCurrentKeyId(result.current_key_id);
      setLabel("");
      setCopied(false);
    } catch (createError) {
      setError(messageFor(createError, "Studio could not create the access key."));
    } finally {
      setBusy(false);
    }
  }

  async function copyIssuedKey() {
    if (!issued) return;
    await copyText(issued.api_key);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  async function revoke(key: WorkspaceAccessKey) {
    if (busy || key.status !== "active" || key.key_id === currentKeyId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const revoked = await revokeWorkspaceAccessKey(key.key_id);
      setKeys((items) => items.map((item) => item.key_id === revoked.key_id ? revoked : item));
      setConfirmRevokeId(null);
      setNotice(`${key.label} can no longer open this workspace.`);
    } catch (revokeError) {
      setError(messageFor(revokeError, "Studio could not revoke the access key."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="access-management" aria-labelledby="access-management-title">
      <div className="access-management-heading">
        <span aria-hidden><UsersRound size={20} /></span>
        <div>
          <h2 id="access-management-title">Workspace access</h2>
          <p>Create a role-based key here, send it through a secure channel, and revoke it when it is no longer needed.</p>
        </div>
        <div className="access-key-count" aria-label={`${activeCount} active access keys`}>
          <strong>{activeCount}</strong>
          <span>active</span>
        </div>
      </div>

      {error ? <p className="access-message access-message-error" role="alert">{error}</p> : null}
      {notice ? <p className="access-message access-message-success" role="status">{notice}</p> : null}

      {issued ? (
        <div className="issued-key-panel" aria-live="polite">
          <div className="issued-key-heading">
            <span aria-hidden><KeyRound size={19} /></span>
            <div>
              <h3>Copy this key now</h3>
              <p>Studio stores only a protected hash. This key cannot be shown again after you close this message.</p>
            </div>
          </div>
          <code>{issued.api_key}</code>
          <div className="issued-key-actions">
            <button className="access-primary-button" onClick={() => void copyIssuedKey()} type="button">
              {copied ? <Check size={16} aria-hidden /> : <Clipboard size={16} aria-hidden />}
              {copied ? "Copied" : "Copy access key"}
            </button>
            <button className="access-secondary-button" onClick={() => setIssued(null)} type="button">
              <Check size={16} aria-hidden />
              I saved it
            </button>
          </div>
        </div>
      ) : (
        <form className="access-create-form" onSubmit={submit}>
          <div className="access-form-field access-form-name">
            <label htmlFor="access-key-name">Who or what will use this key?</label>
            <input
              autoComplete="off"
              id="access-key-name"
              maxLength={80}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="Example: Priya or CI uploads"
              value={label}
            />
          </div>
          <div className="access-form-field">
            <label htmlFor="access-key-role">Permission</label>
            <select
              id="access-key-role"
              onChange={(event) => setRole(event.target.value as WorkspaceRole)}
              value={role}
            >
              <option value="viewer">Viewer · review only</option>
              <option value="editor">Editor · create and change</option>
              <option value="admin">Admin · manage everything</option>
            </select>
          </div>
          <div className="access-form-field">
            <label htmlFor="access-key-expiry">Expires after</label>
            <select
              id="access-key-expiry"
              onChange={(event) => setExpiresInDays(Number(event.target.value))}
              value={expiresInDays}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
              <option value={365}>1 year</option>
            </select>
          </div>
          <p className="access-role-help"><ShieldCheck size={15} aria-hidden /> {roleHelp[role]}</p>
          <button className="access-primary-button" disabled={!label.trim() || busy} type="submit">
            <UserPlus size={16} aria-hidden />
            {busy ? "Creating…" : "Create access key"}
          </button>
        </form>
      )}

      <div className="access-list-heading">
        <div>
          <h3>Existing access</h3>
          <p>Names and expiry dates remain visible; secret key values do not.</p>
        </div>
        <button aria-label="Refresh workspace access" disabled={loading || busy} onClick={() => void loadKeys()} type="button">
          <RefreshCw className={loading ? "access-spin" : undefined} size={15} aria-hidden />
          Refresh
        </button>
      </div>

      {loading ? <p className="access-empty" role="status">Loading workspace access…</p> : null}
      {!loading && keys.length === 0 ? <p className="access-empty">No access keys have been created for this workspace.</p> : null}
      {!loading && keys.length > 0 ? (
        <div className="access-key-list">
          {keys.map((key) => {
            const current = key.key_id === currentKeyId;
            const confirming = key.key_id === confirmRevokeId;
            return (
              <article className="access-key-row" key={key.key_id}>
                <div className="access-key-identity">
                  <span aria-hidden><KeyRound size={16} /></span>
                  <div>
                    <div className="access-key-title">
                      <strong>{key.label}</strong>
                      {current ? <span className="access-current-label">Current sign-in</span> : null}
                    </div>
                    <p>{roleName(key.role)} · expires {formatDate(key.expires_at)} · ID {key.key_id}</p>
                  </div>
                </div>
                <span className={`access-key-status access-key-status-${key.status}`}>{statusName(key.status)}</span>
                <div className="access-key-actions">
                  {confirming ? (
                    <>
                      <button className="access-cancel-button" disabled={busy} onClick={() => setConfirmRevokeId(null)} type="button"><X size={14} aria-hidden /> Cancel</button>
                      <button className="access-revoke-confirm" disabled={busy} onClick={() => void revoke(key)} type="button">{busy ? "Revoking…" : "Confirm revoke"}</button>
                    </>
                  ) : (
                    <button
                      className="access-revoke-button"
                      disabled={key.status !== "active" || current || busy}
                      onClick={() => setConfirmRevokeId(key.key_id)}
                      title={current ? "Sign in with a replacement key before revoking this one" : key.status !== "active" ? "This key is no longer active" : "Revoke this access key"}
                      type="button"
                    >
                      Revoke
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

function roleName(role: WorkspaceRole): string {
  return `${role[0].toUpperCase()}${role.slice(1)}`;
}

function statusName(status: WorkspaceAccessKey["status"]): string {
  return `${status[0].toUpperCase()}${status.slice(1)}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
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
