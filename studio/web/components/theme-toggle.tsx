"use client";

import { Moon, SunMedium } from "lucide-react";

type ThemeToggleProps = {
  theme: "light" | "dark";
  onToggle: () => void;
};

export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  return (
    <button
      type="button"
      className="icon-button"
      data-testid="theme-toggle"
      aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
      onClick={onToggle}
    >
      {theme === "light" ? <Moon aria-hidden size={18} /> : <SunMedium aria-hidden size={18} />}
    </button>
  );
}
