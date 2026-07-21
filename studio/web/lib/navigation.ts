import type { StudioSection } from "@/lib/types";

const viewBySection: Record<StudioSection, string | null> = {
  home: null,
  runs: "comparisons",
  sources: "traces",
  sessions: "sessions",
  divergences: "issues",
  cases: "guardrails",
  setup: "setup",
};

const sectionByView = new Map<string, StudioSection>(
  Object.entries(viewBySection)
    .filter((entry): entry is [StudioSection, string] => entry[1] !== null)
    .map(([section, view]) => [view, section]),
);

export const searchableStudioSections = new Set<StudioSection>([
  "runs",
  "sources",
  "sessions",
  "divergences",
]);

export function studioSectionFromUrl(href: string): StudioSection {
  const url = new URL(href, "http://tracebisect.local");
  return sectionByView.get(url.searchParams.get("view") ?? "") ?? "home";
}

export function studioSectionUrl(section: StudioSection, href: string): string {
  const url = new URL(href, "http://tracebisect.local");
  const view = viewBySection[section];
  if (view === null) {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", view);
  }
  url.hash = "";
  return `${url.pathname}${url.search}`;
}
