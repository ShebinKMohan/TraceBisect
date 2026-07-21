import type { Report, StudioSection } from "@/lib/types";

type SectionOverviewProps = {
  section: StudioSection;
  report: Report | null;
};

const sectionCopy: Record<StudioSection, { title: string; description: string }> = {
  home: {
    title: "Welcome to TraceBisect",
    description: "Follow one clear path from two AI runs to a regression test you can keep.",
  },
  runs: {
    title: "Review the first behavior change",
    description:
      "Choose a comparison, inspect where the new run first changed, then save the behavior you want to protect.",
  },
  sources: {
    title: "Choose two traces to compare",
    description:
      "A trace is the step-by-step record of one AI run. Choose the expected run first, then the new run to check.",
  },
  sessions: {
    title: "Browse sessions",
    description:
      "See related runs together when they came from the same conversation, thread, or scenario.",
  },
  divergences: {
    title: "Review repeated issues",
    description:
      "Find behavior changes that keep returning and decide which ones need a permanent guardrail.",
  },
  cases: {
    title: "Protect fixed behavior",
    description:
      "A guardrail is a saved regression check. Copy its generated pytest test into your repository to catch the bug in CI.",
  },
  setup: {
    title: "Workspace setup",
    description:
      "Learn the simplest setup path, connect your own traces, and manage workspace access in one place.",
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
