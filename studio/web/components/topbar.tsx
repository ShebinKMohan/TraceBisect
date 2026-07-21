import { BookOpen, Search } from "lucide-react";
import type { RefObject } from "react";
import { searchableStudioSections } from "@/lib/navigation";
import type { StudioHealth, StudioSection } from "@/lib/types";

type TopbarProps = {
  activeSection: StudioSection;
  authRequired: boolean;
  searchValue: string;
  onHelp: () => void;
  onSearchChange: (value: string) => void;
  runtime: StudioHealth["runtime"] | null;
  searchInputRef: RefObject<HTMLInputElement | null>;
};

const searchPlaceholders: Partial<Record<StudioSection, string>> = {
  runs: "Search comparisons",
  sources: "Search traces",
  sessions: "Search sessions",
  divergences: "Search issues",
};

export function Topbar({
  activeSection,
  authRequired,
  searchValue,
  onHelp,
  onSearchChange,
  runtime,
  searchInputRef,
}: TopbarProps) {
  const searchable = searchableStudioSections.has(activeSection);
  return (
    <header className="topbar">
      {searchable ? (
        <label className="search-control">
          <Search size={16} aria-hidden />
          <input
            aria-keyshortcuts="Meta+K Control+K"
            data-testid="section-search"
            type="search"
            placeholder={searchPlaceholders[activeSection]}
            ref={searchInputRef}
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
          />
          <kbd>⌘K</kbd>
        </label>
      ) : (
        <div className="topbar-context">
          <span className="local-status-dot" aria-hidden />
          {runtime
            ? authRequired
              ? `Workspace · ${runtime.workspace_id}`
              : runtime.durable
                ? "Durable workspace"
                : "Local workspace"
            : "Checking workspace"}
        </div>
      )}
      <button aria-label="How it works" className="topbar-help" onClick={onHelp} type="button">
        <BookOpen size={16} aria-hidden />
        <span>How it works</span>
      </button>
    </header>
  );
}
