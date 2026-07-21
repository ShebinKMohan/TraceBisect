import { Cable, CheckCircle2, Clock3 } from "lucide-react";
import type { Report } from "@/lib/types";

type IntegrationPanelProps = {
  integrations: Report["integrations"];
};

export function IntegrationPanel({ integrations }: IntegrationPanelProps) {
  return (
    <section className="panel integrations-panel">
      <div className="section-heading compact">
        <div>
          <p>Import options</p>
          <h2>Available imports</h2>
        </div>
        <Cable size={19} aria-hidden />
      </div>
      <div className="integration-list">
        {integrations.map((integration) => {
          const available = integration.status.toLowerCase().includes("available");
          return (
            <article
              className="integration-card"
              data-testid={integration.name.includes("OpenTelemetry") ? "integration-otel" : undefined}
              key={integration.name}
            >
              {available ? <CheckCircle2 size={18} aria-hidden /> : <Clock3 size={18} aria-hidden />}
              <div>
                <strong>{integration.name}</strong>
                <span>{integration.status}</span>
                <p>{integration.description}</p>
              </div>
            </article>
          );
        })}
        {integrations.length === 0 ? (
          <div className="integration-empty" role="status">
            <Cable size={18} aria-hidden />
            <div>
              <strong>No import summary yet.</strong>
              <p>Choose or upload two traces. Studio will show which trace formats were recognized.</p>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
