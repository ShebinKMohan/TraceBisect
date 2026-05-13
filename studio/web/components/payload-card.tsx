import type { JsonValue } from "@/lib/types";
import { formatJson } from "@/lib/format";

type PayloadCardProps = {
  label: string;
  value: JsonValue;
  tone: "expected" | "actual";
};

export function PayloadCard({ label, value, tone }: PayloadCardProps) {
  return (
    <div className={`payload-card payload-${tone}`}>
      <span>{label}</span>
      <code>{formatJson(value)}</code>
    </div>
  );
}
