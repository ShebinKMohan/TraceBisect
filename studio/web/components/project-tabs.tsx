import type { StudioSection } from "@/lib/types";

export type StudioSectionConfig = {
  id: StudioSection;
  label: string;
};

type ProjectTabsProps = {
  sections: StudioSectionConfig[];
  activeSection: StudioSection;
  onSectionChange: (section: StudioSection) => void;
};

export function ProjectTabs({ sections, activeSection, onSectionChange }: ProjectTabsProps) {
  return (
    <nav className="project-tabs" aria-label="Project sections">
      {sections.map((section) => (
        <button
          aria-current={activeSection === section.id ? "page" : undefined}
          aria-selected={activeSection === section.id}
          data-testid={`section-tab-${section.id}`}
          key={section.id}
          onClick={() => onSectionChange(section.id)}
          role="tab"
          type="button"
        >
          {section.label}
        </button>
      ))}
    </nav>
  );
}
