"use client";

import { Activity, Check, Clipboard, FileJson2, PlayCircle, ShieldCheck, TerminalSquare, UploadCloud } from "lucide-react";
import { useState } from "react";
import type { StudioHealth, WorkspaceRole } from "@/lib/types";

const demoCommand = "tracebisect demo";
const uploadCommand = "curl -X POST http://127.0.0.1:8000/api/traces/upload -F 'file=@run.tbtrace'";
const recordCommand = "tracebisect record --output run.tbtrace -- python your_agent.py";
const durableCommand = "TRACEBISECT_STUDIO_STORAGE=sqlite TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db uvicorn tracebisect.studio.api:app --port 8000";
const listKeysCommand = "tracebisect studio keys list --database .tracebisect/studio.db";
const generateMetricsTokenCommand = "tracebisect studio metrics generate-token";

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

export function SettingsPanel({ health, workspaceRole }: { health: StudioHealth | null; workspaceRole: WorkspaceRole | null }) {
  const durable = health?.runtime.durable ?? false;
  const authRequired = health?.auth.required ?? false;
  const credentialSource = health?.auth.credential_source ?? "none";
  const metricsAccess = health?.metrics.access ?? null;
  const workspaceId = health?.runtime.workspace_id ?? "team-a";
  const createKeyCommand = `tracebisect studio keys create --database .tracebisect/studio.db --workspace ${workspaceId} --name 'Browser access' --role editor`;
  return (
    <section className="setup-guide" data-testid="setup-section">
      <div className="setup-mode-banner">
        <span className="local-status-dot" aria-hidden />
        <div>
          <strong>
            {health
              ? durable
                ? authRequired
                  ? "Running with protected durable storage"
                  : "Running with durable workspace storage"
                : "Running as a local workspace"
              : "Checking workspace storage"}
          </strong>
          <p>
            {health
              ? durable
                ? authRequired
                  ? `Your ${workspaceRole ?? "workspace"} key grants access to ${health.runtime.workspace_id}. Its traces, comparisons, and guardrails are saved across API restarts.`
                  : `Traces, comparisons, and guardrails for ${health.runtime.workspace_id} are saved across API restarts.`
                : "No account or API key is required. Uploaded data is held in memory and resets with the API process."
              : "Waiting for the API to confirm whether this workspace is temporary or durable."}
          </p>
        </div>
      </div>

      <div className="setup-heading">
        <h2>Choose the easiest way to start</h2>
        <p>Use the demo first. Connect your own application only when the comparison workflow feels familiar.</p>
      </div>

      <div className="setup-paths">
        <article>
          <span className="setup-path-number">1</span>
          <PlayCircle size={21} aria-hidden />
          <div>
            <h3>Try the built-in demo</h3>
            <p>Creates two refund-agent runs and shows a changed tool argument. No files or configuration needed.</p>
          </div>
          <CopyCommand command={demoCommand} label="demo command" />
        </article>

        <article>
          <span className="setup-path-number">2</span>
          <UploadCloud size={21} aria-hidden />
          <div>
            <h3>Upload exported traces</h3>
            <p>Use the Trace library for the simplest path, or send a <code>.tbtrace</code> or OTel/OpenInference JSON file to the local API.</p>
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
              JSON audit {health?.audit.enabled ? "on" : "off"} · Metrics {metricsAccess === "bearer_token" ? "protected" : metricsAccess === "open_local" ? "local" : metricsAccess === "unavailable" ? "need a token" : "checking"}
            </span>
          </div>
        </article>
        <article>
          <ShieldCheck size={19} aria-hidden />
          <div>
            <strong>Workspace access</strong>
            <span>
              {credentialSource === "managed"
                ? `${workspaceRole ? `${workspaceRole[0].toUpperCase()}${workspaceRole.slice(1)} · ` : ""}hashed, expiring key with operator revocation`
                : credentialSource === "environment"
                  ? "Static environment keys; managed rotation is not enabled"
                  : "Open local mode; no workspace key required"}
            </span>
          </div>
        </article>
      </div>

      <div className="setup-boundary">
        <h2>{durable ? "Durable storage is enabled" : "Keep your work after restarts"}</h2>
        <p>
          {durable
            ? "This API is using the workspace-scoped SQLite store. Keep the database file backed up like any other application data."
            : "Switch the API to the built-in SQLite store when you want traces, comparisons, and guardrails to survive a restart."}
        </p>
        {!durable ? <CopyCommand command={durableCommand} label="durable storage command" /> : null}
      </div>

      {credentialSource === "managed" && workspaceRole === "admin" ? (
        <div className="setup-boundary setup-operations">
          <h2>Manage workspace access safely</h2>
          <p>
            List key IDs and expiry dates without revealing secrets. For rotation, create a replacement, update the user or integration, and only then revoke the old key ID.
          </p>
          <CopyCommand command={listKeysCommand} label="list workspace keys command" />
          <CopyCommand command={createKeyCommand} label="create workspace key command" />
        </div>
      ) : null}

      {credentialSource === "managed" && workspaceRole !== "admin" ? (
        <div className="setup-boundary setup-operations">
          <h2>{workspaceRole === "viewer" ? "Viewer access is read-only" : "Editor access is enabled"}</h2>
          <p>
            {workspaceRole === "viewer"
              ? "You can inspect workspace evidence and copy generated tests. Ask a workspace admin for an editor key when you need to upload, compare, save, or rerun data."
              : "You can upload, compare, save, and rerun workspace data. Key issuance and revocation stay with an operator who controls the Studio database and server secret."}
          </p>
        </div>
      ) : null}

      {authRequired && workspaceRole === "admin" ? (
        <div className="setup-boundary setup-operations">
          <h2>{metricsAccess === "bearer_token" ? "Service monitoring is protected" : "Connect service monitoring"}</h2>
          <p>
            {metricsAccess === "bearer_token"
              ? "The Prometheus metrics endpoint uses its own read-only bearer token. Workspace keys are rejected, so your monitoring service cannot open or change product data."
              : "Protected Studio keeps the metrics endpoint unavailable until you create a separate scraper token. Generate one, save it as TRACEBISECT_STUDIO_METRICS_TOKEN, then restart the API."}
          </p>
          {metricsAccess !== "bearer_token" ? <CopyCommand command={generateMetricsTokenCommand} label="generate metrics token command" /> : null}
        </div>
      ) : null}

      <div className="setup-boundary">
        <h2>What is not enabled yet</h2>
        <p>
          {authRequired
            ? credentialSource === "managed"
              ? "User accounts, account recovery, team membership administration, browser self-service, hosted ingestion, and billing are future production milestones—not active features in this build."
              : "Managed user accounts, hashed key rotation, hosted ingestion, team administration, and billing are future production milestones—not active features in this build."
            : "Authentication, request-scoped workspace access, hosted ingestion, team access, billing, and API keys are future production milestones—not active features in this build."}
        </p>
      </div>
    </section>
  );
}
