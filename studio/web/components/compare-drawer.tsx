import { CircleDollarSign, FileCode2, GitCompare, TriangleAlert } from "lucide-react";
import type { Divergence, Report } from "@/lib/types";
import { FirstDivergenceCard } from "@/components/first-divergence-card";
import { PytestPanel } from "@/components/pytest-panel";
import { friendlySeverity, friendlyTraceName } from "@/lib/format";

type CompareDrawerProps = {
  report: Report | null;
  divergence: Divergence | null;
};

export function CompareDrawer({ report, divergence }: CompareDrawerProps) {
  return (
    <section className="compare-drawer" aria-label="Comparison details">
      <div className="compare-summary-card">
        <p>Selected comparison</p>
        <h2>
          {report
            ? `${friendlyTraceName(report.baseline.display_name)} → ${friendlyTraceName(report.candidate.display_name)}`
            : "Loading report"}
        </h2>
        <div className="summary-pills">
          <span data-testid="metric-divergences">
            <GitCompare size={14} aria-hidden />
            {report?.divergence_count ?? 0} behavior changes
          </span>
          <span>
            <TriangleAlert size={14} aria-hidden />
            {friendlySeverity(divergence?.severity)}
          </span>
          <span>
            <CircleDollarSign size={14} aria-hidden />
            {divergence ? `${divergence.impact.cost_delta_ratio.toFixed(2)}x cost` : "1.00x cost"}
          </span>
          <span>
            <FileCode2 size={14} aria-hidden />
            Test ready
          </span>
        </div>
      </div>
      <FirstDivergenceCard divergence={divergence} />
      <PytestPanel filename={report?.pytest.filename} source={report?.pytest.source} />
    </section>
  );
}
