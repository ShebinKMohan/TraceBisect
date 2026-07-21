"use client";

import {
  BookOpen,
  Braces,
  CircleAlert,
  Database,
  GitCompare,
  Home,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Sparkles,
  SunMedium,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { StudioSection } from "@/lib/types";

const navItems = [
  { id: "home", label: "Home", icon: Home },
  { id: "runs", label: "Compare runs", icon: GitCompare },
  { id: "sources", label: "Trace library", icon: Database },
  { id: "sessions", label: "Sessions", icon: MessagesSquare },
  { id: "divergences", label: "Issues", icon: CircleAlert },
  { id: "cases", label: "Guardrails", icon: ShieldCheck },
  { id: "setup", label: "Setup guide", icon: BookOpen },
] satisfies { id: StudioSection; label: string; icon: LucideIcon }[];

type SidebarProps = {
  activeSection: StudioSection;
  collapsed: boolean;
  onPrimaryAction: () => void;
  onSectionChange: (section: StudioSection) => void;
  onThemeToggle: () => void;
  onToggleCollapsed: () => void;
  theme: "light" | "dark";
};

export function Sidebar({
  activeSection,
  collapsed,
  onPrimaryAction,
  onSectionChange,
  onThemeToggle,
  onToggleCollapsed,
  theme,
}: SidebarProps) {
  const ThemeIcon = theme === "light" ? Moon : SunMedium;

  return (
    <aside className={collapsed ? "sidebar sidebar-collapsed" : "sidebar"} aria-label="TraceBisect Studio navigation">
      <div>
        <div className="sidebar-brand-row">
          <div className="brand-lockup">
            <span className="brand-mark" aria-hidden>
              <Braces size={19} />
            </span>
            <div>
              <strong>TraceBisect</strong>
              <small>Studio · local</small>
            </div>
          </div>
          <button
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="sidebar-collapse-button"
            data-testid="sidebar-collapse-button"
            onClick={onToggleCollapsed}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            type="button"
          >
            {collapsed ? <PanelLeftOpen size={16} aria-hidden /> : <PanelLeftClose size={16} aria-hidden />}
          </button>
        </div>

        <button className="new-trace-button" onClick={onPrimaryAction} title="Compare two traces" type="button">
          <Sparkles size={16} aria-hidden />
          <span>Compare traces</span>
        </button>

        <nav className="nav-stack">
          {navItems.map((item) => {
            const Icon = item.icon;
            const testId = item.id === "setup" ? "sidebar-settings-link" : `sidebar-section-${item.id}`;
            return (
              <button
                aria-current={activeSection === item.id ? "page" : undefined}
                className={activeSection === item.id ? "nav-item nav-item-active" : "nav-item"}
                data-testid={testId}
                key={item.id}
                onClick={() => onSectionChange(item.id)}
                title={item.label}
                type="button"
              >
                <Icon size={17} aria-hidden />
                <span className="nav-item-label">{item.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      <div className="sidebar-footer" aria-label="Local workspace controls">
        <div className="local-workspace-note">
          <span className="local-status-dot" aria-hidden />
          <div>
            <strong>Local workspace</strong>
            <small>Data resets with the API</small>
          </div>
        </div>
        <button
          aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
          className="theme-footer-button"
          data-testid="profile-theme-toggle"
          onClick={onThemeToggle}
          type="button"
        >
          <ThemeIcon size={16} aria-hidden />
          <span>{theme === "light" ? "Dark mode" : "Light mode"}</span>
        </button>
      </div>
    </aside>
  );
}
