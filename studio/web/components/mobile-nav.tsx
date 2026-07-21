"use client";

import { BookOpen, CircleAlert, Database, GitCompare, Home, Menu, MessagesSquare, ShieldCheck, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { StudioSection } from "@/lib/types";

type MobileNavProps = {
  activeSection: StudioSection;
  onSectionChange: (section: StudioSection) => void;
};

const primary = [
  { id: "home" as const, label: "Home", icon: Home },
  { id: "sources" as const, label: "Choose traces", icon: Database },
  { id: "runs" as const, label: "Changes", icon: GitCompare },
  { id: "cases" as const, label: "Guardrails", icon: ShieldCheck },
];

const more = [
  { id: "sessions" as const, label: "Sessions", description: "Related runs grouped together", icon: MessagesSquare },
  { id: "divergences" as const, label: "Issue patterns", description: "Failures grouped by where they first changed", icon: CircleAlert },
  { id: "setup" as const, label: "Workspace setup", description: "Connect data and manage access", icon: BookOpen },
];

export function MobileNav({ activeSection, onSectionChange }: MobileNavProps) {
  const [open, setOpen] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const moreButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeButtonRef.current?.focus();
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") closeMoreMenu();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  function choose(section: StudioSection) {
    onSectionChange(section);
    setOpen(false);
  }

  function closeMoreMenu() {
    setOpen(false);
    window.requestAnimationFrame(() => moreButtonRef.current?.focus());
  }
  const moreActive = more.some((item) => item.id === activeSection);
  return (
    <>
      {open ? (
        <div className="mobile-more-sheet" id="mobile-more-sections" role="dialog" aria-labelledby="mobile-more-title" data-testid="mobile-more-sheet">
          <div>
            <strong id="mobile-more-title">More sections</strong>
            <button aria-label="Close more menu" onClick={closeMoreMenu} ref={closeButtonRef} type="button"><X size={18} /></button>
          </div>
          {more.map((item) => {
            const Icon = item.icon;
            return (
              <button className={activeSection === item.id ? "mobile-more-item mobile-more-item-active" : "mobile-more-item"} key={item.id} onClick={() => choose(item.id)} type="button">
                <Icon size={18} aria-hidden />
                <span><strong>{item.label}</strong><small>{item.description}</small></span>
              </button>
            );
          })}
        </div>
      ) : null}
      <nav className="mobile-nav" aria-label="Mobile navigation">
        {primary.map((item) => {
          const Icon = item.icon;
          return (
            <button aria-current={activeSection === item.id ? "page" : undefined} className={activeSection === item.id ? "mobile-nav-active" : ""} key={item.id} onClick={() => choose(item.id)} type="button">
              <Icon size={19} aria-hidden />
              <span>{item.label}</span>
            </button>
          );
        })}
        <button
          aria-controls="mobile-more-sections"
          aria-expanded={open}
          aria-label={open ? "Close more sections" : "Open more sections"}
          className={moreActive ? "mobile-nav-active" : ""}
          onClick={() => open ? closeMoreMenu() : setOpen(true)}
          ref={moreButtonRef}
          type="button"
        >
          <Menu size={19} aria-hidden />
          <span>More</span>
        </button>
      </nav>
    </>
  );
}
