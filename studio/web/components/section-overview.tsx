import type { Report, StudioSection } from "@/lib/types";

type SectionOverviewProps = {
  section: StudioSection;
  report: Report | null;
};

const sectionCopy: Record<StudioSection, { eyebrow: string; title: string; description: string }> = {
  runs: {
    eyebrow: "Workspace / Refund Ops",
    title: "Comparison history",
    description:
      "Pick a known-good trace, compare a new run, inspect the first behavior change, and save a guardrail test.",
  },
  sources: {
    eyebrow: "Traces",
    title: "Trace inventory",
    description:
      "Browse captured runs, filter by source and model, then upload or compare traces when a regression appears.",
  },
  sessions: {
    eyebrow: "Sessions",
    title: "Conversation sessions",
    description:
      "Group related traces by session, thread, conversation, or scenario so multi-turn behavior is easier to inspect.",
  },
  divergences: {
    eyebrow: "Issues",
    title: "Regression issues",
    description:
      "Cluster repeated behavior changes, inspect the affected trace path, and decide which issues become guardrails.",
  },
  cases: {
    eyebrow: "Guardrails",
    title: "Guardrail datasets",
    description:
      "Save important regressions as rerunnable guardrail datasets and copy generated pytest checks into your repository.",
  },
  setup: {
    eyebrow: "Settings / Refund Ops",
    title: "Workspace settings",
    description:
      "Manage workspace identity, project access, API keys, and ingest configuration for TraceBisect Studio.",
  },
};

export function sectionContent(section: StudioSection) {
  return sectionCopy[section];
}

export function SectionOverview({
  section,
  report: _report,
}: SectionOverviewProps) {
  if (section !== "setup") return null;

  return null;
}
