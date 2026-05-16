import { Search } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";

type TopbarProps = {
  searchValue: string;
  theme: "light" | "dark";
  onSearchChange: (value: string) => void;
  onThemeToggle: () => void;
};

export function Topbar({ searchValue, theme, onSearchChange, onThemeToggle }: TopbarProps) {
  return (
    <header className="topbar">
      <label className="search-control">
        <Search size={16} aria-hidden />
        <input
          type="search"
          placeholder="Search comparisons, traces, events"
          value={searchValue}
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>
      <div className="topbar-actions">
        <ThemeToggle theme={theme} onToggle={onThemeToggle} />
        <div className="profile-chip" aria-label="Current workspace owner">
          <span>TB</span>
          <div>
            <strong>Local workspace</strong>
            <small>TraceBisect Studio</small>
          </div>
        </div>
      </div>
    </header>
  );
}
