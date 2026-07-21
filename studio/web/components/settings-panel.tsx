"use client";

import { Check, Clipboard, FileJson2, PlayCircle, TerminalSquare, UploadCloud } from "lucide-react";
import { useState } from "react";
import type { StudioHealth } from "@/lib/types";

const demoCommand = "tracebisect demo";
const uploadCommand = "curl -X POST http://127.0.0.1:8000/api/traces/upload -F 'file=@run.tbtrace'";
const recordCommand = "tracebisect record --output run.tbtrace -- python your_agent.py";
const durableCommand = "TRACEBISECT_STUDIO_STORAGE=sqlite TRACEBISECT_STUDIO_SQLITE_PATH=.tracebisect/studio.db uvicorn tracebisect.studio.api:app --port 8000";

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

export function SettingsPanel({ health }: { health: StudioHealth | null }) {
  const durable = health?.runtime.durable ?? false;
  return (
    <section className="setup-guide" data-testid="setup-section">
      <div className="setup-mode-banner">
        <span className="local-status-dot" aria-hidden />
        <div>
          <strong>
            {health
              ? durable
                ? "Running with durable workspace storage"
                : "Running as a local workspace"
              : "Checking workspace storage"}
          </strong>
          <p>
            {health
              ? durable
                ? `Traces, comparisons, and guardrails for ${health.runtime.workspace_id} are saved across API restarts.`
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
          <TerminalSquare size={19} aria-hidden />
          <div><strong>Local API</strong><span><code>http://127.0.0.1:8000</code></span></div>
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

      <div className="setup-boundary">
        <h2>What is not enabled yet</h2>
        <p>Authentication, request-scoped workspace access, hosted ingestion, team access, billing, and API keys are future production milestones—not active features in this build.</p>
      </div>
    </section>
  );
}
