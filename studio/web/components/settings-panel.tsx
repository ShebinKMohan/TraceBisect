"use client";

import { Activity, ArrowRight, Check, ChevronDown, Clipboard, FileJson2, PlayCircle, ShieldCheck, TerminalSquare, UploadCloud } from "lucide-react";
import { useState } from "react";
import type { StudioHealth, StudioSection, WorkspaceRole } from "@/lib/types";
import { AccessManagementPanel } from "@/components/access-management-panel";
import { TeamManagementPanel } from "@/components/team-management-panel";

const demoCommand = "tracebisect demo";
const uploadCommand = "curl -X POST http://127.0.0.1:8000/api/traces/upload -F 'file=@run.tbtrace'";
const recordCommand = "tracebisect record --output run.tbtrace -- python your_agent.py";
const durableCommand = "TRACEBISECT_STUDIO_STORAGE=sqlite TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db uvicorn tracebisect.studio.api:app --port 8000";
const generateMetricsTokenCommand = "tracebisect studio metrics generate-token";
const validateAlertRulesCommand = "promtool check rules deploy/prometheus/tracebisect-alerts.yml";

function CopyCommand({ command, label }: { command: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = command;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }
  return (
    <div className="setup-command">
      <code>{command}</code>
      <button aria-label={`Copy ${label}`} onClick={() => void copy()} type="button">
        {copied ? <Check size={15} aria-hidden /> : <Clipboard size={15} aria-hidden />}
        <span>{copied ? "Copied" : "Copy"}</span>
      </button>
    </div>
  );
}

export function SettingsPanel({
  health,
  workspaceRole,
  onSectionChange,
}: {
  health: StudioHealth | null;
  workspaceRole: WorkspaceRole | null;
  onSectionChange: (section: StudioSection) => void;
}) {
  const durable = health?.runtime.durable ?? false;
  const postgres = health?.runtime.kind === "postgres";
  const authRequired = health?.auth.required ?? false;
  const credentialSource = health?.auth.credential_source ?? "none";
  const humanAccounts = health?.auth.human_accounts ?? false;
  const emailDelivery = health?.email.enabled ?? false;
  const emailWebhooks = health?.email.webhooks ?? false;
  const metricsAccess = health?.metrics.access ?? null;
  const workspaceStatusTitle = !health
    ? "Checking workspace storage"
    : !durable
      ? "Local workspace with temporary storage"
      : postgres
        ? "This workspace saves and shares your work"
        : "This workspace saves your work";
  const workspaceStatusDescription = !health
    ? "TraceBisect is confirming how this workspace stores data."
    : !durable
      ? "No sign-in is required, but traces, comparisons, and guardrails reset when the Studio service stops."
      : postgres
        ? humanAccounts
          ? `Work in ${health.runtime.workspace_id} stays available after restarts. Account sign-in and team access are enabled.`
          : `Work in ${health.runtime.workspace_id} stays available after restarts and can be shared by multiple Studio services. Account sign-in is not enabled.`
        : authRequired
          ? `Your ${workspaceRole ?? "workspace"} access opens ${health.runtime.workspace_id}. Its traces, comparisons, and guardrails stay available after restarts.`
          : `Traces, comparisons, and guardrails in ${health.runtime.workspace_id} stay available after restarts.`;
  return (
    <section className="setup-guide" data-testid="setup-section">
      <div className="setup-mode-banner">
        <span className="local-status-dot" aria-hidden />
        <div>
          <strong>{workspaceStatusTitle}</strong>
          <p>{workspaceStatusDescription}</p>
        </div>
      </div>

      <div className="setup-heading">
        <h2>Start here — no terminal required</h2>
        <p>Use the Studio screens first. Open the advanced section only when you are ready to connect an application or manage the workspace.</p>
      </div>

      <div className="setup-paths setup-paths-beginner">
        <article>
          <span className="setup-path-number">1</span>
          <PlayCircle size={21} aria-hidden />
          <div>
            <h3>Review a ready-made comparison</h3>
            <p>See how TraceBisect explains a changed tool argument before adding your own data.</p>
          </div>
          <button className="setup-path-action" onClick={() => onSectionChange("runs")} type="button">
            Open comparisons <ArrowRight size={14} aria-hidden />
          </button>
        </article>

        <article>
          <span className="setup-path-number">2</span>
          <UploadCloud size={21} aria-hidden />
          <div>
            <h3>Compare your own runs</h3>
            <p>Choose the run that worked, then the newer run you want to check.</p>
          </div>
          <button className="setup-path-action" onClick={() => onSectionChange("sources")} type="button">
            Choose traces <ArrowRight size={14} aria-hidden />
          </button>
        </article>

        <article>
          <span className="setup-path-number">3</span>
          <ShieldCheck size={21} aria-hidden />
          <div>
            <h3>Keep the behavior you fixed</h3>
            <p>Save the comparison as a guardrail so the same problem is easier to catch next time.</p>
          </div>
          <button className="setup-path-action" onClick={() => onSectionChange("cases")} type="button">
            Open guardrails <ArrowRight size={14} aria-hidden />
          </button>
        </article>
      </div>

      <details className="setup-advanced">
        <summary>
          <span>
            <strong>Advanced setup and workspace administration</strong>
            <small>Application integration, file formats, storage, access, team members, and monitoring</small>
          </span>
          <ChevronDown size={18} aria-hidden />
        </summary>
        <div className="setup-advanced-body">
          <div className="setup-heading">
            <h2>Connect TraceBisect to your application</h2>
            <p>These commands are for developers or workspace operators. They are not required to understand the comparison workflow.</p>
          </div>

          <div className="setup-paths setup-paths-advanced">
            <article>
              <span className="setup-path-number">1</span>
              <PlayCircle size={21} aria-hidden />
              <div>
                <h3>Run the example from a terminal</h3>
                <p>Creates two refund-agent runs and a ready-to-review behavior change.</p>
              </div>
              <CopyCommand command={demoCommand} label="demo command" />
            </article>

            <article>
              <span className="setup-path-number">2</span>
              <UploadCloud size={21} aria-hidden />
              <div>
                <h3>Upload through the Studio API</h3>
                <p>Send a <code>.tbtrace</code> or OTel/OpenInference JSON file directly to the service.</p>
              </div>
              <CopyCommand command={uploadCommand} label="upload command" />
            </article>

            <article>
              <span className="setup-path-number">3</span>
              <TerminalSquare size={21} aria-hidden />
              <div>
                <h3>Record a Python scenario</h3>
                <p>TraceBisect gives your command an output path. Your scenario writes one canonical trace to that path.</p>
              </div>
              <CopyCommand command={recordCommand} label="record command" />
            </article>
          </div>

      <div className="setup-facts">
        <article>
          <FileJson2 size={19} aria-hidden />
          <div><strong>Accepted files</strong><span><code>.tbtrace</code> and OTel/OpenInference <code>.json</code></span></div>
        </article>
        <article>
          <UploadCloud size={19} aria-hidden />
          <div><strong>Upload limit</strong><span>5 MB per file in the default local configuration</span></div>
        </article>
        <article>
          <Activity size={19} aria-hidden />
          <div>
            <strong>API operations</strong>
            <span>
              JSON audit {health?.audit.enabled ? "on" : "off"} · Safe errors {health?.errors.enabled ? "on" : "off"} · Metrics {metricsAccess === "bearer_token" ? "protected" : metricsAccess === "open_local" ? "local" : metricsAccess === "unavailable" ? "need a token" : "checking"}
            </span>
          </div>
        </article>
        <article>
          <ShieldCheck size={19} aria-hidden />
          <div>
            <strong>Workspace access</strong>
            <span>
              {credentialSource === "managed"
                ? `${workspaceRole ? `${workspaceRole[0].toUpperCase()}${workspaceRole.slice(1)} · ` : ""}short-lived browser session backed by a revocable key`
                : credentialSource === "environment"
                  ? "Static environment keys; managed rotation is not enabled"
                  : "Open local mode; no workspace key required"}
            </span>
          </div>
        </article>
      </div>

      <div className="setup-boundary">
        <h2>{postgres ? "Managed PostgreSQL workspace storage is enabled" : durable ? "Durable storage is enabled" : "Keep your work after restarts"}</h2>
        <p>
          {postgres
            ? humanAccounts
              ? "Workspace data, managed keys, human accounts, team membership, recovery codes, and sessions are multi-instance aware. Share invitation links privately; automated invitation email still requires SQLite. Backups and point-in-time recovery belong to your database provider."
              : "Workspace data, managed keys, and browser sessions are multi-instance aware. Human accounts are available when an operator configures the dedicated identity secret. Backups and point-in-time recovery belong to your database provider."
            : durable
            ? "This API is using the workspace-scoped SQLite store. Keep the database file backed up like any other application data."
            : "Switch the API to the built-in SQLite store when you want traces, comparisons, and guardrails to survive a restart."}
        </p>
        {!durable ? <CopyCommand command={durableCommand} label="durable storage command" /> : null}
      </div>

      {humanAccounts && workspaceRole === "admin" ? <TeamManagementPanel /> : null}

      {credentialSource === "managed" && workspaceRole === "admin" ? (
        <AccessManagementPanel />
      ) : null}

      {credentialSource === "managed" && workspaceRole !== "admin" ? (
        <div className="setup-boundary setup-operations">
          <h2>{workspaceRole === "viewer" ? "Viewer access is read-only" : "Editor access is enabled"}</h2>
          <p>
            {workspaceRole === "viewer"
              ? "You can inspect workspace evidence and copy generated tests. Ask a workspace admin for an editor key when you need to upload, compare, save, or rerun data."
              : "You can upload, compare, save, and rerun workspace data. Ask a workspace admin to create, rotate, or revoke access from Settings."}
          </p>
        </div>
      ) : null}

      {authRequired && workspaceRole === "admin" ? (
        <div className="setup-boundary setup-operations">
          <h2>{metricsAccess === "bearer_token" ? "Service monitoring is protected" : "Connect service monitoring"}</h2>
          <p>
            {metricsAccess === "bearer_token"
              ? <>The Prometheus endpoint uses its own read-only token. Workspace keys are rejected. Starter alerts and safe response steps are documented in <code>docs/operations/studio-alert-runbook.md</code>.</>
              : "Protected Studio keeps the metrics endpoint unavailable until you create a separate scraper token. Generate one, save it as TRACEBISECT_STUDIO_METRICS_TOKEN, then restart the API."}
          </p>
          {metricsAccess !== "bearer_token" ? <CopyCommand command={generateMetricsTokenCommand} label="generate metrics token command" /> : null}
          {metricsAccess === "bearer_token" ? <CopyCommand command={validateAlertRulesCommand} label="validate alert rules command" /> : null}
        </div>
      ) : null}

      <div className="setup-boundary">
        <h2>What is not enabled yet</h2>
        <p>
          {authRequired
            ? credentialSource === "managed"
              ? humanAccounts
                ? emailDelivery
                  ? emailWebhooks
                    ? "Sender-domain monitoring, email ownership re-verification, optional multi-factor sign-in, hosted ingestion, and billing remain production milestones—not active features in this build."
                    : "Delivery and bounce tracking, email ownership re-verification, optional multi-factor sign-in, hosted ingestion, and billing remain production milestones—not active features in this build."
                  : "Automated invitation email delivery, email verification, optional multi-factor sign-in, hosted ingestion, and billing remain production milestones—not active features in this build."
                : "Human accounts, recovery, invitations, hosted ingestion, and billing are future production milestones—not active features in this build."
              : "Managed user accounts, hashed key rotation, hosted ingestion, team administration, and billing are future production milestones—not active features in this build."
            : "Authentication, request-scoped workspace access, hosted ingestion, team access, billing, and API keys are future production milestones—not active features in this build."}
        </p>
      </div>
        </div>
      </details>
    </section>
  );
}
