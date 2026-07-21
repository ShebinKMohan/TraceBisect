"use client";

import {
  BookOpen,
  Braces,
  CircleAlert,
  Database,
  GitCompare,
  Home,
  LockKeyhole,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Sparkles,
  SunMedium,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { StudioHealth, StudioSection, WorkspaceRole } from "@/lib/types";

const navItems = [
  { id: "home", label: "Home", icon: Home },
  { id: "sources", label: "Choose traces", icon: Database },
  { id: "runs", label: "Review changes", icon: GitCompare },
  { id: "cases", label: "Guardrails", icon: ShieldCheck },
  { id: "sessions", label: "Sessions", icon: MessagesSquare },
  { id: "divergences", label: "Repeated issues", icon: CircleAlert },
  { id: "setup", label: "Workspace setup", icon: BookOpen },
] satisfies { id: StudioSection; label: string; icon: LucideIcon }[];

type SidebarProps = {
  activeSection: StudioSection;
  authRequired: boolean;
  canEdit: boolean;
  collapsed: boolean;
  onLock: () => void;
  onPrimaryAction: () => void;
  onSectionChange: (section: StudioSection) => void;
  onThemeToggle: () => void;
  onToggleCollapsed: () => void;
  runtime: StudioHealth["runtime"] | null;
  theme: "light" | "dark";
  workspaceRole: WorkspaceRole | null;
};

export function Sidebar({
  activeSection,
  authRequired,
  canEdit,
  collapsed,
  onLock,
  onPrimaryAction,
  onSectionChange,
  onThemeToggle,
  onToggleCollapsed,
  runtime,
  theme,
  workspaceRole,
}: SidebarProps) {
  const ThemeIcon = theme === "light" ? Moon : SunMedium;
  const durable = runtime?.durable ?? false;
  const workspaceLabel = runtime
    ? durable
      ? "Durable workspace"
      : "Local workspace"
    : "Checking workspace";
  const workspaceDetail = runtime
    ? authRequired
      ? `${workspaceRole ? `${workspaceRole[0].toUpperCase()}${workspaceRole.slice(1)}` : "Protected"} · ${runtime.workspace_id}`
      : durable
      ? "Saved across API restarts"
      : "Data resets with the API"
    : "Reading API storage mode";

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
              <small>{runtime ? (durable ? "Studio · durable" : "Studio · local") : "Studio · connecting"}</small>
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

        <button className="new-trace-button" disabled={!canEdit} onClick={onPrimaryAction} title={canEdit ? "Start a new comparison" : "Editor access is required"} type="button">
          <Sparkles size={16} aria-hidden />
          <span>Start comparison</span>
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

      <div className="sidebar-footer" aria-label="Workspace controls">
        <div className="local-workspace-note">
          <span className="local-status-dot" aria-hidden />
          <div>
            <strong>{workspaceLabel}</strong>
            <small>{workspaceDetail}</small>
          </div>
        </div>
        {authRequired ? (
          <button
            aria-label="Lock this workspace"
            className="theme-footer-button"
            data-testid="lock-workspace"
            onClick={onLock}
            type="button"
          >
            <LockKeyhole size={16} aria-hidden />
            <span>Lock workspace</span>
          </button>
        ) : null}
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
