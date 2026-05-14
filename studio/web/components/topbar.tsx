import { Bell, CircleHelp, Search } from "lucide-react";
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
          placeholder="Search traces, events, divergences"
          value={searchValue}
          onChange={(event) => onSearchChange(event.target.value)}
        />
      </label>
      <div className="topbar-actions">
        <button className="icon-button" type="button" aria-label="Help">
          <CircleHelp size={18} aria-hidden />
        </button>
        <button className="icon-button notification-button" type="button" aria-label="Notifications">
          <Bell size={18} aria-hidden />
          <span aria-hidden />
        </button>
        <ThemeToggle theme={theme} onToggle={onThemeToggle} />
        <div className="profile-chip" aria-label="Current workspace owner">
          <span>SM</span>
          <div>
            <strong>Shebin</strong>
            <small>Maintainer</small>
          </div>
        </div>
      </div>
    </header>
  );
}
