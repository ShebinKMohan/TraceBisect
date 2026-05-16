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
  { id: "runs", label: "Runs", icon: LayoutDashboard },
  { id: "sources", label: "Trace Sources", icon: Cable },
  { id: "divergences", label: "Divergences", icon: Activity },
  { id: "cases", label: "Case Library", icon: FlaskConical },
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
        <strong>tb.</strong>
      </div>

      <nav className="nav-stack">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              aria-current={activeSection === item.id ? "page" : undefined}
              className={activeSection === item.id ? "nav-item nav-item-active" : "nav-item"}
              key={`${item.id}-${item.label}`}
              onClick={() => onSectionChange(item.id as StudioSection)}
              title={item.label}
              type="button"
            >
              <Icon size={17} aria-hidden />
              <span className="sr-only">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
