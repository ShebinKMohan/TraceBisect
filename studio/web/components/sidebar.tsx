"use client";

import {
  Activity,
  BookOpen,
  BriefcaseBusiness,
  Braces,
  Bug,
  ChevronDown,
  Database,
  GitCompare,
  LogOut,
  Mail,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  SunMedium,
} from "lucide-react";
import { useState } from "react";
import type { LucideIcon } from "lucide-react";
import type { StudioSection } from "@/lib/types";

const navItems = [
  { id: "runs", label: "Comparisons", icon: GitCompare },
  { id: "sources", label: "Traces", icon: Database },
  { id: "sessions", label: "Sessions", icon: MessagesSquare },
  { id: "divergences", label: "Issues", icon: Activity },
  { id: "cases", label: "Guardrails", icon: ShieldCheck },
] satisfies { id: StudioSection; label: string; icon: LucideIcon }[];

type SidebarProps = {
  activeSection: StudioSection;
  collapsed: boolean;
  onSectionChange: (section: StudioSection) => void;
  onThemeToggle: () => void;
  onToggleCollapsed: () => void;
  theme: "light" | "dark";
};

export function Sidebar({
  activeSection,
  collapsed,
  onSectionChange,
  onThemeToggle,
  onToggleCollapsed,
  theme,
}: SidebarProps) {
  const [profileOpen, setProfileOpen] = useState(false);
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
              <small>v1.2.0</small>
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

        <button className="new-trace-button" title="Upload a new trace" type="button">
          <Plus size={16} aria-hidden />
          <span>New Trace</span>
        </button>

        <nav className="nav-stack">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                aria-current={activeSection === item.id ? "page" : undefined}
                className={activeSection === item.id ? "nav-item nav-item-active" : "nav-item"}
                data-testid={`sidebar-section-${item.id}`}
                key={`${item.id}-${item.label}`}
                onClick={() => onSectionChange(item.id as StudioSection)}
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

      <div className="sidebar-footer" aria-label="Workspace account">
        <button className="sidebar-footer-link" title="Workspace" type="button">
          <BriefcaseBusiness size={15} aria-hidden />
          <span>Workspace</span>
        </button>
        <button
          aria-current={activeSection === "setup" ? "page" : undefined}
          className={
            activeSection === "setup"
              ? "sidebar-settings-link sidebar-settings-link-active"
              : "sidebar-settings-link"
          }
          data-testid="sidebar-settings-link"
          onClick={() => onSectionChange("setup")}
          title="Settings"
          type="button"
        >
          <Settings size={15} aria-hidden />
          <span>Settings</span>
        </button>
        <div className="sidebar-profile-wrapper">
          {profileOpen ? (
            <div className="profile-popover" data-testid="profile-popover" role="menu">
              <div className="profile-popover-header">
                <span aria-hidden>TB</span>
                <div>
                  <strong>Refund Ops</strong>
                  <small>org_tracebisect_demo</small>
                </div>
              </div>
              <label className="profile-popover-search">
                <Search size={14} aria-hidden />
                <input placeholder="Search workspaces..." type="search" />
              </label>
              <button className="profile-menu-item profile-menu-item-active" role="menuitem" type="button">
                <span aria-hidden>✓</span>
                <span>Refund Ops</span>
              </button>
              <button className="profile-menu-item" role="menuitem" type="button">
                <Mail size={15} aria-hidden />
                <span>Invitations</span>
              </button>
              <button
                className="profile-menu-item"
                data-testid="profile-theme-toggle"
                onClick={onThemeToggle}
                role="menuitem"
                type="button"
              >
                <ThemeIcon size={15} aria-hidden />
                <span>{theme === "light" ? "Dark mode" : "Light mode"}</span>
              </button>
              <button className="profile-menu-item" role="menuitem" type="button">
                <BookOpen size={15} aria-hidden />
                <span>Documentation</span>
              </button>
              <button className="profile-menu-item" role="menuitem" type="button">
                <Bug size={15} aria-hidden />
                <span>Report a bug</span>
              </button>
              <button className="profile-menu-item profile-menu-item-danger" role="menuitem" type="button">
                <LogOut size={15} aria-hidden />
                <span>Log out</span>
              </button>
            </div>
          ) : null}
          <button
            aria-expanded={profileOpen}
            className="sidebar-profile-card"
            data-testid="sidebar-profile-button"
            onClick={() => setProfileOpen((current) => !current)}
            title="Workspace profile"
            type="button"
          >
            <span aria-hidden>TB</span>
            <div>
              <strong>Refund Ops</strong>
              <small>org_tracebisect_demo</small>
            </div>
            <ChevronDown size={13} aria-hidden />
          </button>
          </div>
      </div>
    </aside>
  );
}
