import { CircleDollarSign, FileCode2, GitCompare, TriangleAlert } from "lucide-react";
import type { Divergence, Report } from "@/lib/types";
import { FirstDivergenceCard } from "@/components/first-divergence-card";
import { PytestPanel } from "@/components/pytest-panel";

type CompareDrawerProps = {
  report: Report | null;
  divergence: Divergence | null;
};

export function CompareDrawer({ report, divergence }: CompareDrawerProps) {
  return (
    <section className="compare-drawer" aria-label="Regression export">
      <div className="compare-summary-card">
        <p>Comparison</p>
        <h2>{report ? `${report.baseline.display_name} → ${report.candidate.display_name}` : "Loading report"}</h2>
        <div className="summary-pills">
          <span data-testid="metric-divergences">
            <GitCompare size={14} aria-hidden />
            {report?.divergence_count ?? 0} divergences
          </span>
          <span>
            <TriangleAlert size={14} aria-hidden />
            {divergence?.severity ?? "INFO"}
          </span>
          <span>
            <CircleDollarSign size={14} aria-hidden />
            {divergence ? `${divergence.impact.cost_delta_ratio.toFixed(2)}x cost` : "1.00x cost"}
          </span>
          <span>
            <FileCode2 size={14} aria-hidden />
            pytest ready
          </span>
        </div>
      </div>
      <FirstDivergenceCard divergence={divergence} />
      <PytestPanel filename={report?.pytest.filename} source={report?.pytest.source} />
    </section>
  );
}
