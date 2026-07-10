# ADR-013: External cron trigger (cron-job.org) — GitHub schedule events demoted to fallback

**Date:** 2026-07-10 · **Status:** accepted · **Extends** ADR-004.

## Context

ADR-004 chose GitHub Actions cron and anticipated delays/drops as documented failure modes.
Production measurements over 2026-07-09/10 show the failure is chronic and severe for this
repository, not occasional:

- **Uniform ~3h20 delay** on daily events, two days running: predict scheduled 07:05 UTC ran
  10:27 (07-09) and 10:25 (07-10); the retry entries (07:50, 08:35) were delayed by the same
  amount (10:39, 11:28), so extra schedule entries cannot help.
- **~90% of high-frequency events dropped**: the keepalive scheduled every 12 minutes fired
  ~5 times/day (gaps of 1.5-3 h), so the Render cold-start mitigation was ineffective too.
- Consequences: both emissions flagged `late_issue=True` (correctly excluded from the headline
  record), and the 07-10 emission at 10:26 UTC even missed the 12:00 CEST day-ahead auction —
  a product-contract failure, despite every workflow succeeding once started.

`workflow_dispatch` events created via the REST API start within seconds; only `schedule`
events sit in the unreliable queue.

## Decision

Move the time-critical triggers to **cron-job.org** (free tier, purpose-built external cron):

1. **Keepalive**: GET the API `/health` directly every 10 min, 06:00-23:00 UTC (no GitHub
   involvement at all).
2. **Ingest** (06:30 UTC) and **predict** (07:05 UTC): POST to the GitHub REST API
   `.../actions/workflows/{ingest,predict}.yml/dispatches` with a **fine-grained PAT** scoped
   to this single repository with only *Actions: read and write* permission.

The existing `schedule:` entries in the workflows are **kept as a fallback tier**: ingestion is
idempotent and predictions are insert-only, so a delayed duplicate run is harmless, and if the
external service ever dies the system degrades to late-but-present emissions instead of none.

## Consequences

- Zero cost preserved; one new external dependency (cron-job.org) holding a minimal-scope PAT —
  rotate it if the account is ever in doubt.
- Punctuality SLA moves from "whenever GitHub's queue drains" (~3h20 observed) to seconds.
- The late-issue grace (2 h) and the headline-scoring rules are unchanged; with a working
  trigger the emissions should land ~07:05-07:10 UTC, well inside both the grace window and
  the 12:00 CEST auction deadline.
