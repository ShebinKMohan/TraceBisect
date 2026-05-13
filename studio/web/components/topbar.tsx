import { Bell, CircleHelp, Search } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";

type TopbarProps = {
  theme: "light" | "dark";
  onThemeToggle: () => void;
};

export function Topbar({ theme, onThemeToggle }: TopbarProps) {
  return (
    <header className="topbar">
      <label className="search-control">
        <Search size={16} aria-hidden />
        <input type="search" placeholder="Search traces, events, divergences" />
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
