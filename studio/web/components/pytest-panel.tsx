"use client";

import { Check, Copy, FileCode2 } from "lucide-react";
import { useState } from "react";

type PytestPanelProps = {
  filename?: string;
  source?: string;
};

export function PytestPanel({ filename, source }: PytestPanelProps) {
  const [copied, setCopied] = useState(false);

  async function copySource() {
    if (!source) return;
    try {
      await navigator.clipboard.writeText(source);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = source;
      textarea.setAttribute("readonly", "true");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return (
    <section className="panel pytest-panel">
      <div className="section-heading compact">
        <div>
          <p>CI guardrail</p>
          <h2>{filename ?? "test_tracebisect_regression.py"}</h2>
        </div>
        <button
          type="button"
          className="secondary-action"
          data-testid="copy-pytest"
          disabled={!source}
          onClick={() => void copySource()}
        >
          {copied ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{source ?? "Loading generated pytest..."}</code>
      </pre>
      <div className="pytest-footer">
        <FileCode2 size={15} aria-hidden />
        Ready for repository tests once the baseline trace is committed.
      </div>
    </section>
  );
}
