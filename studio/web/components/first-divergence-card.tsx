import { ArrowRight, CircleDollarSign, GitBranch, TriangleAlert } from "lucide-react";
import type { Divergence } from "@/lib/types";
import { PayloadCard } from "@/components/payload-card";

type FirstDivergenceCardProps = {
  divergence: Divergence | null;
};

export function FirstDivergenceCard({ divergence }: FirstDivergenceCardProps) {
  const severity = divergence?.severity ?? "INFO";
  return (
    <section className="panel first-divergence" data-testid="first-divergence-card">
      <div className="section-heading">
        <div>
          <p>First divergence</p>
          <h2>{divergence?.description ?? "No behavior drift detected"}</h2>
        </div>
        <span className={`severity-pill severity-${severity.toLowerCase()}`}>
          <TriangleAlert size={15} aria-hidden />
          {severity}
        </span>
      </div>

      {divergence ? (
        <>
          <div className="diff-lane">
            <PayloadCard label="Expected" value={divergence.expected} tone="expected" />
            <div className="diff-connector" aria-hidden>
              <ArrowRight size={20} />
            </div>
            <PayloadCard label="Actual" value={divergence.actual} tone="actual" />
          </div>

          <div className="impact-strip">
            <span>
              <GitBranch size={15} aria-hidden />
              {divergence.impact.affected_event_count} affected events
            </span>
            <span>
              <CircleDollarSign size={15} aria-hidden />
              {divergence.impact.cost_delta_ratio.toFixed(2)}x cost ratio
            </span>
            <span>{divergence.impact.final_output_changed ? "Final output changed" : "Final output stable"}</span>
          </div>
        </>
      ) : (
        <div className="empty-state">No high-severity drift was found in this comparison.</div>
      )}
    </section>
  );
}
