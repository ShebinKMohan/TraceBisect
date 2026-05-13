import {
  Activity,
  Boxes,
  Braces,
  Cable,
  FileCode2,
  GitCompare,
  LayoutDashboard,
  ShieldCheck,
} from "lucide-react";

const navItems = [
  { label: "Dashboard", icon: LayoutDashboard, active: true },
  { label: "Comparisons", icon: GitCompare },
  { label: "Trace Sources", icon: Cable },
  { label: "Divergences", icon: Activity },
  { label: "Pytest Export", icon: FileCode2 },
  { label: "CI Guardrails", icon: ShieldCheck },
];

export function Sidebar() {
  return (
    <aside className="sidebar" aria-label="TraceBisect Studio navigation">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden>
          <Braces size={19} />
        </span>
        <div>
          <strong>TraceBisect</strong>
          <span>Studio</span>
        </div>
      </div>

      <div className="workspace-switcher">
        <Boxes size={16} aria-hidden />
        <div>
          <span>Agent workspace</span>
          <strong>Refund Ops</strong>
        </div>
      </div>

      <nav className="nav-stack">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <a
              className={item.active ? "nav-item nav-item-active" : "nav-item"}
              href="#"
              key={item.label}
            >
              <Icon size={17} aria-hidden />
              <span>{item.label}</span>
            </a>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <span>V1 Engine</span>
        <strong>Local + OTel ready</strong>
      </div>
    </aside>
  );
}
