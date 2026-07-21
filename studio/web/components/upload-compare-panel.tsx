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
  const sameTraceSelected = Boolean(baselineId && candidateId && baselineId === candidateId);
  const disabled = readOnly || busy || !baselineId || !candidateId || sameTraceSelected;
  const selectionHelp = readOnly
    ? "Viewer access can browse traces but cannot start a comparison."
    : !baselineId && !candidateId
      ? "Choose a known-good run and a new run to continue."
      : !baselineId
        ? "Choose the known-good run to continue."
        : !candidateId
          ? "Choose the new run you want to check."
          : sameTraceSelected
            ? "Choose two different runs so TraceBisect can find what changed."
            : "Both runs are ready to compare.";
  return (
    <section className="panel upload-panel">
      <div className="section-heading compact">
        <div>
          <p>Start a comparison</p>
          <h2>Choose the expected run and the new run</h2>
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
          disabled={readOnly || busy}
          description="Choose the run whose behavior you trust. This is what TraceBisect treats as expected."
          label="Known-good run"
          selectedId={baselineId}
          traces={traces}
          onSelect={onBaselineChange}
          onUpload={(file) => onUpload(file, "baseline")}
        />
        <TraceUpload
          id="candidate-upload"
          disabled={readOnly || busy}
          description="Choose the newer run you want to check for behavior changes."
          label="New run to check"
          selectedId={candidateId}
          traces={traces}
          onSelect={onCandidateChange}
          onUpload={(file) => onUpload(file, "candidate")}
        />
      </div>

      <button
        aria-describedby="comparison-selection-help"
        className="primary-action"
        type="button"
        data-testid="compare-button"
        disabled={disabled}
        onClick={onCompare}
        title={selectionHelp}
      >
        {readOnly ? "Editor access required" : busy ? "Comparing..." : "Find first behavior change"}
      </button>
      <p className={disabled ? "comparison-selection-help" : "comparison-selection-help comparison-selection-ready"} id="comparison-selection-help" role="status">
        {selectionHelp}
      </p>
    </section>
  );
}

type TraceUploadProps = {
  id: string;
  disabled: boolean;
  description: string;
  label: string;
  selectedId: string;
  traces: TraceSummary[];
  onSelect: (value: string) => void;
  onUpload: (file: File) => void;
};

function TraceUpload({ id, disabled, description, label, selectedId, traces, onSelect, onUpload }: TraceUploadProps) {
  const selectId = `${id}-select`;
  return (
    <div className="upload-card">
      <div className="trace-choice-copy">
        <strong>{label}</strong>
        <p>{description}</p>
      </div>
      <label className="trace-choice-label" htmlFor={selectId}>Choose from the trace library</label>
      <select
        id={selectId}
        aria-label={`${label} trace`}
        disabled={disabled}
        value={selectedId}
        onChange={(event) => onSelect(event.currentTarget.value)}
      >
        <option value="">Choose a saved trace</option>
        {traces.map((trace) => (
          <option key={trace.id} value={trace.id}>
            {friendlyTraceName(trace.display_name)}
          </option>
        ))}
      </select>
      <div className="trace-choice-divider" aria-hidden><span>or</span></div>
      <label className="trace-choice-label" htmlFor={id}>Upload a new trace file</label>
      <input
        id={id}
        data-testid={id}
        type="file"
        accept=".tbtrace,.json,application/json"
        disabled={disabled}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) {
            onUpload(file);
            event.currentTarget.value = "";
          }
        }}
      />
      <small><code>.tbtrace</code> or OTel/OpenInference JSON · 5 MB maximum</small>
    </div>
  );
}
