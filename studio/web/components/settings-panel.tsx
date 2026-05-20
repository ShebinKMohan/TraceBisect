"use client";

import { Copy, KeyRound, PlugZap, Trash2, UsersRound } from "lucide-react";

const apiKeys = [
  {
    key: "tb_live_4d9...b821",
    description: "Studio ingest service",
    workspace: "Refund Ops",
    created: "May 16, 2026",
    lastUsed: "Today",
  },
  {
    key: "tb_dev_91a...0fc4",
    description: "Local recorder",
    workspace: "Refund Ops",
    created: "May 14, 2026",
    lastUsed: "Yesterday",
  },
];

const members = [
  { name: "Shebin Mohan", email: "owner@tracebisect.local", role: "Owner", access: "All projects" },
  { name: "QA Reviewer", email: "qa@tracebisect.local", role: "Reviewer", access: "Refund Ops" },
  { name: "CI Bot", email: "ci@tracebisect.local", role: "Service account", access: "Guardrails only" },
];

const settingsTabs = ["Organization", "API Keys", "Usage & Billing", "Team Members", "Ingest Endpoints"];

export function SettingsPanel() {
  return (
    <section className="settings-layout" data-testid="setup-section">
      <nav className="settings-tab-strip" aria-label="Workspace settings sections">
        {settingsTabs.map((item) => (
          <button className={item === "API Keys" ? "settings-tab-active" : ""} key={item} type="button">
            {item}
          </button>
        ))}
      </nav>
      <div className="settings-content-stack">
        <article className="panel settings-panel">
          <div className="settings-panel-heading">
            <div>
              <h2>API keys</h2>
              <span>Keys used to authenticate trace ingestion and API access.</span>
            </div>
            <button className="settings-primary-action" type="button">
              <KeyRound size={15} aria-hidden />
              Create New Key
            </button>
          </div>

          <div className="settings-api-table" data-testid="settings-api-keys">
            <div className="settings-table-head">
              <span>Name</span>
              <span>Secret key</span>
              <span>Created</span>
              <span>Last used</span>
              <span>Actions</span>
            </div>
            {apiKeys.map((item) => (
              <div className="settings-table-row" key={item.key}>
                <span>{item.description}</span>
                <code>{item.key}</code>
                <span>{item.created}</span>
                <span>{item.lastUsed}</span>
                <span className="settings-row-actions">
                  <button aria-label={`Copy ${item.description} key`} type="button">
                    <Copy size={14} aria-hidden />
                  </button>
                  <button aria-label={`Delete ${item.description} key`} type="button">
                    <Trash2 size={14} aria-hidden />
                  </button>
                </span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel settings-panel">
          <div className="settings-panel-heading compact">
            <div>
              <h2>Ingest endpoints</h2>
              <span>Configure your LLM clients or agents to send traces to these endpoints.</span>
            </div>
            <PlugZap size={18} aria-hidden />
          </div>
          <div className="settings-endpoint-list settings-endpoint-code-list">
            <div>
              <span>TraceBisect Native API</span>
              <code>POST /v1/traces</code>
              <pre>{`curl -X POST https://api.tracebisect.com/v1/traces \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "trace_id": "req-123",
    "name": "chat_completion",
    "start_time": "2026-05-17T10:30:00Z"
  }'`}</pre>
            </div>
            <div>
              <span>OpenTelemetry (OTLP) JSON</span>
              <code>POST /v1/otel/traces</code>
              <pre>{`export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp.tracebisect.com"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer YOUR_API_KEY"
python my_llm_app.py`}</pre>
            </div>
          </div>
        </article>

        <article className="panel settings-panel">
          <div className="settings-panel-heading">
            <div>
              <p>Members</p>
              <h2>Workspace access</h2>
              <span>Invite teammates, service accounts, and reviewers per workspace or project.</span>
            </div>
            <button className="settings-secondary-action" type="button">
              <UsersRound size={15} aria-hidden />
              Invite member
            </button>
          </div>

          <div className="settings-members-list" data-testid="settings-members">
            {members.map((member) => (
              <div className="settings-member-row" key={member.email}>
                <span className="settings-member-avatar" aria-hidden>
                  {member.name
                    .split(" ")
                    .map((part) => part[0])
                    .join("")
                    .slice(0, 2)}
                </span>
                <div>
                  <strong>{member.name}</strong>
                  <small>{member.email}</small>
                </div>
                <span>{member.role}</span>
                <span>{member.access}</span>
              </div>
            ))}
          </div>
        </article>
      </div>
    </section>
  );
}
