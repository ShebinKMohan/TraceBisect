# TraceBisect Studio Visual System

This palette and type scale are derived from the compact SaaS reference screen used for the Studio redesign.

## Palette

```css
:root {
  --bg: #c8c8c6;             /* outside canvas */
  --app-bg: #f8f9f7;         /* main application area */
  --shell: #ffffff;          /* topbar, header, cards, tables */
  --sidebar: #fbfcfa;        /* navigation rail */
  --surface-muted: #f5f7f4;
  --surface-soft: #f9faf8;
  --border: #e2e6e0;
  --border-strong: #d5dbd2;

  --text: #171c18;
  --text-muted: #66706a;
  --text-subtle: #969f98;

  --accent: #087646;         /* primary green */
  --accent-hover: #065f38;
  --accent-soft: #e8f5ee;
  --accent-line: #b8dbc8;

  --danger: #b42318;
  --danger-soft: #feedec;
  --warning: #a16207;
  --warning-soft: #fff7e6;
  --success: #17803d;
  --success-soft: #eaf7ee;
  --code-bg: #111827;
  --code-text: #e5e7eb;
}

html[data-theme="dark"] {
  --bg: #161918;
  --app-bg: #101413;
  --shell: #171d1b;
  --sidebar: #121715;
  --surface-muted: #1d2421;
  --surface-soft: #1a211f;
  --border: #2a3531;
  --border-strong: #3a4742;

  --text: #f3f6f4;
  --text-muted: #b8c4be;
  --text-subtle: #84918a;

  --accent: #38c883;
  --accent-hover: #66dba2;
  --accent-soft: rgba(56, 200, 131, 0.12);
  --accent-line: rgba(56, 200, 131, 0.28);

  --danger: #ff6b61;
  --danger-soft: rgba(180, 35, 24, 0.18);
  --warning: #f5c56a;
  --warning-soft: rgba(161, 98, 7, 0.18);
  --success: #66dba2;
  --success-soft: rgba(23, 128, 61, 0.18);
  --code-bg: #081214;
  --code-text: #e8fff8;
}
```

Green is reserved for selected navigation, active tabs, success state, and primary actions. Most of the UI stays white, grey, black text, and thin borders.

## Product Language

TraceBisect Studio should present the user's workflow, not internal schema names.

- Primary object: `Comparison`.
- Primary question: "Did the new trace regress from the known-good trace?"
- Primary workflow: choose a known-good baseline, compare a new run, inspect the first behavior change, save a guardrail test.
- Default page: `Comparison history`.
- Top-level navigation: `Comparisons`, `Sources`, `Review`, `Tests`, `Setup`.
- Avoid raw labels in primary UI: `.tbtrace` filenames, `changed_tool_args`, `RUN_START`, `TOOL_CALL`, raw trace IDs, source event IDs, parent IDs, and JSON payloads.
- Keep raw filenames, IDs, and payload JSON in detail or raw-payload surfaces only.
- Use human labels such as `Tool arguments changed`, `Model call`, `Run completed`, `Regression found`, and `Generated pytest guardrail`.

## Type Scale

- Primary UI font: `Geist`, with `ui-sans-serif`, `system-ui`, and `-apple-system` fallbacks.
- Code and payload font: `Geist Mono`, with platform monospace fallbacks.
- Body: `12.5px / 1.4`, weight `400`.
- Search, filters, table cells: `12px-12.5px`, weight `400-500`.
- Table headers and metadata labels: `9.5px-10.5px`, weight `600`.
- Panel titles: `12px-13px`, weight `600`.
- Page title: `20px`, weight `600`.
- Stat values: `18px`, weight `600`.
- Badges: `10.5px-11.5px`, weight `600`.

Avoid hero-sized text inside the product app. TraceBisect Studio is an engineer-facing workbench, not a marketing page.

## Layout Rules

- Outer canvas: neutral grey.
- App shell: centered white surface with a labelled navigation rail on desktop and compact tabs on mobile.
- Primary content: comparison filters, small stat cards, and dense comparison history table.
- Secondary content: trace tree and event inspector for comparison/review pages.
- Upload/import content belongs under `Sources`; pytest export and guardrail copy belong under `Review`, `Tests`, or `Setup`.
- Border radius: `6px-8px` for controls and panels, `12px-14px` only for the outer app shell.
- Avoid blur-heavy glass, decorative gradients, large shadows, and chart-first dashboard layouts.
