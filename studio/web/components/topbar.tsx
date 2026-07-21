import { BookOpen, Search } from "lucide-react";
import type { StudioSection } from "@/lib/types";

type TopbarProps = {
  activeSection: StudioSection;
  searchValue: string;
  onHelp: () => void;
  onSearchChange: (value: string) => void;
};

const searchableSections = new Set<StudioSection>(["runs", "sources", "sessions", "divergences"]);

const searchPlaceholders: Partial<Record<StudioSection, string>> = {
  runs: "Search comparisons",
  sources: "Search traces",
  sessions: "Search sessions",
  divergences: "Search issues",
};

export function Topbar({ activeSection, searchValue, onHelp, onSearchChange }: TopbarProps) {
  const searchable = searchableSections.has(activeSection);
  return (
    <header className="topbar">
      {searchable ? (
        <label className="search-control">
          <Search size={16} aria-hidden />
          <input
            type="search"
            placeholder={searchPlaceholders[activeSection]}
            value={searchValue}
            onChange={(event) => onSearchChange(event.target.value)}
          />
          <kbd>⌘K</kbd>
        </label>
      ) : (
        <div className="topbar-context">
          <span className="local-status-dot" aria-hidden />
          Local workspace
        </div>
      )}
      <button aria-label="How it works" className="topbar-help" onClick={onHelp} type="button">
        <BookOpen size={16} aria-hidden />
        <span>How it works</span>
      </button>
    </header>
  );
}
