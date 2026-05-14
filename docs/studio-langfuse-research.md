# TraceBisect Studio Langfuse Research Notes

> Status: Product and architecture reference notes for the SaaS pivot. Langfuse was inspected read-only at commit `1e3535e126fc918781f45753706ff0f576b12175`. Do not treat this as permission to copy Enterprise-licensed code.

## License Boundary

Langfuse is open core. Its core code is MIT licensed, while `ee/`, `web/src/ee/`, and `worker/src/ee/` use Langfuse's Enterprise License. TraceBisect should use Langfuse as an architecture and product reference, not as a code transplant. If any MIT-covered code is copied later, preserve the MIT notice in `THIRD_PARTY_NOTICES.md` and keep the copied section small and auditable.

## Product Lesson

TraceBisect should not become a smaller clone of Langfuse. Langfuse, LangSmith, and OpenTelemetry explain what happened inside a run. TraceBisect's stronger wedge is comparison: import two runs, align them, find the first meaningful behavior change, and export a regression test.

The Studio UI should therefore be a trace-comparison workbench first, not a generic analytics dashboard.

## UI Patterns To Rebuild In TraceBisect

- Dense trace/run table as the primary page.
- Compact toolbar with search, time range, source, severity, divergence type, and status filters.
- Row inspector or right-side drawer so users can inspect a run without losing table context.
- Full comparison view with a tree/timeline on the left and a tabbed inspector on the right.
- Tabs for `Diff`, `Input / Output`, `Raw JSON`, and `Pytest`.
- URL-synced state for selected run, selected node, active tab, search, and filters.
- Local/session storage for column visibility, row density, and drawer width.

## TraceBisect Information Architecture

Use fewer modules than Langfuse:

- `Runs` — imported or recorded traces, grouped by scenario/session.
- `Comparisons` — baseline-vs-candidate pairs with first divergence and severity.
- `Regression Tests` — generated pytest artifacts and CI status.
- `Imports` — `.tbtrace`, OTel/OpenInference JSON, CLI recorder, and future Langfuse/LangSmith import setup.
- `Metrics` — fixed health cards only: comparisons, failed regressions, severity mix, tests generated, cost/token deltas.
- `Settings` — project-level import and API configuration.

Do not add prompt management, eval queues, annotation queues, billing, RBAC, or a custom dashboard builder until the comparison workflow is excellent.

## Backend Patterns To Adopt

- Treat `Trace` as the run/session object and `Event` as the observation/span object.
- Keep `project_id` / workspace scoping in the data model before real multi-tenancy, even if the first MVP stores data locally.
- Ingest small summary data first; fetch heavy payloads only for selected runs/events.
- Preserve source IDs: `trace_id`, `span_id`, `parent_span_id`, source convention, timestamps, name/type, input/output, metadata, model, usage, cost, status, and errors.
- Continue using OTel/OpenInference as the first serious ingestion path. Add direct Langfuse/LangSmith imports later only after the core comparison path is stable.

## Near-Term Implementation Backlog

1. Keep polishing the compact Studio workbench visual system.
2. Add table-first run filtering: source, severity, divergence type, and status.
3. Add a right inspector that can switch between run summary, first divergence, raw payload, and generated pytest.
4. Add a full comparison route for one baseline/candidate pair.
5. Persist comparisons in SQLite or Postgres-lite before adding multi-user SaaS concerns.
6. Add API-key scoped ingestion only after persistence exists.
7. Add Langfuse/LangSmith direct import as an MVP-plus feature, not the first product surface.
