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
    title: "Compare two runs",
    description:
      "Find the first meaningful behavior change between a known-good run and a new run.",
  },
  sources: {
    title: "Choose your traces",
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
    title: "Setup guide",
    description:
      "Start with the demo, upload exported traces, or record a run from your own Python scenario.",
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
