import { UploadCloud } from "lucide-react";
import type { TraceSummary } from "@/lib/types";
import { friendlyTraceName } from "@/lib/format";

type UploadComparePanelProps = {
  traces: TraceSummary[];
  baselineId: string;
  candidateId: string;
  busy: boolean;
  readOnly: boolean;
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
  readOnly,
  onBaselineChange,
  onCandidateChange,
  onUpload,
  onCompare,
}: UploadComparePanelProps) {
  const disabled = readOnly || busy || !baselineId || !candidateId;
  return (
    <section className="panel upload-panel">
      <div className="section-heading compact">
        <div>
          <p>Trace upload</p>
          <h2>Add or compare traces</h2>
        </div>
        <UploadCloud size={19} aria-hidden />
      </div>

      {readOnly ? (
        <div className="read-only-panel-note" role="status">
          Viewer access is read-only. Browse the trace library below, or ask an admin for an editor key to upload and compare runs.
        </div>
      ) : null}

      <div className="upload-grid">
        <TraceUpload
          id="baseline-upload"
          disabled={readOnly}
          label="Known-good baseline"
          selectedId={baselineId}
          traces={traces}
          onSelect={onBaselineChange}
          onUpload={(file) => onUpload(file, "baseline")}
        />
        <TraceUpload
          id="candidate-upload"
          disabled={readOnly}
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
        title={readOnly ? "Editor access is required" : "Compare the selected traces"}
      >
        {readOnly ? "Editor access required" : busy ? "Comparing..." : "Find first behavior change"}
      </button>
    </section>
  );
}

type TraceUploadProps = {
  id: string;
  disabled: boolean;
  label: string;
  selectedId: string;
  traces: TraceSummary[];
  onSelect: (value: string) => void;
  onUpload: (file: File) => void;
};

function TraceUpload({ id, disabled, label, selectedId, traces, onSelect, onUpload }: TraceUploadProps) {
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
        disabled={disabled}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) onUpload(file);
        }}
      />
      <select
        aria-label={`${label} trace`}
        disabled={disabled}
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
