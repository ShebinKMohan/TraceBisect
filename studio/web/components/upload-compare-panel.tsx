import { UploadCloud } from "lucide-react";
import type { TraceSummary } from "@/lib/types";
import { friendlyTraceName } from "@/lib/format";

type UploadComparePanelProps = {
  traces: TraceSummary[];
  baselineId: string;
  candidateId: string;
  busy: boolean;
  onBaselineChange: (value: string) => void;
  onCandidateChange: (value: string) => void;
  onUpload: (file: File, role: "baseline" | "candidate") => void;
  onCompare: () => void;
};

export function UploadComparePanel({
  traces,
  baselineId,
  candidateId,
  busy,
  onBaselineChange,
  onCandidateChange,
  onUpload,
  onCompare,
}: UploadComparePanelProps) {
  const disabled = busy || !baselineId || !candidateId;
  return (
    <section className="panel upload-panel">
      <div className="section-heading compact">
        <div>
          <p>Trace sources</p>
          <h2>Add traces</h2>
        </div>
        <UploadCloud size={19} aria-hidden />
      </div>

      <div className="upload-grid">
        <TraceUpload
          id="baseline-upload"
          label="Known-good baseline"
          selectedId={baselineId}
          traces={traces}
          onSelect={onBaselineChange}
          onUpload={(file) => onUpload(file, "baseline")}
        />
        <TraceUpload
          id="candidate-upload"
          label="New run to check"
          selectedId={candidateId}
          traces={traces}
          onSelect={onCandidateChange}
          onUpload={(file) => onUpload(file, "candidate")}
        />
      </div>

      <button
        className="primary-action"
        type="button"
        data-testid="compare-button"
        disabled={disabled}
        onClick={onCompare}
      >
        {busy ? "Comparing..." : "Find first behavior change"}
      </button>
    </section>
  );
}

type TraceUploadProps = {
  id: string;
  label: string;
  selectedId: string;
  traces: TraceSummary[];
  onSelect: (value: string) => void;
  onUpload: (file: File) => void;
};

function TraceUpload({ id, label, selectedId, traces, onSelect, onUpload }: TraceUploadProps) {
  return (
    <div className="upload-card">
      <label htmlFor={id}>
        <span>{label}</span>
        <strong>Upload .tbtrace or JSON</strong>
        <small>Max 5 MB. Parsed locally by TraceBisect Studio.</small>
      </label>
      <input
        id={id}
        data-testid={id}
        type="file"
        accept=".tbtrace,.json,application/json"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) onUpload(file);
        }}
      />
      <select
        aria-label={`${label} trace`}
        value={selectedId}
        onChange={(event) => onSelect(event.currentTarget.value)}
      >
        <option value="">Select uploaded trace</option>
        {traces.map((trace) => (
          <option key={trace.id} value={trace.id}>
            {friendlyTraceName(trace.display_name)}
          </option>
        ))}
      </select>
    </div>
  );
}
