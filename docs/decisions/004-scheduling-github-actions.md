# ADR-004: GitHub Actions cron, hardened against its documented failure modes

**Date:** 2026-07-07 · **Status:** accepted

## Context

The pipeline needs free daily/weekly scheduling. GitHub Actions `schedule:` on a public repo has unlimited minutes, but three documented reliability hazards:

1. **Scheduled workflows are disabled after 60 consecutive days without repo activity.**
2. Cron firing is best-effort: 10–30 min delays are routine; runs are occasionally **skipped entirely**, especially at the top of the hour.
3. A skipped run of a market-deadline job (the day-ahead forecast must be issued before the 12:00 CET SDAC gate) is unrecoverable — you cannot honestly re-issue a "day-ahead" forecast after the market clears.

## Decision

GitHub Actions stays (charter: no heavy orchestrators), with four hardenings:

- **Keepalive = product:** a daily job regenerates the README forecast-vs-actual chart and force-pushes it to a single-commit `charts` branch. Daily repo activity defeats hazard 1 with zero history bloat, and doubles as the always-fresh portfolio hero image.
- **Never schedule at `:00`** — all crons use minute offsets (e.g. `17 5 * * *`).
- **Sweeper pattern for the deadline job:** `predict.yml` has two `schedule:` entries (primary + a later sweeper); the first step exits 0 if today's prediction row already exists; a hard, loud abort if wall clock is past the safety cutoff. A skipped primary becomes a non-event.
- **Idempotent, self-healing ingestion:** the morning job re-ingests a sliding `[now−3d, now]` window (all sources, prices included), so a fully failed day heals itself the next morning. Watchdog fails loudly if the last successful ingestion is older than 26 h.

## Consequences

- Jobs must tolerate imprecise start times by design: features are built "as-of" a fixed nominal cutoff, never `now()`.
- GitHub's native workflow-failure emails are the alert channel; the alert path is drill-tested (deliberately broken once) before being relied on.
