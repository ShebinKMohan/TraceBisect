import { Bell, CircleHelp, Search } from "lucide-react";

type TopbarProps = {
  searchValue: string;
  onSearchChange: (value: string) => void;
};

export function Topbar({ searchValue, onSearchChange }: TopbarProps) {
  return (
    <header className="topbar">
      <label className="search-control">
        <Search size={16} aria-hidden />
        <input
          type="search"
          placeholder="Search comparisons, traces, sessions, issues"
          value={searchValue}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <kbd>⌘K</kbd>
      </label>
      <div className="topbar-utilities" aria-label="Workspace utilities">
        <button className="icon-button notification-button" type="button" aria-label="Notifications">
          <Bell size={16} aria-hidden />
          <span aria-hidden />
        </button>
        <button className="icon-button" type="button" aria-label="Help">
          <CircleHelp size={17} aria-hidden />
        </button>
      </div>
    </header>
  );
}
