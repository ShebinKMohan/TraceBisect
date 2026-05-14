import {
  Activity,
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
        <strong>tb.</strong>
      </div>

      <nav className="nav-stack">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <a
              className={item.active ? "nav-item nav-item-active" : "nav-item"}
              href="#"
              key={item.label}
              title={item.label}
            >
              <Icon size={17} aria-hidden />
              <span className="sr-only">{item.label}</span>
            </a>
          );
        })}
      </nav>
    </aside>
  );
}
