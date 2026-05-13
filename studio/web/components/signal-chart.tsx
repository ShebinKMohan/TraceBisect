import type { Divergence } from "@/lib/types";

type SignalChartProps = {
  divergences: Divergence[];
};

const months = ["01", "02", "03", "04", "05", "06", "07", "08"];

export function SignalChart({ divergences }: SignalChartProps) {
  const firstRatio = divergences[0]?.impact.cost_delta_ratio ?? 1;
  return (
    <section className="panel signal-chart" aria-label="Regression signal chart">
      <div className="section-heading compact">
        <div>
          <p>Regression signal</p>
          <h2>Cost and behavior drift</h2>
        </div>
        <span className="chart-chip">{divergences.length} findings</span>
      </div>
      <div className="chart-area">
        {months.map((month, index) => {
          const primary = 34 + ((index * 19) % 58);
          const secondary = 22 + ((index * 31) % 48);
          const active = index === 4;
          return (
            <div className="bar-group" key={month}>
              <div className="bars" aria-hidden>
                <span style={{ height: `${primary}%` }} className={active ? "bar-active" : ""} />
                <span style={{ height: `${secondary}%` }} />
              </div>
              <small>{month}</small>
            </div>
          );
        })}
        <div className="chart-callout">
          <strong>{firstRatio.toFixed(2)}x</strong>
          <span>first cost ratio</span>
        </div>
      </div>
    </section>
  );
}
