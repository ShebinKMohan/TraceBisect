import { Check } from "lucide-react";
import type { StudioSection } from "@/lib/types";

type WorkflowStepsProps = {
  activeSection: StudioSection;
  onSectionChange: (section: StudioSection) => void;
};

const steps = [
  { number: 1, label: "Choose traces", section: "sources" as const },
  { number: 2, label: "Review first change", section: "runs" as const },
  { number: 3, label: "Save a guardrail", section: "cases" as const },
];

function activeStepFor(section: StudioSection): number {
  if (section === "sources") return 1;
  if (section === "cases") return 3;
  return 2;
}

export function WorkflowSteps({ activeSection, onSectionChange }: WorkflowStepsProps) {
  const activeStep = activeStepFor(activeSection);
  return (
    <nav className="workflow-steps" aria-label="Comparison workflow" data-testid="workflow-steps">
      {steps.map((step) => {
        const complete = step.number < activeStep;
        const active = step.number === activeStep;
        return (
          <button
            aria-current={active ? "step" : undefined}
            className={active ? "workflow-step workflow-step-active" : complete ? "workflow-step workflow-step-complete" : "workflow-step"}
            key={step.number}
            onClick={() => onSectionChange(step.section)}
            type="button"
          >
            <span>{complete ? <Check size={14} aria-hidden /> : step.number}</span>
            <strong>{step.label}</strong>
          </button>
        );
      })}
    </nav>
  );
}
