"use client";

import { BookOpen, CircleAlert, Database, GitCompare, Home, Menu, MessagesSquare, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import type { StudioSection } from "@/lib/types";

type MobileNavProps = {
  activeSection: StudioSection;
  onSectionChange: (section: StudioSection) => void;
};

const primary = [
  { id: "home" as const, label: "Home", icon: Home },
  { id: "runs" as const, label: "Compare", icon: GitCompare },
  { id: "sources" as const, label: "Traces", icon: Database },
  { id: "sessions" as const, label: "Sessions", icon: MessagesSquare },
];

const more = [
  { id: "divergences" as const, label: "Issues", description: "Repeated behavior changes", icon: CircleAlert },
  { id: "cases" as const, label: "Guardrails", description: "Saved regression checks", icon: ShieldCheck },
  { id: "setup" as const, label: "Setup guide", description: "Connect your own traces", icon: BookOpen },
];

export function MobileNav({ activeSection, onSectionChange }: MobileNavProps) {
  const [open, setOpen] = useState(false);
  function choose(section: StudioSection) {
    onSectionChange(section);
    setOpen(false);
  }
  const moreActive = more.some((item) => item.id === activeSection);
  return (
    <>
      {open ? (
        <div className="mobile-more-sheet" role="dialog" aria-label="More sections" data-testid="mobile-more-sheet">
          <div>
            <strong>More</strong>
            <button aria-label="Close more menu" onClick={() => setOpen(false)} type="button"><X size={18} /></button>
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
        <button aria-expanded={open} className={moreActive ? "mobile-nav-active" : ""} onClick={() => setOpen((value) => !value)} type="button">
          <Menu size={19} aria-hidden />
          <span>More</span>
        </button>
      </nav>
    </>
  );
}
