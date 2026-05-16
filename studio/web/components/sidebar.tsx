import {
  Activity,
  Braces,
  Cable,
  FlaskConical,
  LayoutDashboard,
  ShieldCheck,
} from "lucide-react";
import type { StudioSection } from "@/lib/types";

const navItems = [
  { id: "runs", label: "Comparisons", icon: LayoutDashboard },
  { id: "sources", label: "Sources", icon: Cable },
  { id: "divergences", label: "Review", icon: Activity },
  { id: "cases", label: "Tests", icon: FlaskConical },
  { id: "setup", label: "Setup", icon: ShieldCheck },
];

type SidebarProps = {
  activeSection: StudioSection;
  onSectionChange: (section: StudioSection) => void;
};

export function Sidebar({ activeSection, onSectionChange }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="TraceBisect Studio navigation">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden>
          <Braces size={19} />
        </span>
        <strong>TraceBisect</strong>
      </div>

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
    </aside>
  );
}
