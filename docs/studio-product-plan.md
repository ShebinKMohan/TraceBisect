# TraceBisect Studio Product Plan

> Status: Active pivot plan for the portfolio-heavy web product.
> Branch: `feat/tracebisect-studio`.
> Audience: Codex, Claude, and any implementation agent continuing this work.

## Product Reframe

TraceBisect is no longer positioned as only a local CLI. The CLI remains the core
engine, but the portfolio-grade product is **TraceBisect Studio**: a SaaS-style
web dashboard for debugging AI-agent regressions.

The simplest product sentence is:

> TraceBisect Studio compares two AI-agent runs, finds the first meaningful
> behavior change, visualizes the regression, and exports a pytest test so the
> bug cannot silently return.

The comparison to existing tools:

- Langfuse, LangSmith, and OpenTelemetry show what happened in one run.
- TraceBisect Studio shows where behavior changed between two runs.
- The CLI/export layer turns that finding into a CI regression test.

## Current Engine Assets

The existing Python package is valuable and should not be rewritten casually.
Use it as the backend engine:

- `tracebisect.schema` — canonical trace dataclasses.
- `tracebisect.jsonl` — `.tbtrace` JSONL read/write.
- `tracebisect.otel` — OpenTelemetry/OpenInference JSON import.
- `tracebisect.align` — deterministic trace alignment.
- `tracebisect.diff` — V1 divergence detection.
- `tracebisect.render` — terminal rendering.
- `tracebisect.testing` — generated-pytest runtime.
- `tracebisect.demo` — deterministic refund-search baseline/candidate traces.

The first Studio implementation should wrap these modules instead of duplicating
logic in TypeScript or a new service.

## MVP Scope

The first Studio MVP is deliberately narrow:

1. A FastAPI backend that exposes the existing engine.
2. A Next.js 16 frontend that renders a real comparison report.
3. A seeded demo using the existing refund-search traces.
4. Upload support for `.tbtrace` and OTel/OpenInference JSON.
5. A compare action that returns first-divergence data and pytest export text.

This is enough for a recruiter, interviewer, or engineer to understand the
product without installing the CLI first.

## Initial Architecture

Use a two-app structure inside this repository:

```text
src/tracebisect/studio/
  api.py        # FastAPI app and HTTP models
  service.py    # Pure-Python service wrapping tracebisect engine

studio/web/
  package.json
  app/
    layout.tsx
    page.tsx
    globals.css
  components/
  lib/
```

Backend:

- FastAPI, served locally with `uvicorn tracebisect.studio.api:app`.
- In-memory storage for the MVP.
- Upload endpoint parses `.tbtrace` through `read_trace`.
- Upload endpoint parses OTel JSON through `import_otel_json`.
- Compare endpoint calls `align`, `detect_divergences`, and export helpers.

Frontend:

- Next.js 16 App Router in `studio/web`.
- Standardized palette: `#2C6975`, `#68B2A0`, `#CDE0C9`, `#E0ECDE`, `#FFFFFF`.
- Light and dark dashboard modes with the same component structure.
- No auth, billing, teams, or database in the first slice.
- Show the seeded demo immediately on first load.
- UI must look like a product dashboard, not a docs page.

## Required Screens

The first usable Studio UI should include:

- Dashboard header with product positioning.
- Project/run summary for the demo refund agent.
- Trace timeline/tree for baseline and candidate.
- First divergence card with severity, type, expected, actual, and impact.
- Before/after payload diff panel.
- Copyable pytest regression test block.
- Upload controls for baseline and candidate traces.

## Integration Story

Do not overbuild direct integrations immediately.

V1 Studio supports:

- `.tbtrace` upload.
- OTel/OpenInference JSON upload.
- OpenTelemetry-first integration story.

Later adapters can add:

- Langfuse direct import.
- LangSmith direct import.
- GitHub PR/test generation.
- Persistent Postgres projects and comparison history.

This means the public pitch can mention Langfuse/LangSmith compatibility through
OpenTelemetry-style trace data, but the first implementation should not pretend
to have direct SaaS API integrations until they are built.

See `docs/studio-langfuse-research.md` for the Langfuse-derived architecture
notes. That research should guide the Studio roadmap, but TraceBisect should
rebuild the relevant patterns around its comparison engine instead of copying
Langfuse wholesale.

See `docs/studio-production-hardening.md` for the current API, upload,
throttling, and security boundary. The local MVP is hardened against obvious
bad inputs, but auth, persistence, API keys, and distributed rate limiting are
still separate SaaS milestones.

## Out Of Scope For This Slice

Do not add these in the first Studio MVP:

- Authentication.
- Billing.
- Team RBAC.
- Multi-tenant production isolation.
- Prompt management.
- Full eval platform.
- Hosted long-term trace storage.
- Langfuse/LangSmith native API import.
- GitHub App / marketplace integration.

Those can be portfolio expansion phases after the visual demo works.

## Validation Expectations

Every implementation pass should keep the existing CLI product green:

```bash
conda run -n tracebisect-dev pytest
conda run -n tracebisect-dev ruff check .
conda run -n tracebisect-dev ruff format --check .
conda run -n tracebisect-dev mypy --strict src/tracebisect
```

For the web app:

```bash
cd studio/web
npm install
npm run build
```

When a dev server is started, verify the UI in a browser, not just through unit
tests. The product is now judged visually as well as functionally.
