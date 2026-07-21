"use client";

import {
  Check,
  Clipboard,
  MailPlus,
  RefreshCw,
  ShieldCheck,
  UserMinus,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  createWorkspaceInvitation,
  fetchWorkspaceInvitations,
  fetchWorkspaceMembers,
  removeWorkspaceMember,
  revokeWorkspaceInvitation,
  updateWorkspaceMember,
} from "@/lib/api";
import type {
  WorkspaceInvitation,
  WorkspaceMembership,
  WorkspaceRole,
} from "@/lib/types";

const roleHelp: Record<WorkspaceRole, string> = {
  viewer: "Can review evidence but cannot change workspace data.",
  editor: "Can upload, compare, save guardrails, and rerun checks.",
  admin: "Can do everything an editor can and manage people and access keys.",
};

export function TeamManagementPanel() {
  const [members, setMembers] = useState<WorkspaceMembership[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<WorkspaceRole>("viewer");
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [issuedLink, setIssuedLink] = useState<string | null>(null);
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pendingInvitations = useMemo(
    () => invitations.filter((item) => item.status === "pending"),
    [invitations],
  );

  useEffect(() => {
    void loadTeam();
  }, []);

  async function loadTeam() {
    setLoading(true);
    setError(null);
    try {
      const [memberPayload, invitationPayload] = await Promise.all([
        fetchWorkspaceMembers(),
        fetchWorkspaceInvitations(),
      ]);
      setMembers(memberPayload.members);
      setCurrentUserId(memberPayload.current_user_id);
      setInvitations(invitationPayload);
    } catch (loadError) {
      setError(messageFor(loadError, "Studio could not load the workspace team."));
    } finally {
      setLoading(false);
    }
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || busy || issuedLink) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createWorkspaceInvitation({
        email: email.trim(),
        role,
        expires_in_days: expiresInDays,
      });
      const link = `${window.location.origin}${window.location.pathname}#invite=${encodeURIComponent(result.invitation_token)}`;
      setIssuedLink(link);
      setInvitations((items) => [
        result.invitation,
        ...items.filter((item) => item.invitation_id !== result.invitation.invitation_id),
      ]);
      setEmail("");
      setCopied(false);
    } catch (inviteError) {
      setError(messageFor(inviteError, "Studio could not create the invitation."));
    } finally {
      setBusy(false);
    }
  }

  async function copyIssuedLink() {
    if (!issuedLink) return;
    await copyText(issuedLink);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  async function changeRole(member: WorkspaceMembership, nextRole: WorkspaceRole) {
    if (busy || member.role === nextRole) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await updateWorkspaceMember(member.user_id, nextRole);
      setMembers((items) => items.map((item) => item.user_id === updated.user_id ? updated : item));
      setNotice(`${member.display_name} now has ${roleName(nextRole).toLowerCase()} access.`);
    } catch (updateError) {
      setError(messageFor(updateError, "Studio could not change this permission."));
    } finally {
      setBusy(false);
    }
  }

  async function remove(member: WorkspaceMembership) {
    if (busy || member.user_id === currentUserId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await removeWorkspaceMember(member.user_id);
      setMembers((items) => items.filter((item) => item.user_id !== member.user_id));
      setConfirmRemoveId(null);
      setNotice(`${member.display_name} no longer has access to this workspace.`);
    } catch (removeError) {
      setError(messageFor(removeError, "Studio could not remove this person."));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(invitation: WorkspaceInvitation) {
    if (busy || invitation.status !== "pending") return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const revoked = await revokeWorkspaceInvitation(invitation.invitation_id);
      setInvitations((items) => items.map((item) => item.invitation_id === revoked.invitation_id ? revoked : item));
      setNotice(`The invitation for ${invitation.email} is no longer active.`);
    } catch (revokeError) {
      setError(messageFor(revokeError, "Studio could not revoke this invitation."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="access-management team-management" aria-labelledby="team-management-title">
      <div className="access-management-heading">
        <span aria-hidden><UsersRound size={20} /></span>
        <div>
          <h2 id="team-management-title">People and invitations</h2>
          <p>Invite people by email, choose what they can do, and remove access immediately.</p>
        </div>
        <div className="access-key-count" aria-label={`${members.length} workspace members`}>
          <strong>{members.length}</strong><span>people</span>
        </div>
      </div>

      {error ? <p className="access-message access-message-error" role="alert">{error}</p> : null}
      {notice ? <p className="access-message access-message-success" role="status">{notice}</p> : null}

      {issuedLink ? (
        <div className="issued-key-panel" aria-live="polite">
          <div className="issued-key-heading">
            <span aria-hidden><MailPlus size={19} /></span>
            <div><h3>Send this invitation link now</h3><p>The secret link is shown once. Share it privately with the invited person.</p></div>
          </div>
          <code>{issuedLink}</code>
          <div className="issued-key-actions">
            <button className="access-primary-button" onClick={() => void copyIssuedLink()} type="button">{copied ? <Check size={16} aria-hidden /> : <Clipboard size={16} aria-hidden />}{copied ? "Copied" : "Copy invitation link"}</button>
            <button className="access-secondary-button" onClick={() => setIssuedLink(null)} type="button"><Check size={16} aria-hidden />I sent it</button>
          </div>
        </div>
      ) : (
        <form className="access-create-form team-invite-form" onSubmit={invite}>
          <div className="access-form-field access-form-name">
            <label htmlFor="team-invite-email">Who do you want to invite?</label>
            <input autoComplete="email" id="team-invite-email" maxLength={254} onChange={(event) => setEmail(event.target.value)} placeholder="person@company.com" type="email" value={email} />
          </div>
          <div className="access-form-field">
            <label htmlFor="team-invite-role">Permission</label>
            <select id="team-invite-role" onChange={(event) => setRole(event.target.value as WorkspaceRole)} value={role}>
              <option value="viewer">Viewer · review only</option>
              <option value="editor">Editor · create and change</option>
              <option value="admin">Admin · manage everything</option>
            </select>
          </div>
          <div className="access-form-field">
            <label htmlFor="team-invite-expiry">Link expires after</label>
            <select id="team-invite-expiry" onChange={(event) => setExpiresInDays(Number(event.target.value))} value={expiresInDays}>
              <option value={1}>1 day</option><option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option>
            </select>
          </div>
          <p className="access-role-help"><ShieldCheck size={15} aria-hidden />{roleHelp[role]}</p>
          <button className="access-primary-button" disabled={!email.trim() || busy} type="submit"><MailPlus size={16} aria-hidden />{busy ? "Creating…" : "Create invitation"}</button>
        </form>
      )}

      <div className="access-list-heading">
        <div><h3>Workspace people</h3><p>Permission changes affect active sessions immediately.</p></div>
        <button aria-label="Refresh workspace team" disabled={loading || busy} onClick={() => void loadTeam()} type="button"><RefreshCw className={loading ? "access-spin" : undefined} size={15} aria-hidden />Refresh</button>
      </div>
      {loading ? <p className="access-empty" role="status">Loading workspace people…</p> : null}
      {!loading && members.length === 0 ? <p className="access-empty">No people have accepted an invitation yet.</p> : null}
      {!loading && members.length ? (
        <div className="access-key-list">
          {members.map((member) => {
            const current = member.user_id === currentUserId;
            const confirming = confirmRemoveId === member.user_id;
            return (
              <article className="access-key-row team-member-row" key={member.user_id}>
                <div className="access-key-identity"><span aria-hidden><UsersRound size={16} /></span><div><div className="access-key-title"><strong>{member.display_name}</strong>{current ? <span className="access-current-label">You</span> : null}</div><p>{member.email} · joined {formatDate(member.created_at)}</p></div></div>
                <select aria-label={`Permission for ${member.display_name}`} disabled={busy || current} onChange={(event) => void changeRole(member, event.target.value as WorkspaceRole)} title={current ? "Ask another admin to change your own permission" : "Change workspace permission"} value={member.role}><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option></select>
                <div className="access-key-actions">
                  {confirming ? <><button className="access-cancel-button" disabled={busy} onClick={() => setConfirmRemoveId(null)} type="button"><X size={14} aria-hidden />Cancel</button><button className="access-revoke-confirm" disabled={busy} onClick={() => void remove(member)} type="button">{busy ? "Removing…" : "Confirm remove"}</button></> : <button className="access-revoke-button" disabled={current || busy} onClick={() => setConfirmRemoveId(member.user_id)} title={current ? "You cannot remove the account in current use" : "Remove this person"} type="button"><UserMinus size={14} aria-hidden />Remove</button>}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}

      <div className="access-list-heading team-pending-heading"><div><h3>Pending invitations</h3><p>{pendingInvitations.length ? `${pendingInvitations.length} waiting to be accepted` : "No active invitation links"}</p></div></div>
      {pendingInvitations.length ? <div className="access-key-list">{pendingInvitations.map((invitation) => <article className="access-key-row team-invitation-row" key={invitation.invitation_id}><div className="access-key-identity"><span aria-hidden><MailPlus size={16} /></span><div><strong>{invitation.email}</strong><p>{roleName(invitation.role)} · expires {formatDate(invitation.expires_at)}</p></div></div><span className="access-key-status access-key-status-active">Pending</span><div className="access-key-actions"><button className="access-revoke-button" disabled={busy} onClick={() => void revoke(invitation)} type="button">Revoke</button></div></article>)}</div> : <p className="access-empty">Create an invitation when someone needs account access.</p>}
    </section>
  );
}

function roleName(role: WorkspaceRole): string {
  return `${role[0].toUpperCase()}${role.slice(1)}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "unknown" : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
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
