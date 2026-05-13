import type { ReactNode } from "react";

type MetricCardProps = {
  icon: ReactNode;
  label: string;
  value: string | number;
  detail: string;
  testId?: string;
  featured?: boolean;
};

export function MetricCard({ icon, label, value, detail, testId, featured = false }: MetricCardProps) {
  return (
    <article className={featured ? "metric-card metric-card-featured" : "metric-card"} data-testid={testId}>
      <div className="metric-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
